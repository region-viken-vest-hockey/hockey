"""Shared canonical-season lifecycle helpers, constants and derived-projection readers."""

from __future__ import annotations

import os
from datetime import date as _date, datetime, timezone
from typing import Any, Mapping

from tournament_scheduler.calendar_bookings import project_associations_into_problem
from tournament_scheduler.canonical_baseline import approval_fingerprint
from tournament_scheduler.canonical_banned_dates import project_banned_dates_into_problem
from tournament_scheduler.canonical_holiday_exceptions import project_exceptions_into_problem
from tournament_scheduler.guest_slots import GUEST_SLOT_OPEN, active_guest_slots
from tournament_scheduler.infrastructure.canonical_revision_history import (
    load_canonical_schedule_at_revision,
)
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.published_baseline import (
    backfill_projection_entry,
    baseline_migration_requires_backfill,
    baseline_projection,
    baseline_requires_backfill,
    projection_problem_from_schedule,
)
from tournament_scheduler.pipeline.export_projection_guard import tournament_projection

APPROVED_STATUS = "approved"


STALE_APPROVAL_STATUS = "stale_approval"


PENDING_REVIEW_STATUS = "pending_review"


def _publication_canonical_projection(
    service,
    baseline: Mapping[str, Any],
) -> dict[str, dict[str, Any]] | None:
    """Resolve the canonical projection at a baseline's publication revision."""

    publication_schedule = load_canonical_schedule_at_revision(
        str(baseline.get("season") or ""),
        str(baseline.get("canonical_revision") or ""),
        season_root=getattr(service.store, "root", "season"),
    )
    if not isinstance(publication_schedule, Mapping):
        return None
    return tournament_projection(
        publication_schedule.get("plan") or {},
        projection_problem_from_schedule(publication_schedule),
    )


def attested_additions(
    baseline: Mapping[str, Any],
    *,
    publication_projection: Mapping[str, Mapping[str, Any]] | None,
    current_projection: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Build the attested omission/materialization entries for reconciliation.

    Legacy attested entries are backfilled from the historically authoritative
    projection: omissions from the publication revision, post-publication
    materializations from current canonical state. The immutable baseline record
    is never rewritten.
    """

    migration = baseline.get("migration") if isinstance(baseline.get("migration"), Mapping) else {}
    additions: dict[str, dict[str, Any]] = {}
    for entry in migration.get("publication_omissions") or []:
        if not isinstance(entry, Mapping):
            continue
        tournament_id = str(entry.get("tournament_id") or "")
        if not tournament_id:
            continue
        additions[tournament_id] = backfill_projection_entry(
            entry,
            authoritative=(publication_projection or {}).get(tournament_id),
        )
    for entry in migration.get("materializations") or []:
        if not isinstance(entry, Mapping):
            continue
        tournament_id = str(entry.get("tournament_id") or "")
        if not tournament_id:
            continue
        additions[tournament_id] = backfill_projection_entry(
            entry,
            authoritative=(current_projection or {}).get(tournament_id),
        )
    return additions


def published_baseline_reconciliation(
    service,
    baseline: Mapping[str, Any],
    *,
    current_projection: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return the (published projection, attested additions) pair for a baseline.

    A published baseline recorded before the versioned operational projection
    lacks occupied-interval/cancellation/guest facts. Those are recovered from
    the historically authoritative canonical schedule at the recorded
    publication revision; the immutable baseline record itself is never
    rewritten.
    """

    needs_backfill = baseline_requires_backfill(baseline) or baseline_migration_requires_backfill(baseline)
    publication_projection = _publication_canonical_projection(service, baseline) if needs_backfill else None
    published = baseline_projection(
        baseline,
        publication_canonical_projection=publication_projection,
    )
    additions = attested_additions(
        baseline,
        publication_projection=publication_projection,
        current_projection=current_projection,
    )
    return published, additions


def _operator_identity(actor: str | None) -> str:
    return actor or os.environ.get("RVV_OPERATOR") or os.environ.get("USER") or "operator"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


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
        resolved = project_exceptions_into_problem(resolved, decisions)
        resolved = project_banned_dates_into_problem(resolved, decisions)
        resolved = project_associations_into_problem(resolved, decisions, schedule.get("plan") or {})
    return resolved


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
