"""Participant-only neighborhood repair through the bounded CP-SAT maintenance path.

For localized hard findings that re-staffing rosters can fix, the neighborhood
search asks the generic maintenance solver first (ADR 0008) instead of running
the stochastic local search. Placement (host, date, arena, start time) is never
moved here; a finding that needs placement keeps the local-search widening.

The solver's output is only a candidate: it is re-verified by the independent
verifier, must be hard-valid and strictly reduce the hard-violation count, and
is rejected if it transfers hosting responsibility, exactly like a local-search
result.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence, Tuple

from tournament_scheduler.hosting.responsibility import (
    responsibility_regression_reason,
    unexplained_responsibility_transfers,
)
from tournament_scheduler.planning.verification import verify_canonical_candidate
from tournament_scheduler.repair.host_team_missing import RepairOption, _codes, candidate_fingerprint
from tournament_scheduler.stage3.cpsat_maintenance import solve_maintenance_problem
from tournament_scheduler.stage3.maintenance_problem import (
    DEFAULT_SEARCH_BUDGET_SECONDS,
    DIMENSION_PARTICIPANTS,
    build_participant_maintenance_problem,
)

CPSAT_ENGINE = "cp_sat"

# Hard findings a participant re-staffing can repair. Everything else
# (arena conflicts, holiday dates, an excluded host club) needs placement.
PARTICIPANT_FIXABLE_CODES = frozenset(
    {"host_team_missing", "club_hard_max_exceeded", "duplicate_participation_same_date"}
)


def participant_fixable(codes: Iterable[str]) -> bool:
    resolved = {str(code) for code in codes}
    return bool(resolved) and resolved <= PARTICIPANT_FIXABLE_CODES


def cpsat_neighborhood_options(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    verification: Mapping[str, Any],
    *,
    age_groups: Sequence[str],
    neighborhood_ids: Sequence[str],
    fingerprint: str,
    tag: str,
    effects_for: Callable[[Mapping[str, Any], Mapping[str, Any]], Dict[str, Any]],
    search_budget_seconds: float = DEFAULT_SEARCH_BUDGET_SECONDS,
    seed: int = 0,
) -> Tuple[List[RepairOption], List[Dict[str, Any]], Dict[str, Any]]:
    """One verified participant-only option for the neighborhood, or why none."""
    if not age_groups:
        return [], [{"reason": "maintenance_search_no_age_group", "engine": CPSAT_ENGINE}], {}
    maintenance = build_participant_maintenance_problem(
        candidate,
        problem,
        age_groups=age_groups,
        tournament_ids=neighborhood_ids,
        search_budget_seconds=search_budget_seconds,
        seed=seed,
        repair_baseline_conflicts=True,
    )
    solved = solve_maintenance_problem(maintenance, dict(candidate), dict(problem))
    base = {"engine": CPSAT_ENGINE, "seed": int(seed)}
    if not solved.has_candidate or solved.candidate is None:
        refusal = {
            "reason": f"maintenance_search_{solved.status}",
            "solver_status": solved.diagnostics.get("solver_status"),
        }
        return [], [{**base, **refusal}], {}
    result = solved.candidate
    after = verify_canonical_candidate(dict(result), dict(problem))
    before_total = sum(Counter(_codes(verification)).values())
    after_codes = Counter(_codes(after))
    if not after.get("ok"):
        return (
            [],
            [{**base, "reason": "search_candidate_not_hard_valid", "remaining_violation_codes": sorted(after_codes)}],
            {},
        )
    transfer = responsibility_regression_reason(candidate, result, problem)
    if transfer:
        return (
            [],
            [
                {
                    **base,
                    "reason": transfer,
                    "responsibility_transfers": unexplained_responsibility_transfers(candidate, result, problem),
                }
            ],
            {},
        )
    if sum(after_codes.values()) >= before_total:
        return [], [{**base, "reason": "no_hard_improvement", "hard_violations_before": before_total}], {}
    result_fingerprint = candidate_fingerprint(result)
    # Result and request fingerprints make replay fail closed when a bounded
    # apply-time solve returns a different incumbent than the previewed solve.
    option_id = (
        f"{fingerprint[:12]}:search_neighborhood:{int(seed)}:{tag}:{solved.problem_fingerprint}:{result_fingerprint}"
    )
    option = RepairOption(
        option_id=option_id,
        finding_id="search_neighborhood",
        action="search_neighborhood",
        tournament_id="",
        arguments={
            "engine": CPSAT_ENGINE,
            "seed": int(seed),
            "dimensions": [DIMENSION_PARTICIPANTS],
            "neighborhood_tournament_ids": list(neighborhood_ids),
            "patched_tournament_ids": [str(p["tournament_id"]) for p in solved.patches],
            "maintenance_problem_fingerprint": solved.problem_fingerprint,
        },
        hard_feasible=True,
        effects=effects_for(result, after),
        evidence={
            "verification_ok": True,
            "result_fingerprint": result_fingerprint,
            "engine": CPSAT_ENGINE,
            "seed": int(seed),
            "solver_status": solved.diagnostics.get("solver_status"),
        },
    )
    return [option], [], {option_id: result}
