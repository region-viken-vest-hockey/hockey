"""Canonical facts projected into a maintenance problem (locks, pins, pool).

Kept apart from :mod:`maintenance_problem` so the problem type stays small and
the projection of canonical state has one obvious home.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from tournament_scheduler.planning.contract import _team_identity

TeamId = Tuple[str, str, str]


def participant_fixed_reasons(problem: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """Tournament ids whose *roster* is fixed by canonical state, with the reason.

    A participant lock fixes the roster. An operator pin fixes it too. A
    placement-only lock fixes placement only, so it must not appear here --
    but the planning-problem contract folds every canonical lock into
    ``manual_adjustments.pinned_tournament_ids``, so canonical-derived pins are
    subtracted again and only participant locks and genuine operator pins stay.
    """
    problem = problem or {}
    locks = (problem.get("canonical_baseline") or {}).get("locks") or {}
    reasons: Dict[str, str] = {}
    placement_only: set[str] = set()
    for tournament_id, lock in locks.items():
        lock = lock or {}
        if lock.get("participants"):
            reasons[str(tournament_id)] = "participants_locked"
        elif lock.get("placement"):
            placement_only.add(str(tournament_id))
    for value in (problem.get("manual_adjustments") or {}).get("pinned_tournament_ids") or []:
        tournament_id = str(value)
        if tournament_id in placement_only or tournament_id in reasons:
            continue
        reasons[tournament_id] = "operator_pinned"
    return reasons


def registered_pool(candidate: Mapping[str, Any], problem: Optional[Mapping[str, Any]]) -> Dict[str, List[TeamId]]:
    pool: Dict[str, set[TeamId]] = {}
    for raw in (problem or {}).get("teams", []) or []:
        if isinstance(raw, Mapping):
            identity = _team_identity(dict(raw))
            pool.setdefault(identity[2], set()).add(identity)
    for tournament in candidate.get("tournaments", []) or []:
        for raw in tournament.get("teams", []) or []:
            if isinstance(raw, Mapping):
                identity = _team_identity(dict(raw))
                pool.setdefault(identity[2], set()).add(identity)
    return {group: sorted(members) for group, members in pool.items()}
