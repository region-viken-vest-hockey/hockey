"""The single canonical-season mutation service.

Every production write to a promoted season goes through
:class:`CanonicalSeasonService`. The service owns one deterministic lifecycle:

    load snapshot
      -> mutate (schedule and/or decisions)
      -> verify the full candidate
      -> reconcile derived projections and decisions
      -> append history
      -> compute the canonical-state revision
      -> write both files through CanonicalSeasonStore

Schedule-changing operations (``move_tournament`` / ``apply_candidate`` /
``promote``) and decision-only operations (``approve`` / ``unapprove`` /
participation accept/revoke) share that boundary, so a decision-only change
still produces a fresh ``canonical_state_revision`` and a rejected mutation
leaves both canonical files untouched.

This module holds no persistence mechanics (that is the store) and no
scheduling rules (those are domain providers/verifier); it is the application
layer that sequences them.
"""

from __future__ import annotations

import copy
import os
from datetime import date as _date, datetime, timezone
from typing import Any, Mapping

from tournament_scheduler.canonical_baseline import approval_fingerprint, resolve_approval
from tournament_scheduler.canonical_state import (
    BANNED_DATES_KEY,
    CANONICAL_STATE_REVISION_KEY,
    CHANGE_PROTECTIONS_KEY,
    PARTICIPATION_ACCEPTANCES_KEY,
    REQUEST_CONSTRAINTS_KEY,
    canonical_state_revision,
    compute_canonical_state_revision,
    migrate_participation_acceptance_ids,
    participation_acceptance_id,
    schedule_fingerprint,
)
from tournament_scheduler.change_protections import (
    ACTIVE as CHANGE_PROTECTION_ACTIVE,
    RELEASED as CHANGE_PROTECTION_RELEASED,
    active_change_protections,
    append_change_protections,
    build_move_protections,
    build_swap_protections,
    protection_violations,
)
from tournament_scheduler.canonical_banned_dates import (
    ACTIVE as BANNED_DATE_ACTIVE,
    RELEASED as BANNED_DATE_RELEASED,
    BannedDateError,
    active_banned_date_records,
    append_banned_dates,
    banned_date_report as build_banned_date_report,
    project_banned_dates_into_problem,
    validate_and_normalize as normalize_banned_date,
)
from tournament_scheduler.request_constraints import (
    ACTIVE as REQUEST_CONSTRAINT_ACTIVE,
    RELEASED as REQUEST_CONSTRAINT_RELEASED,
    RequestConstraintError,
    active_request_constraints,
    append_request_constraints,
    request_constraint_records,
    request_constraint_report,
    request_constraint_violations,
    validate_and_normalize as normalize_request_constraint,
)
from tournament_scheduler.guest_slots import (
    DEFAULT_GUEST_AGE_GROUPS,
    GUEST_SLOT_FILLED,
    GUEST_SLOT_OPEN,
    GUEST_SLOT_RELEASED,
    active_guest_slot_count,
    active_guest_slots,
    capacity_places,
    guest_slot_records,
    guest_slot_summary,
    new_guest_slot,
    rvv_team_count,
    rvv_teams,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    DECISIONS_SCHEMA_VERSION,
    SEASON_STATE_SCHEMA_VERSION,
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
    DEFAULT_SEASON_ROOT,
    SeasonStateError,
    season_id_from_plan,
)
from tournament_scheduler.operational_acceptability import (
    check_operational_acceptability,
    required_opt_in_flags,
)
from tournament_scheduler.participation_targets import OPERATOR_ACCEPTED
from tournament_scheduler.plan_derived_state import reconcile_plan_derived_state
from tournament_scheduler.planning_contract import extract_candidate, verify_candidate
from tournament_scheduler.serialization.season_plan import SEASON_PLAN_SCHEMA_VERSION

# Statuses an approval lifecycle can be in. ``stale_approval`` means a
# previously approved tournament changed without an explicit unapprove, so
# the stored fingerprint no longer proves the current placement.
APPROVED_STATUS = "approved"
STALE_APPROVAL_STATUS = "stale_approval"
PENDING_REVIEW_STATUS = "pending_review"


def _operator_identity(actor: str | None) -> str:
    return actor or os.environ.get("RVV_OPERATOR") or os.environ.get("USER") or "operator"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _record_promotion_trace(
    work_dir: str | os.PathLike[str],
    *,
    run_id: str | None,
    season: str,
    actor: str | None,
    schedule: dict[str, Any],
    candidate_fingerprint: str | None,
    export_fingerprint: str | None,
) -> None:
    """Best-effort controller-trace event for a canonical promotion.

    Promotion is the deliberate handoff from an audited review export to the
    operational baseline. Recording it in the same append-only trace as the
    convergence decisions lets an analyst link the exact reviewed candidate to
    the season revision that became canonical, without a second evidence file.
    """
    try:
        from ..pipeline.controller_trace import EVENT_PROMOTION, ControllerTrace

        ControllerTrace(work_dir, run_id).emit(
            EVENT_PROMOTION,
            season=season,
            actor=_operator_identity(actor),
            schedule_revision=str(schedule.get("revision") or ""),
            schedule_fingerprint=str(schedule.get("fingerprint") or ""),
            candidate_fingerprint=str(candidate_fingerprint or ""),
            export_fingerprint=str(export_fingerprint or ""),
        )
    except Exception:
        # Observability must never fail or roll back a committed promotion.
        pass


def _initial_decisions(plan_dict: dict[str, Any]) -> dict[str, Any]:
    records: dict[str, Any] = {}
    for tournament in plan_dict.get("tournaments", []):
        tournament_id = str(tournament.get("id") or "")
        if not tournament_id:
            continue
        records[tournament_id] = {
            "status": PENDING_REVIEW_STATUS,
            "placement_locked": False,
            "participants_locked": False,
            "approved_fingerprint": None,
            "approved_at": None,
            "approved_by": None,
            "note": "",
        }
    return records


def _append_decision_history(
    decisions: dict[str, Any],
    *,
    event: str,
    tournament_id: str,
    actor: str | None,
    now: str,
    tournament_fingerprint: str | None = None,
    previous_fingerprint: str | None = None,
    note: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    """Append a durable approval-lifecycle audit entry to decisions.json."""

    history = decisions.setdefault("history", [])
    entry = {
        "event": event,
        "tournament_id": tournament_id,
        "actor": _operator_identity(actor),
        "at": now,
        "tournament_fingerprint": tournament_fingerprint,
        "previous_fingerprint": previous_fingerprint,
        "schedule_fingerprint": decisions.get("schedule_fingerprint"),
        "note": note or "",
    }
    if details:
        entry["details"] = details
    history.append(entry)


def _reconcile_decisions(
    existing: dict[str, Any],
    plan_dict: dict[str, Any],
    *,
    now: str,
) -> dict[str, Any]:
    """Carry approval/lock state forward for surviving tournaments only.

    A tournament whose identity survives keeps its record. A previously
    approved tournament whose facts changed (possible only when it was approved
    without a placement lock) becomes an explicit ``stale_approval`` with its
    old fingerprint retained for audit. Removed tournaments drop their records;
    new ids start at ``pending_review``.
    """

    reconciled: dict[str, Any] = {}
    for tournament in plan_dict.get("tournaments", []) or []:
        tournament_id = str(tournament.get("id") or "")
        if not tournament_id:
            continue
        record = dict(existing.get(tournament_id) or {})
        if not record:
            record = {
                "status": PENDING_REVIEW_STATUS,
                "placement_locked": False,
                "participants_locked": False,
                "approved_fingerprint": None,
                "approved_at": None,
                "approved_by": None,
                "note": "",
            }
        elif record.get("status") in (APPROVED_STATUS, STALE_APPROVAL_STATUS) or record.get(
            "approved_fingerprint"
        ):
            approved_fingerprint = record.get("approved_fingerprint")
            fingerprint_matches = bool(approved_fingerprint) and approved_fingerprint == approval_fingerprint(
                tournament
            )
            if record.get("status") == STALE_APPROVAL_STATUS or not fingerprint_matches:
                record = {
                    "status": STALE_APPROVAL_STATUS,
                    "placement_locked": False,
                    "participants_locked": False,
                    "approved_fingerprint": approved_fingerprint,
                    "approved_at": record.get("approved_at"),
                    "approved_by": record.get("approved_by"),
                    "note": record.get("note") or "",
                    "stale_at": record.get("stale_at") or now,
                    "stale_reason": "approval invalidated by schedule change",
                }
        reconciled[tournament_id] = record
    return reconciled


def _placement_snapshot(tournament: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "date": tournament.get("date"),
        "arena": tournament.get("arena"),
        "host_club": tournament.get("host_club"),
        "start_time": tournament.get("start_time"),
    }


def _parse_iso_date_for_move(value: str, field: str) -> _date:
    try:
        return _date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise SeasonStateError(f"Invalid {field}: {value!r}; expected YYYY-MM-DD") from exc


def _validate_start_time_for_move(value: str | None) -> None:
    if value is None:
        return
    try:
        hour_s, minute_s = str(value).split(":", 1)
        hour = int(hour_s)
        minute = int(minute_s)
    except (TypeError, ValueError) as exc:
        raise SeasonStateError(f"Invalid start_time: {value!r}; expected HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise SeasonStateError(f"Invalid start_time: {value!r}; expected HH:MM")


def _attributable_blockers(
    verification: dict[str, Any],
    tournament_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a verification result into this tournament's hard/unresolved blockers."""

    hard: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for violation in verification.get("violations") or []:
        owner = violation.get("tournament_id")
        if owner is not None:
            if str(owner) == tournament_id:
                hard.append(violation)
            continue
        if tournament_id and tournament_id in str(violation.get("message") or ""):
            hard.append(violation)
    for placement in verification.get("manual_external_conflict_placements") or []:
        if str(placement.get("tournament_id") or "") == tournament_id:
            unresolved.append(
                {
                    "code": "manual_external_conflict_placements",
                    "message": (
                        f"Tournament {tournament_id} has a known external calendar conflict; "
                        "resolve it before approving"
                    ),
                    "tournament_id": tournament_id,
                }
            )
    return hard, unresolved


def _resolve_plan_problem(
    schedule: Mapping[str, Any],
    problem: dict[str, Any] | None,
    decisions: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the verification problem for a canonical plan mutation.

    Callers may pass it explicitly; otherwise the promoted verification
    context is the durable owner of the normalized planning problem. Active
    canonical operator banned dates are always projected into the problem's
    existing ``manual_adjustments.banned_dates`` read path, so every
    schedule-changing boundary and every repair/search consumer sees the same
    authoritative ban set.
    """

    if isinstance(problem, Mapping):
        resolved: dict[str, Any] | None = dict(problem)
    else:
        context = schedule.get("verification_context")
        candidate_problem = context.get("problem") if isinstance(context, Mapping) else None
        resolved = dict(candidate_problem) if isinstance(candidate_problem, Mapping) else None
    if resolved is None:
        return None
    if decisions is not None:
        return project_banned_dates_into_problem(resolved, decisions)
    return resolved


def _configured_capacity(problem: Mapping[str, Any] | None, age_group: str) -> int | None:
    """Return the configured participant+guest place capacity for an age group."""

    parallel = ((problem or {}).get("parallel_games") or {}).get(age_group)
    if isinstance(parallel, int) and parallel > 0:
        return parallel * 2
    return None


def _regenerate_tournament_games(
    tournament: dict[str, Any],
    problem: Mapping[str, Any] | None,
) -> None:
    """Regenerate a canonical tournament's games from its current participants.

    Guest participants are preserved as guests. While a guest place is still
    open the generated games are explicitly provisional (a round-robin among
    the known RVV teams); filling the place regenerates the complete schedule.
    """

    from tournament_scheduler.game_generation import generate_tournament_games
    from tournament_scheduler.models import Team

    age_group = str(tournament.get("age_group") or "")
    teams = [
        Team(
            club=str(team.get("club") or ""),
            label=str(team.get("label") or ""),
            age_group=str(team.get("age_group") or age_group),
            target_tournament_count=team.get("target_tournament_count"),
            guest=bool(team.get("guest", False)),
        )
        for team in tournament.get("teams", [])
    ]
    parallel = int(((problem or {}).get("parallel_games") or {}).get(age_group, 1) or 1)
    rounds = ((problem or {}).get("rounds_per_tournament") or {}).get(age_group)
    tournament["games"] = [
        {
            "home": game.home.label,
            "away": game.away.label,
            "parallel_slot": game.parallel_slot,
            "round_number": game.round_number,
        }
        for game in generate_tournament_games(teams, parallel, rounds)
    ]


def _apply_move_to_plan(
    plan: dict[str, Any],
    *,
    tournament_id: str,
    date: str | None = None,
    arena: str | None = None,
    host_club: str | None = None,
    start_time: str | None = None,
    problem: Mapping[str, Any] | None = None,
    allow_cross_half: bool = False,
) -> dict[str, Any]:
    """Apply one placement move to an in-memory plan.

    Single owner of placement-move mutation semantics, shared by the direct
    ``move_tournament`` boundary and atomic batch maintenance. It validates the
    target (known, not cancelled), the planning window and the before/after
    planning half, resolves an arena to its owning club, and writes only the
    placement fields the caller actually changed. Lock, protection, constraint,
    hard-verification and operational gates stay at the caller's boundary.
    """

    if not any(value is not None for value in (date, arena, host_club, start_time)):
        raise SeasonStateError(
            "Refusing canonical move: specify at least one target placement field"
        )
    target_date = _parse_iso_date_for_move(date, "date") if date is not None else None
    _validate_start_time_for_move(start_time)

    tournaments = plan.get("tournaments", []) or []
    target = next((t for t in tournaments if str(t.get("id")) == tournament_id), None)
    if target is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    if target.get("cancelled"):
        raise SeasonStateError(f"Tournament {tournament_id} is cancelled and cannot be moved")

    original_placement = _placement_snapshot(target)
    original_fingerprint = approval_fingerprint(target)

    if target_date is not None:
        start_raw = plan.get("start_date") or (problem or {}).get("start_date")
        end_raw = plan.get("end_date") or (problem or {}).get("end_date")
        if start_raw and end_raw:
            window_start = _parse_iso_date_for_move(str(start_raw), "season start_date")
            window_end = _parse_iso_date_for_move(str(end_raw), "season end_date")
            if not (window_start <= target_date <= window_end):
                raise SeasonStateError(
                    f"Cannot move {tournament_id}: target date {target_date.isoformat()} is outside "
                    f"the planning window {window_start.isoformat()}\u2013{window_end.isoformat()}"
                )
            if not allow_cross_half and original_placement.get("date"):
                from tournament_scheduler import planning_half

                split = planning_half.christmas_split_date(window_start, window_end)
                old_half = planning_half.tournament_half(
                    _parse_iso_date_for_move(str(original_placement["date"]), "current date"), split
                )
                new_half = planning_half.tournament_half(target_date, split)
                if old_half != new_half:
                    raise SeasonStateError(
                        f"Cannot move {tournament_id}: target date crosses planning half "
                        f"({old_half} -> {new_half}); pass allow_cross_half only for an explicit policy exception"
                    )

    effective_host_club = host_club
    if arena is not None and host_club is None:
        from tournament_scheduler.club_distances import arena_to_club

        owner = arena_to_club(arena)
        if owner and owner != target.get("host_club"):
            effective_host_club = owner

    changed = False
    for field, value in {
        "date": date,
        "arena": arena,
        "host_club": effective_host_club,
        "start_time": start_time,
    }.items():
        if value is not None and target.get(field) != value:
            target[field] = value
            changed = True
    if changed:
        target.pop("requires_host_confirmation", None)
        target.pop("host_confirmation_reason", None)
    return {
        "tournament": target,
        "original_placement": original_placement,
        "original_fingerprint": original_fingerprint,
        "effective_host_club": effective_host_club,
        "changed": changed,
    }


def _apply_swap_to_plan(
    plan: dict[str, Any],
    *,
    tournament_a_id: str,
    team_a_label: str,
    tournament_b_id: str,
    team_b_label: str,
    problem: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply one same-age participant swap to an in-memory plan.

    Single owner of roster-swap mutation semantics, shared by the direct
    ``swap_participants`` boundary and atomic batch maintenance: tournament
    existence/cancellation validation, the same-age restriction, participant
    lookup (guests are rejected), duplicate/no-op checks, the actual exchange
    and regeneration of both tournaments' games. Lock, protection, constraint,
    hard-verification, hosting-responsibility and consequence gates stay at the
    caller's boundary.
    """

    if tournament_a_id == tournament_b_id:
        raise SeasonStateError("Participant swap requires two different tournaments")

    tournaments = plan.get("tournaments", []) or []
    by_id = {
        str(tournament.get("id") or ""): tournament
        for tournament in tournaments
        if tournament.get("id")
    }
    tournament_a = by_id.get(tournament_a_id)
    tournament_b = by_id.get(tournament_b_id)
    if tournament_a is None:
        raise SeasonStateError(
            f"Unknown tournament id in canonical schedule: {tournament_a_id}"
        )
    if tournament_b is None:
        raise SeasonStateError(
            f"Unknown tournament id in canonical schedule: {tournament_b_id}"
        )
    for tournament_id, tournament in (
        (tournament_a_id, tournament_a),
        (tournament_b_id, tournament_b),
    ):
        if tournament.get("cancelled"):
            raise SeasonStateError(
                f"Tournament {tournament_id} is cancelled and cannot participate in a roster swap"
            )

    age_group_a = str(tournament_a.get("age_group") or "")
    age_group_b = str(tournament_b.get("age_group") or "")
    if not age_group_a or age_group_a != age_group_b:
        raise SeasonStateError(
            "Participant swap requires tournaments in the same age group; "
            f"got {age_group_a or '<missing>'} and {age_group_b or '<missing>'}"
        )

    def locate_team(
        tournament: dict[str, Any],
        *,
        tournament_id: str,
        label: str,
    ) -> tuple[int, dict[str, Any]]:
        matches = [
            (index, team)
            for index, team in enumerate(tournament.get("teams", []) or [])
            if str(team.get("label") or "") == label
        ]
        if not matches:
            raise SeasonStateError(
                f"Team {label!r} is not a participant in tournament {tournament_id}"
            )
        if len(matches) > 1:
            raise SeasonStateError(
                f"Team label {label!r} is ambiguous in tournament {tournament_id}"
            )
        index, team = matches[0]
        if bool(team.get("guest", False)):
            raise SeasonStateError(
                f"Team {label!r} in tournament {tournament_id} is a guest participant; "
                "use the guest-slot lifecycle instead"
            )
        return index, team

    index_a, team_a = locate_team(
        tournament_a, tournament_id=tournament_a_id, label=team_a_label
    )
    index_b, team_b = locate_team(
        tournament_b, tournament_id=tournament_b_id, label=team_b_label
    )

    def team_identity(team: Mapping[str, Any], fallback_age_group: str) -> tuple[str, str, str]:
        return (
            str(team.get("club") or ""),
            str(team.get("label") or ""),
            str(team.get("age_group") or fallback_age_group),
        )

    identity_a = team_identity(team_a, age_group_a)
    identity_b = team_identity(team_b, age_group_b)
    if identity_a == identity_b:
        raise SeasonStateError("Participant swap would be a no-op")

    for index, existing in enumerate(tournament_a.get("teams", []) or []):
        if index != index_a and team_identity(existing, age_group_a) == identity_b:
            raise SeasonStateError(
                f"Cannot swap {team_b_label!r} into {tournament_a_id}: "
                "that team already participates there"
            )
    for index, existing in enumerate(tournament_b.get("teams", []) or []):
        if index != index_b and team_identity(existing, age_group_b) == identity_a:
            raise SeasonStateError(
                f"Cannot swap {team_a_label!r} into {tournament_b_id}: "
                "that team already participates there"
            )

    before_fingerprint_a = approval_fingerprint(tournament_a)
    before_fingerprint_b = approval_fingerprint(tournament_b)
    original_team_a = copy.deepcopy(team_a)
    original_team_b = copy.deepcopy(team_b)
    tournament_a["teams"][index_a] = copy.deepcopy(team_b)
    tournament_b["teams"][index_b] = copy.deepcopy(team_a)
    _regenerate_tournament_games(tournament_a, problem)
    _regenerate_tournament_games(tournament_b, problem)
    return {
        "tournament_a": tournament_a,
        "tournament_b": tournament_b,
        "team_a": original_team_a,
        "team_b": original_team_b,
        "index_a": index_a,
        "index_b": index_b,
        "identity_a": identity_a,
        "identity_b": identity_b,
        "age_group": age_group_a,
        "before_fingerprint_a": before_fingerprint_a,
        "before_fingerprint_b": before_fingerprint_b,
    }


def _apply_cancel_to_plan(
    plan: dict[str, Any],
    *,
    tournament_id: str,
    reason: str = "",
) -> dict[str, Any]:
    """Mark one tournament cancelled in an in-memory plan.

    Cancellation is represented by the first-class ``cancelled`` flag the
    verifier already honours. The full canonical gates decide whether a batch
    containing a cancellation is acceptable; this helper only applies the
    domain fact.
    """

    tournaments = plan.get("tournaments", []) or []
    target = next((t for t in tournaments if str(t.get("id")) == tournament_id), None)
    if target is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    if target.get("cancelled"):
        raise SeasonStateError(f"Tournament {tournament_id} is already cancelled")
    if active_guest_slots(target):
        raise SeasonStateError(
            f"Cannot cancel {tournament_id}: it still has active guest reservations; "
            "release them explicitly before cancelling"
        )
    target["cancelled"] = True
    if reason:
        target["cancellation_reason"] = reason
    else:
        target.pop("cancellation_reason", None)
    return {
        "tournament_id": tournament_id,
        "reason": reason,
        "original_date": target.get("date"),
        "age_group": target.get("age_group"),
    }


_BATCH_MOVE_FIELDS: tuple[str, ...] = ("date", "arena", "host_club", "start_time")


def _normalize_batch_operation(raw: Any) -> dict[str, Any]:
    """Validate and normalize one atomic-batch operation payload."""

    if not isinstance(raw, Mapping):
        raise SeasonStateError("Each batch operation must be a JSON object")
    op = str(raw.get("op") or raw.get("operation") or raw.get("action") or "").strip().lower()
    if op in ("move", "placement_move"):
        tournament_id = str(raw.get("tournament_id") or raw.get("id") or "").strip()
        if not tournament_id:
            raise SeasonStateError("A batch move operation requires tournament_id")
        operation = {
            "op": "move",
            "tournament_id": tournament_id,
            "date": raw.get("date"),
            "arena": raw.get("arena"),
            "host_club": raw.get("host_club"),
            "start_time": raw.get("start_time"),
            "allow_cross_half": bool(raw.get("allow_cross_half", False)),
        }
        if not any(operation[field] is not None for field in _BATCH_MOVE_FIELDS):
            raise SeasonStateError(
                f"A batch move for {tournament_id} requires at least one placement field"
            )
        return operation
    if op in ("swap", "swap_participants", "participant_swap"):
        resolved = {
            "tournament_a": str(raw.get("tournament_a") or raw.get("tournament_a_id") or "").strip(),
            "team_a": str(raw.get("team_a") or raw.get("team_a_label") or "").strip(),
            "tournament_b": str(raw.get("tournament_b") or raw.get("tournament_b_id") or "").strip(),
            "team_b": str(raw.get("team_b") or raw.get("team_b_label") or "").strip(),
        }
        missing = sorted(key for key, value in resolved.items() if not value)
        if missing:
            raise SeasonStateError(
                "A batch swap_participants operation requires " + ", ".join(missing)
            )
        return {"op": "swap_participants", **resolved}
    if op in ("cancel", "cancel_tournament"):
        tournament_id = str(raw.get("tournament_id") or raw.get("id") or "").strip()
        if not tournament_id:
            raise SeasonStateError("A batch cancel operation requires tournament_id")
        return {
            "op": "cancel",
            "tournament_id": tournament_id,
            "reason": str(raw.get("reason") or raw.get("note") or ""),
        }
    raise SeasonStateError(f"Unsupported batch operation {op!r}")


def _batch_operation_tournament_ids(operation: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the tournament ids one normalized batch operation references."""

    op = operation.get("op")
    if op == "move":
        return (str(operation.get("tournament_id") or ""),)
    if op == "swap_participants":
        return (
            str(operation.get("tournament_a") or ""),
            str(operation.get("tournament_b") or ""),
        )
    if op == "cancel":
        return (str(operation.get("tournament_id") or ""),)
    return ()


def _guest_reservation_signature(plan: Mapping[str, Any]) -> dict[str, list[tuple[str, str]]]:
    """Return ``{tournament_id: [(slot_id, status), ...]}`` for active reservations.

    Used to reject a canonical replan/apply that would silently drop or
    rewrite a reservation instead of going through reserve/fill/release.
    """

    signature: dict[str, list[tuple[str, str]]] = {}
    for tournament in plan.get("tournaments", []) or []:
        active = [
            (str(record.get("id") or ""), str(record.get("status") or GUEST_SLOT_OPEN))
            for record in active_guest_slots(tournament)
        ]
        if active:
            signature[str(tournament.get("id") or "")] = active
    return signature


class CanonicalSeasonService:
    """Application-layer mutation service over a canonical-season store."""

    def __init__(
        self,
        store: CanonicalSeasonStore | None = None,
        *,
        root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    ) -> None:
        self.store = store or CanonicalSeasonStore(root)

    # -- lifecycle ---------------------------------------------------------

    def load(self, season: str) -> CanonicalSeasonSnapshot:
        return self.store.load(season)

    def _commit(self, snapshot: CanonicalSeasonSnapshot, *, require_absent: bool = False) -> CanonicalSeasonSnapshot:
        """Persist a snapshot under one fresh canonical-state revision.

        The revision is computed from the *complete* new state, so schedule
        mutations and decision-only mutations both advance it. Derived
        projections and decisions must already have been reconciled by the
        caller; the store installs both files atomically.
        """

        decisions = dict(snapshot.decisions)
        migrate_participation_acceptance_ids(decisions)
        guard_violations = protection_violations(snapshot.schedule.get("plan") or {}, decisions)
        if guard_violations:
            messages = "; ".join(str(item.get("message")) for item in guard_violations)
            raise SeasonStateError(
                "Refusing canonical commit: it would undo an accepted change: " + messages
            )
        decisions[CANONICAL_STATE_REVISION_KEY] = compute_canonical_state_revision(
            snapshot.schedule, decisions
        )
        committed = snapshot.with_decisions(decisions)
        self.store.write(committed, require_absent=require_absent)
        return committed

    def _assert_request_constraints_satisfied(
        self,
        plan: Mapping[str, Any],
        decisions: Mapping[str, Any],
        *,
        action: str,
    ) -> None:
        """Refuse a schedule-changing commit that violates an active constraint.

        Request constraints are hard maintenance requirements: every canonical
        schedule-changing entry point calls this at its application boundary, so
        a generator/search that already considered the constraints is still
        re-checked against the authoritative active set. Decision-only writes
        (recording a new request, approval, release) deliberately do not.
        """

        violations = request_constraint_violations(plan, decisions)
        if not violations:
            return
        messages = "; ".join(str(item.get("message")) for item in violations)
        raise SeasonStateError(
            f"Refusing canonical {action}: it violates an active request constraint: {messages}"
        )

    # -- request constraints ----------------------------------------------

    def request_constraint_report(
        self,
        season: str,
        *,
        include_released: bool = False,
    ) -> dict[str, Any]:
        """Read-only lifecycle + derived satisfaction of request constraints."""

        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        constraints = request_constraint_report(
            schedule.get("plan") or {},
            decisions,
            include_released=include_released,
        )
        return {
            "season": season,
            "revision": schedule.get("revision"),
            "canonical_state_revision": canonical_state_revision(schedule, decisions),
            "active_count": sum(
                1 for item in constraints if item.get("status") == REQUEST_CONSTRAINT_ACTIVE
            ),
            "unsatisfied_count": sum(
                1
                for item in constraints
                if item.get("status") == REQUEST_CONSTRAINT_ACTIVE and not item.get("satisfied")
            ),
            "constraints": constraints,
        }

    def add_request_constraint(
        self,
        *,
        season: str,
        type: str,
        request_id: str,
        teams: list[Mapping[str, Any]] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        min_days: int | None = None,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Persist one validated typed request constraint (decision-only write).

        Recording a real club/operator request may intentionally leave the
        current schedule non-conforming. This write is therefore always allowed
        when the definition itself is valid: it persists the constraint, reports
        its current structured violation(s), and advances the canonical-state
        revision so every previously generated repair/search option is stale.
        The schedule is not touched.
        """

        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        plan = schedule.get("plan") or {}
        try:
            normalized = normalize_request_constraint(
                {
                    "type": type,
                    "request_id": request_id,
                    "teams": [dict(team) for team in (teams or [])],
                    "date_from": date_from,
                    "date_to": date_to,
                    "min_days": min_days,
                },
                plan,
            )
        except RequestConstraintError as exc:
            raise SeasonStateError(str(exc)) from exc

        now = _now_iso()
        resolved_actor = _operator_identity(actor)
        record = {
            **normalized,
            "status": REQUEST_CONSTRAINT_ACTIVE,
            "created_at": now,
            "created_by": resolved_actor,
            "note": note or "",
            "source_revision": canonical_state_revision(schedule, decisions),
        }
        existing = request_constraint_records(decisions, include_released=True)
        stored = next(
            (item for item in existing if str(item.get("id") or "") == record["id"]),
            None,
        )
        if stored is not None:
            # Idempotent retry: return the already-stored constraint with its
            # current derived status instead of creating a conflicting copy.
            is_active = str(stored.get("status") or REQUEST_CONSTRAINT_ACTIVE) == REQUEST_CONSTRAINT_ACTIVE
            violations = (
                [
                    violation
                    for violation in request_constraint_violations(plan, decisions)
                    if str(violation.get("constraint_id") or "") == record["id"]
                ]
                if is_active
                else []
            )
            return {
                "season": season,
                "created": False,
                "constraint": {
                    **stored,
                    "satisfied": (not violations) if is_active else None,
                    "violations": violations,
                },
                "canonical_state_revision": canonical_state_revision(schedule, decisions),
            }
        append_request_constraints(decisions, [record])

        _append_decision_history(
            decisions,
            event="add_request_constraint",
            tournament_id="",
            actor=resolved_actor,
            now=now,
            note=note,
            details={
                "constraint_id": record["id"],
                "type": record["type"],
                "request_id": record["request_id"],
                "teams": record["teams"],
                "date_from": record.get("date_from"),
                "date_to": record.get("date_to"),
                "min_days": record.get("min_days"),
            },
        )
        updated = {**decisions, "updated_at": now}
        committed = self._commit(snapshot.with_decisions(updated))
        violations = [
            violation
            for violation in request_constraint_violations(
                committed.schedule.get("plan") or {}, committed.decisions
            )
            if str(violation.get("constraint_id") or "") == record["id"]
        ]
        return {
            "season": season,
            "created": True,
            "constraint": {
                **record,
                "satisfied": not violations,
                "violations": violations,
            },
            "canonical_state_revision": canonical_state_revision(
                committed.schedule, committed.decisions
            ),
        }

    def release_request_constraints(
        self,
        *,
        season: str,
        constraint_ids: list[str] | None = None,
        request_id: str | None = None,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Explicitly release/supersede request constraints with audit history."""

        wanted_ids = {str(value) for value in (constraint_ids or []) if str(value)}
        wanted_request = str(request_id or "")
        if not wanted_ids and not wanted_request:
            raise SeasonStateError(
                "Refusing constraint release: provide --constraint-id and/or --request-id"
            )

        snapshot = self.load(season)
        decisions = copy.deepcopy(snapshot.decisions)
        records = decisions.get(REQUEST_CONSTRAINTS_KEY, []) or []
        now = _now_iso()
        resolved_actor = _operator_identity(actor)
        released: list[str] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            if str(record.get("status") or REQUEST_CONSTRAINT_ACTIVE) != REQUEST_CONSTRAINT_ACTIVE:
                continue
            matches_id = str(record.get("id") or "") in wanted_ids
            matches_request = bool(wanted_request) and str(record.get("request_id") or "") == wanted_request
            if not (matches_id or matches_request):
                continue
            record["status"] = REQUEST_CONSTRAINT_RELEASED
            record["released_at"] = now
            record["released_by"] = resolved_actor
            record["release_reason"] = note or ""
            released.append(str(record.get("id") or ""))

        if not released:
            raise SeasonStateError("No active request constraints matched the release request")

        decisions["updated_at"] = now
        _append_decision_history(
            decisions,
            event="release_request_constraint",
            tournament_id="",
            actor=resolved_actor,
            now=now,
            note=note,
            details={"released_constraint_ids": released, "request_id": wanted_request},
        )
        committed = self._commit(snapshot.with_decisions(decisions))
        return {
            "season": season,
            "canonical_state_revision": canonical_state_revision(
                committed.schedule, committed.decisions
            ),
            "released_constraint_ids": released,
            "active_count": len(active_request_constraints(committed.decisions)),
        }

    # -- banned dates ------------------------------------------------------

    def banned_date_report(
        self,
        season: str,
        *,
        include_released: bool = False,
    ) -> dict[str, Any]:
        """Read-only lifecycle + derived satisfaction of operator banned dates."""

        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        plan = schedule.get("plan") or {}
        entries = build_banned_date_report(plan, decisions, include_released=include_released)
        active = [entry for entry in entries if entry.get("status") == BANNED_DATE_ACTIVE]
        return {
            "season": season,
            "revision": schedule.get("revision"),
            "canonical_state_revision": canonical_state_revision(schedule, decisions),
            "active_count": len(active),
            "unsatisfied_count": sum(1 for entry in active if not entry.get("satisfied")),
            "affected_tournament_ids": sorted(
                {
                    tournament_id
                    for entry in active
                    for tournament_id in entry.get("affected_tournament_ids") or []
                }
            ),
            "banned_dates": entries,
        }

    def add_banned_date(
        self,
        *,
        season: str,
        date: str,
        request_id: str,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Persist one global operator date ban (policy/decision-only write).

        Recording a real-world date restriction is allowed even when the
        current schedule still has tournaments on that date, exactly like
        ``season add-constraint``: the ban is persisted, its current structured
        violation(s) are reported, and the canonical-state revision advances so
        every stale maintenance/search/batch option is invalidated. The
        schedule itself is not touched -- repair is a separate operation.
        """

        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        plan = schedule.get("plan") or {}
        try:
            normalized = normalize_banned_date(
                {"date": date, "request_id": request_id, "note": note}
            )
        except BannedDateError as exc:
            raise SeasonStateError(str(exc)) from exc

        existing = active_banned_date_records(decisions)
        stored = next(
            (
                record
                for record in existing
                if str(record.get("date") or "") == normalized["date"]
            ),
            None,
        )
        if stored is not None:
            entry = next(
                entry
                for entry in build_banned_date_report(plan, decisions)
                if str(entry.get("id") or "") == str(stored.get("id") or "")
            )
            return {
                "season": season,
                "created": False,
                "banned_date": entry,
                "canonical_state_revision": canonical_state_revision(schedule, decisions),
            }

        now = _now_iso()
        resolved_actor = _operator_identity(actor)
        record = {
            **normalized,
            "status": BANNED_DATE_ACTIVE,
            "created_at": now,
            "created_by": resolved_actor,
            "source_revision": canonical_state_revision(schedule, decisions),
        }
        updated = dict(decisions)
        append_banned_dates(updated, [record])
        _append_decision_history(
            updated,
            event="ban_date",
            tournament_id="",
            actor=resolved_actor,
            now=now,
            note=note,
            details={
                "banned_date_id": record["id"],
                "date": record["date"],
                "request_id": record["request_id"],
            },
        )
        updated["updated_at"] = now
        committed = self._commit(snapshot.with_decisions(updated))
        entry = next(
            entry
            for entry in build_banned_date_report(
                committed.schedule.get("plan") or {}, committed.decisions
            )
            if str(entry.get("id") or "") == record["id"]
        )
        return {
            "season": season,
            "created": True,
            "banned_date": entry,
            "canonical_state_revision": canonical_state_revision(
                committed.schedule, committed.decisions
            ),
        }

    def release_banned_dates(
        self,
        *,
        season: str,
        date_ids: list[str] | None = None,
        dates: list[str] | None = None,
        request_id: str | None = None,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Explicitly remove one or more active banned dates with audit history."""

        wanted_ids = {str(value) for value in (date_ids or []) if str(value)}
        wanted_dates = {str(value) for value in (dates or []) if str(value)}
        wanted_request = str(request_id or "")
        if not wanted_ids and not wanted_dates and not wanted_request:
            raise SeasonStateError(
                "Refusing ban release: provide --date, --ban-id and/or --request-id"
            )

        snapshot = self.load(season)
        decisions = copy.deepcopy(snapshot.decisions)
        records = decisions.get(BANNED_DATES_KEY, []) or []
        now = _now_iso()
        resolved_actor = _operator_identity(actor)
        released: list[str] = []
        released_dates: list[str] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            if str(record.get("status") or BANNED_DATE_ACTIVE) != BANNED_DATE_ACTIVE:
                continue
            matches_id = str(record.get("id") or "") in wanted_ids
            matches_date = str(record.get("date") or "") in wanted_dates
            matches_request = bool(wanted_request) and str(record.get("request_id") or "") == wanted_request
            if not (matches_id or matches_date or matches_request):
                continue
            record["status"] = BANNED_DATE_RELEASED
            record["released_at"] = now
            record["released_by"] = resolved_actor
            record["release_reason"] = note or ""
            released.append(str(record.get("id") or ""))
            released_dates.append(str(record.get("date") or ""))

        if not released:
            raise SeasonStateError("No active banned dates matched the release request")

        decisions["updated_at"] = now
        _append_decision_history(
            decisions,
            event="unban_date",
            tournament_id="",
            actor=resolved_actor,
            now=now,
            note=note,
            details={
                "released_banned_date_ids": released,
                "released_dates": released_dates,
                "request_id": wanted_request,
            },
        )
        committed = self._commit(snapshot.with_decisions(decisions))
        return {
            "season": season,
            "canonical_state_revision": canonical_state_revision(
                committed.schedule, committed.decisions
            ),
            "released_banned_date_ids": released,
            "released_dates": sorted(set(released_dates)),
            "active_count": len(active_banned_date_records(committed.decisions)),
        }

    # -- promotion ---------------------------------------------------------

    def promote(
        self,
        *,
        work_dir: str | os.PathLike[str] = ".pipeline",
        season: str | None = None,
        actor: str | None = None,
        force: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Promote the reviewed Stage 4 candidate into canonical season state."""

        from tournament_scheduler.pipeline.state import PipelineState, StageName
        from tournament_scheduler.pipeline.verification_context import (
            VerificationContextError,
            resolve_promotion_verification_context,
        )

        state = PipelineState(work_dir)
        checkpoint = state.read_stage(StageName.PLANNING)
        if not checkpoint:
            raise SeasonStateError("No Stage 3 planning checkpoint found to promote")
        candidate = extract_candidate(checkpoint)
        try:
            bound_context = resolve_promotion_verification_context(
                work_dir=str(work_dir), candidate=candidate
            )
        except VerificationContextError as exc:
            raise SeasonStateError(f"Refusing promotion: {exc}") from exc
        result = verify_candidate(candidate, bound_context["problem"])
        if not result.get("ok", True):
            messages = "; ".join(
                str(v.get("message") or v.get("code")) for v in result.get("violations", [])
            )
            raise SeasonStateError(
                f"Refusing promotion: selected candidate fails hard verification: {messages}"
            )

        reviewed_plan = bound_context.get("reviewed_plan")
        plan_dict = dict(reviewed_plan) if isinstance(reviewed_plan, dict) else dict(candidate)
        plan_dict["schema_version"] = SEASON_PLAN_SCHEMA_VERSION
        resolved_season = season or season_id_from_plan(plan_dict)
        if (
            self.store.schedule_path(resolved_season).exists()
            or self.store.decisions_path(resolved_season).exists()
        ) and not force:
            raise SeasonStateError(
                f"Canonical season state already exists for {resolved_season}; "
                "use --force only for deliberate replacement"
            )

        now = _now_iso()
        fingerprint = schedule_fingerprint(plan_dict)
        schedule_payload = {
            "schema_version": SEASON_STATE_SCHEMA_VERSION,
            "season": resolved_season,
            "created_at": now,
            "updated_at": now,
            "revision": fingerprint,
            "fingerprint": fingerprint,
            "plan_schema_version": SEASON_PLAN_SCHEMA_VERSION,
            "plan": plan_dict,
            "verification_context": dict(bound_context["context"]),
            "promoted_from": {
                "work_dir": str(work_dir),
                "run_id": bound_context["run_id"],
                "stage3_fingerprint": fingerprint,
                "stage4_export_fingerprint": bound_context["export_fingerprint"],
                "stage4_export_dir": bound_context.get("export_dir"),
                "verification_context_schema_version": bound_context["context"].get("schema_version"),
                "verification_context_problem_fingerprint": bound_context.get("problem_fingerprint"),
                "verification_context_candidate_fingerprint": bound_context["candidate_fingerprint"],
                "verification_context_verified_ok": True,
                "public_export_context_fingerprint": bound_context.get("public_export_context_fingerprint"),
            },
        }
        decisions_payload = {
            "schema_version": DECISIONS_SCHEMA_VERSION,
            "season": resolved_season,
            "created_at": now,
            "updated_at": now,
            "schedule_fingerprint": fingerprint,
            "actor": _operator_identity(actor),
            "decisions": _initial_decisions(plan_dict),
        }
        snapshot = CanonicalSeasonSnapshot(
            season=resolved_season,
            schedule=schedule_payload,
            decisions=decisions_payload,
            export_context=bound_context.get("public_export_context"),
        )
        committed = self._commit(snapshot, require_absent=not force)
        _record_promotion_trace(
            work_dir,
            run_id=bound_context.get("run_id"),
            season=resolved_season,
            actor=actor,
            schedule=committed.schedule,
            candidate_fingerprint=bound_context.get("candidate_fingerprint"),
            export_fingerprint=bound_context.get("export_fingerprint"),
        )
        return committed.schedule, committed.decisions

    # -- schedule-changing mutations --------------------------------------

    def move_tournament(
        self,
        *,
        season: str,
        tournament_id: str,
        date: str | None = None,
        arena: str | None = None,
        host_club: str | None = None,
        start_time: str | None = None,
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        note: str = "",
        dry_run: bool = False,
        allow_cross_half: bool = False,
        run_id: str | None = None,
        request_id: str | None = None,
        allow_manual_placement: bool = False,
        allow_host_confirmation: bool = False,
    ) -> dict[str, Any]:
        """Apply or preview a bounded placement mutation to canonical state.

        A direct operator move and an automatic repair are semantically
        different, so a move onto known ``fixed_busy`` ice, an untrusted host
        calendar, or a host-controlled (``movable_busy``) slot is refused by
        default. The operator may opt in explicitly for a deliberate provisional
        placement with ``allow_manual_placement`` / ``allow_host_confirmation``.
        """

        if not any(value is not None for value in (date, arena, host_club, start_time)):
            raise SeasonStateError(
                "Refusing canonical move: specify at least one target placement field"
            )
        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        before_fingerprint = str(schedule.get("revision") or schedule.get("fingerprint") or "")
        before_canonical_revision = canonical_state_revision(schedule, decisions)
        resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
        plan = dict(schedule["plan"])
        tournaments = [dict(t) for t in plan.get("tournaments", [])]
        plan["tournaments"] = tournaments
        target = next((t for t in tournaments if str(t.get("id")) == tournament_id), None)
        if target is None:
            raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
        if target.get("cancelled"):
            raise SeasonStateError(f"Tournament {tournament_id} is cancelled and cannot be moved")
        record = decisions.get("decisions", {}).get(tournament_id, {})
        resolved = resolve_approval(record, target)
        if resolved["placement_locked"]:
            raise SeasonStateError(
                f"Tournament {tournament_id} has an active placement lock and cannot be moved; "
                "unapprove it explicitly first"
            )

        move_result = _apply_move_to_plan(
            plan,
            tournament_id=tournament_id,
            date=date,
            arena=arena,
            host_club=host_club,
            start_time=start_time,
            problem=resolved_problem,
            allow_cross_half=allow_cross_half,
        )
        if not move_result["changed"]:
            return schedule
        moved_tournament = move_result["tournament"]
        original_placement = move_result["original_placement"]
        original_tournament_fingerprint = move_result["original_fingerprint"]

        result = verify_candidate(plan, resolved_problem) if resolved_problem else verify_candidate(plan)
        if not result.get("ok", True):
            messages = "; ".join(
                str(v.get("message") or v.get("code")) for v in result.get("violations", [])
            )
            raise SeasonStateError(
                f"Refusing canonical mutation: candidate fails hard verification: {messages}"
            )
        before_verification = (
            verify_candidate(dict(schedule.get("plan") or {}), resolved_problem)
            if resolved_problem
            else verify_candidate(dict(schedule.get("plan") or {}))
        )
        operational_acceptability = check_operational_acceptability(
            schedule.get("plan") or {},
            before_verification,
            plan,
            result,
            allow_manual_placement=allow_manual_placement,
            allow_host_confirmation=allow_host_confirmation,
        )
        reconcile_plan_derived_state(plan, result, problem=resolved_problem)
        existing_protection_violations = protection_violations(plan, decisions)
        constraint_violations = request_constraint_violations(plan, decisions)

        now = _now_iso()
        fingerprint = schedule_fingerprint(plan)
        new_placement = _placement_snapshot(moved_tournament or target)
        changed_placement_fields = {
            field: new_placement.get(field)
            for field in ("date", "arena", "host_club", "start_time")
            if original_placement.get(field) != new_placement.get(field)
        }
        new_protections = build_move_protections(
            tournament_id=tournament_id,
            changed_fields=changed_placement_fields,
            request_id=str(request_id or ""),
            actor=_operator_identity(actor),
            note=note,
            created_at=now,
            source_revision=before_canonical_revision,
        )
        updated_schedule = dict(schedule)
        updated_schedule.update(
            {
                "updated_at": now,
                "revision": fingerprint,
                "fingerprint": fingerprint,
                "plan": plan,
            }
        )
        if dry_run:
            updated_schedule["dry_run"] = True
            updated_schedule["move_preview"] = {
                "tournament_id": tournament_id,
                "old_placement": original_placement,
                "new_placement": new_placement,
                "before_fingerprint": before_fingerprint,
                "after_fingerprint": fingerprint,
                "before_canonical_revision": before_canonical_revision,
                "verification_result": result,
                "run_id": run_id,
                "existing_change_protection_violations": existing_protection_violations,
                "change_protection_acceptable": not existing_protection_violations,
                "request_constraint_violations": constraint_violations,
                "request_constraint_acceptable": not constraint_violations,
                "operational_acceptability": operational_acceptability,
                "protections_to_add": new_protections,
                "request_id": str(request_id or ""),
            }
            return updated_schedule

        if not operational_acceptability["ok"]:
            messages = "; ".join(
                str(item.get("message")) for item in operational_acceptability["regressions"]
            )
            flags = ", ".join(required_opt_in_flags(operational_acceptability))
            raise SeasonStateError(
                "Refusing canonical move: it newly introduces operational placement work: "
                f"{messages}"
                + (f"; pass {flags} only for an explicit provisional placement" if flags else "")
            )
        if existing_protection_violations:
            messages = "; ".join(
                str(item.get("message")) for item in existing_protection_violations
            )
            raise SeasonStateError(
                "Refusing canonical move: it would undo an accepted change: " + messages
            )
        if constraint_violations:
            messages = "; ".join(
                str(item.get("message")) for item in constraint_violations
            )
            raise SeasonStateError(
                "Refusing canonical move: it violates an active request constraint: " + messages
            )

        updated_decisions = dict(decisions)
        updated_decisions["schedule_fingerprint"] = fingerprint
        updated_decisions["updated_at"] = now
        updated_decisions["decisions"] = _reconcile_decisions(
            updated_decisions.get("decisions", {}), plan, now=now
        )
        append_change_protections(updated_decisions, new_protections)
        _append_decision_history(
            updated_decisions,
            event="move",
            tournament_id=tournament_id,
            actor=actor,
            now=now,
            tournament_fingerprint=approval_fingerprint(moved_tournament or target),
            previous_fingerprint=original_tournament_fingerprint,
            note=note,
            details={
                "old_placement": original_placement,
                "new_placement": new_placement,
                "before_fingerprint": before_fingerprint,
                "after_fingerprint": fingerprint,
                "before_canonical_revision": before_canonical_revision,
                "verification_result": result,
                "operational_acceptability": operational_acceptability,
                "run_id": run_id,
            },
        )
        committed = self._commit(snapshot.with_schedule(updated_schedule).with_decisions(updated_decisions))
        return committed.schedule


    def swap_participants(
        self,
        *,
        season: str,
        tournament_a_id: str,
        team_a_label: str,
        tournament_b_id: str,
        team_b_label: str,
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        note: str = "",
        dry_run: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Swap one RVV participant between two same-age canonical tournaments.

        This is a narrow operator mutation for an already-promoted season. It
        deliberately keeps both placements/hosts fixed, regenerates the games
        for both rosters, and validates the whole season through the same
        canonical lock, guest-slot, hard-verification and hosting-responsibility
        gates used by apply_candidate.
        """

        if tournament_a_id == tournament_b_id:
            raise SeasonStateError("Participant swap requires two different tournaments")

        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
        plan = copy.deepcopy(schedule["plan"])

        by_id = {
            str(tournament.get("id") or ""): tournament
            for tournament in plan.get("tournaments", []) or []
            if tournament.get("id")
        }
        for tournament_id in (tournament_a_id, tournament_b_id):
            tournament = by_id.get(tournament_id)
            if tournament is None:
                raise SeasonStateError(
                    f"Unknown tournament id in canonical schedule: {tournament_id}"
                )
            if tournament.get("cancelled"):
                raise SeasonStateError(
                    f"Tournament {tournament_id} is cancelled and cannot participate in a roster swap"
                )
            resolved = resolve_approval(
                decisions.get("decisions", {}).get(tournament_id, {}),
                tournament,
            )
            if resolved["participants_locked"]:
                raise SeasonStateError(
                    f"Tournament {tournament_id} has an active participant lock; "
                    "unapprove it explicitly first"
                )

        swap_result = _apply_swap_to_plan(
            plan,
            tournament_a_id=tournament_a_id,
            team_a_label=team_a_label,
            tournament_b_id=tournament_b_id,
            team_b_label=team_b_label,
            problem=resolved_problem,
        )
        tournament_a = swap_result["tournament_a"]
        tournament_b = swap_result["tournament_b"]
        team_a = swap_result["team_a"]
        team_b = swap_result["team_b"]
        identity_a = swap_result["identity_a"]
        identity_b = swap_result["identity_b"]
        age_group_a = swap_result["age_group"]
        before_fingerprint_a = swap_result["before_fingerprint_a"]
        before_fingerprint_b = swap_result["before_fingerprint_b"]

        from tournament_scheduler.canonical_baseline import (
            build_canonical_baseline,
            change_cost,
            verify_canonical_locks,
        )

        baseline = build_canonical_baseline(schedule, decisions)
        lock_violations = verify_canonical_locks(baseline, plan)
        if lock_violations:
            messages = "; ".join(str(v.get("message")) for v in lock_violations)
            raise SeasonStateError(
                f"Refusing canonical participant swap: candidate violates canonical locks: {messages}"
            )

        result = (
            verify_candidate(plan, resolved_problem)
            if resolved_problem
            else verify_candidate(plan)
        )
        if not result.get("ok", True):
            messages = "; ".join(
                str(v.get("message") or v.get("code"))
                for v in result.get("violations", [])
            )
            raise SeasonStateError(
                f"Refusing canonical participant swap: candidate fails hard verification: {messages}"
            )

        if resolved_problem:
            from tournament_scheduler.hosting_responsibility import (
                unexplained_responsibility_transfers,
            )

            transfers = unexplained_responsibility_transfers(
                schedule.get("plan"),
                plan,
                resolved_problem,
            )
            if transfers:
                messages = "; ".join(str(entry.get("message")) for entry in transfers)
                raise SeasonStateError(
                    "Refusing canonical participant swap: candidate transfers hosting "
                    f"responsibility: {messages}"
                )

        reconcile_plan_derived_state(plan, result, problem=resolved_problem)
        existing_protection_violations = protection_violations(plan, decisions)
        constraint_violations = request_constraint_violations(plan, decisions)
        candidate_revision = schedule_fingerprint(plan)
        cost = change_cost(baseline, plan)

        from tournament_scheduler.team_schedule_quality import (
            compare_team_schedule_consequence,
        )

        team_consequences = {
            "team_a": compare_team_schedule_consequence(
                schedule.get("plan") or {},
                plan,
                identity_a,
                problem=resolved_problem,
            ),
            "team_b": compare_team_schedule_consequence(
                schedule.get("plan") or {},
                plan,
                identity_b,
                problem=resolved_problem,
            ),
        }
        consequence_acceptable = all(
            analysis.get("acceptable", False)
            for analysis in team_consequences.values()
        )
        protection_request_id = str(request_id or "")
        protection_created_at = _now_iso()
        new_protections = build_swap_protections(
            team_a=team_a,
            tournament_a_id=tournament_a_id,
            team_b=team_b,
            tournament_b_id=tournament_b_id,
            request_id=protection_request_id,
            actor=_operator_identity(actor),
            note=note,
            created_at=protection_created_at,
            source_revision=canonical_state_revision(schedule, decisions),
        )
        details = {
            "tournament_a_id": tournament_a_id,
            "tournament_b_id": tournament_b_id,
            "age_group": age_group_a,
            "team_a": {
                "club": identity_a[0],
                "label": identity_a[1],
            },
            "team_b": {
                "club": identity_b[0],
                "label": identity_b[1],
            },
            "tournament_a_date": tournament_a.get("date"),
            "tournament_b_date": tournament_b.get("date"),
            "before_fingerprint_a": before_fingerprint_a,
            "before_fingerprint_b": before_fingerprint_b,
            "candidate_revision": candidate_revision,
            "team_consequences": team_consequences,
            "consequence_acceptable": consequence_acceptable,
            "existing_change_protection_violations": existing_protection_violations,
            "change_protection_acceptable": not existing_protection_violations,
            "request_constraint_violations": constraint_violations,
            "request_constraint_acceptable": not constraint_violations,
            "protections_to_add": new_protections,
            "request_id": protection_request_id,
        }

        if dry_run:
            return {
                "season": season,
                "dry_run": True,
                "current_revision": schedule.get("revision"),
                "candidate_revision": candidate_revision,
                "verification_result": result,
                "change_cost": cost,
                "swap": details,
            }

        if existing_protection_violations:
            messages = "; ".join(
                str(item.get("message")) for item in existing_protection_violations
            )
            raise SeasonStateError(
                "Refusing canonical participant swap: it would undo an accepted change: "
                + messages
            )
        if constraint_violations:
            messages = "; ".join(
                str(item.get("message")) for item in constraint_violations
            )
            raise SeasonStateError(
                "Refusing canonical participant swap: it violates an active request constraint: "
                + messages
            )

        if not consequence_acceptable:
            regressions = []
            for team_name, analysis in team_consequences.items():
                for regression in analysis.get("material_regressions", []):
                    regressions.append(
                        f"{team_name}:{regression.get('code')}"
                    )
            raise SeasonStateError(
                "Refusing canonical participant swap: it materially worsens an affected "
                "team's schedule: " + ", ".join(regressions)
            )

        updated_schedule, updated_decisions, applied_cost = self.apply_candidate(
            season=season,
            candidate=plan,
            problem=resolved_problem,
            actor=actor,
            _new_change_protections=new_protections,
            _history_event={
                "event": "participant_swap",
                "tournament_id": tournament_a_id,
                "previous_fingerprint": before_fingerprint_a,
                "note": note,
                "details": details,
            },
        )
        return {
            "season": season,
            "dry_run": False,
            "revision": updated_schedule.get("revision"),
            "canonical_state_revision": canonical_state_revision(
                updated_schedule,
                updated_decisions,
            ),
            "verification_result": result,
            "change_cost": applied_cost,
            "swap": details,
        }

    # -- atomic scoped batch maintenance --------------------------------

    def batch_maintenance(
        self,
        *,
        season: str,
        operations: list[Mapping[str, Any]],
        scope: list[str] | None = None,
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        note: str = "",
        dry_run: bool = False,
        request_id: str | None = None,
        allow_manual_placement: bool = False,
        allow_host_confirmation: bool = False,
    ) -> dict[str, Any]:
        """Compose several canonical mutations against one in-memory candidate.

        Request constraints are hard: every single ``move`` / ``swap`` / generic
        ``apply`` commit must leave the whole season satisfying every active
        constraint. When one operator request records several independent
        violations, no single mutation can satisfy the whole active set. This
        boundary applies an explicitly scoped set of operations to one in-memory
        copy of the current canonical plan, runs the complete authoritative
        gates once on the final candidate, and commits exactly once.

        The caller declares the affected tournament ids. Every operation must
        reference only in-scope tournaments, and any tournament that changes
        outside the declared scope refuses the whole batch. No canonical state
        is written until every gate passes; an invalid final candidate (or a
        batch that leaves any active request constraint violated) is refused
        without a partial write. It is generic: nothing here is specific to one
        real-world request or calendar.
        """

        if not operations:
            raise SeasonStateError("Refusing canonical batch: at least one operation is required")
        resolved_request_id = str(request_id or "").strip()
        if not resolved_request_id:
            raise SeasonStateError(
                "Refusing canonical batch: a stable --request-id is required so the batch is auditable"
            )
        scope_ids = {str(item).strip() for item in (scope or []) if str(item).strip()}
        if not scope_ids:
            raise SeasonStateError(
                "Refusing canonical batch: declare the affected tournament ids with --scope"
            )

        normalized_operations = [
            _normalize_batch_operation(operation) for operation in operations
        ]
        referenced_ids = sorted(
            {
                tournament_id
                for operation in normalized_operations
                for tournament_id in _batch_operation_tournament_ids(operation)
            }
        )
        referenced_outside_scope = [
            tournament_id for tournament_id in referenced_ids if tournament_id not in scope_ids
        ]

        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
        before_plan = schedule.get("plan") or {}
        before_fingerprint = str(schedule.get("revision") or schedule.get("fingerprint") or "")
        before_canonical_revision = canonical_state_revision(schedule, decisions)
        before_tournaments = {
            str(tournament.get("id") or ""): tournament
            for tournament in before_plan.get("tournaments", []) or []
            if tournament.get("id")
        }
        unknown_scope_ids = sorted(scope_ids - set(before_tournaments))
        if unknown_scope_ids:
            raise SeasonStateError(
                "Refusing canonical batch: scope names unknown tournament ids: "
                + ", ".join(unknown_scope_ids)
            )

        candidate_plan = copy.deepcopy(before_plan)
        now = _now_iso()
        resolved_actor = _operator_identity(actor)

        applied_moves: list[dict[str, Any]] = []
        applied_swaps: list[dict[str, Any]] = []
        applied_cancellations: list[dict[str, Any]] = []
        new_protections: list[dict[str, Any]] = []

        for operation in normalized_operations:
            op = operation["op"]
            if op == "move":
                result = _apply_move_to_plan(
                    candidate_plan,
                    tournament_id=operation["tournament_id"],
                    date=operation.get("date"),
                    arena=operation.get("arena"),
                    host_club=operation.get("host_club"),
                    start_time=operation.get("start_time"),
                    problem=resolved_problem,
                    allow_cross_half=bool(operation.get("allow_cross_half", False)),
                )
                applied_moves.append(
                    {
                        "tournament_id": operation["tournament_id"],
                        "changed": bool(result["changed"]),
                        "old_placement": result["original_placement"],
                        "new_placement": _placement_snapshot(result["tournament"]),
                    }
                )
            elif op == "swap_participants":
                result = _apply_swap_to_plan(
                    candidate_plan,
                    tournament_a_id=operation["tournament_a"],
                    team_a_label=operation["team_a"],
                    tournament_b_id=operation["tournament_b"],
                    team_b_label=operation["team_b"],
                    problem=resolved_problem,
                )
                new_protections.extend(
                    build_swap_protections(
                        team_a=result["team_a"],
                        tournament_a_id=operation["tournament_a"],
                        team_b=result["team_b"],
                        tournament_b_id=operation["tournament_b"],
                        request_id=resolved_request_id,
                        actor=resolved_actor,
                        note=note,
                        created_at=now,
                        source_revision=before_canonical_revision,
                    )
                )
                applied_swaps.append(
                    {
                        "tournament_a_id": operation["tournament_a"],
                        "tournament_b_id": operation["tournament_b"],
                        "team_a": {
                            key: result["team_a"].get(key)
                            for key in ("club", "label", "age_group")
                        },
                        "team_b": {
                            key: result["team_b"].get(key)
                            for key in ("club", "label", "age_group")
                        },
                        "identity_a": result["identity_a"],
                        "identity_b": result["identity_b"],
                        "age_group": result["age_group"],
                    }
                )
            elif op == "cancel":
                applied_cancellations.append(
                    _apply_cancel_to_plan(
                        candidate_plan,
                        tournament_id=operation["tournament_id"],
                        reason=str(operation.get("reason") or ""),
                    )
                )
            else:  # pragma: no cover - normalization already rejects unknown operators
                raise SeasonStateError(f"Unsupported batch operation {op!r}")

        candidate_tournaments = {
            str(tournament.get("id") or ""): tournament
            for tournament in candidate_plan.get("tournaments", []) or []
            if tournament.get("id")
        }

        # Placement protections are derived from the final pre-batch -> final
        # difference, so a repeated move of one tournament protects the result.
        for tournament_id in sorted(scope_ids):
            before_tournament = before_tournaments.get(tournament_id)
            after_tournament = candidate_tournaments.get(tournament_id)
            if before_tournament is None or after_tournament is None:
                continue
            before_placement = _placement_snapshot(before_tournament)
            after_placement = _placement_snapshot(after_tournament)
            changed_fields = {
                field: after_placement.get(field)
                for field in ("date", "arena", "host_club", "start_time")
                if before_placement.get(field) != after_placement.get(field)
            }
            if changed_fields:
                new_protections.extend(
                    build_move_protections(
                        tournament_id=tournament_id,
                        changed_fields=changed_fields,
                        request_id=resolved_request_id,
                        actor=resolved_actor,
                        note=note,
                        created_at=now,
                        source_revision=before_canonical_revision,
                    )
                )

        from tournament_scheduler.canonical_baseline import (
            build_canonical_baseline,
            change_cost,
            verify_canonical_locks,
        )

        baseline = build_canonical_baseline(schedule, decisions)
        verification_result = (
            verify_candidate(candidate_plan, resolved_problem)
            if resolved_problem
            else verify_candidate(candidate_plan)
        )
        before_verification = (
            verify_candidate(dict(before_plan), resolved_problem)
            if resolved_problem
            else verify_candidate(dict(before_plan))
        )
        operational_acceptability = check_operational_acceptability(
            before_plan,
            before_verification,
            candidate_plan,
            verification_result,
            allow_manual_placement=allow_manual_placement,
            allow_host_confirmation=allow_host_confirmation,
        )
        lock_violations = verify_canonical_locks(baseline, candidate_plan)
        existing_protection_violations = protection_violations(candidate_plan, decisions)
        constraint_violations = request_constraint_violations(candidate_plan, decisions)
        before_guest_signature = _guest_reservation_signature(before_plan)
        after_guest_signature = _guest_reservation_signature(candidate_plan)
        guest_integrity_ok = before_guest_signature == after_guest_signature

        hosting_transfers: list[dict[str, Any]] = []
        if resolved_problem:
            from tournament_scheduler.hosting_responsibility import (
                unexplained_responsibility_transfers,
            )

            hosting_transfers = unexplained_responsibility_transfers(
                before_plan, candidate_plan, resolved_problem
            )

        changed_ids = sorted(
            tournament_id
            for tournament_id, tournament in candidate_tournaments.items()
            if before_tournaments.get(tournament_id) != tournament
        )
        missing_ids = sorted(set(before_tournaments) - set(candidate_tournaments))
        changed_ids = sorted(set(changed_ids) | set(missing_ids))
        changed_outside_scope = [
            tournament_id for tournament_id in changed_ids if tournament_id not in scope_ids
        ]

        team_consequences: dict[str, Any] = {}
        if applied_swaps:
            from tournament_scheduler.team_schedule_quality import (
                compare_team_schedule_consequence,
            )

            for swap in applied_swaps:
                for tournament_key, team_key, identity in (
                    ("tournament_a_id", "team_a", swap["identity_a"]),
                    ("tournament_b_id", "team_b", swap["identity_b"]),
                ):
                    key = f"{swap[tournament_key]}:{swap[team_key]['label']}"
                    team_consequences[key] = compare_team_schedule_consequence(
                        before_plan,
                        candidate_plan,
                        identity,
                        problem=resolved_problem,
                    )
        consequence_acceptable = (
            all(
                analysis.get("acceptable", False)
                for analysis in team_consequences.values()
            )
            if team_consequences
            else True
        )

        reconcile_plan_derived_state(
            candidate_plan, verification_result, problem=resolved_problem
        )
        candidate_fingerprint = schedule_fingerprint(candidate_plan)
        candidate_cost = change_cost(baseline, candidate_plan)

        refusal_reasons: list[str] = []
        if referenced_outside_scope:
            refusal_reasons.append(
                "operations reference tournaments outside the declared scope: "
                + ", ".join(referenced_outside_scope)
            )
        if changed_outside_scope:
            refusal_reasons.append(
                "tournaments outside the declared scope changed: "
                + ", ".join(changed_outside_scope)
            )
        if not verification_result.get("ok", True):
            refusal_reasons.append("final candidate fails hard verification")
        if not operational_acceptability["ok"]:
            refusal_reasons.append(
                "final candidate newly introduces operational placement work"
            )
        if lock_violations:
            refusal_reasons.append("final candidate violates an approval/lock")
        if existing_protection_violations:
            refusal_reasons.append(
                "final candidate would undo an accepted change protection"
            )
        if not guest_integrity_ok:
            refusal_reasons.append("final candidate would change reserved guest slots")
        if hosting_transfers:
            refusal_reasons.append("final candidate transfers hosting responsibility")
        if constraint_violations:
            refusal_reasons.append(
                f"final candidate leaves {len(constraint_violations)} active "
                "request-constraint violation(s)"
            )
        if not consequence_acceptable:
            refusal_reasons.append(
                "final candidate materially worsens an affected team's schedule"
            )

        updated_schedule = {
            **schedule,
            "updated_at": now,
            "revision": candidate_fingerprint,
            "fingerprint": candidate_fingerprint,
            "plan": candidate_plan,
        }
        updated_decisions = {
            **decisions,
            "updated_at": now,
            "schedule_fingerprint": candidate_fingerprint,
            "decisions": _reconcile_decisions(decisions.get("decisions", {}), candidate_plan, now=now),
        }
        append_change_protections(updated_decisions, new_protections)

        report: dict[str, Any] = {
            "season": season,
            "dry_run": bool(dry_run),
            "request_id": resolved_request_id,
            "declared_scope": sorted(scope_ids),
            "operations": normalized_operations,
            "referenced_tournament_ids": referenced_ids,
            "referenced_outside_scope": referenced_outside_scope,
            "changed_tournament_ids": changed_ids,
            "changed_outside_scope": changed_outside_scope,
            "moves": applied_moves,
            "swaps": applied_swaps,
            "cancellations": applied_cancellations,
            "before_schedule_revision": before_fingerprint,
            "before_canonical_revision": before_canonical_revision,
            "candidate_schedule_revision": candidate_fingerprint,
            "verification_result": verification_result,
            "hard_verification_ok": bool(verification_result.get("ok", True)),
            "operational_acceptability": operational_acceptability,
            "operational_acceptable": bool(operational_acceptability.get("ok")),
            "lock_violations": lock_violations,
            "change_protection_violations": existing_protection_violations,
            "change_protection_acceptable": not existing_protection_violations,
            "guest_reservation_integrity": {
                "ok": guest_integrity_ok,
                "before": before_guest_signature,
                "after": after_guest_signature,
            },
            "hosting_responsibility_transfers": hosting_transfers,
            "hosting_responsibility_ok": not hosting_transfers,
            "request_constraint_violations": constraint_violations,
            "remaining_request_constraint_violations": constraint_violations,
            "request_constraint_acceptable": not constraint_violations,
            "team_consequences": team_consequences,
            "consequence_acceptable": consequence_acceptable,
            "change_cost": candidate_cost,
            "protections_to_add": new_protections,
            "refused": bool(refusal_reasons),
            "refusal_reasons": refusal_reasons,
        }

        if dry_run:
            report["committed"] = False
            preview_schedule = {
                **schedule,
                "revision": candidate_fingerprint,
                "fingerprint": candidate_fingerprint,
                "plan": candidate_plan,
            }
            report["candidate_canonical_revision"] = compute_canonical_state_revision(
                preview_schedule, updated_decisions
            )
            return report

        if refusal_reasons:
            messages = "; ".join(refusal_reasons)
            if constraint_violations:
                messages += ": " + "; ".join(
                    str(item.get("message")) for item in constraint_violations
                )
            raise SeasonStateError(f"Refusing canonical batch: {messages}")

        _append_decision_history(
            updated_decisions,
            event="batch_maintenance",
            tournament_id="",
            actor=actor,
            now=now,
            note=note,
            details={
                "request_id": resolved_request_id,
                "declared_scope": sorted(scope_ids),
                "changed_tournament_ids": changed_ids,
                "operations": normalized_operations,
                "before_schedule_revision": before_fingerprint,
                "after_schedule_revision": candidate_fingerprint,
                "before_canonical_revision": before_canonical_revision,
                "moves": applied_moves,
                "swaps": applied_swaps,
                "cancellations": applied_cancellations,
                "protections_added": [
                    str(protection.get("id") or "") for protection in new_protections
                ],
                "verification_ok": bool(verification_result.get("ok", True)),
                "operational_acceptable": bool(operational_acceptability.get("ok")),
            },
        )
        committed = self._commit(
            snapshot.with_schedule(updated_schedule).with_decisions(updated_decisions)
        )
        report["committed"] = True
        report["revision"] = committed.schedule.get("revision")
        report["canonical_state_revision"] = canonical_state_revision(
            committed.schedule, committed.decisions
        )
        return report

    def apply_candidate(
        self,
        *,
        season: str,
        candidate: dict[str, Any],
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        change_weights: dict[str, float] | None = None,
        allow_guest_slot_changes: bool = False,
        _history_event: Mapping[str, Any] | None = None,
        _new_change_protections: list[dict[str, Any]] | None = None,
        allow_manual_placement: bool = False,
        allow_host_confirmation: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Apply a verified replan candidate to canonical season state.

        Hard verification is necessary but not sufficient: the candidate must
        also not newly introduce fixed-busy/manual placement work or a
        host-confirmation dependency relative to the current canonical plan,
        unless the operator explicitly opted in for a provisional placement.
        """

        from tournament_scheduler.canonical_baseline import (
            build_canonical_baseline,
            change_cost,
            verify_canonical_locks,
        )

        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        baseline = build_canonical_baseline(schedule, decisions)
        normalized_candidate = extract_candidate(candidate)

        # A reservation is durable canonical state: a replan/apply must not
        # silently drop or rewrite one. Filling and releasing are the only
        # deliberate transitions, and both go through their own operations.
        if not allow_guest_slot_changes:
            before_reservations = _guest_reservation_signature(schedule.get("plan") or {})
            after_reservations = _guest_reservation_signature(normalized_candidate)
            if before_reservations != after_reservations:
                changed = sorted(
                    set(before_reservations) | set(after_reservations)
                )
                raise SeasonStateError(
                    "Refusing canonical apply: it would change reserved guest slots on "
                    f"{changed}; use reserve/fill/release explicitly"
                )
        accepted_change_violations = protection_violations(normalized_candidate, decisions)
        if accepted_change_violations:
            messages = "; ".join(
                str(item.get("message")) for item in accepted_change_violations
            )
            raise SeasonStateError(
                "Refusing canonical apply: it would undo an accepted change: " + messages
            )
        active_constraint_violations = request_constraint_violations(
            normalized_candidate, decisions
        )
        if active_constraint_violations:
            messages = "; ".join(
                str(item.get("message")) for item in active_constraint_violations
            )
            raise SeasonStateError(
                "Refusing canonical apply: it violates an active request constraint: " + messages
            )

        lock_violations = verify_canonical_locks(baseline, normalized_candidate)
        if lock_violations:
            messages = "; ".join(str(v.get("message")) for v in lock_violations)
            raise SeasonStateError(
                f"Refusing canonical apply: candidate violates canonical locks: {messages}"
            )

        result = (
            verify_candidate(normalized_candidate, problem)
            if problem
            else verify_candidate(normalized_candidate)
        )
        if not result.get("ok", True):
            messages = "; ".join(
                str(v.get("message") or v.get("code")) for v in result.get("violations", [])
            )
            raise SeasonStateError(
                f"Refusing canonical apply: candidate fails hard verification: {messages}"
            )

        before_plan = schedule.get("plan") or {}
        before_verification = (
            verify_candidate(dict(before_plan), problem)
            if problem
            else verify_candidate(dict(before_plan))
        )
        operational_acceptability = check_operational_acceptability(
            before_plan,
            before_verification,
            normalized_candidate,
            result,
            allow_manual_placement=allow_manual_placement,
            allow_host_confirmation=allow_host_confirmation,
        )
        if not operational_acceptability["ok"]:
            messages = "; ".join(
                str(item.get("message")) for item in operational_acceptability["regressions"]
            )
            flags = ", ".join(required_opt_in_flags(operational_acceptability))
            raise SeasonStateError(
                "Refusing canonical apply: candidate newly introduces operational placement "
                f"work: {messages}"
                + (f"; pass {flags} only for an explicit provisional placement" if flags else "")
            )

        if problem:
            from tournament_scheduler.hosting_responsibility import (
                unexplained_responsibility_transfers,
            )

            transfers = unexplained_responsibility_transfers(
                schedule.get("plan"), normalized_candidate, problem
            )
            if transfers:
                messages = "; ".join(str(entry.get("message")) for entry in transfers)
                raise SeasonStateError(
                    f"Refusing canonical apply: candidate transfers hosting responsibility: {messages}"
                )

        plan = dict(normalized_candidate)
        plan.pop("source", None)
        plan["schema_version"] = SEASON_PLAN_SCHEMA_VERSION
        plan.setdefault("start_date", schedule["plan"].get("start_date"))
        plan.setdefault("end_date", schedule["plan"].get("end_date"))
        reconcile_plan_derived_state(plan, result, problem=problem)

        now = _now_iso()
        fingerprint = schedule_fingerprint(plan)
        updated_schedule = {
            **schedule,
            "updated_at": now,
            "revision": fingerprint,
            "fingerprint": fingerprint,
            "plan_schema_version": SEASON_PLAN_SCHEMA_VERSION,
            "plan": plan,
            "applied_from": {
                "previous_revision": schedule.get("revision"),
                "actor": _operator_identity(actor),
                "operational_acceptability": operational_acceptability,
            },
        }
        updated_decisions = {
            **decisions,
            "updated_at": now,
            "schedule_fingerprint": fingerprint,
            "decisions": _reconcile_decisions(decisions.get("decisions", {}), plan, now=now),
        }
        if _new_change_protections:
            append_change_protections(updated_decisions, _new_change_protections)
        if _history_event:
            history_tournament_id = str(_history_event.get("tournament_id") or "")
            history_tournament = next(
                (
                    tournament
                    for tournament in plan.get("tournaments", []) or []
                    if str(tournament.get("id") or "") == history_tournament_id
                ),
                None,
            )
            _append_decision_history(
                updated_decisions,
                event=str(_history_event.get("event") or "apply_candidate"),
                tournament_id=history_tournament_id,
                actor=actor,
                now=now,
                tournament_fingerprint=(
                    approval_fingerprint(history_tournament)
                    if history_tournament is not None
                    else None
                ),
                previous_fingerprint=(
                    str(_history_event.get("previous_fingerprint"))
                    if _history_event.get("previous_fingerprint")
                    else None
                ),
                note=str(_history_event.get("note") or ""),
                details=(
                    dict(_history_event.get("details") or {})
                    if isinstance(_history_event.get("details"), Mapping)
                    else None
                ),
            )
        cost = change_cost(baseline, plan, weights=change_weights)
        committed = self._commit(
            snapshot.with_schedule(updated_schedule).with_decisions(updated_decisions)
        )
        return committed.schedule, committed.decisions, cost

    def normalize_placements(
        self,
        *,
        season: str,
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        note: str = "",
        dry_run: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Upgrade an already-generated canonical plan to the #381 state model.

        Genuinely unplaced tournaments (exhausted slot search, no concrete
        slot, or a fixed external calendar conflict with no verified
        alternative) are moved out of ``plan.tournaments`` into durable
        ``unresolved_tournament_placements`` obligations. Operator-approved /
        placement-locked tournaments are confirmation and are never demoted.
        Every unaffected tournament id, placement and roster is preserved;
        the full verifier and the derived projections are recomputed before
        the result is accepted.
        """
        from tournament_scheduler.placement_normalization import (
            normalize_unplaced_placements,
        )

        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        verification_context = schedule.get("verification_context")
        resolved_problem = problem
        if resolved_problem is None and isinstance(verification_context, Mapping):
            candidate_problem = verification_context.get("problem")
            if isinstance(candidate_problem, Mapping):
                resolved_problem = dict(candidate_problem)

        plan = copy.deepcopy(schedule["plan"])
        approvals = decisions.get("decisions", {})
        report = normalize_unplaced_placements(plan, resolved_problem, approvals=approvals)
        if not report.get("changed"):
            return schedule, decisions

        result = (
            verify_candidate(plan, resolved_problem)
            if resolved_problem
            else verify_candidate(plan)
        )
        if not result.get("ok", True):
            messages = "; ".join(
                str(v.get("message") or v.get("code")) for v in result.get("violations", [])
            )
            raise SeasonStateError(
                f"Refusing placement normalization: candidate fails hard verification: {messages}"
            )
        reconcile_plan_derived_state(plan, result, problem=resolved_problem)
        if not dry_run:
            self._assert_request_constraints_satisfied(
                plan, decisions, action="placement normalization"
            )

        now = _now_iso()
        fingerprint = schedule_fingerprint(plan)
        updated_schedule = {
            **schedule,
            "updated_at": now,
            "revision": fingerprint,
            "fingerprint": fingerprint,
            "plan_schema_version": SEASON_PLAN_SCHEMA_VERSION,
            "plan": plan,
            "normalized_from": {
                "previous_revision": schedule.get("revision"),
                "actor": _operator_identity(actor),
                "removed_tournament_ids": report.get("removed_tournament_ids", []),
                "obligation_count": report.get("obligation_count"),
            },
        }
        updated_decisions = {
            **decisions,
            "updated_at": now,
            "schedule_fingerprint": fingerprint,
            "decisions": _reconcile_decisions(decisions.get("decisions", {}), plan, now=now),
        }
        if dry_run:
            updated_schedule["dry_run"] = True
            updated_schedule["placement_normalization"] = report
            return updated_schedule, decisions

        _append_decision_history(
            updated_decisions,
            event="normalize_placements",
            tournament_id="",
            actor=actor,
            now=now,
            note=note,
            details={
                "removed_tournament_ids": report.get("removed_tournament_ids", []),
                "obligation_count": report.get("obligation_count"),
                "verification_ok": True,
            },
        )
        committed = self._commit(
            snapshot.with_schedule(updated_schedule).with_decisions(updated_decisions)
        )
        return committed.schedule, committed.decisions

    # -- reserved guest slots ---------------------------------------------

    def guest_slot_report(self, season: str) -> dict[str, Any]:
        """Read-only lifecycle status of every reserved guest place."""

        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        tournaments: list[dict[str, Any]] = []
        for tournament in schedule["plan"].get("tournaments", []) or []:
            records = guest_slot_records(tournament)
            if not records:
                continue
            tournaments.append(
                {
                    "tournament_id": str(tournament.get("id") or ""),
                    "date": tournament.get("date"),
                    "age_group": tournament.get("age_group"),
                    "host_club": tournament.get("host_club"),
                    "rvv_team_count": rvv_team_count(tournament),
                    **guest_slot_summary(tournament),
                }
            )
        return {
            "season": season,
            "revision": schedule.get("revision"),
            "canonical_state_revision": canonical_state_revision(schedule, decisions),
            "tournament_count": len(tournaments),
            "reserved_total": sum(int(entry["reserved"]) for entry in tournaments),
            "open_total": sum(int(entry["open"]) for entry in tournaments),
            "filled_total": sum(int(entry["filled"]) for entry in tournaments),
            "tournaments": tournaments,
        }

    def guest_slot_candidates(
        self,
        *,
        season: str,
        age_groups: list[str] | tuple[str, ...] | None = None,
        max_per_tournament: int = 1,
        problem: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Deterministic candidate facts for a policy-level reservation request.

        This never chooses *which* tournaments receive a reservation; it exposes
        the legal alternatives with the facts (free places, existing
        reservations, half, approval state, displaceable participants) and a
        deterministic spread-aware ranking so the controller can make and
        record the contextual choice.
        """

        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
        allowed = {str(group) for group in (age_groups or DEFAULT_GUEST_AGE_GROUPS)}
        records = decisions.get("decisions", {}) or {}
        from tournament_scheduler import planning_half

        start = schedule["plan"].get("start_date")
        end = schedule["plan"].get("end_date")
        split = None
        if start and end:
            try:
                split = planning_half.christmas_split_date(
                    _date.fromisoformat(str(start)), _date.fromisoformat(str(end))
                )
            except ValueError:
                split = None

        candidates: list[dict[str, Any]] = []
        for tournament in schedule["plan"].get("tournaments", []) or []:
            age_group = str(tournament.get("age_group") or "")
            if age_group not in allowed or tournament.get("cancelled"):
                continue
            tournament_id = str(tournament.get("id") or "")
            capacity = _configured_capacity(resolved_problem, age_group)
            used = capacity_places(tournament)
            free = (capacity - used) if capacity is not None else None
            active = active_guest_slot_count(tournament)
            approval = resolve_approval(records.get(tournament_id), tournament)
            locked = bool(approval.get("placement_locked") or approval.get("participants_locked"))
            displaceable = self._displaceable_team_options(tournament)
            has_room = locked is False and (
                free is None
                or free > 0
                or (active < max_per_tournament and bool(displaceable))
            )
            half = None
            try:
                tournament_date = _date.fromisoformat(str(tournament.get("date")))
                if split is not None:
                    half = planning_half.tournament_half(tournament_date, split)
            except (TypeError, ValueError):
                tournament_date = None
            candidates.append(
                {
                    "tournament_id": tournament_id,
                    "date": tournament.get("date"),
                    "age_group": age_group,
                    "host_club": tournament.get("host_club"),
                    "half": half,
                    "configured_capacity": capacity,
                    "current_places": used,
                    "free_places": free,
                    "active_reservations": active,
                    "at_max_per_tournament": active >= max_per_tournament,
                    "reservation_requires_participant_change": bool(
                        locked is False and free is not None and free <= 0 and displaceable
                    ),
                    "placement_locked": bool(approval.get("placement_locked")),
                    "participants_locked": bool(approval.get("participants_locked")),
                    "approval_status": approval.get("status"),
                    "legal": has_room,
                    "displaceable_team_options": displaceable,
                }
            )

        # Deterministic, spread-aware ranking: reservations that need no
        # participant change come first, then a half that is currently short of
        # reservations, then earlier dates, then the stable tournament id.
        half_totals: dict[str, int] = {}
        for candidate in candidates:
            key = str(candidate.get("half") or "unknown")
            half_totals[key] = half_totals.get(key, 0) + int(candidate.get("active_reservations") or 0)

        def _rank(candidate: dict[str, Any]) -> tuple:
            half_key = str(candidate.get("half") or "unknown")
            free = candidate.get("free_places")
            needs_change = bool(candidate.get("reservation_requires_participant_change"))
            return (
                0 if candidate["legal"] else 1,
                1 if needs_change else 0,
                half_totals.get(half_key, 0),
                str(candidate.get("date") or ""),
                str(candidate.get("tournament_id") or ""),
            )

        ranked = sorted(candidates, key=_rank)
        for index, candidate in enumerate(ranked, start=1):
            candidate["rank"] = index

        return {
            "season": season,
            "revision": schedule.get("revision"),
            "canonical_state_revision": canonical_state_revision(schedule, decisions),
            "age_groups": sorted(allowed),
            "max_per_tournament": max_per_tournament,
            "candidates": ranked,
            "legal_candidates": [c for c in ranked if c["legal"]],
        }

    @staticmethod
    def _displaceable_team_options(tournament: Mapping[str, Any]) -> list[dict[str, Any]]:
        """RVV participants that a reservation may displace without breaking host representation."""

        rvv = rvv_teams(tournament)
        host_club = str(tournament.get("host_club") or "")
        host_club_teams = [team for team in rvv if str(team.get("club") or "") == host_club]
        protect_host = len(host_club_teams) == 1
        options: list[dict[str, Any]] = []
        for team in rvv:
            club = str(team.get("club") or "")
            if protect_host and club == host_club:
                continue
            options.append(
                {
                    "label": str(team.get("label") or ""),
                    "club": club,
                    "age_group": str(team.get("age_group") or ""),
                }
            )
        return options

    def reserve_guest_slot(
        self,
        *,
        season: str,
        tournament_id: str,
        count: int = 1,
        displaced_teams: list[str] | None = None,
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        note: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Reserve one or more guest places on one canonical tournament.

        Spare capacity is reserved without removing a real team. When the
        tournament is already full, the caller must name the participant(s) to
        displace from the deterministic ``guest_slot_candidates`` facts; a
        participant is never dropped arbitrarily, and host representation must
        survive (enforced by the independent verifier).
        """

        if int(count) < 1:
            raise SeasonStateError("Refusing guest reservation: count must be at least 1")
        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
        plan = dict(schedule["plan"])
        tournaments = [dict(t) for t in plan.get("tournaments", [])]
        target = next((t for t in tournaments if str(t.get("id")) == tournament_id), None)
        if target is None:
            raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
        if target.get("cancelled"):
            raise SeasonStateError(f"Tournament {tournament_id} is cancelled and cannot receive a reservation")
        record = decisions.get("decisions", {}).get(tournament_id, {})
        approval = resolve_approval(record, target)
        if approval.get("placement_locked") or approval.get("participants_locked"):
            raise SeasonStateError(
                f"Tournament {tournament_id} has an active approval/lock and cannot be mutated; "
                "unapprove it explicitly first"
            )

        age_group = str(target.get("age_group") or "")
        capacity = _configured_capacity(resolved_problem, age_group)
        used = capacity_places(target)
        free = (capacity - used) if capacity is not None else None
        requested = int(count)
        needed_displacements = 0
        if free is not None:
            needed_displacements = max(0, requested - max(0, free))
        displaced = [str(label) for label in (displaced_teams or [])]
        if needed_displacements > len(displaced):
            raise SeasonStateError(
                f"Refusing guest reservation on {tournament_id}: the tournament has "
                f"{max(0, free or 0)} free place(s) and no arbitrary participant is dropped; "
                f"request guest_slot_candidates and name {needed_displacements} displaced team(s)"
            )

        rvv_labels = {str(team.get("label") or "") for team in rvv_teams(target)}
        host_club = str(target.get("host_club") or "")
        host_rvv = [team for team in rvv_teams(target) if str(team.get("club") or "") == host_club]
        for label in displaced:
            if label not in rvv_labels:
                raise SeasonStateError(
                    f"Refusing guest reservation on {tournament_id}: {label!r} is not a participant"
                )
            if len(host_rvv) == 1 and str(host_rvv[0].get("label")) == label:
                raise SeasonStateError(
                    f"Refusing guest reservation on {tournament_id}: {label!r} is the host club's "
                    "only participating team and host representation must survive"
                )

        now = _now_iso()
        resolved_actor = _operator_identity(actor)
        records = [dict(item) for item in (target.get("guest_slots") or []) if isinstance(item, Mapping)]
        used_ids = {str(item.get("id") or "") for item in records}
        created: list[dict[str, Any]] = []
        for _ in range(requested):
            index = 1
            while f"guest:{tournament_id}:{index}" in used_ids:
                index += 1
            slot = new_guest_slot(
                reserved_by=resolved_actor,
                reserved_at=now,
                note=note,
                slot_id=f"guest:{tournament_id}:{index}",
            )
            used_ids.add(str(slot["id"]))
            created.append(slot)
        target["guest_slots"] = records + created
        if displaced:
            target["teams"] = [
                team
                for team in target.get("teams", [])
                if team.get("guest") or str(team.get("label") or "") not in set(displaced)
            ]
        target["reserved_guest_slots"] = active_guest_slot_count(target)
        _regenerate_tournament_games(target, resolved_problem)
        plan["tournaments"] = tournaments

        result = (
            verify_candidate(plan, resolved_problem)
            if resolved_problem
            else verify_candidate(plan)
        )
        if not result.get("ok", True):
            messages = "; ".join(
                str(v.get("message") or v.get("code")) for v in result.get("violations", [])
            )
            raise SeasonStateError(
                f"Refusing canonical guest reservation: candidate fails hard verification: {messages}"
            )
        reconcile_plan_derived_state(plan, result, problem=resolved_problem)
        if not dry_run:
            self._assert_request_constraints_satisfied(
                plan, decisions, action="guest reservation"
            )

        fingerprint = schedule_fingerprint(plan)
        details = {
            "slots": created,
            "displaced_teams": displaced,
            "age_group": age_group,
            "configured_capacity": capacity,
            "places_before": used,
            "before_fingerprint": str(schedule.get("revision") or ""),
            "after_fingerprint": fingerprint,
            "verification_ok": True,
        }
        updated_schedule = {
            **schedule,
            "updated_at": now,
            "revision": fingerprint,
            "fingerprint": fingerprint,
            "plan": plan,
        }
        if dry_run:
            updated_schedule["dry_run"] = True
            updated_schedule["guest_reservation_preview"] = {
                "tournament_id": tournament_id,
                **details,
            }
            return updated_schedule

        updated_decisions = {
            **decisions,
            "updated_at": now,
            "schedule_fingerprint": fingerprint,
            "decisions": _reconcile_decisions(decisions.get("decisions", {}), plan, now=now),
        }
        _append_decision_history(
            updated_decisions,
            event="reserve_guest_slot",
            tournament_id=tournament_id,
            actor=resolved_actor,
            now=now,
            tournament_fingerprint=approval_fingerprint(target),
            note=note,
            details=details,
        )
        committed = self._commit(
            snapshot.with_schedule(updated_schedule).with_decisions(updated_decisions)
        )
        return committed.schedule

    def fill_guest_slot(
        self,
        *,
        season: str,
        tournament_id: str,
        slot_id: str | None,
        external_team: dict[str, Any],
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Accept an external team into a reserved place and regenerate games."""

        if not isinstance(external_team, Mapping) or not str(external_team.get("label") or ""):
            raise SeasonStateError("Refusing guest fill: an external team label is required")
        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
        plan = dict(schedule["plan"])
        tournaments = [dict(t) for t in plan.get("tournaments", [])]
        target = next((t for t in tournaments if str(t.get("id")) == tournament_id), None)
        if target is None:
            raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")

        records = [dict(item) for item in (target.get("guest_slots") or []) if isinstance(item, Mapping)]
        chosen: dict[str, Any] | None = None
        for item in records:
            if str(item.get("status") or GUEST_SLOT_OPEN) != GUEST_SLOT_OPEN:
                continue
            if slot_id is not None and str(item.get("id") or "") != slot_id:
                continue
            chosen = item
            break
        if chosen is None:
            raise SeasonStateError(
                f"Refusing guest fill on {tournament_id}: no open reservation matches {slot_id!r}"
            )
        label = str(external_team.get("label") or "")
        if any(str(team.get("label") or "") == label for team in target.get("teams", [])):
            raise SeasonStateError(f"Refusing guest fill on {tournament_id}: {label!r} already participates")

        now = _now_iso()
        resolved_actor = _operator_identity(actor)
        external = {
            "club": str(external_team.get("club") or ""),
            "label": label,
            "age_group": str(external_team.get("age_group") or target.get("age_group") or ""),
        }
        chosen["status"] = GUEST_SLOT_FILLED
        chosen["external_team"] = external
        chosen["filled_at"] = now
        chosen["filled_by"] = resolved_actor
        target["guest_slots"] = records
        target.setdefault("teams", []).append({**external, "guest": True})
        target["reserved_guest_slots"] = active_guest_slot_count(target)
        _regenerate_tournament_games(target, resolved_problem)
        plan["tournaments"] = tournaments

        result = (
            verify_candidate(plan, resolved_problem)
            if resolved_problem
            else verify_candidate(plan)
        )
        if not result.get("ok", True):
            messages = "; ".join(
                str(v.get("message") or v.get("code")) for v in result.get("violations", [])
            )
            raise SeasonStateError(
                f"Refusing canonical guest fill: candidate fails hard verification: {messages}"
            )
        reconcile_plan_derived_state(plan, result, problem=resolved_problem)
        self._assert_request_constraints_satisfied(plan, decisions, action="guest fill")

        fingerprint = schedule_fingerprint(plan)
        updated_schedule = {
            **schedule,
            "updated_at": now,
            "revision": fingerprint,
            "fingerprint": fingerprint,
            "plan": plan,
        }
        updated_decisions = {
            **decisions,
            "updated_at": now,
            "schedule_fingerprint": fingerprint,
            "decisions": _reconcile_decisions(decisions.get("decisions", {}), plan, now=now),
        }
        _append_decision_history(
            updated_decisions,
            event="fill_guest_slot",
            tournament_id=tournament_id,
            actor=resolved_actor,
            now=now,
            tournament_fingerprint=approval_fingerprint(target),
            note=note,
            details={
                "slot_id": chosen.get("id"),
                "external_team": external,
                "before_fingerprint": str(schedule.get("revision") or ""),
                "after_fingerprint": fingerprint,
                "verification_ok": True,
            },
        )
        committed = self._commit(
            snapshot.with_schedule(updated_schedule).with_decisions(updated_decisions)
        )
        return committed.schedule

    def release_guest_slot(
        self,
        *,
        season: str,
        tournament_id: str,
        slot_id: str | None = None,
        replacement_team: dict[str, Any] | None = None,
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        note: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Release a reservation, optionally filling it with a real RVV team.

        Releasing an open reservation on a tournament that then falls below its
        verified shape is refused unless ``replacement_team`` supplies a legal
        RVV participant, so a released place never silently becomes an
        underfilled tournament.
        """

        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
        plan = dict(schedule["plan"])
        tournaments = [dict(t) for t in plan.get("tournaments", [])]
        target = next((t for t in tournaments if str(t.get("id")) == tournament_id), None)
        if target is None:
            raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")

        records = [dict(item) for item in (target.get("guest_slots") or []) if isinstance(item, Mapping)]
        chosen: dict[str, Any] | None = None
        for item in records:
            if str(item.get("status") or GUEST_SLOT_OPEN) not in (GUEST_SLOT_OPEN, GUEST_SLOT_FILLED):
                continue
            if slot_id is not None and str(item.get("id") or "") != slot_id:
                continue
            chosen = item
            break
        if chosen is None:
            raise SeasonStateError(
                f"Refusing guest release on {tournament_id}: no active reservation matches {slot_id!r}"
            )

        now = _now_iso()
        resolved_actor = _operator_identity(actor)
        removed_guest = None
        if str(chosen.get("status")) == GUEST_SLOT_FILLED:
            external = chosen.get("external_team") or {}
            guest_label = str(external.get("label") or "")
            if guest_label:
                target["teams"] = [
                    team
                    for team in target.get("teams", [])
                    if not (team.get("guest") and str(team.get("label") or "") == guest_label)
                ]
                removed_guest = guest_label
        if isinstance(replacement_team, Mapping) and str(replacement_team.get("label") or ""):
            label = str(replacement_team.get("label") or "")
            if any(str(team.get("label") or "") == label for team in target.get("teams", [])):
                raise SeasonStateError(
                    f"Refusing guest release on {tournament_id}: replacement {label!r} already participates"
                )
            target.setdefault("teams", []).append(
                {
                    "club": str(replacement_team.get("club") or ""),
                    "label": label,
                    "age_group": str(replacement_team.get("age_group") or target.get("age_group") or ""),
                }
            )

        chosen["status"] = GUEST_SLOT_RELEASED
        chosen["released_at"] = now
        chosen["released_by"] = resolved_actor
        chosen["release_reason"] = note or ""
        target["guest_slots"] = records
        target["reserved_guest_slots"] = active_guest_slot_count(target)
        _regenerate_tournament_games(target, resolved_problem)
        plan["tournaments"] = tournaments

        result = (
            verify_candidate(plan, resolved_problem)
            if resolved_problem
            else verify_candidate(plan)
        )
        if not result.get("ok", True):
            messages = "; ".join(
                str(v.get("message") or v.get("code")) for v in result.get("violations", [])
            )
            raise SeasonStateError(
                f"Refusing canonical guest release: candidate fails hard verification: {messages}"
            )
        reconcile_plan_derived_state(plan, result, problem=resolved_problem)
        if not dry_run:
            self._assert_request_constraints_satisfied(
                plan, decisions, action="guest release"
            )

        fingerprint = schedule_fingerprint(plan)
        details = {
            "slot_id": chosen.get("id"),
            "removed_guest": removed_guest,
            "replacement_team": dict(replacement_team) if isinstance(replacement_team, Mapping) else None,
            "before_fingerprint": str(schedule.get("revision") or ""),
            "after_fingerprint": fingerprint,
            "verification_ok": True,
        }
        updated_schedule = {
            **schedule,
            "updated_at": now,
            "revision": fingerprint,
            "fingerprint": fingerprint,
            "plan": plan,
        }
        if dry_run:
            updated_schedule["dry_run"] = True
            updated_schedule["guest_release_preview"] = {"tournament_id": tournament_id, **details}
            return updated_schedule

        updated_decisions = {
            **decisions,
            "updated_at": now,
            "schedule_fingerprint": fingerprint,
            "decisions": _reconcile_decisions(decisions.get("decisions", {}), plan, now=now),
        }
        _append_decision_history(
            updated_decisions,
            event="release_guest_slot",
            tournament_id=tournament_id,
            actor=resolved_actor,
            now=now,
            tournament_fingerprint=approval_fingerprint(target),
            note=note,
            details=details,
        )
        committed = self._commit(
            snapshot.with_schedule(updated_schedule).with_decisions(updated_decisions)
        )
        return committed.schedule

    # -- decision-only mutations ------------------------------------------


    def change_protection_report(
        self,
        season: str,
        *,
        include_released: bool = False,
    ) -> dict[str, Any]:
        """Return durable accepted-change guards for a promoted season."""

        snapshot = self.load(season)
        records = [
            dict(record)
            for record in snapshot.decisions.get(CHANGE_PROTECTIONS_KEY, []) or []
            if isinstance(record, Mapping)
            and (
                include_released
                or str(record.get("status") or CHANGE_PROTECTION_ACTIVE)
                == CHANGE_PROTECTION_ACTIVE
            )
        ]
        return {
            "season": season,
            "canonical_state_revision": canonical_state_revision(
                snapshot.schedule, snapshot.decisions
            ),
            "active_count": sum(
                1
                for record in records
                if str(record.get("status") or CHANGE_PROTECTION_ACTIVE)
                == CHANGE_PROTECTION_ACTIVE
            ),
            "protections": records,
        }

    def release_change_protections(
        self,
        *,
        season: str,
        protection_ids: list[str] | None = None,
        request_id: str | None = None,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Release accepted-change guards for an explicitly superseding request."""

        wanted_ids = {str(value) for value in (protection_ids or []) if str(value)}
        wanted_request = str(request_id or "")
        if not wanted_ids and not wanted_request:
            raise SeasonStateError(
                "Refusing protection release: provide --protection-id and/or --request-id"
            )

        snapshot = self.load(season)
        decisions = copy.deepcopy(snapshot.decisions)
        records = decisions.get(CHANGE_PROTECTIONS_KEY, []) or []
        now = _now_iso()
        resolved_actor = _operator_identity(actor)
        released: list[str] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            if str(record.get("status") or CHANGE_PROTECTION_ACTIVE) != CHANGE_PROTECTION_ACTIVE:
                continue
            matches_id = str(record.get("id") or "") in wanted_ids
            matches_request = bool(wanted_request) and str(record.get("request_id") or "") == wanted_request
            if not (matches_id or matches_request):
                continue
            record["status"] = CHANGE_PROTECTION_RELEASED
            record["released_at"] = now
            record["released_by"] = resolved_actor
            record["release_reason"] = note or ""
            released.append(str(record.get("id") or ""))

        if not released:
            raise SeasonStateError("No active change protections matched the release request")

        decisions["updated_at"] = now
        _append_decision_history(
            decisions,
            event="release_change_protection",
            tournament_id="",
            actor=resolved_actor,
            now=now,
            note=note,
            details={
                "released_protection_ids": released,
                "request_id": wanted_request,
            },
        )
        committed = self._commit(snapshot.with_decisions(decisions))
        return {
            "season": season,
            "canonical_state_revision": canonical_state_revision(
                committed.schedule, committed.decisions
            ),
            "released_protection_ids": released,
            "active_count": len(active_change_protections(committed.decisions)),
        }

    def approve_tournament(
        self,
        *,
        season: str,
        tournament_id: str,
        actor: str | None = None,
        note: str = "",
        placement_locked: bool = True,
        participants_locked: bool = False,
        problem: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Approve/lock one tournament after canonical hard verification."""

        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        plan = schedule["plan"]
        tournament = next(
            (t for t in plan.get("tournaments", []) if str(t.get("id")) == tournament_id), None
        )
        if tournament is None:
            raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")

        verification = verify_candidate(plan, problem) if problem else verify_candidate(plan)
        hard_blockers, unresolved_blockers = _attributable_blockers(verification, tournament_id)
        blockers = hard_blockers + unresolved_blockers
        if blockers:
            messages = "; ".join(
                str(blocker.get("message") or blocker.get("code")) for blocker in blockers
            )
            raise SeasonStateError(
                f"Refusing to approve {tournament_id}: tournament fails canonical verification: {messages}"
            )

        approved_at = _now_iso()
        resolved_actor = _operator_identity(actor)
        tournament_fingerprint = approval_fingerprint(tournament)
        previous = dict(decisions["decisions"].get(tournament_id, {}))
        record = dict(previous)
        record.update(
            {
                "status": APPROVED_STATUS,
                "placement_locked": bool(placement_locked),
                "participants_locked": bool(participants_locked),
                "approved_fingerprint": tournament_fingerprint,
                "approved_at": approved_at,
                "approved_by": resolved_actor,
                "note": note,
            }
        )
        record.pop("stale_at", None)
        record.pop("stale_reason", None)
        record.pop("unapproved_at", None)
        record.pop("unapproved_by", None)
        updated = dict(decisions)
        updated["decisions"] = dict(updated.get("decisions", {}))
        updated["decisions"][tournament_id] = record
        updated["updated_at"] = approved_at
        _append_decision_history(
            updated,
            event="approve",
            tournament_id=tournament_id,
            actor=resolved_actor,
            now=approved_at,
            tournament_fingerprint=tournament_fingerprint,
            previous_fingerprint=previous.get("approved_fingerprint"),
            note=note,
        )
        return self._commit(snapshot.with_decisions(updated)).decisions

    def unapprove_tournament(
        self,
        *,
        season: str,
        tournament_id: str,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Explicitly revoke approval and all locks for one canonical tournament."""

        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        plan = schedule["plan"]
        tournament = next(
            (t for t in plan.get("tournaments", []) if str(t.get("id")) == tournament_id), None
        )
        if tournament is None:
            raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
        existing = decisions["decisions"].get(tournament_id)
        if existing is None:
            raise SeasonStateError(f"Unknown tournament id in decisions state: {tournament_id}")

        now = _now_iso()
        resolved_actor = _operator_identity(actor)
        previous_fingerprint = existing.get("approved_fingerprint")
        record = dict(existing)
        record.update(
            {
                "status": PENDING_REVIEW_STATUS,
                "placement_locked": False,
                "participants_locked": False,
                "approved_fingerprint": None,
                "approved_at": None,
                "approved_by": None,
                "note": note or existing.get("note") or "",
                "unapproved_at": now,
                "unapproved_by": resolved_actor,
            }
        )
        record.pop("stale_at", None)
        record.pop("stale_reason", None)
        updated = dict(decisions)
        updated["decisions"] = dict(updated.get("decisions", {}))
        updated["decisions"][tournament_id] = record
        updated["updated_at"] = now
        _append_decision_history(
            updated,
            event="unapprove",
            tournament_id=tournament_id,
            actor=resolved_actor,
            now=now,
            previous_fingerprint=previous_fingerprint,
            note=note,
        )
        return self._commit(snapshot.with_decisions(updated)).decisions

    def load_participation_acceptances(self, season: str) -> list[dict[str, Any]]:
        """Return the active (non-revoked) operator participation acceptances."""

        decisions = self.load(season).decisions
        records = decisions.get(PARTICIPATION_ACCEPTANCES_KEY) or []
        if not isinstance(records, list):
            return []
        return [
            {**record, "id": _acceptance_record_id(record)}
            for record in records
            if isinstance(record, dict) and not record.get("revoked_at")
        ]

    def record_participation_acceptance(
        self,
        *,
        season: str,
        club: str,
        label: str,
        age_group: str,
        scope: str,
        direction: str,
        actual: int,
        target: int,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Persist an explicit operator acceptance of one participation deviation."""

        if not club or not label or not scope:
            raise SeasonStateError(
                "Refusing participation acceptance: club, team label and scope are required"
            )
        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        now = _now_iso()
        resolved_actor = _operator_identity(actor)
        acceptance_id = participation_acceptance_id(club, label, age_group, scope)
        record = {
            "id": acceptance_id,
            "club": club,
            "label": label,
            "age_group": age_group,
            "scope": scope,
            "direction": direction,
            "target": int(target),
            "actual": int(actual),
            "accepted_deviation": int(actual) - int(target),
            "status": OPERATOR_ACCEPTED,
            "accepted_at": now,
            "accepted_by": resolved_actor,
            "note": note or "",
            "schedule_fingerprint": schedule.get("fingerprint"),
        }
        existing = decisions.get(PARTICIPATION_ACCEPTANCES_KEY) or []
        if not isinstance(existing, list):
            existing = []
        kept = [
            entry
            for entry in existing
            if not isinstance(entry, dict)
            or _acceptance_record_id(entry) != acceptance_id
        ]
        kept.append(record)
        updated = {**decisions, PARTICIPATION_ACCEPTANCES_KEY: kept, "updated_at": now}
        self._commit(snapshot.with_decisions(updated))
        return record

    def revoke_participation_acceptance(
        self,
        *,
        season: str,
        club: str,
        label: str,
        age_group: str,
        scope: str,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Revoke one active participation acceptance, preserving its audit trail."""

        snapshot = self.load(season)
        decisions = snapshot.decisions
        now = _now_iso()
        resolved_actor = _operator_identity(actor)
        acceptance_id = participation_acceptance_id(club, label, age_group, scope)
        records = decisions.get(PARTICIPATION_ACCEPTANCES_KEY) or []
        if not isinstance(records, list):
            records = []
        updated_records: list[dict[str, Any]] = []
        revoked: dict[str, Any] | None = None
        for record in records:
            if (
                isinstance(record, dict)
                and _acceptance_record_id(record) == acceptance_id
                and not record.get("revoked_at")
            ):
                revoked = {
                    **record,
                    "id": acceptance_id,
                    "revoked_at": now,
                    "revoked_by": resolved_actor,
                    "revoke_note": note or "",
                }
                updated_records.append(revoked)
            else:
                updated_records.append(record)
        if revoked is None:
            raise SeasonStateError(f"No active participation acceptance for {acceptance_id!r}")
        updated = {**decisions, PARTICIPATION_ACCEPTANCES_KEY: updated_records, "updated_at": now}
        self._commit(snapshot.with_decisions(updated))
        return revoked

    # -- read-only projections --------------------------------------------

    def approval_report(self, season: str) -> dict[str, Any]:
        """Read-only approval/lock status for every canonical tournament."""

        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        records = decisions.get("decisions", {}) or {}
        tournaments: list[dict[str, Any]] = []
        stale_approvals: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for tournament in schedule["plan"].get("tournaments", []) or []:
            tournament_id = str(tournament.get("id") or "")
            if not tournament_id:
                continue
            seen_ids.add(tournament_id)
            resolved = resolve_approval(records.get(tournament_id), tournament)
            entry = {
                "tournament_id": tournament_id,
                "status": resolved["status"],
                "stale": resolved["stale"],
                "placement_locked": resolved["placement_locked"],
                "participants_locked": resolved["participants_locked"],
                "approved_fingerprint": resolved["approved_fingerprint"],
                "current_fingerprint": resolved["current_fingerprint"],
                "approved_at": resolved["approved_at"],
                "approved_by": resolved["approved_by"],
                "note": resolved["note"],
                "stale_reason": resolved.get("stale_reason"),
            }
            tournaments.append(entry)
            if resolved["stale"]:
                stale_approvals.append(
                    {
                        "code": "stale_approval",
                        "tournament_id": tournament_id,
                        "approved_fingerprint": resolved["approved_fingerprint"],
                        "current_fingerprint": resolved["current_fingerprint"],
                        "stale_reason": resolved.get("stale_reason"),
                    }
                )
        orphaned = [
            {
                "code": "orphaned_approval",
                "tournament_id": tournament_id,
                "approved_fingerprint": (record or {}).get("approved_fingerprint"),
            }
            for tournament_id, record in records.items()
            if tournament_id not in seen_ids and (record or {}).get("approved_fingerprint")
        ]
        counts = {
            "total": len(tournaments),
            "approved": sum(1 for entry in tournaments if entry["status"] == APPROVED_STATUS),
            "stale": len(stale_approvals),
            "orphaned": len(orphaned),
            "locked": sum(
                1
                for entry in tournaments
                if entry["placement_locked"] or entry["participants_locked"]
            ),
            "pending_review": sum(
                1 for entry in tournaments if entry["status"] == PENDING_REVIEW_STATUS
            ),
        }
        return {
            "season": season,
            "schedule_fingerprint": decisions.get("schedule_fingerprint"),
            "revision": schedule.get("revision"),
            "canonical_state_revision": canonical_state_revision(schedule, decisions),
            "counts": counts,
            "tournaments": tournaments,
            "stale_approvals": stale_approvals,
            "orphaned_approvals": orphaned,
        }


def _acceptance_record_id(record: Mapping[str, Any]) -> str:
    """Return the canonical acceptance id, migrating a legacy record on read.

    A legacy record omitted ``age_group`` from its id. Recomputing from the
    stored fields lets revocation match it before any write migrates the file.
    """

    club = str(record.get("club") or "")
    label = str(record.get("label") or "")
    age_group = str(record.get("age_group") or "")
    scope = str(record.get("scope") or "")
    if club and label and age_group and scope:
        return participation_acceptance_id(club, label, age_group, scope)
    return str(record.get("id") or "")


__all__ = [
    "APPROVED_STATUS",
    "CanonicalSeasonService",
    "PENDING_REVIEW_STATUS",
    "STALE_APPROVAL_STATUS",
]
