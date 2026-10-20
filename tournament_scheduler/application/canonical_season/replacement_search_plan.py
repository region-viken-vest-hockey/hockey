"""Plan-level helpers for one-tournament replacement candidates.

Pure over the plan it is given: the tournament lookup and the candidate plan
copy that a replacement dry-run would produce. Nothing here writes state.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError

from .replacement import _apply_replace_participant_to_plan


def find_tournament(plan: Mapping[str, Any], tournament_id: str) -> dict[str, Any]:
    tournament = next(
        (
            item
            for item in plan.get("tournaments", []) or []
            if str(item.get("id") or "") == tournament_id
        ),
        None,
    )
    if tournament is None:
        raise SeasonStateError(
            f"Unknown tournament id in canonical schedule: {tournament_id}"
        )
    if tournament.get("cancelled"):
        raise SeasonStateError(f"Tournament {tournament_id} is cancelled")
    return tournament


def substituted_plan(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    tournament_id: str,
    remove: str,
    add: str,
) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(plan))
    _apply_replace_participant_to_plan(
        candidate,
        tournament_id=tournament_id,
        remove_team_label=remove,
        add_team_label=add,
        problem=problem,
    )
    return candidate
