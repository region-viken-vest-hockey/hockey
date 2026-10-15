"""Comparison and effective-gate policy for rotatable participant pools."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.participation.temporal_coverage import (
    DEFAULT_TEMPORAL_COVERAGE_THRESHOLD_DAYS,
)

from .participant_pools import (
    TeamIdentity,
    participant_pool_schedule_profile,
    resolve_rotatable_participant_pool,
)

MATERIAL_TEMPORAL_GAP_DELTA_DAYS = 14
EXTREME_SIBLING_SPREAD = 3


def compare_participant_pool_profiles(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    """Classify real pool regressions while retaining label-level diagnostics."""

    material: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    before_spacing = before.get("spacing") or {}
    after_spacing = after.get("spacing") or {}
    for key, code in (
        ("unavoidable_same_day_overflow", "more_unavoidable_same_day_pool_load"),
        ("unavoidable_back_to_back_days", "more_unavoidable_back_to_back_days"),
        ("unavoidable_gaps_under_7", "more_gaps_under_7_days"),
        ("unavoidable_consecutive_weekends", "more_unavoidable_consecutive_weekends"),
        ("unavoidable_gaps_under_14", "more_gaps_under_14_days"),
    ):
        old = int(before_spacing.get(key, 0) or 0)
        new = int(after_spacing.get(key, 0) or 0)
        if new > old:
            material.append(
                {"code": code, "scope": "participant_pool", "before": old, "after": new, "delta": new - old}
            )

    old_gap = int((before.get("temporal") or {}).get("max_gap_days", 0) or 0)
    new_gap = int((after.get("temporal") or {}).get("max_gap_days", 0) or 0)
    if new_gap > DEFAULT_TEMPORAL_COVERAGE_THRESHOLD_DAYS and new_gap - old_gap >= MATERIAL_TEMPORAL_GAP_DELTA_DAYS:
        material.append(
            {
                "code": "temporal_coverage_materially_worse",
                "scope": "participant_pool",
                "before": old_gap,
                "after": new_gap,
                "delta": new_gap - old_gap,
                "threshold_days": DEFAULT_TEMPORAL_COVERAGE_THRESHOLD_DAYS,
            }
        )
    elif new_gap > old_gap:
        warnings.append(
            {"code": "temporal_coverage_worse", "scope": "participant_pool", "before": old_gap, "after": new_gap}
        )

    before_distribution = before.get("label_distribution") or {}
    after_distribution = after.get("label_distribution") or {}
    old_spread = int(before_distribution.get("spread", 0) or 0)
    new_spread = int(after_distribution.get("spread", 0) or 0)
    if new_spread > old_spread:
        entry = {
            "code": "sibling_label_imbalance_worse",
            "scope": "participant_pool",
            "before": old_spread,
            "after": new_spread,
            "delta": new_spread - old_spread,
        }
        if new_spread >= EXTREME_SIBLING_SPREAD:
            material.append({**entry, "code": "extreme_sibling_label_imbalance"})
        else:
            warnings.append(entry)
    old_deprived = set(before_distribution.get("deprived_labels") or [])
    new_deprived = set(after_distribution.get("deprived_labels") or [])
    newly_deprived = sorted(new_deprived - old_deprived)
    if newly_deprived and int(after.get("opportunity_count", 0) or 0) >= len(
        (after.get("pool") or {}).get("team_labels") or []
    ):
        material.append(
            {"code": "severe_sibling_deprivation", "scope": "participant_pool", "labels": newly_deprived}
        )
    return {
        "acceptable": not material,
        "material_regressions": material,
        "warnings": warnings,
        "before": dict(before),
        "after": dict(after),
    }


def compare_rotatable_participant_pool_consequence(
    before_plan: Mapping[str, Any],
    after_plan: Mapping[str, Any],
    identity: TeamIdentity,
    *,
    problem: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    pool = resolve_rotatable_participant_pool(problem, identity)
    if pool is None:
        return None
    before = participant_pool_schedule_profile(before_plan, pool, problem=problem)
    after = participant_pool_schedule_profile(after_plan, pool, problem=problem)
    return compare_participant_pool_profiles(before, after)


def apply_rotatable_pool_policy(
    team_level: Mapping[str, Any],
    before_plan: Mapping[str, Any],
    after_plan: Mapping[str, Any],
    identity: TeamIdentity,
    *,
    problem: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Replace exact spacing/coverage blockers with unavoidable pool blockers."""

    pool_level = compare_rotatable_participant_pool_consequence(
        before_plan, after_plan, identity, problem=problem
    )
    if pool_level is None:
        return dict(team_level)
    governed = {"more_gaps_under_7_days", "more_gaps_under_14_days", "temporal_coverage_materially_worse"}
    exact = [dict(item) for item in team_level.get("material_regressions", []) or []]
    effective = [item for item in exact if str(item.get("code") or "") not in governed]
    effective.extend(dict(item) for item in pool_level.get("material_regressions", []) or [])
    pool_codes = {str(item.get("code") or "") for item in pool_level.get("material_regressions", []) or []}
    warnings = [dict(item) for item in team_level.get("warnings", []) or []]
    warnings.extend(dict(item) for item in pool_level.get("warnings", []) or [])
    warnings.extend(
        {
            "code": "exact_label_regression_distributable_in_pool",
            "exact_regression": item,
            "scope": "exact_team",
        }
        for item in exact
        if str(item.get("code") or "") in governed and str(item.get("code") or "") not in pool_codes
    )
    return {
        **team_level,
        "acceptable": not effective,
        "material_regressions": effective,
        "warnings": warnings,
        "team_level": dict(team_level),
        "participant_pool": pool_level,
    }


__all__ = [
    "apply_rotatable_pool_policy",
    "compare_participant_pool_profiles",
    "compare_rotatable_participant_pool_consequence",
]
