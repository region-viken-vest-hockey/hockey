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
    CANONICAL_STATE_REVISION_KEY,
    PARTICIPATION_ACCEPTANCES_KEY,
    canonical_state_revision,
    compute_canonical_state_revision,
    migrate_participation_acceptance_ids,
    participation_acceptance_id,
    schedule_fingerprint,
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
        decisions[CANONICAL_STATE_REVISION_KEY] = compute_canonical_state_revision(
            snapshot.schedule, decisions
        )
        committed = snapshot.with_decisions(decisions)
        self.store.write(committed, require_absent=require_absent)
        return committed

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
            season=resolved_season, schedule=schedule_payload, decisions=decisions_payload
        )
        committed = self._commit(snapshot, require_absent=not force)
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
    ) -> dict[str, Any]:
        """Apply or preview a bounded placement mutation to canonical state."""

        if not any(value is not None for value in (date, arena, host_club, start_time)):
            raise SeasonStateError(
                "Refusing canonical move: specify at least one target placement field"
            )
        target_date = _parse_iso_date_for_move(date, "date") if date is not None else None
        _validate_start_time_for_move(start_time)

        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        before_fingerprint = str(schedule.get("revision") or schedule.get("fingerprint") or "")
        before_canonical_revision = canonical_state_revision(schedule, decisions)
        plan = dict(schedule["plan"])
        tournaments = [dict(t) for t in plan.get("tournaments", [])]
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

        original_placement = _placement_snapshot(target)
        original_tournament_fingerprint = approval_fingerprint(target)
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
        moved_tournament: dict[str, Any] | None = None
        for tournament in tournaments:
            if str(tournament.get("id")) != tournament_id:
                continue
            for field, value in {
                "date": date,
                "arena": arena,
                "host_club": effective_host_club,
                "start_time": start_time,
            }.items():
                if value is not None and tournament.get(field) != value:
                    tournament[field] = value
                    changed = True
            if changed:
                tournament.pop("requires_host_confirmation", None)
                tournament.pop("host_confirmation_reason", None)
            moved_tournament = tournament
            break
        if not changed:
            return schedule

        plan["tournaments"] = tournaments
        result = verify_candidate(plan, problem) if problem else verify_candidate(plan)
        if not result.get("ok", True):
            messages = "; ".join(
                str(v.get("message") or v.get("code")) for v in result.get("violations", [])
            )
            raise SeasonStateError(
                f"Refusing canonical mutation: candidate fails hard verification: {messages}"
            )
        reconcile_plan_derived_state(plan, result, problem=problem)

        now = _now_iso()
        fingerprint = schedule_fingerprint(plan)
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
                "new_placement": _placement_snapshot(moved_tournament or target),
                "before_fingerprint": before_fingerprint,
                "after_fingerprint": fingerprint,
                "before_canonical_revision": before_canonical_revision,
                "verification_result": result,
                "run_id": run_id,
            }
            return updated_schedule

        updated_decisions = dict(decisions)
        updated_decisions["schedule_fingerprint"] = fingerprint
        updated_decisions["updated_at"] = now
        updated_decisions["decisions"] = _reconcile_decisions(
            updated_decisions.get("decisions", {}), plan, now=now
        )
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
                "new_placement": _placement_snapshot(moved_tournament or target),
                "before_fingerprint": before_fingerprint,
                "after_fingerprint": fingerprint,
                "before_canonical_revision": before_canonical_revision,
                "verification_result": result,
                "run_id": run_id,
            },
        )
        committed = self._commit(snapshot.with_schedule(updated_schedule).with_decisions(updated_decisions))
        return committed.schedule

    def apply_candidate(
        self,
        *,
        season: str,
        candidate: dict[str, Any],
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        change_weights: dict[str, float] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Apply a verified replan candidate to canonical season state."""

        from tournament_scheduler.canonical_baseline import (
            build_canonical_baseline,
            change_cost,
            verify_canonical_locks,
        )

        snapshot = self.load(season)
        schedule, decisions = snapshot.schedule, snapshot.decisions
        baseline = build_canonical_baseline(schedule, decisions)
        normalized_candidate = extract_candidate(candidate)

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
            },
        }
        updated_decisions = {
            **decisions,
            "updated_at": now,
            "schedule_fingerprint": fingerprint,
            "decisions": _reconcile_decisions(decisions.get("decisions", {}), plan, now=now),
        }
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

    # -- decision-only mutations ------------------------------------------

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
