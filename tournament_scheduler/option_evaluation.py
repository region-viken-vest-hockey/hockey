"""Option measurement, consequences, and Pareto selection for maintenance.

This module owns the measurement of repair options on multiple dimensions,
consequence analysis, and Pareto-optimal selection for maintenance decisions.
"""

from __future__ import annotations

from .findings_construction import _avoidability_count
from .findings_construction import _count
from .findings_construction import _manual_count
from .findings_construction import _unresolved_avoidability_count
from .findings_construction import _unresolved_participation_deviation_count
from .impact_analysis import _changed_tournament_ids
from .planning_contract import score_candidate
from .quality_objectives import QUALITY_OBJECTIVE_DIMENSIONS, compare_quality_scores
from .quality_objectives import quality_objective_vector
from .quality_objectives import with_unresolved_obligations_count
from typing import Any
from typing import Dict
from typing import Iterable
from typing import Mapping
from typing import Tuple




# The objective vector every maintenance option is measured on, oriented
# "lower is better" so one uniform dominance check applies. Defect and
# host-confirmation counts come from the same independent verifier that produced
# the findings; change cost is the number of tournaments whose placement/roster
# signature changed. An option is only on the front when no other option is at
# least as good everywhere and strictly better somewhere -- a verified option is
# not automatically a non-dominated one.
#
# The vector deliberately combines two families:
#   * hard/defect and change-cost dimensions owned by this module (a maintenance
#     repair must never buy a lower travel cost with a new hard violation);
#   * the shared Stage-3 soft-quality objectives (opponent diversity, turnaround,
#     same-club clustering, temporal coverage) plus travel, so a local repair is
#     compared on the same planner-independent quality facts Stage 3 uses
#     instead of only on the defect it was asked to fix.
MAINTENANCE_DEFECT_DIMENSIONS: Tuple[str, ...] = (
    "hard_violations",
    "unresolved_hosting_obligations",
    "hosting_balance_imbalances",
    "manual_placements",
    "unresolved_placement_obligations",
    "participation_deviations",
    "avoidable_participation_deviations",
    "host_confirmation_dependencies",
    "changed_tournament_count",
    "host_sibling_preference_violations",
)


# Travel is a first-class operational consequence of moving a tournament to a
# different host/date, so it is measured both as a season total and as the worst
# single team. Computed by the canonical ``compute_team_travel_distances``.
TRAVEL_OBJECTIVE_DIMENSIONS: Tuple[str, ...] = (
    "total_travel_km",
    "max_team_travel_km",
)


def _objective_vector(
    candidate: Mapping[str, Any],
    verification: Mapping[str, Any],
    before_plan: Mapping[str, Any],
    *,
    score: Mapping[str, Any],
    travel: Mapping[str, Any],
    active_constraints: Iterable[Mapping[str, Any]] = (),
) -> Dict[str, float]:
    """Extract the uniformly "lower is better" maintenance objective vector.

    Combines the verifier-derived defect/change-cost dimensions owned here, the
    shared Stage-3 soft-quality vector (``quality_objectives``) and travel, so a
    single uniform dominance check and one bounded Pareto front cover both the
    hard and the soft consequences of a repair.
    """
    from .request_constraints import count_host_sibling_preference_violations

    # Compute host sibling preference violations for the candidate plan.
    # We need a minimal decisions-like object with the active constraints.
    decisions_for_preference = {
        "request_constraints": list(active_constraints),
    }
    host_sibling_violations = count_host_sibling_preference_violations(
        candidate, decisions_for_preference
    )

    vector: Dict[str, float] = {
        "hard_violations": float(_count(verification, "violations")),
        "unresolved_hosting_obligations": float(
            _count(verification, "unresolved_hosting_obligations")
        ),
        "hosting_balance_imbalances": float(
            _count(verification, "hosting_balance_imbalances")
        ),
        "manual_placements": float(_manual_count(verification)),
        "unresolved_placement_obligations": float(
            len(candidate.get("unresolved_tournament_placements") or [])
        ),
        "participation_deviations": float(_unresolved_participation_deviation_count(verification)),
        "avoidable_participation_deviations": float(
            _unresolved_avoidability_count(verification, "avoidable")
        ),
        "host_confirmation_dependencies": float(
            _count(verification, "movable_allocations_used")
        ),
        "changed_tournament_count": float(
            len(_changed_tournament_ids(before_plan, candidate))
        ),
        "host_sibling_preference_violations": float(host_sibling_violations),
    }
    vector.update(quality_objective_vector(dict(score)))
    for dimension in TRAVEL_OBJECTIVE_DIMENSIONS:
        vector[dimension] = float(travel.get(dimension, 0.0))
    return vector


def _travel_metrics(plan: Mapping[str, Any]) -> Dict[str, Any]:
    """Return canonical season travel totals for a plan dict.

    Delegates to the one travel implementation (``compute_team_travel_distances``)
    rather than re-summing arena distances here. A plan that cannot be decoded
    (for example a synthetic candidate missing model fields) reports zero travel
    with ``available: false`` instead of failing the repair surface.
    """
    try:
        from .club_distances import compute_team_travel_distances
        from .serialization.season_plan import season_plan_from_dict

        season_plan = season_plan_from_dict(dict(plan))
        team_travel = compute_team_travel_distances(season_plan)
    except Exception:
        return {
            "total_travel_km": 0.0,
            "max_team_travel_km": 0.0,
            "available": False,
        }
    values = list(team_travel.values())
    return {
        "total_travel_km": float(sum(values)),
        "max_team_travel_km": float(max(values) if values else 0),
        "available": True,
    }


def _metric_delta(
    before_plan: Mapping[str, Any],
    before_verification: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any] | None = None,
    after_verification: Mapping[str, Any] | None = None,
    problem: Mapping[str, Any] | None = None,
    decisions: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    after_plan = candidate if candidate is not None else before_plan
    after = after_verification or before_verification
    
    # Compute host sibling preference violations for before and after plans
    host_sibling_before = 0
    host_sibling_after = 0
    if decisions is not None:
        from .request_constraints import count_host_sibling_preference_violations
        host_sibling_before = count_host_sibling_preference_violations(before_plan, decisions)
        host_sibling_after = count_host_sibling_preference_violations(after_plan, decisions)
    
    delta = {
        "hard_violations": _count(after, "violations") - _count(before_verification, "violations"),
        "hard_violations_before": _count(before_verification, "violations"),
        "hard_violations_after": _count(after, "violations"),
        "unresolved_hosting_obligations_before": _count(before_verification, "unresolved_hosting_obligations"),
        "unresolved_hosting_obligations_after": _count(after, "unresolved_hosting_obligations"),
        "hosting_balance_imbalances_before": _count(before_verification, "hosting_balance_imbalances"),
        "hosting_balance_imbalances_after": _count(after, "hosting_balance_imbalances"),
        "manual_placements_before": _manual_count(before_verification),
        "manual_placements_after": _manual_count(after),
        "unresolved_placement_obligations_before": len(
            before_plan.get("unresolved_tournament_placements") or []
        ),
        "unresolved_placement_obligations_after": len(
            after_plan.get("unresolved_tournament_placements") or []
        ),
        "participation_deviations_before": _count(before_verification, "participation_deviations"),
        "participation_deviations_after": _count(after, "participation_deviations"),
        "bounded_search_exhausted_before": _avoidability_count(before_verification, "bounded_search_exhausted"),
        "bounded_search_exhausted_after": _avoidability_count(after, "bounded_search_exhausted"),
        "avoidable_before": _avoidability_count(before_verification, "avoidable"),
        "avoidable_after": _avoidability_count(after, "avoidable"),
        "changed_tournament_count": len(_changed_tournament_ids(before_plan, after_plan)),
        "host_sibling_preference_violations_before": host_sibling_before,
        "host_sibling_preference_violations_after": host_sibling_after,
        "host_sibling_preference_violations_delta": host_sibling_after - host_sibling_before,
    }
    delta.update(
        _quality_delta(
            before_plan, after_plan, problem=dict(problem) if problem is not None else None
        )
    )
    return delta


def _quality_delta(
    before_plan: Mapping[str, Any],
    after_plan: Mapping[str, Any],
    *,
    problem: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    """Shared soft-quality + travel delta for one before/after plan pair.

    Uses the same Stage-3 quality comparison (``compare_quality_scores``) the
    promotion gate uses, so a maintenance action returns the exact
    improvement/regression evidence an operator would see from Stage 3 -- not a
    second, maintenance-only quality rule.
    """
    before_score = with_unresolved_obligations_count(
        score_candidate(dict(before_plan), problem=problem)
    )
    after_score = with_unresolved_obligations_count(
        score_candidate(dict(after_plan), problem=problem)
    )
    comparison = compare_quality_scores(before_score, after_score)
    before_travel = _travel_metrics(before_plan)
    after_travel = _travel_metrics(after_plan)
    return {
        "quality_metrics": comparison["metrics"],
        "quality_regressions": comparison["regressions"],
        "total_travel_km_before": before_travel["total_travel_km"],
        "total_travel_km_after": after_travel["total_travel_km"],
        "total_travel_km_delta": after_travel["total_travel_km"] - before_travel["total_travel_km"],
        "max_team_travel_km_before": before_travel["max_team_travel_km"],
        "max_team_travel_km_after": after_travel["max_team_travel_km"],
        "max_team_travel_km_delta": after_travel["max_team_travel_km"]
        - before_travel["max_team_travel_km"],
    }


def _rejected_delta(
    season: str,
    revision: str,
    reason: str,
    *,
    delta: Mapping[str, Any] | None = None,
    **extra: Any,
) -> Dict[str, Any]:
    return {
        "season": season,
        "ok": False,
        "reason": reason,
        "revision_before": revision,
        "revision_after": revision,
        "canonical_revision_unchanged": True,
        "delta": dict(delta or {}),
        **extra,
    }


PARETO_DIMENSIONS: Tuple[str, ...] = (
    MAINTENANCE_DEFECT_DIMENSIONS + QUALITY_OBJECTIVE_DIMENSIONS + TRAVEL_OBJECTIVE_DIMENSIONS
)
