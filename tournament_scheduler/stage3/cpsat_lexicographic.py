"""Priority-tier (lexicographic) solving for the Stage 3 CP-SAT participant model.

Split out of :mod:`tournament_scheduler.stage3.cpsat`: the model builder owns
*what* the objective tiers are, this helper owns *how* they are solved in
priority order.
"""

from __future__ import annotations

from typing import Any


def solve_lexicographic(
    model: Any,
    solver: Any,
    x: "dict[Any, Any]",
    objective_tiers: "list[tuple[str, list[Any]]]",
    tier_values: "dict[str, float]",
    total_budget_seconds: float,
) -> Any:
    """Solve priority *objective_tiers* in order, fixing each optimum.

    Stage ``k`` minimizes tier ``k`` and then adds ``tier_k <= value`` so no
    lower tier can trade a higher one away. The wall-clock budget is shared
    evenly across the non-empty tiers. A tier that ends ``FEASIBLE`` (budget)
    still fixes its best value. Returns the last stage's status; a first-stage
    failure is returned as-is so the caller reports no-candidate.
    """
    from ortools.sat.python import cp_model

    tiers = [(name, terms) for name, terms in objective_tiers if terms]
    solver.parameters.max_time_in_seconds = max(0.1, total_budget_seconds / max(1, len(tiers)))
    status_code = cp_model.UNKNOWN
    for position, (name, terms) in enumerate(tiers):
        expression = sum(terms)
        model.Minimize(expression)
        status_code = solver.Solve(model)
        if status_code not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return status_code
        tier_values[name] = float(solver.ObjectiveValue())
        if position == len(tiers) - 1:
            break
        model.Add(expression <= int(round(solver.ObjectiveValue())))
        model.ClearHints()
        for variable in x.values():
            model.AddHint(variable, solver.Value(variable))
    return status_code
