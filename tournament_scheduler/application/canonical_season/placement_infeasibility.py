"""Canonical lifecycle for durable placement-infeasibility proofs.

This is the application-layer owner for recording and reading the typed
evidence that one unplaced placement obligation cannot be satisfied with the
current authoritative capacity. It runs the repository-owned bounded search for
the selected obligations, and only persists a proof when the search walked its
whole declared scope and produced zero legal candidates
(:mod:`tournament_scheduler.placement_infeasibility`). The raw obligation is
never removed and the search never mutates the schedule: this is a
decision-only, revision-bound write, exactly like an ice-time override or an
operator acceptance.

Consumers (``season findings`` / ``season audit``) read the persisted proof and
attach it to the matching finding; the shared resolution classifier then treats
a *current* proof as ``proven_infeasible_with_current_capacity`` and reopens the
obligation as soon as the capacity/calendar fingerprint or the search
capability changes.
"""

from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping

from tournament_scheduler.canonical_state import canonical_state_revision
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.placement_infeasibility import (
    ACTIVE,
    build_infeasibility_proof,
    clear_obligation_proofs,
    proof_is_current,
    proof_records,
    proofs_by_obligation,
    replace_obligation_proof,
)
from tournament_scheduler.unplaced_placement_repair import (
    enumerate_unplaced_placement_repairs,
    unplaced_placement_search_capability,
)

from .shared import (
    _append_decision_history,
    _now_iso,
    _operator_identity,
    _resolve_plan_problem,
)


def _obligations(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(entry)
        for entry in plan.get("unresolved_tournament_placements") or []
        if isinstance(entry, Mapping) and str(entry.get("id") or "")
    ]


def _select_obligations(
    plan: Mapping[str, Any],
    finding_ids: Iterable[str] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    available = _obligations(plan)
    wanted = {str(value) for value in (finding_ids or []) if str(value)}
    if not wanted:
        return available, []
    selected = [entry for entry in available if str(entry.get("id") or "") in wanted]
    missing = sorted(wanted - {str(entry.get("id") or "") for entry in selected})
    return selected, missing


def record_placement_infeasibility_proofs(
    service,
    *,
    season: str,
    finding_ids: Iterable[str] | None = None,
    actor: str | None = None,
    note: str = "",
    expected_revision: str | None = None,
    problem: dict[str, Any] | None = None,
    allow_search: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the canonical bounded search and persist a proof per infeasible obligation."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule.get("plan") or {}
    current_revision = canonical_state_revision(schedule, decisions)
    if expected_revision and str(expected_revision) != current_revision:
        raise SeasonStateError(
            "Refusing to record placement-infeasibility proofs: the canonical revision "
            f"changed ({str(expected_revision)[:12]} -> {current_revision[:12]})"
        )
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
    if resolved_problem is None:
        raise SeasonStateError(
            "Canonical season carries no verification-context problem; infeasibility "
            "search cannot be run without re-promotion"
        )
    selected, missing = _select_obligations(plan, finding_ids)
    if not selected:
        raise SeasonStateError("No unresolved placement obligations matched the request")

    capability = unplaced_placement_search_capability()
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    proofs: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for obligation in selected:
        obligation_id = str(obligation.get("id") or "")
        result = enumerate_unplaced_placement_repairs(
            plan,
            resolved_problem,
            finding_ids=[obligation_id],
            allow_search=allow_search,
        )
        coverage = dict((result.get("coverage") or {}).get(obligation_id) or {})
        rejected = [
            entry
            for entry in result.get("rejected_candidates") or []
            if str(entry.get("finding_id") or "") == obligation_id
        ]
        proof = build_infeasibility_proof(
            obligation=obligation,
            coverage=coverage,
            rejected_candidates=rejected,
            capability=capability,
            plan=plan,
            problem=resolved_problem,
            canonical_revision=current_revision,
            actor=resolved_actor,
            recorded_at=now,
        )
        if proof is None:
            skipped.append(
                {
                    "obligation_id": obligation_id,
                    "reason": "search_scope_not_complete",
                    "coverage_status": coverage.get("status"),
                    "untried": list(coverage.get("untried") or []),
                    "option_count": len(result.get("options") or []),
                }
            )
            continue
        proofs.append(proof)

    report: dict[str, Any] = {
        "season": season,
        "revision": current_revision,
        "canonical_state_revision": current_revision,
        "search_capability": capability.to_dict(),
        "probing_allow_search": bool(allow_search),
        "requested_obligation_count": len(selected),
        "recorded_count": len(proofs),
        "skipped_count": len(skipped),
        "missing_finding_ids": missing,
        "recorded": proofs,
        "skipped": skipped,
        "dry_run": bool(dry_run),
    }
    if dry_run or not proofs:
        return report

    updated = copy.deepcopy(decisions)
    for proof in proofs:
        replace_obligation_proof(updated, proof)
    _append_decision_history(
        updated,
        event="record_placement_infeasibility",
        tournament_id="",
        actor=resolved_actor,
        now=now,
        note=note,
        details={
            "obligation_ids": [proof["obligation_id"] for proof in proofs],
            "capacity_fingerprints": sorted(
                {str(proof["capacity_fingerprint"]) for proof in proofs}
            ),
            "search_capability_fingerprint": capability.fingerprint,
        },
    )
    updated["updated_at"] = now
    committed = service._commit(
        snapshot.with_decisions(updated), expected_revision=expected_revision
    )
    report["canonical_state_revision"] = canonical_state_revision(
        committed.schedule, committed.decisions
    )
    return report


def release_placement_infeasibility_proofs(
    service,
    *,
    season: str,
    finding_ids: Iterable[str] | None = None,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Supersede active infeasibility proofs so the obligations reopen."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule.get("plan") or {}
    selected, missing = _select_obligations(plan, finding_ids)
    if not selected:
        raise SeasonStateError("No unresolved placement obligations matched the request")
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    updated = copy.deepcopy(decisions)
    cleared = clear_obligation_proofs(
        updated,
        obligation_ids=[str(entry.get("id") or "") for entry in selected],
        cleared_at=now,
        cleared_by=resolved_actor,
        reason=note,
    )
    if not cleared:
        raise SeasonStateError("No active placement-infeasibility proofs matched the request")
    _append_decision_history(
        updated,
        event="release_placement_infeasibility",
        tournament_id="",
        actor=resolved_actor,
        now=now,
        note=note,
        details={"obligation_ids": sorted(cleared)},
    )
    updated["updated_at"] = now
    committed = service._commit(snapshot.with_decisions(updated))
    return {
        "season": season,
        "canonical_state_revision": canonical_state_revision(
            committed.schedule, committed.decisions
        ),
        "released_obligation_ids": sorted(cleared),
        "missing_finding_ids": missing,
    }


def placement_infeasibility_report(
    service,
    *,
    season: str,
    problem: dict[str, Any] | None = None,
    include_superseded: bool = False,
) -> dict[str, Any]:
    """Read-only status of recorded proofs, including current/stale evaluation."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule.get("plan") or {}
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
    capability = unplaced_placement_search_capability()
    by_id = {str(entry.get("id") or ""): entry for entry in _obligations(plan)}
    entries: list[dict[str, Any]] = []
    active_current = 0
    for record in proof_records(decisions, include_superseded=include_superseded):
        obligation_id = str(record.get("obligation_id") or record.get("id") or "")
        obligation = by_id.get(obligation_id)
        if obligation is None:
            current, stale_reason = False, "obligation_no_longer_unplaced"
        else:
            current, stale_reason = proof_is_current(
                record,
                plan=plan,
                problem=resolved_problem,
                obligation=obligation,
                current_capability=capability,
            )
        if current:
            active_current += 1
        entries.append({**record, "current": current, "stale_reason": stale_reason})
    unresolved = sorted(set(by_id) - set(proofs_by_obligation(decisions)))
    return {
        "season": season,
        "revision": schedule.get("revision"),
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "search_capability": capability.to_dict(),
        "active_proof_count": len([entry for entry in entries if entry.get("status") == ACTIVE]),
        "current_proof_count": active_current,
        "unproven_obligation_ids": unresolved,
        "proofs": entries,
    }


__all__ = [
    "placement_infeasibility_report",
    "record_placement_infeasibility_proofs",
    "release_placement_infeasibility_proofs",
]
