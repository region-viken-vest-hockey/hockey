"""Single-tournament placement moves."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.canonical_baseline import approval_fingerprint, resolve_approval
from tournament_scheduler.canonical_state import (
    canonical_state_revision,
    schedule_fingerprint,
)
from tournament_scheduler.change_protections import (
    append_change_protections,
    build_move_protections,
    protection_violations,
)
from tournament_scheduler.request_constraints import (
    request_constraint_violations,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.operational_acceptability import (
    check_operational_acceptability,
    required_opt_in_flags,
)
from tournament_scheduler.plan_derived_state import reconcile_plan_derived_state
from tournament_scheduler.planning_contract import verify_candidate

from .shared import (
    _operator_identity,
    _now_iso,
    _append_decision_history,
    _reconcile_decisions,
    _resolve_plan_problem,
    _placement_snapshot,
    _parse_iso_date_for_move,
    _validate_start_time_for_move,
)

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


def move_tournament(
    service,
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
    snapshot = service.load(season)
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
    committed = service._commit(snapshot.with_schedule(updated_schedule).with_decisions(updated_decisions))
    return committed.schedule
