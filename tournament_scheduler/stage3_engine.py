"""Stage 3 engine-dispatch boundary (issue #276 Phase 1).

A single narrow seam between "which search engine produced this candidate"
and everything downstream (:func:`stage3_ab.build_ab_report`,
:mod:`stage3_decision`, the CLI). ``local_search`` — the existing
:func:`stage3_optimizer.optimize_candidate` — stays the default and the only
engine that behaves exactly as before; ``cp_sat`` is the fixed-skeleton
participant engine from :mod:`stage3_cpsat`, still shadow/experimental.

Both engines already consume the same normalized ``problem``/``baseline``
contract and emit the same candidate contract (:mod:`planning_contract`), so
this module is deliberately just a dispatch table, not a plugin framework:
adding a third engine later means adding one more branch here, nothing else.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

DEFAULT_ENGINE = "local_search"
ENGINE_CHOICES: "tuple[str, ...]" = ("local_search", "cp_sat")


class UnknownEngineError(ValueError):
    """Raised when *engine* is not one of :data:`ENGINE_CHOICES`."""

    def __init__(self, engine: str) -> None:
        self.engine = engine
        super().__init__(
            f"Unknown planner engine {engine!r}; choices: {', '.join(ENGINE_CHOICES)}"
        )


def run_planner(
    *,
    engine: str = DEFAULT_ENGINE,
    problem: Optional[Dict[str, Any]],
    baseline: Dict[str, Any],
    request: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Produce a Stage 3 candidate from *baseline* using *engine*.

    *request* carries engine-specific tuning knobs (e.g. ``iterations``/
    ``seed``/``weights``/``move_dates`` for ``local_search``,
    ``solve_budget_seconds``/``seed``/``feasibility_only``/``decompose_by_half``
    for ``cp_sat``); unrecognized keys for
    the selected engine are ignored rather than rejected, so one request dict
    can be built once by a caller that does not need to know which engine
    ends up handling it.

    Raises :class:`UnknownEngineError` for any other *engine* value.
    :func:`stage3_cpsat.optimize_candidate_cp_sat`'s own
    ``CpSatUnavailable``/``CpSatNoCandidate`` propagate unchanged — this
    boundary does not swallow or reinterpret engine-specific failures.
    """
    request = dict(request or {})

    if engine == "local_search":
        from .stage3_optimizer import optimize_candidate

        return optimize_candidate(
            baseline,
            problem,
            iterations=int(request.get("iterations", 4000)),
            seed=int(request.get("seed", 0)),
            weights=request.get("weights"),
            per_age_group_weights=request.get("per_age_group_weights"),
            move_dates=bool(request.get("move_dates", False)),
            date_swap_probability=float(request.get("date_swap_probability", 0.3)),
            move_dates_within_half=bool(request.get("move_dates_within_half", False)),
            move_hosts=bool(request.get("move_hosts", False)),
            move_slots=bool(request.get("move_slots", False)),
            plateau_iterations=request.get("plateau_iterations"),
        )

    if engine == "cp_sat":
        from .stage3_cpsat import optimize_candidate_cp_sat

        return optimize_candidate_cp_sat(
            baseline,
            problem,
            solve_budget_seconds=float(request.get("solve_budget_seconds", 30.0)),
            seed=int(request.get("seed", 0)),
            feasibility_only=bool(request.get("feasibility_only", False)),
            decompose_by_half=bool(request.get("decompose_by_half", False)),
        )

    raise UnknownEngineError(engine)
