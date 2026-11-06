"""Sibling-only roster search through the bounded CP-SAT maintenance path.

The intra-club distribution and home-representation providers keep their cheap
deterministic moves; when those are not enough, their combinatorial widening
(coupled pairs, multi-tournament rotations) is served here instead of by a
private enumerate-and-score loop (ADR 0008).

This module only translates: it asks the generic maintenance solver for a
patch whose movable teams are the club's own siblings, and converts the patch
back into the replayable ``swap`` units the providers already apply, evaluate
and gate. It never decides acceptability, never moves placement, and never
adopts a candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from tournament_scheduler.planning.contract import _team_identity
from tournament_scheduler.stage3.cpsat_maintenance import solve_maintenance_problem
from tournament_scheduler.stage3.cpsat_scope import team_key
from tournament_scheduler.stage3.maintenance_problem import (
    DEFAULT_SEARCH_BUDGET_SECONDS,
    build_participant_maintenance_problem,
)


@dataclass(frozen=True)
class SiblingSearch:
    """Result of one sibling-scoped solve: a swap plan, or why there is none."""

    status: str
    swaps: Tuple[Dict[str, Any], ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


def _sibling_keys(candidate: Mapping[str, Any], problem: Mapping[str, Any], club: str, age_group: str) -> List[str]:
    keys = set()
    pools: Iterable[Any] = list(problem.get("teams", []) or [])
    for tournament in candidate.get("tournaments", []) or []:
        pools = [*pools, *(tournament.get("teams", []) or [])]
    for raw in pools:
        identity = _team_identity(dict(raw))
        if identity[0] == club and identity[2] == age_group:
            keys.add(team_key(identity))
    return sorted(keys)


def _swaps_from_patches(candidate: Mapping[str, Any], patches: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    by_id = {str(t.get("id", i)): t for i, t in enumerate(candidate.get("tournaments", []) or [])}
    swaps: List[Dict[str, Any]] = []
    for patch in patches:
        tournament_id = str(patch["tournament_id"])
        before = {_team_identity(dict(t)): dict(t) for t in by_id[tournament_id].get("teams", []) or []}
        after = {_team_identity(dict(t)): dict(t) for t in patch.get("teams", []) or []}
        removed = sorted(set(before) - set(after))
        added = sorted(set(after) - set(before))
        for out, into in zip(removed, added):
            swaps.append(
                {
                    "tournament_id": tournament_id,
                    "remove": {"club": out[0], "label": out[1], "age_group": out[2]},
                    "add": {"club": into[0], "label": into[1], "age_group": into[2]},
                }
            )
    return swaps


def solve_sibling_swaps(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    club: str,
    age_group: str,
    tournament_ids: Iterable[str],
    balance_home: bool = False,
    seed: int = 0,
    search_budget_seconds: float = DEFAULT_SEARCH_BUDGET_SECONDS,
) -> SiblingSearch:
    """Search sibling substitutions for *club*'s teams in *age_group*.

    Only the club's own sibling teams may enter or leave a roster, only within
    *tournament_ids*, and nothing but participants can change. ``balance_home``
    adds the home-representation tier after participation counts.
    """
    maintenance = build_participant_maintenance_problem(
        candidate,
        problem,
        age_groups=[age_group],
        tournament_ids=tournament_ids,
        search_budget_seconds=search_budget_seconds,
        seed=seed,
        movable_teams=_sibling_keys(candidate, problem, club, age_group),
        home_balance={"club": club, "age_group": age_group} if balance_home else None,
    )
    solved = solve_maintenance_problem(maintenance, dict(candidate), dict(problem))
    diagnostics = {"maintenance_status": solved.status, "solver_status": solved.diagnostics.get("solver_status")}
    if not solved.has_candidate:
        return SiblingSearch(status=solved.status, diagnostics=diagnostics)
    swaps = _swaps_from_patches(candidate, solved.patches)
    if not swaps:
        return SiblingSearch(status="no_swaps", diagnostics=diagnostics)
    return SiblingSearch(status="solved", swaps=tuple(swaps), diagnostics=diagnostics)


def search_rejection(base: Mapping[str, Any], result: SiblingSearch) -> Optional[Dict[str, Any]]:
    """Rejection entry for a solve that produced no plan (None when it did)."""
    if result.status == "solved":
        return None
    return {**base, "reason": f"maintenance_search_{result.status}", "cpsat": True, **result.diagnostics}
