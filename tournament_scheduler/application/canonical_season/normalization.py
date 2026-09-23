"""Canonical placement and arena-identity normalization."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from tournament_scheduler.canonical_baseline import approval_fingerprint
from tournament_scheduler.canonical_state import (
    CHANGE_PROTECTIONS_KEY,
    schedule_fingerprint,
)
from tournament_scheduler.change_protections import (
    ACTIVE as CHANGE_PROTECTION_ACTIVE,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.plan_derived_state import reconcile_plan_derived_state
from tournament_scheduler.planning_contract import verify_candidate
from tournament_scheduler.serialization.season_plan import SEASON_PLAN_SCHEMA_VERSION

from .shared import (
    APPROVED_STATUS,
    _operator_identity,
    _now_iso,
    _append_decision_history,
    _reconcile_decisions,
)

def normalize_placements(
    service,
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
    from tournament_scheduler.published_baseline import (
        SeasonSealedError,
        is_published_sealed,
    )

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    if not dry_run and is_published_sealed(decisions):
        raise SeasonSealedError(
            "Refusing to normalize placements on a published_sealed season: "
            "mutating normalization could silently move or demote published "
            "tournaments. '--dry-run' remains available as a diagnostic; a real "
            "conflict must be resolved with an explicit move/cancel/booking decision."
        )
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
        service._assert_request_constraints_satisfied(
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
    committed = service._commit(
        snapshot.with_schedule(updated_schedule).with_decisions(updated_decisions)
    )
    return committed.schedule, committed.decisions


def normalize_arena_identities(
    service,
    *,
    season: str,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Re-emit the canonical schedulable arena across a promoted season.

    Arena identity is owned by ``club_registry.canonical_arena_name``: a
    tournament already placed at its club's canonical arena is left
    byte-identical, while one at a legacy alias of that same venue (for
    example ``Ringerikshallen`` -> ``Schjongshallen``) is rewritten in
    place. This is an identity correction, not a scheduling change: dates,
    start times, hosts, participants, games, approvals, change guards,
    request constraints and guest reservations are preserved, and any
    active ``arena`` placement guard is re-pointed to the canonical value
    so the accepted intent survives. The stored verification problem's
    club->arena mapping and canonical-baseline snapshot are re-emitted too,
    so a later repair/replan cannot reintroduce the legacy label.
    """

    from tournament_scheduler.club_registry import canonical_arena_name
    from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    before_plan = schedule.get("plan") or {}
    before_tournaments = {
        str(tournament.get("id") or ""): copy.deepcopy(tournament)
        for tournament in before_plan.get("tournaments", []) or []
    }

    plan = copy.deepcopy(before_plan)
    changes: list[dict[str, Any]] = []
    for tournament in plan.get("tournaments", []) or []:
        old_arena = tournament.get("arena")
        if not isinstance(old_arena, str) or not old_arena:
            continue
        new_arena = canonical_arena_name(old_arena)
        if new_arena is None or new_arena == old_arena:
            continue
        tournament["arena"] = new_arena
        changes.append(
            {
                "tournament_id": str(tournament.get("id") or ""),
                "from": old_arena,
                "to": new_arena,
            }
        )

    report = {
        "changed": bool(changes),
        "changed_count": len(changes),
        "changes": changes,
    }
    if not changes:
        # Report the (empty) result without writing canonical state; do not
        # leak the previous migration's report back as if it were current.
        return {**schedule, "arena_normalization": report}, decisions

    # Prove the rewrite touched only the ``arena`` field on the reported ids.
    for change in changes:
        before = before_tournaments[change["tournament_id"]]
        after = next(
            tournament
            for tournament in plan["tournaments"]
            if str(tournament.get("id") or "") == change["tournament_id"]
        )
        before_compare = copy.deepcopy(before)
        after_compare = copy.deepcopy(after)
        before_compare.pop("arena", None)
        after_compare.pop("arena", None)
        if before_compare != after_compare:
            raise SeasonStateError(
                "Refusing arena normalization: tournament "
                f"{change['tournament_id']} changed beyond its arena"
            )

    # Re-emit derived arena projections (counts keyed by arena name).
    arena_counts = plan.get("arena_counts")
    if isinstance(arena_counts, Mapping):
        rewritten_counts: dict[str, Any] = {}
        for arena, count in arena_counts.items():
            if isinstance(arena, str) and not arena.startswith("_"):
                canonical = canonical_arena_name(arena) or arena
            else:
                canonical = arena
            if canonical in rewritten_counts and isinstance(count, int) and isinstance(
                rewritten_counts[canonical], int
            ):
                rewritten_counts[canonical] += count
            else:
                rewritten_counts[canonical] = count
        plan["arena_counts"] = rewritten_counts

    # Re-emit the stored verification problem's shared arena identities.
    resolved_problem: dict[str, Any] | None = None
    verification_context = schedule.get("verification_context")
    if isinstance(verification_context, Mapping):
        stored_problem = verification_context.get("problem")
        if isinstance(stored_problem, Mapping):
            resolved_problem = copy.deepcopy(dict(stored_problem))
            clubs = resolved_problem.get("clubs")
            if isinstance(clubs, Mapping):
                resolved_problem["clubs"] = {
                    club: (
                        canonical_arena_name(arena) or arena
                        if isinstance(arena, str)
                        else arena
                    )
                    for club, arena in clubs.items()
                }
            baseline = resolved_problem.get("canonical_baseline")
            if isinstance(baseline, Mapping):
                for baseline_tournament in baseline.get("tournaments", []) or []:
                    if not isinstance(baseline_tournament, dict):
                        continue
                    baseline_arena = baseline_tournament.get("arena")
                    if isinstance(baseline_arena, str):
                        baseline_tournament["arena"] = (
                            canonical_arena_name(baseline_arena) or baseline_arena
                        )

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
            f"Refusing arena normalization: candidate fails hard verification: {messages}"
        )
    reconcile_plan_derived_state(plan, result, problem=resolved_problem)
    if not dry_run:
        service._assert_request_constraints_satisfied(
            plan, decisions, action="arena normalization"
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
        "arena_normalization": report,
    }
    if resolved_problem is not None and isinstance(verification_context, Mapping):
        updated_context = dict(verification_context)
        updated_context["problem"] = resolved_problem
        updated_context["problem_fingerprint"] = stable_payload_sha256(resolved_problem)
        updated_schedule["verification_context"] = updated_context

    # Preserve approvals and active placement guards across the identity
    # change: the venue is the same even though its label changed.
    records = {
        str(key): dict(value)
        for key, value in (decisions.get("decisions") or {}).items()
        if isinstance(value, Mapping)
    }
    for change in changes:
        record = records.get(change["tournament_id"])
        if not record:
            continue
        if str(record.get("status") or "") == APPROVED_STATUS and record.get(
            "approved_fingerprint"
        ):
            tournament = next(
                entry
                for entry in plan["tournaments"]
                if str(entry.get("id") or "") == change["tournament_id"]
            )
            record["approved_fingerprint"] = approval_fingerprint(tournament)
            records[change["tournament_id"]] = record

    protections = decisions.get(CHANGE_PROTECTIONS_KEY) or []
    updated_protections: list[dict[str, Any]] = []
    for protection in protections:
        if not isinstance(protection, Mapping):
            updated_protections.append(dict(protection))
            continue
        record = dict(protection)
        if (
            str(record.get("status") or CHANGE_PROTECTION_ACTIVE)
            == CHANGE_PROTECTION_ACTIVE
            and str(record.get("kind") or "") == "placement_field"
            and str(record.get("field") or "") == "arena"
        ):
            old_value = record.get("value")
            if isinstance(old_value, str):
                new_value = canonical_arena_name(old_value)
                if new_value and new_value != old_value:
                    record["value"] = new_value
                    record["previous_value"] = old_value
        updated_protections.append(record)

    updated_decisions = {
        **decisions,
        "updated_at": now,
        "schedule_fingerprint": fingerprint,
        "decisions": _reconcile_decisions(records, plan, now=now),
        CHANGE_PROTECTIONS_KEY: updated_protections,
    }
    if dry_run:
        updated_schedule["dry_run"] = True
        return updated_schedule, decisions

    _append_decision_history(
        updated_decisions,
        event="normalize_arena_identities",
        tournament_id="",
        actor=actor,
        now=now,
        note=note,
        details={
            "changed_count": len(changes),
            "changes": changes,
            "verification_ok": True,
        },
    )
    committed = service._commit(
        snapshot.with_schedule(updated_schedule).with_decisions(updated_decisions)
    )
    return committed.schedule, committed.decisions
