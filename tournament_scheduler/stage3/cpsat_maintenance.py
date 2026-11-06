"""CP-SAT search over a :class:`MaintenanceProblem`, returning candidate patches.

This is the single combinatorial participant-maintenance path (ADR 0008). It
reuses the fixed-skeleton participant model in ``stage3/cpsat.py`` rather than
adding another search: the maintenance problem only decides *which* tournaments
that model may re-staff (everything else is pinned to its exact baseline roster)
and binds the answer to the baseline it was solved against.

It never mutates canonical state, never decides acceptability, and never
claims a patch is applicable: callers must still run
``planning.contract.verify_candidate`` and the maintenance consequence gates,
and may only apply through the revision-bound canonical repair path.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional

from tournament_scheduler.planning.contract import candidate_content_fingerprint
from tournament_scheduler.stage3.cpsat import CpSatNoCandidate, CpSatUnavailable, optimize_candidate_cp_sat
from tournament_scheduler.stage3.maintenance_problem import (
    MaintenanceProblem,
    MaintenanceSolveResult,
    StaleMaintenanceBaseline,
    apply_patches,
    problem_content_fingerprint,
)

# CP-SAT statuses that mean "the budget ran out", never "no solution exists".
_BUDGET_STATUSES = frozenset({"UNKNOWN"})
# Statuses that are a proof (or a deterministic structural finding) that the
# bounded scope has no valid assignment.
_INFEASIBLE_STATUSES = frozenset(
    {"INFEASIBLE", "INFEASIBLE_NO_BYE_ROSTER_SIZE", "BASELINE_CONSTRAINT_CONFLICT", "MODEL_INVALID"}
)


def _roster_signature(tournament: Mapping[str, Any]) -> tuple:
    return tuple(
        sorted(
            (str(team.get("club") or ""), str(team.get("label") or ""), str(team.get("age_group") or ""))
            for team in tournament.get("teams", []) or []
        )
    )


def _scoped_inputs(
    maintenance: MaintenanceProblem, candidate: Mapping[str, Any], problem: Optional[Mapping[str, Any]]
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Sub-candidate/sub-problem holding only the scoped age groups, with every
    non-mutable tournament pinned to its baseline roster.

    Participation, duplicate-date and pairing rules are per age group, so
    dropping other age groups loses no constraint and keeps the model small.
    """
    mutable = set(maintenance.mutable_tournament_ids)
    groups = set(maintenance.scope_age_groups)
    sub_candidate = {key: deepcopy(value) for key, value in candidate.items() if key != "tournaments"}
    sub_candidate["tournaments"] = [
        deepcopy(tournament)
        for tournament in candidate.get("tournaments", []) or []
        if str(tournament.get("age_group") or "") in groups
    ]
    pinned = sorted(
        str(tournament.get("id"))
        for tournament in sub_candidate["tournaments"]
        if str(tournament.get("id")) not in mutable
    )
    sub_problem = deepcopy(dict(problem or {}))
    adjustments = dict(sub_problem.get("manual_adjustments") or {})
    # Overrides, not extends: the contract folds placement-only canonical locks
    # into pins, but a placement-only lock must not freeze participants.
    adjustments["pinned_tournament_ids"] = pinned
    sub_problem["manual_adjustments"] = adjustments
    return sub_candidate, sub_problem


def solve_maintenance_problem(
    maintenance: MaintenanceProblem,
    candidate: Mapping[str, Any],
    problem: Optional[Mapping[str, Any]],
) -> MaintenanceSolveResult:
    """Search *maintenance*'s scope and return candidate patches.

    Raises :class:`StaleMaintenanceBaseline` when *candidate* or *problem* is
    not the exact baseline the maintenance problem was built from: a result is
    only ever meaningful against that revision.
    """
    baseline_fingerprint = candidate_content_fingerprint(candidate)
    if baseline_fingerprint != maintenance.baseline_fingerprint:
        raise StaleMaintenanceBaseline(maintenance.baseline_fingerprint, baseline_fingerprint)
    problem_fingerprint = problem_content_fingerprint(problem)
    if problem_fingerprint != maintenance.problem_fingerprint:
        raise StaleMaintenanceBaseline(maintenance.problem_fingerprint, problem_fingerprint, what="planning problem")

    def result(status: str, **kwargs: Any) -> MaintenanceSolveResult:
        diagnostics = {
            "maintenance_problem_fingerprint": maintenance.fingerprint(),
            "mutable_tournament_count": len(maintenance.mutable_tournament_ids),
            "fixed_tournament_count": len(maintenance.fixed_participants),
            "excluded_tournament_ids": dict(maintenance.excluded_tournament_ids),
            "fixed_reasons": dict(maintenance.fixed_reasons),
            "objective_tiers": list(maintenance.objective_tiers),
            "search_budget_seconds": maintenance.search_budget_seconds,
            "seed": maintenance.seed,
            **kwargs.pop("diagnostics", {}),
        }
        return MaintenanceSolveResult(
            status=status,
            problem_fingerprint=maintenance.fingerprint(),
            baseline_fingerprint=baseline_fingerprint,
            diagnostics=diagnostics,
            **kwargs,
        )

    if not maintenance.mutable_tournament_ids:
        return result("no_mutable_scope")

    sub_candidate, sub_problem = _scoped_inputs(maintenance, candidate, problem)
    try:
        solved = optimize_candidate_cp_sat(
            sub_candidate,
            sub_problem,
            solve_budget_seconds=maintenance.search_budget_seconds,
            seed=maintenance.seed,
            decompose_by_half=True,
            lexicographic=True,
        )
    except CpSatUnavailable as exc:
        return result("unavailable", diagnostics={"reason": str(exc)})
    except CpSatNoCandidate as exc:
        status = "budget_exhausted" if exc.status in _BUDGET_STATUSES else "infeasible"
        if exc.status not in _BUDGET_STATUSES and exc.status not in _INFEASIBLE_STATUSES:
            status = "budget_exhausted"
        return result(
            status,
            diagnostics={"solver_status": exc.status, "solver_diagnostics": dict(exc.diagnostics)},
        )

    solved_by_id = {str(t.get("id")): t for t in solved.get("tournaments", []) or []}
    baseline_by_id = {str(t.get("id", index)): t for index, t in enumerate(candidate.get("tournaments", []) or [])}
    patches: List[Dict[str, Any]] = []
    for tournament_id in maintenance.mutable_tournament_ids:
        new = solved_by_id.get(tournament_id)
        old = baseline_by_id.get(tournament_id)
        if new is None or old is None or _roster_signature(new) == _roster_signature(old):
            continue
        patches.append(
            {
                "tournament_id": tournament_id,
                "teams": deepcopy(new.get("teams", [])),
                "games": deepcopy(new.get("games", [])),
            }
        )
    source = solved.get("source") or {}
    diagnostics = {
        "solver_status": source.get("status"),
        "objective_value": source.get("objective_value"),
        "best_objective_bound": source.get("best_objective_bound"),
        "half_diagnostics": source.get("half_diagnostics"),
        "lexicographic": source.get("lexicographic"),
    }
    if not patches:
        return result("unchanged", diagnostics=diagnostics)
    patched = apply_patches(candidate, patches)
    return result(
        "solved",
        patches=tuple(patches),
        candidate=patched,
        candidate_fingerprint=candidate_content_fingerprint(patched),
        diagnostics=diagnostics,
    )
