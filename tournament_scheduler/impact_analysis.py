"""Metric, change, and team-impact analysis for maintenance.

This module owns the measurement of changes between plans, including
team and tournament changes, plan fingerprints, and priority improvement tiers.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from .planning_contract import score_candidate
from .quality_objectives import quality_objective_vector, with_unresolved_obligations_count


def _changed_team_ids(before: Mapping[str, Any], after: Mapping[str, Any]) -> List[str]:
    """Return the sorted list of team IDs that changed between two plans.

    Args:
        before: The plan before changes
        after: The plan after changes

    Returns:
        Sorted list of team IDs that changed
    """
    changed = set(_changed_tournament_ids(before, after))
    team_ids: Set[str] = set()
    for tid in changed:
        before_by_id = {
            str(t.get("id")): t for t in before.get("tournaments", []) or []
        }
        after_by_id = {
            str(t.get("id")): t for t in after.get("tournaments", []) or []
        }
        before_t = before_by_id.get(tid)
        after_t = after_by_id.get(tid)
        if before_t is None or after_t is None:
            continue
        before_teams = {
            str(tm.get("id")) for tm in before_t.get("teams", []) or []
        }
        after_teams = {
            str(tm.get("id")) for tm in after_t.get("teams", []) or []
        }
        team_ids.update(before_t.symmetric_difference(after_teams))
    return sorted(team_ids)


def _changed_tournament_ids(before: Mapping[str, Any], after: Mapping[str, Any]) -> List[str]:
    """Return the sorted list of tournament IDs that changed between two plans.

    Args:
        before: The plan before changes
        after: The plan after changes

    Returns:
        Sorted list of tournament IDs that changed
    """
    before_by_id = {str(t.get("id")): _signature(t) for t in before.get("tournaments", []) or []}
    after_by_id = {str(t.get("id")): _signature(t) for t in after.get("tournaments", []) or []}
    changed: Set[str] = set()
    for tid, sig in before_by_id.items():
        if tid not in after_by_id or after_by_id[tid] != sig:
            changed.add(tid)
    for tid, sig in after_by_id.items():
        if tid not in before_by_id or before_by_id[tid] != sig:
            changed.add(tid)
    return sorted(changed)


def _signature(tournament: Mapping[str, Any]) -> Any:
    """Compute a stable signature for a tournament.

    Args:
        tournament: The tournament to signature

    Returns:
        A hashable signature representing the tournament's identity
    """
    return (
        str(tournament.get("id") or ""),
        str(tournament.get("name") or ""),
        str(tournament.get("age_group") or ""),
        str(tournament.get("club") or ""),
        str(tournament.get("home") or ""),
        frozenset(
            (
                str(t.get("id") or ""),
                str(t.get("name") or ""),
            )
            for t in (tournament.get("teams", []) or [])
            if t.get("id") is not None
        ),
    )


def _plan_fingerprint(plan: Mapping[str, Any]) -> str:
    """Compute a stable fingerprint for a plan.

    Args:
        plan: The plan to fingerprint

    Returns:
        A string fingerprint representing the plan's identity
    """
    return _plan_content_fingerprint(plan)


def _plan_content_fingerprint(plan: Mapping[str, Any]) -> str:
    """Compute a content-based fingerprint for a plan.

    Args:
        plan: The plan to fingerprint

    Returns:
        A string fingerprint representing the plan's content
    """
    # This is a simplified version - in reality, this would use a proper
    # hashing mechanism like SHA-256 on the plan's content
    return str(hash(tuple(sorted(plan.items()))))


def _priority_improvement_tier(delta: Mapping[str, Any]) -> Optional[int]:
    """Determine the priority improvement tier based on a metric delta.

    Args:
        delta: The metric delta between before and after states

    Returns:
        The priority improvement tier (1-4) or None if no improvement
    """
    # This is a simplified version - the actual implementation would
    # analyze the delta to determine the improvement tier
    changed_count = delta.get("changed_tournament_count", 0)
    if changed_count == 0:
        return None
    elif changed_count <= 5:
        return 1
    elif changed_count <= 15:
        return 2
    elif changed_count <= 30:
        return 3
    else:
        return 4