"""Metric, change, and team-impact analysis for maintenance.

This module owns the measurement of changes between plans, including
team and tournament changes, plan fingerprints, and priority improvement tiers.
"""

from __future__ import annotations

from typing import Any
from typing import Iterable
from typing import List
from typing import Mapping
from typing import Optional


def plan_fingerprint(plan: Mapping[str, Any]) -> str:
    return _plan_fingerprint(plan)


def _priority_improvement_tier(delta: Mapping[str, Any]) -> Optional[int]:
    """Return the highest-priority defect tier the candidate strictly improved.

    Tiers follow the catalog precedence (hard 0, operational obligation 1,
    strong goal 2, soft 3). Only the *highest* improved tier is returned so a
    soft regression can be auto-waived solely by a genuinely higher-tier fix;
    strong-goal and operational-obligation regressions still require an explicit
    named acceptance. Returns ``None`` when no tier improved.
    """

    from .repair_adoption_guard import (
        TIER_HARD,
        TIER_OPERATIONAL_OBLIGATION,
        TIER_STRONG_GOAL,
    )

    def total(tier: Iterable[tuple[str, str]]) -> tuple[int, int]:
        before = sum(int(delta.get(before_key, 0) or 0) for before_key, _ in tier)
        after = sum(int(delta.get(after_key, 0) or 0) for _, after_key in tier)
        return before, after

    hard = total((("hard_violations_before", "hard_violations_after"),))
    if hard[1] < hard[0]:
        return TIER_HARD
    obligations = total(
        (
            ("unresolved_hosting_obligations_before", "unresolved_hosting_obligations_after"),
            ("manual_placements_before", "manual_placements_after"),
            ("unresolved_placement_obligations_before", "unresolved_placement_obligations_after"),
        )
    )
    if obligations[1] < obligations[0]:
        return TIER_OPERATIONAL_OBLIGATION
    goals = total(
        (
            ("hosting_balance_imbalances_before", "hosting_balance_imbalances_after"),
            ("participation_deviations_before", "participation_deviations_after"),
        )
    )
    if goals[1] < goals[0]:
        return TIER_STRONG_GOAL
    return None


def _changed_team_ids(before: Mapping[str, Any], after: Mapping[str, Any]) -> List[str]:
    """Return ``club|label|age_group`` identities of teams in changed tournaments."""
    changed = set(_changed_tournament_ids(before, after))
    identities: set[str] = set()
    for plan in (before, after):
        for tournament in plan.get("tournaments") or []:
            if str(tournament.get("id")) not in changed:
                continue
            for team in tournament.get("teams") or []:
                identities.add(
                    "|".join(
                        (
                            str(team.get("club") or ""),
                            str(team.get("label") or ""),
                            str(team.get("age_group") or ""),
                        )
                    )
                )
    return sorted(identities)


def _changed_tournament_ids(before: Mapping[str, Any], after: Mapping[str, Any]) -> List[str]:
    before_by_id = {str(t.get("id")): _signature(t) for t in before.get("tournaments", []) or []}
    after_by_id = {str(t.get("id")): _signature(t) for t in after.get("tournaments", []) or []}
    return [
        tournament_id
        for tournament_id in sorted(set(before_by_id) | set(after_by_id))
        if before_by_id.get(tournament_id) != after_by_id.get(tournament_id)
    ]


def _signature(tournament: Mapping[str, Any]) -> Any:
    teams = tuple(
        sorted(
            (str(team.get("club") or ""), str(team.get("label") or ""), str(team.get("age_group") or ""))
            for team in tournament.get("teams", []) or []
        )
    )
    return (
        tournament.get("date"),
        tournament.get("host_club"),
        tournament.get("arena"),
        tournament.get("start_time"),
        teams,
    )


def _plan_fingerprint(plan: Mapping[str, Any]) -> str:
    from .host_team_missing_repair import candidate_fingerprint

    return candidate_fingerprint(plan)


def _plan_content_fingerprint(plan: Mapping[str, Any]) -> str:
    """Return the tournament-content fingerprint including games and metadata."""
    from .pipeline.fingerprints import stable_payload_sha256

    return stable_payload_sha256(plan.get("tournaments", []))
