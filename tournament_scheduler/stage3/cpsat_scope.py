"""Maintenance-scope hooks for the Stage 3 CP-SAT participant model.

The model in :mod:`tournament_scheduler.stage3.cpsat` optimizes participant
assignment. A bounded maintenance solve narrows it through
``problem["maintenance_scope"]`` (written by
:mod:`tournament_scheduler.stage3.cpsat_maintenance`, never by callers):

* ``movable_teams`` -- only these ``club:label:age_group`` identities may enter
  or leave a roster; every other team keeps its baseline membership in every
  slot (sibling-only substitution is the common case);
* ``home_balance`` -- ``{"club": ..., "age_group": ...}``: hold each sibling's
  appearance count at its baseline and add a tier that evens out how often
  each represents the club at its own hosted tournaments;
* ``repair_baseline_conflicts`` -- the baseline may already double-book a team
  on one date (that is the finding being repaired), so do not refuse it.

A final ``minimal_change`` tier prefers the smallest roster difference from the
baseline, so a maintenance result is a reviewable patch and not a reshuffle.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from tournament_scheduler.hosting.representation import clubs_represent_same_club


def team_key(identity: Sequence[str]) -> str:
    return ":".join(identity)


def movable_team_keys(problem: Optional[Mapping[str, Any]]) -> Optional[frozenset[str]]:
    scope = (problem or {}).get("maintenance_scope") or {}
    teams = scope.get("movable_teams")
    return None if teams is None else frozenset(str(item) for item in teams)


def fix_immovable_teams(
    model: Any,
    x: Mapping[Any, Any],
    slot: Any,
    eligible: Iterable[Any],
    movable: frozenset[str],
) -> None:
    """Pin every team outside *movable* to its baseline membership in *slot*."""
    baseline = set(slot.baseline_team_ids)
    for identity in eligible:
        if team_key(identity) not in movable:
            model.Add(x[(slot.index, identity)] == int(identity in baseline))


def home_balance_terms(
    model: Any,
    x: Mapping[Any, Any],
    slots: Sequence[Any],
    teams_by_age_group: Mapping[str, Sequence[Any]],
    problem: Optional[Mapping[str, Any]],
) -> List[Any]:
    """Spread (max - min) of each sibling's appearances at the club's hosted slots.

    A home-balance solve moves *which* sibling represents the club, not how
    often each plays: every sibling's total appearances over *slots* are held
    at their baseline count (this adds those constraints to *model*).
    """
    scope = ((problem or {}).get("maintenance_scope") or {}).get("home_balance") or {}
    club, age_group = str(scope.get("club") or ""), str(scope.get("age_group") or "")
    if not club or not age_group:
        return []
    siblings = [i for i in teams_by_age_group.get(age_group, []) if i[0] == club]
    home_slots = [s for s in slots if s.age_group == age_group and clubs_represent_same_club(s.host_club, club)]
    if len(siblings) < 2 or not home_slots:
        return []
    for identity in siblings:
        baseline_total = sum(1 for s in slots if identity in s.baseline_team_ids)
        model.Add(sum(x[(s.index, identity)] for s in slots if s.age_group == age_group) == baseline_total)
    counts = []
    for identity in siblings:
        count = model.NewIntVar(0, len(home_slots), f"home_{team_key(identity)}")
        model.Add(count == sum(x[(s.index, identity)] for s in home_slots))
        counts.append(count)
    high = model.NewIntVar(0, len(home_slots), "home_high")
    low = model.NewIntVar(0, len(home_slots), "home_low")
    for count in counts:
        model.Add(high >= count)
        model.Add(low <= count)
    return [high - low]


def change_cost_terms(
    x: Mapping[Any, Any], slots: Sequence[Any], teams_by_age_group: Mapping[str, Sequence[Any]]
) -> List[Any]:
    """One unit per roster membership that differs from the baseline."""
    terms: List[Any] = []
    for slot in slots:
        baseline = set(slot.baseline_team_ids)
        for identity in teams_by_age_group.get(slot.age_group, []):
            var = x[(slot.index, identity)]
            terms.append(1 - var if identity in baseline else var)
    return terms


def repairs_baseline_conflicts(problem: Optional[Mapping[str, Any]]) -> bool:
    """Whether the solve is a repair of a baseline that may already break a hard rule."""
    return bool(((problem or {}).get("maintenance_scope") or {}).get("repair_baseline_conflicts"))


def scope_payload(
    movable_teams: Optional[Iterable[str]],
    home_balance: Optional[Mapping[str, str]],
    repair_baseline_conflicts: bool = False,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if repair_baseline_conflicts:
        payload["repair_baseline_conflicts"] = True
    if movable_teams is not None:
        payload["movable_teams"] = sorted(str(item) for item in movable_teams)
    if home_balance:
        payload["home_balance"] = dict(home_balance)
    return payload
