"""Focused contracts for bounded lexicographic CP-SAT solving."""

from __future__ import annotations

from types import SimpleNamespace
from collections.abc import Sequence
from typing import Any

import pytest

pytest.importorskip("ortools", reason="OR-Tools is the optional cpsat extra")
from ortools.sat.python import cp_model

import tournament_scheduler.stage3.cpsat_lexicographic as lexicographic


class _Model:
    def Minimize(self, _expression: Any) -> None:
        pass

    def Add(self, _constraint: Any) -> None:
        pass

    def ClearHints(self) -> None:
        pass

    def AddHint(self, _variable: Any, _value: int) -> None:
        pass


class _Solver:
    def __init__(self, statuses: Sequence[Any], clock: list[float] | None = None) -> None:
        self.parameters = SimpleNamespace(max_time_in_seconds=0.0)
        self._statuses = iter(statuses)
        self._clock = clock
        self.budgets: list[float] = []

    def Solve(self, _model: Any) -> Any:
        budget = float(self.parameters.max_time_in_seconds)
        self.budgets.append(budget)
        if self._clock is not None:
            self._clock[0] += budget
        return next(self._statuses)

    def StatusName(self, status: Any) -> str:
        return cp_model.CpSolver().StatusName(status)

    def ObjectiveValue(self) -> float:
        return 3.0

    def Value(self, _variable: Any) -> int:
        return 0


def _solve(solver: _Solver, *, budget: float = 1.0) -> tuple[int, dict[str, float], list[dict[str, Any]]]:
    values: dict[str, float] = {}
    diagnostics: list[dict[str, Any]] = []
    status = lexicographic.solve_lexicographic(
        _Model(),
        solver,
        {"x": object()},
        [("first", [1]), ("second", [1])],
        values,
        diagnostics,
        budget,
    )
    return status, values, diagnostics


def test_feasible_earlier_tier_prevents_claiming_lexicographic_optimality(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lexicographic, "perf_counter", lambda: 0.0)
    status, values, diagnostics = _solve(_Solver([cp_model.FEASIBLE, cp_model.OPTIMAL]))

    assert status == cp_model.FEASIBLE
    assert values == {"first": 3.0, "second": 3.0}
    assert [tier["status"] for tier in diagnostics] == ["FEASIBLE", "OPTIMAL"]


def test_all_tiers_share_one_total_wall_clock_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(lexicographic, "perf_counter", lambda: clock[0])
    solver = _Solver([cp_model.OPTIMAL] * 3, clock)
    diagnostics: list[dict[str, Any]] = []

    status = lexicographic.solve_lexicographic(
        _Model(),
        solver,
        {"x": object()},
        [("first", [1]), ("second", [1]), ("third", [1])],
        {},
        diagnostics,
        0.2,
    )

    assert status == cp_model.OPTIMAL
    assert sum(solver.budgets) == pytest.approx(0.2)
    assert clock[0] == pytest.approx(0.2)
    assert all(tier["budget_seconds"] < 0.1 for tier in diagnostics)
