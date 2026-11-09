"""Priority-tier (lexicographic) solving for the Stage 3 CP-SAT participant model.

Split out of :mod:`tournament_scheduler.stage3.cpsat`: the model builder owns
*what* the objective tiers are, this helper owns *how* they are solved in
priority order.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any


def solve_lexicographic(
    model: Any,
    solver: Any,
    x: "dict[Any, Any]",
    objective_tiers: "list[tuple[str, list[Any]]]",
    tier_values: "dict[str, float]",
    tier_diagnostics: "list[dict[str, Any]]",
    total_budget_seconds: float,
) -> Any:
    """Solve priority *objective_tiers* within one total wall-clock budget.

    Stage ``k`` minimizes tier ``k`` and then fixes its incumbent so no lower
    tier can trade a higher one away. ``OPTIMAL`` is returned only when every
    non-empty tier proved optimal; one bounded ``FEASIBLE`` tier makes the
    whole lexicographic result ``FEASIBLE``. Per-tier status and allowance are
    recorded in *tier_diagnostics*.
    """
    from ortools.sat.python import cp_model

    tiers = [(name, terms) for name, terms in objective_tiers if terms]
    started = perf_counter()
    status_code = cp_model.UNKNOWN
    all_optimal = True
    for position, (name, terms) in enumerate(tiers):
        remaining = float(total_budget_seconds) - (perf_counter() - started)
        if remaining <= 0:
            return cp_model.FEASIBLE if position else cp_model.UNKNOWN
        allowance = remaining / (len(tiers) - position)
        solver.parameters.max_time_in_seconds = allowance
        expression = sum(terms)
        model.Minimize(expression)
        status_code = solver.Solve(model)
        status_name = solver.StatusName(status_code)
        tier = {"name": name, "status": status_name, "budget_seconds": allowance}
        if status_code not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            tier_diagnostics.append(tier)
            return status_code
        value = float(solver.ObjectiveValue())
        tier_values[name] = value
        tier_diagnostics.append({**tier, "objective_value": value})
        all_optimal = all_optimal and status_code == cp_model.OPTIMAL
        if position == len(tiers) - 1:
            break
        model.Add(expression <= int(round(value)))
        model.ClearHints()
        for variable in x.values():
            model.AddHint(variable, solver.Value(variable))
    return cp_model.OPTIMAL if all_optimal else cp_model.FEASIBLE
