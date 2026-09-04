"""Old-vs-new Stage 3 planner A/B comparison (issue #257, scope items 6-7).

Runs the deterministic verifier and scorer from :mod:`planning_contract`
over two candidates produced from the *same* normalized planning problem —
the existing ``SeasonPlanner`` baseline and a Stage 3 v2 candidate (e.g. the
:mod:`stage3_optimizer` repair pass) — and reports whether the new candidate
is a strict-or-better improvement, per age group and overall.

Pure function over the stable ``candidate``/``planning_problem`` contracts:
no LLM calls, no dependency on either planner's internals, so it can compare
candidates from any two planner implementations that emit the same contract.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .planning_contract import score_candidate, verify_candidate

# (report path, direction) — "higher" means bigger is better, "lower" means
# smaller is better. Mirrors the promotion criteria in issue #257 scope item 7
# (participation balance, opponent diversity, repeated matchups, turnaround
# spacing, same-club clustering, hosting fairness).
_METRIC_PATHS: List[Tuple[str, str]] = [
    ("participation.spread", "lower"),
    ("opponent_diversity.unique_pairs", "higher"),
    ("opponent_diversity.pairwise_novelty", "higher"),
    ("opponent_diversity.pairs_meeting_3_plus", "lower"),
    ("opponent_diversity.max_pair_repeat", "lower"),
    ("opponent_diversity.inter_club_diversity", "higher"),
    ("opponent_diversity.same_club_pairing_count", "lower"),
    ("opponent_diversity.max_same_club_teams_per_tournament", "lower"),
    ("turnaround.min_turnaround_days", "higher"),
    ("turnaround.gaps_under_days.7", "lower"),
    ("turnaround.gaps_under_days.14", "lower"),
    ("hosting.spread", "lower"),
]


def _get_path(report: Dict[str, Any], path: str) -> Any:
    value: Any = report
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        # `turnaround.gaps_under_days` is keyed by int threshold (see
        # score_candidate), not str, so a dotted string path needs both
        # lookups tried.
        if part in value:
            value = value[part]
        elif part.isdigit() and int(part) in value:
            value = value[int(part)]
        else:
            return None
    return value


def _compare_scores(old_report: Dict[str, Any], new_report: Dict[str, Any]) -> Dict[str, Any]:
    """Diff two :func:`score_candidate` reports metric-by-metric.

    A metric "regresses" when the new value moves strictly in the wrong
    direction for its declared direction (e.g. a "lower is better" metric
    going up). Equal values never count as a regression — issue #257 asks
    for "preserves or improves", not strict improvement on every axis.
    """
    metrics: List[Dict[str, Any]] = []
    regressions: List[str] = []
    for path, direction in _METRIC_PATHS:
        old_value = _get_path(old_report, path)
        new_value = _get_path(new_report, path)
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


def _candidate_for_age_group(candidate: Dict[str, Any], age_group: str) -> Dict[str, Any]:
    filtered = dict(candidate)
    filtered["tournaments"] = [
        t for t in candidate.get("tournaments", []) if t.get("age_group") == age_group
    ]
    return filtered


def _age_groups(candidate: Dict[str, Any]) -> List[str]:
    groups = {
        t.get("age_group") for t in candidate.get("tournaments", []) if t.get("age_group")
    }
    return sorted(groups)


def build_ab_report(
    old_candidate: Dict[str, Any],
    new_candidate: Dict[str, Any],
    problem: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a full-season old-vs-new comparison report.

    Verifies both candidates against *problem* (hard requirements) and
    scores both overall and per age group, so a regression hidden inside a
    single age group's aggregate isn't masked by improvements in another.
    """
    old_verification = verify_candidate(old_candidate, problem)
    new_verification = verify_candidate(new_candidate, problem)

    old_overall = score_candidate(old_candidate)
    new_overall = score_candidate(new_candidate)
    overall_comparison = _compare_scores(old_overall, new_overall)

    age_groups = sorted(set(_age_groups(old_candidate)) | set(_age_groups(new_candidate)))
    by_age_group: Dict[str, Any] = {}
    for age_group in age_groups:
        old_ag = score_candidate(_candidate_for_age_group(old_candidate, age_group))
        new_ag = score_candidate(_candidate_for_age_group(new_candidate, age_group))
        by_age_group[age_group] = {
            "old": old_ag,
            "new": new_ag,
            "comparison": _compare_scores(old_ag, new_ag),
        }

    # A hard-constraint regression is specifically: old passed, new fails.
    hard_constraint_regressed = old_verification["ok"] and not new_verification["ok"]

    # A whole-season aggregate can improve while a single age group
    # regresses (the aggregate averages over groups). Promotion must look at
    # *every* age group's regressions, not just the overall comparison —
    # otherwise a regression hidden inside one group is masked by
    # improvements in another (issue #257 Task 1).
    per_age_group_regressions = {
        age_group: entry["comparison"]["regressions"]
        for age_group, entry in by_age_group.items()
        if entry["comparison"]["regressions"]
    }

    # dominates_baseline: no new hard violations, and no protected quality
    # regression anywhere — overall or in any single age group.
    dominates_baseline = (
        not hard_constraint_regressed
        and not overall_comparison["regressions"]
        and not per_age_group_regressions
    )

    # production_ready: the candidate is dominant AND passes the verifier
    # outright (zero hard violations of its own, not merely "no worse than
    # baseline" — the baseline itself may carry pre-existing violations).
    production_ready = dominates_baseline and new_verification["ok"]

    return {
        "old": {"verification": old_verification, "score": old_overall},
        "new": {"verification": new_verification, "score": new_overall},
        "overall_comparison": overall_comparison,
        "by_age_group": by_age_group,
        "per_age_group_regressions": per_age_group_regressions,
        "hard_constraint_regressed": hard_constraint_regressed,
        "dominates_baseline": dominates_baseline,
        "production_ready": production_ready,
        # Deprecated alias for production_ready, kept for existing callers.
        "promotable": production_ready,
    }
