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

from typing import Any, Dict, List, Optional

from .canonical_baseline import change_cost
from .planning_contract import score_candidate, verify_candidate
from .quality_objectives import (
    compare_quality_scores as _compare_scores,
    with_unresolved_obligations_count as _with_unresolved_count,
)


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


def _problem_for_age_group(
    problem: Optional[Dict[str, Any]], age_group: str
) -> Optional[Dict[str, Any]]:
    """Filter *problem*'s registered teams down to a single age group.

    Without this, the per-age-group hosting coverage matrix would carry
    every other age group's registered clubs too, and (since the candidate
    passed alongside it is already filtered to one age group) report them
    as unresolved hosting obligations they never had a chance to satisfy.
    """
    if problem is None:
        return None
    filtered = dict(problem)
    filtered["teams"] = [
        team for team in problem.get("teams", []) or [] if team.get("age_group") == age_group
    ]
    return filtered


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

    old_overall = score_candidate(old_candidate, problem=problem)
    new_overall = score_candidate(new_candidate, problem=problem)
    overall_comparison = _compare_scores(
        _with_unresolved_count(old_overall), _with_unresolved_count(new_overall)
    )

    age_groups = sorted(set(_age_groups(old_candidate)) | set(_age_groups(new_candidate)))
    by_age_group: Dict[str, Any] = {}
    for age_group in age_groups:
        age_group_problem = _problem_for_age_group(problem, age_group)
        old_ag = score_candidate(
            _candidate_for_age_group(old_candidate, age_group), problem=age_group_problem
        )
        new_ag = score_candidate(
            _candidate_for_age_group(new_candidate, age_group), problem=age_group_problem
        )
        by_age_group[age_group] = {
            "old": old_ag,
            "new": new_ag,
            "comparison": _compare_scores(
                _with_unresolved_count(old_ag), _with_unresolved_count(new_ag)
            ),
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

    # Baseline-aware replanning (issue #355): when the problem carries a
    # promoted canonical baseline, report how far the new candidate moves
    # away from it.  This is an informational soft-cost metric — the hard
    # approval locks are already enforced by verify_candidate — so search and
    # the operator can prefer the smallest change that resolves the problem.
    change_cost_report = None
    if problem and problem.get("canonical_baseline"):
        change_cost_report = change_cost(problem["canonical_baseline"], new_candidate)

    return {
        "old": {"verification": old_verification, "score": old_overall},
        "new": {"verification": new_verification, "score": new_overall},
        "overall_comparison": overall_comparison,
        "by_age_group": by_age_group,
        "per_age_group_regressions": per_age_group_regressions,
        "hard_constraint_regressed": hard_constraint_regressed,
        "dominates_baseline": dominates_baseline,
        "production_ready": production_ready,
        "change_cost": change_cost_report,
        # Deprecated alias for production_ready, kept for existing callers.
        "promotable": production_ready,
    }
