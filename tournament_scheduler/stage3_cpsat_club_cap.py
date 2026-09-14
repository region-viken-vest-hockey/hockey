"""issue #324 CP-SAT club-concentration modeling, split out of `stage3_cpsat.py`.

Explicitly models 3rd-or-later teams from one club in one tournament, per
(slot, club) -- the aggregate `same_club_terms` pair-co-occurrence penalty in
`_solve_slot_group` is correlated with this but not equivalent, so a
candidate could improve the aggregate pairing count while still clustering
3+ teams from one club in a slot. `build_club_excess_terms` closes that gap
with its own dedicated, heavily-weighted term.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .planning_contract import HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT


def build_club_excess_terms(model: Any, x: dict, slots, teams_by_age_group: dict) -> list:
    """Return one `excess_over_2` IntVar per (slot, club) with 3+ eligible teams.

    `x[(slot.index, identity)]` is the existing participation boolean for
    that team in that slot; `identity[0]` is the team's club. Each returned
    term is constrained to `>= club_count - 2`, so it is 0 unless the model
    actually assigns a 3rd-or-later team from that club to that slot.

    issue #326: alongside that soft `excess_over_2` objective term, every
    (slot, club) pair with 3+ eligible teams also gets a real hard
    constraint capping `club_count` at `HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT`
    -- the solver must never return a candidate with 4+ teams from one club
    in one tournament, no matter how cheap the objective makes it.
    """
    club_excess_terms: list = []
    club_excess_serial = 0
    for slot in slots:
        eligible = teams_by_age_group.get(slot.age_group, [])
        clubs_in_slot: "dict[str, list[Any]]" = defaultdict(list)
        for identity in eligible:
            clubs_in_slot[identity[0]].append(x[(slot.index, identity)])
        for club_vars in clubs_in_slot.values():
            if len(club_vars) <= 2:
                continue
            club_excess_serial += 1
            club_count = model.NewIntVar(0, len(club_vars), f"club_count_{club_excess_serial}")
            model.Add(club_count == sum(club_vars))
            model.Add(club_count <= HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT)
            excess_over_2 = model.NewIntVar(0, len(club_vars), f"club_excess_over_2_{club_excess_serial}")
            model.Add(excess_over_2 >= club_count - 2)
            club_excess_terms.append(excess_over_2)
    return club_excess_terms
