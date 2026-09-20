"""Canonical planner-independent quality objectives for scheduling candidates.

Two very different surfaces compare candidates on the *same* soft quality
dimensions: Stage 3's multi-objective search / A/B promotion gate, and the
promoted-season maintenance loop. Those dimensions all come from
:func:`tournament_scheduler.planning_contract.score_candidate` and must not be
re-derived, with subtly different names or orientations, per surface.

This module owns:

* the declared quality metric list with each metric's direction
  (:data:`QUALITY_METRIC_PATHS`) and the comparison helper
  (:func:`compare_quality_scores`);
* the uniformly "lower is better" objective vector
  (:data:`QUALITY_OBJECTIVE_DIMENSIONS`, :func:`quality_objective_vector`) used
  for Pareto/dominance checks.

It never verifies a candidate and never decides which candidate wins; it only
extracts and compares facts from one ``score_candidate`` report.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# (report path, direction): "higher" means a bigger value is better, "lower"
# means a smaller value is better. Mirrors the promotion criteria for the soft
# quality goals (participation balance, opponent diversity, repeated matchups,
# turnaround spacing, same-club clustering, hosting fairness, temporal
# coverage).
QUALITY_METRIC_PATHS: List[Tuple[str, str]] = [
    ("participation.spread", "lower"),
    # Participation target compliance is a guardrail dimension, not one more
    # equal-weighted term: a candidate with strictly worse bounded target
    # deviation must not be promoted merely because it improves a
    # lower-priority quality metric. The *club-pool* unresolved metrics are
    # used, not the raw per-team totals, so a pure intra-club label imbalance
    # (an aggregate-complete pool split 5+3) is not treated as an equal-weight
    # missing participation opportunity.
    ("participation.club_pool_unresolved_season_total_absolute_deviation", "lower"),
    ("participation.club_pool_unresolved_max_team_season_deviation", "lower"),
    ("participation.club_pool_unresolved_half_total_absolute_deviation", "lower"),
    ("participation.club_pool_unresolved_max_team_half_deviation", "lower"),
    ("participation.club_pool_unresolved_avoidable_deviation_count", "lower"),
    ("participation.club_pool_unresolved_shortfall_count", "lower"),
    ("opponent_diversity.unique_pairs", "higher"),
    ("opponent_diversity.pairwise_novelty", "higher"),
    ("opponent_diversity.pairs_meeting_3_plus", "lower"),
    ("opponent_diversity.max_pair_repeat", "lower"),
    ("opponent_diversity.inter_club_diversity", "higher"),
    ("opponent_diversity.same_club_pairing_count", "lower"),
    ("opponent_diversity.max_same_club_teams_per_tournament", "lower"),
    # An aggregate/count metric so one candidate can't silently trade fewer
    # same-club *pairings* for more 3rd-or-later-team clusters.
    ("opponent_diversity.club_count_excess_over_2", "lower"),
    ("opponent_diversity.tournaments_with_3plus_same_club", "lower"),
    ("turnaround.min_turnaround_days", "higher"),
    ("turnaround.gaps_under_days.7", "lower"),
    ("turnaround.gaps_under_days.14", "lower"),
    ("hosting.spread", "lower"),
    ("hosting.unresolved_obligations_count", "lower"),
    # Intra-club sibling home representation: which of a multi-team club's
    # teams actually shows up when the club hosts. Subordinate to hard
    # validity and the participation guardrails above (a spread of 0-1 is
    # balanced, so the "material" variants are used, not the raw spread).
    ("home_representation.max_material_spread", "lower"),
    ("home_representation.material_skew_pool_count", "lower"),
    ("temporal.max_gap_days", "lower"),
    ("temporal.offenders_count", "lower"),
]

# Objective vector dimensions, all oriented "lower is better" so a single
# uniform dominance check works across every dimension (``score_candidate``'s
# inter_club_diversity is a "higher is better" fraction, so it is stored
# inverted -- see :func:`quality_objective_vector`). Participation target
# compliance is a guardrail dimension placed first so a lower-priority quality
# gain cannot justify a worse bounded deviation.
QUALITY_OBJECTIVE_DIMENSIONS: Tuple[str, ...] = (
    "participation_season_deviation",
    "participation_avoidable",
    "participation_club_pool_unresolved_shortfalls",
    "max_pair_repeat",
    "same_club_pairing_count",
    "gaps_under_7",
    "gaps_under_14",
    "hosting_spread",
    "home_representation_max_material_spread",
    "home_representation_material_skew_pool_count",
    "inter_club_diversity_inverted",
    "temporal_max_gap_days",
)


def get_metric_path(report: Dict[str, Any], path: str) -> Any:
    """Read a dotted metric *path* from a ``score_candidate`` *report*.

    ``turnaround.gaps_under_days`` is keyed by ``int`` threshold, not ``str``,
    so a dotted string path needs both lookups tried. Returns ``None`` when any
    segment is missing or the walk leaves the dict structure.
    """
    value: Any = report
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        if part in value:
            value = value[part]
        elif part.isdigit() and int(part) in value:
            value = value[int(part)]
        else:
            return None
    return value


def with_unresolved_obligations_count(report: Dict[str, Any]) -> Dict[str, Any]:
    """Fold ``hosting.unresolved_obligations`` (a list) into a comparable count.

    ``score_candidate(..., problem=problem)`` reports the unresolved
    club x age-group hosting rows as a list; :func:`compare_quality_scores`
    only diffs numeric leaves, so surface the count alongside it rather than
    teaching the generic path-walker about list-valued metrics.
    """
    hosting = report.get("hosting")
    unresolved = hosting.get("unresolved_obligations") if isinstance(hosting, dict) else None
    if not isinstance(unresolved, list):
        return report
    report = dict(report)
    report["hosting"] = {**hosting, "unresolved_obligations_count": len(unresolved)}
    return report


def compare_quality_scores(
    old_report: Dict[str, Any], new_report: Dict[str, Any]
) -> Dict[str, Any]:
    """Diff two :func:`score_candidate` reports metric-by-metric.

    A metric "regresses" when the new value moves strictly in the wrong
    direction for its declared direction (e.g. a "lower is better" metric going
    up). Equal values never count as a regression -- the contract asks for
    "preserves or improves", not strict improvement on every axis.
    """
    metrics: List[Dict[str, Any]] = []
    regressions: List[str] = []
    for path, direction in QUALITY_METRIC_PATHS:
        old_value = get_metric_path(old_report, path)
        new_value = get_metric_path(new_report, path)
        if old_value is None or new_value is None:
            continue
        delta = new_value - old_value
        regressed = (direction == "lower" and delta > 0) or (direction == "higher" and delta < 0)
        metrics.append(
            {
                "metric": path,
                "direction": direction,
                "old": old_value,
                "new": new_value,
                "delta": delta,
                "regressed": regressed,
            }
        )
        if regressed:
            regressions.append(path)
    return {"metrics": metrics, "regressions": regressions}


def quality_objective_vector(score: Dict[str, Any]) -> Dict[str, float]:
    """Extract a uniformly "lower is better" vector from a ``score_candidate`` result."""
    gaps = (score.get("turnaround") or {}).get("gaps_under_days") or {}
    opponent = score.get("opponent_diversity") or {}
    hosting = score.get("hosting") or {}
    home_representation = score.get("home_representation") or {}
    temporal = score.get("temporal") or {}
    participation = score.get("participation") or {}
    # Prefer the club-pool unresolved totals (present whenever the score came
    # from ``evaluate_participation``) so an intra-club label imbalance is not
    # an equal-weight objective; fall back to the raw per-team totals for
    # hand-built/partial reports that predate the club-pool view.
    season_deviation = participation.get(
        "club_pool_unresolved_season_total_absolute_deviation",
        participation.get("season_total_absolute_deviation", 0),
    )
    avoidable = participation.get(
        "club_pool_unresolved_avoidable_deviation_count",
        participation.get("avoidable_deviation_count", 0),
    )
    return {
        "participation_season_deviation": float(season_deviation),
        "participation_avoidable": float(avoidable),
        "participation_club_pool_unresolved_shortfalls": float(
            participation.get("club_pool_unresolved_shortfall_count", 0)
        ),
        "max_pair_repeat": float(opponent.get("max_pair_repeat", 0)),
        "same_club_pairing_count": float(opponent.get("same_club_pairing_count", 0)),
        "gaps_under_7": float(gaps.get(7, 0)),
        "gaps_under_14": float(gaps.get(14, 0)),
        "hosting_spread": float(hosting.get("spread", 0)),
        "home_representation_max_material_spread": float(
            home_representation.get("max_material_spread", 0)
        ),
        "home_representation_material_skew_pool_count": float(
            home_representation.get("material_skew_pool_count", 0)
        ),
        "inter_club_diversity_inverted": 1.0 - float(opponent.get("inter_club_diversity", 0.0)),
        "temporal_max_gap_days": float(temporal.get("max_gap_days", 0)),
    }


__all__ = [
    "QUALITY_METRIC_PATHS",
    "QUALITY_OBJECTIVE_DIMENSIONS",
    "compare_quality_scores",
    "get_metric_path",
    "quality_objective_vector",
    "with_unresolved_obligations_count",
]
