"""Independently verified participation deltas for a CP-SAT solve.

The CP-SAT model encodes participation targets as goals, but its own objective
value is not evidence about the plan the repository will actually judge. This
module re-measures the baseline and the solver output with the canonical
``score_candidate`` / ``verify_candidate`` contract, so a solve's diagnostics
state the participation change an independent evaluator sees, and never claim
more than that evaluator confirms. It encodes no rule of its own.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from tournament_scheduler.planning.contract import score_candidate, verify_candidate

# Participation metrics from ``score_candidate`` (lower is better for each).
PARITY_METRICS = (
    "season_total_absolute_deviation",
    "half_total_absolute_deviation",
    "max_team_season_deviation",
    "max_team_half_deviation",
    "deviation_count",
)

EVALUATOR = "planning.contract.score_candidate+verify_candidate"


def _participation(candidate: Mapping[str, Any], problem: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    scored = score_candidate(dict(candidate), problem=dict(problem) if problem else None)
    block = scored.get("participation") or {}
    return {metric: block.get(metric) for metric in PARITY_METRICS}


def verified_participation_deltas(
    baseline: Mapping[str, Any],
    optimized: Mapping[str, Any],
    problem: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Return baseline/optimized participation metrics and their signed delta.

    ``delta`` is optimized minus baseline, so a negative value is an
    improvement. ``verified`` is the independent hard verdict on the optimized
    candidate; it does not describe solver status or optimality.
    """
    before = _participation(baseline, problem)
    after = _participation(optimized, problem)
    verification = verify_candidate(dict(optimized), dict(problem) if problem else None)
    return {
        "evaluator": EVALUATOR,
        "verified": bool(verification.get("ok")),
        "baseline": before,
        "optimized": after,
        "delta": {
            metric: (after[metric] - before[metric])
            if isinstance(after[metric], (int, float)) and isinstance(before[metric], (int, float))
            else None
            for metric in PARITY_METRICS
        },
    }
