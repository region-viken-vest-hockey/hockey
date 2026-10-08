"""Option measurement, consequences, and Pareto selection for maintenance.

This module owns the measurement of repair options on multiple dimensions,
consequence analysis, and Pareto-optimal selection for maintenance decisions.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .pareto import non_dominated_indices, representative_indices
from .quality_objectives import (
    QUALITY_OBJECTIVE_DIMENSIONS,
    compare_quality_scores,
    quality_objective_vector,
    with_unresolved_obligations_count,
)
from .request_constraints import (
    active_request_constraints,
    compare_constraint_violations,
    request_constraint_report,
)
from .season_state import canonical_state_revision
from .planning_contract import verify_candidate
from .participation_targets import INTRA_CLUB_DISTRIBUTION


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


def _objective_vector(
    *,
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    verification: Mapping[str, Any],
    dimensions: Iterable[str],
    decisions: Mapping[str, Any],
    active_constraints: Optional[Mapping[str, Any]] = None,
) -> List[float]:
    """Compute the objective vector for a plan in maintenance context.

    The vector is oriented "lower is better" for all dimensions, combining:
    - Hard violations and obligations from verification
    - Change cost (tournaments with changed placement/roster)
    - Travel metrics
    - Quality objectives (opponent diversity, turnaround, etc.)
    - Request constraint violations

    Args:
        plan: The candidate plan being evaluated
        problem: The planning problem with canonical overlays
        verification: The result from verify_candidate(plan, problem)
        dimensions: The dimensions to include in the vector
        decisions: Canonical decisions (locks + approval state)
        active_constraints: Active request constraints (optional)

    Returns:
        Objective vector as a list of floats (lower is better)
    """
    if active_constraints is None:
        active_constraints = active_request_constraints(decisions)
    
    # Start with an empty vector
    vector: List[float] = []
    
    # Add hard/defect dimensions
    for dim in MAINTENANCE_DEFECT_DIMENSIONS:
        if dim == "hard_violations":
            vector.append(len(verification.get("violations") or []))
        elif dim == "unresolved_hosting_obligations":
            # This would come from hosting obligations verification
            vector.append(0)  # Placeholder
        elif dim == "hosting_balance_imbalances":
            # This would come from hosting balance verification
            vector.append(0)  # Placeholder
        elif dim == "manual_placements":
            vector.append(len(verification.get("manual_placements") or []))
        elif dim == "unresolved_placement_obligations":
            # This would come from placement obligations verification
            vector.append(0)  # Placeholder
        elif dim == "participation_deviations":
            # Count unresolved participation deviations
            unresolved_count = 0
            for dev in verification.get("participation_deviations") or []:
                if int(dev.get("deviation") or 0) != 0:
                    unresolved_count += 1
            vector.append(unresolved_count)
        elif dim == "avoidable_participation_deviations":
            # Count avoidable participation deviations
            avoidable_count = 0
            for dev in verification.get("participation_deviations") or []:
                actual = int(dev.get("actual") or 0)
                target = int(dev.get("target") or 0)
                if actual != target:
                    avoidable_count += 1
            vector.append(avoidable_count)
        elif dim == "host_confirmation_dependencies":
            # This would come from host confirmation dependencies
            vector.append(0)  # Placeholder
        elif dim == "changed_tournament_count":
            # This would be computed in _metric_delta
            vector.append(0)  # Placeholder - will be overridden
        elif dim == "host_sibling_preference_violations":
            # This would come from request constraints
            vector.append(0)  # Placeholder
    
    # Add travel metrics
    if "travel" in dimensions:
        travel_metrics = _travel_metrics(plan)
        vector.append(travel_metrics.get("total_travel", 0.0))
    
    # Add quality objectives
    quality_scores = quality_objective_vector(
        verify_candidate(dict(plan), problem=dict(problem))
    )
    for dim in QUALITY_OBJECTIVE_DIMENSIONS:
        if dim in dimensions:
            vector.append(quality_scores.get(dim, 0.0))
    
    # Add request constraint violations
    if "request_constraint_violations" in dimensions:
        constraint_violations = compare_constraint_violations(
            plan, plan, active_constraints  # Simplified - should compare before/after
        )
        vector.append(len(constraint_violations.get("violations", [])))
    
    return vector


def _travel_metrics(plan: Mapping[str, Any]) -> Dict[str, Any]:
    """Compute travel-related metrics for a plan.

    Args:
        plan: The candidate plan being evaluated

    Returns:
        Dictionary of travel metrics
    """
    # This is a simplified version - the actual implementation would
    # compute travel distances based on team locations and tournament venues
    return {
        "total_travel": 0.0,
        "average_travel": 0.0,
        "max_travel": 0.0,
    }


def _metric_delta(
    after_plan: Mapping[str, Any],
    before_plan: Mapping[str, Any],
    *,
    decisions: Mapping[str, Any],
) -> Dict[str, Any]:
    """Compute the metric delta between two plans.

    Args:
        after_plan: The plan after applying a repair option
        before_plan: The plan before applying a repair option
        decisions: Canonical decisions (locks + approval state)

    Returns:
        Dictionary containing the metric delta
    """
    # Compute changed tournaments
    changed_tournament_ids = _changed_tournament_ids(before_plan, after_plan)
    
    # Compute basic metrics
    delta: Dict[str, Any] = {
        "changed_tournament_ids": changed_tournament_ids,
        "changed_tournament_count": len(changed_tournament_ids),
    }
    
    # TODO: Add more detailed metric computations
    
    return delta


def _quality_delta(
    after_plan: Mapping[str, Any],
    before_plan: Mapping[str, Any],
    *,
    decisions: Mapping[str, Any],
) -> Dict[str, Any]:
    """Compute the quality delta between two plans.

    Args:
        after_plan: The plan after applying a repair option
        before_plan: The plan before applying a repair option
        decisions: Canonical decisions (locks + approval state)

    Returns:
        Dictionary containing the quality delta
    """
    # This would compute the difference in quality objectives between plans
    # For now, returning a placeholder
    return {
        "quality_improvement": 0.0,
        "quality_details": {},
    }


def _rejected_delta(
    season: str,
    revision: str,
    reason: str,
    *,
    option_id: Optional[str] = None,
    verification: Optional[Mapping[str, Any]] = None,
    request_constraint_violations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Create a rejection delta for a repair option.

    Args:
        season: The season identifier
        revision: The canonical revision
        reason: The reason for rejection
        option_id: The ID of the rejected option (optional)
        verification: The verification result (optional)
        request_constraint_violations: Request constraint violations (optional)

    Returns:
        Rejection delta dictionary
    """
    delta: Dict[str, Any] = {
        "schema_version": 1,  # SEASON_MAINTENANCE_SCHEMA_VERSION would be imported
        "season": season,
        "revision": revision,
        "option_id": option_id,
        "ok": False,
        "reason": reason,
    }
    
    if verification is not None:
        delta["verification"] = verification
        
    if request_constraint_violations is not None:
        delta["request_constraint���_violations"] = request_constraint_violations
        
    return delta


def _annotate_pareto(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    options: List[Dict[str, Any]],
    finding: Mapping[str, Any],
    dimensions: Iterable[str],
    *,
    active_constraints: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Annotate options with Pareto frontier information.

    Args:
        plan: The candidate plan being evaluated
        problem: The planning problem with canonical overlays
        options: List of repair options to annotate
        finding: The finding these options address
        dimensions: The dimensions used for evaluation
        active_constraints: Active request constraints (optional)

    Returns:
        The options list with Pareto annotation added to each option
    """
    if not options:
        return options
        
    # Compute objective vectors for all options
    vectors: List[Dict[str, float]] = []
    for option in options:
        # This is a simplified version - in reality, we'd need to compute
        # the objective vector for each option's candidate plan
        vector: Dict[str, float] = {dim: 0.0 for dim in MAINTENANCE_DEFECT_DIMENSIONS}
        vectors.append(vector)
    
    # Find the Pareto frontier
    if vectors:
        front_indices = non_dominated_indices(vectors)
        representative_indices_list = representative_indices(
            [vectors[i] for i in front_indices], 
            len(front_indices)  # Use all as representatives for simplicity
        )
        
        # Annotate options
        for i, option in enumerate(options):
            option["pareto"] = {
                "is_on_front": i in front_indices,
                "is_representative": i in representative_indices_list,
                "rank": front_indices.index(i) if i in front_indices else -1,
            }
    
    return options