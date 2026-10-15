"""Resolve exact-team or rotatable-pool subjects for spacing findings."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.participation.participant_pools import (
    participant_pool_schedule_profile,
    resolve_rotatable_participant_pool,
)
from tournament_scheduler.participation.team_schedule_quality import (
    CLOSE_GAP_DAYS,
    team_schedule_profile,
)


def spacing_finding_subject(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    identity: tuple[str, str, str],
) -> dict[str, Any] | None:
    """Return the policy subject only when it has actionable close spacing."""

    pool = resolve_rotatable_participant_pool(problem, identity)
    if pool is not None:
        pool_profile = participant_pool_schedule_profile(plan, pool, problem=problem)
        pool_gaps = pool_profile.get("spacing") or {}
        if int(pool_gaps.get("unavoidable_gaps_under_7", 0) or 0) <= 0:
            return None
    profile = team_schedule_profile(plan, identity, problem=problem)
    gaps = profile.get("spacing") or {}
    min_gap = gaps.get("min_gap_days")
    if min_gap is None or int(min_gap) >= CLOSE_GAP_DAYS:
        return None
    return {
        "pool": pool,
        "dates": profile.get("tournament_dates") or [],
        "min_gap": min_gap,
        "labels": {identity[1]},
        "subject_label": identity[1],
    }


__all__ = ["spacing_finding_subject"]
