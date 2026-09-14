"""Unit tests for tournament_scheduler.stage3_cpsat_club_cap (issue #326)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

ortools = pytest.importorskip(
    "ortools", reason="OR-Tools is the optional 'cpsat' extra; skip when not installed"
)
from ortools.sat.python import cp_model

from tournament_scheduler.stage3_cpsat_club_cap import build_club_excess_terms


@dataclass
class _Slot:
    index: int
    age_group: str


def test_solver_never_assigns_4_plus_teams_from_one_club():
    """issue #326: even when the objective strongly favors packing one
    club's teams into a slot, the hard `club_count <= 3` constraint must
    make 4+ infeasible, not merely expensive."""
    model = cp_model.CpModel()
    slot = _Slot(index=0, age_group="U11")
    slots = [slot]

    jar_identities = [("Jar", f"Jar {i}", "U11") for i in range(1, 6)]  # 5 Jar teams
    other_identity = ("Kongsberg", "Kongsberg 1", "U11")
    teams_by_age_group = {"U11": jar_identities + [other_identity]}

    x = {}
    for identity in teams_by_age_group["U11"]:
        x[(slot.index, identity)] = model.NewBoolVar(f"x_{identity}")

    club_excess_terms = build_club_excess_terms(model, x, slots, teams_by_age_group)
    assert club_excess_terms  # Jar has 5 eligible teams, so a term was built

    # Force the model to assign as many Jar teams as possible.
    model.Maximize(sum(x[(slot.index, identity)] for identity in jar_identities))

    solver = cp_model.CpSolver()
    status = solver.Solve(model)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    jar_count = sum(solver.Value(x[(slot.index, identity)]) for identity in jar_identities)
    assert jar_count == 3


def test_all_5_jar_teams_forced_is_infeasible():
    """Forcing all 5 eligible same-club teams into the slot must be
    infeasible under the hard cap, not silently accepted."""
    model = cp_model.CpModel()
    slot = _Slot(index=0, age_group="U11")
    slots = [slot]

    jar_identities = [("Jar", f"Jar {i}", "U11") for i in range(1, 6)]
    teams_by_age_group = {"U11": jar_identities}

    x = {}
    for identity in jar_identities:
        x[(slot.index, identity)] = model.NewBoolVar(f"x_{identity}")
        model.Add(x[(slot.index, identity)] == 1)

    build_club_excess_terms(model, x, slots, teams_by_age_group)

    solver = cp_model.CpSolver()
    status = solver.Solve(model)
    assert status == cp_model.INFEASIBLE
