"""Stage 3 v2-optimizer and pareto-optimizer entry points."""

from __future__ import annotations

from typing import Any

from .interactive_state_io import _current_run_id, _read_stage3_interactive_state

def _run_stage3_v2_optimize(
    state: "Any",
    cfg: "dict[str, Any]",
    scraping: "dict[str, Any]",
    start: "Any",
    end: "Any",
    arguments: "dict[str, Any] | None",
    log_fn: "Any",
) -> "tuple[dict[str, Any] | None, bool]":
    """Execute an accepted interactive ``optimize_plan`` decision via the
    Stage 3 engine-dispatch boundary (issue #262 P0 / issue #276), instead of
    rerunning the legacy ``SeasonPlanner`` multi-seed loop.

    Mirrors ``cli.plan_command._execute_optimize_plan``'s headless behavior:
    takes the current best Stage 3 candidate, runs
    ``stage3_engine.run_planner`` against it with the LLM-selected engine and
    search arguments (already schema-validated by
    ``application.decisions.decide`` against the ``"v2_optimizer"`` schema
    before this function ever runs), and writes the result as the new Stage
    3 checkpoint. ``SeasonPlanner``/``_run_stage3`` remains the baseline
    generator for the *first* Stage 3 attempt only — this is only reached
    for a subsequent ``optimize_plan`` decision on an already-planned run.

    *arguments.get("engine")* selects the search engine (``"local_search"``
    default, or ``"cp_sat"`` for the shadow/experimental CP-SAT participant
    optimizer). A ``cp_sat`` failure (``CpSatUnavailable``/``CpSatNoCandidate``
    -- missing OR-Tools, or the solver ran out of budget without a feasible
    candidate) is an expected shadow-mode outcome, not a bug: it is recorded
    as an attempt-log entry for evidence and the existing checkpoint is
    returned unchanged (never written, never aborted), so the interactive
    loop simply re-presents the unchanged baseline for another decision
    instead of silently adopting or publishing a solver candidate.

    Returns ``(checkpoint, abort)`` -- *checkpoint* is ``None`` and *abort*
    is ``True`` when there is no baseline candidate to optimize.
    """
    from ...planning_contract import build_planning_problem, extract_candidate
    from ...pipeline.evidence_bundle import append_stage3_attempt_log_entry
    from ...pipeline.state import StageName, StageStatus
    from ...stage3_cpsat import CpSatNoCandidate, CpSatUnavailable
    from ...stage3_engine import run_planner

    arguments = arguments or {}
    planning_checkpoint = state.read_stage(StageName.PLANNING)
    if not planning_checkpoint:
        log_fn("optimize_plan: no Stage 3 checkpoint (baseline) to optimize -- aborting")
        return None, True
    try:
        baseline_candidate = extract_candidate(planning_checkpoint)
    except ValueError as exc:
        log_fn(f"optimize_plan: could not read baseline candidate: {exc}")
        return None, True

    problem = build_planning_problem(cfg, scraping, start.date(), end.date())

    engine = str(arguments.get("engine") or "local_search").replace("-", "_")
    weights = arguments.get("weights")
    request = {
        "iterations": int(arguments.get("iterations", 4000)),
        "seed": int(arguments.get("seed", 0)),
        "weights": {k: float(v) for k, v in weights.items()} if isinstance(weights, dict) else None,
        "move_dates": bool(arguments.get("move_dates", False)),
        "date_swap_probability": float(arguments.get("date_swap_probability", 0.3)),
        "move_dates_within_half": bool(arguments.get("move_dates_within_half", False)),
        "move_hosts": bool(arguments.get("move_hosts", False)),
        "move_slots": bool(arguments.get("move_slots", False)),
        "solve_budget_seconds": float(arguments.get("solve_budget_seconds", 30.0)),
    }

    from time import perf_counter

    from ...pipeline.run_manifest import RunManifest

    _solve_started = perf_counter()
    try:
        new_candidate = run_planner(engine=engine, problem=problem, baseline=baseline_candidate, request=request)
    except (CpSatUnavailable, CpSatNoCandidate) as exc:
        interactive_state = _read_stage3_interactive_state(state, expected_run_id=_current_run_id(state))
        attempt_preview = int(interactive_state.get("attempts_used", 0)) + 1
        log_fn(f"optimize_plan: engine={engine} produced no candidate ({exc}) -- keeping current baseline")
        try:
            append_stage3_attempt_log_entry(
                state.work_dir,
                {
                    "attempt": attempt_preview,
                    "engine": engine,
                    "candidate_source": None,
                    "engine_error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "status": getattr(exc, "status", None),
                        "runtime_seconds": getattr(exc, "runtime_seconds", None),
                    },
                },
            )
        except Exception as log_exc:
            log_fn(f"optimize_plan: could not append engine-failure attempt-log entry: {log_exc}")
        return planning_checkpoint, False

    solve_seconds = perf_counter() - _solve_started
    timing_key = "stage3_cp_sat_seconds" if engine == "cp_sat" else "stage3_local_search_seconds"
    try:
        manifest = RunManifest(state.work_dir)
        manifest.record_timing(timing_key, solve_seconds)
        if engine == "cp_sat":
            manifest.record_cp_sat_invocation(
                {
                    "source": "explicit_optimize_plan",
                    "cache_hit": False,
                    "status": "solved",
                    "runtime_seconds": round(solve_seconds, 6),
                }
            )
    except Exception as exc:
        log_fn(f"optimize_plan: could not record timing telemetry ({exc})")

    if engine == "cp_sat":
        # issue #310: cache this explicit solve by the same fingerprint the
        # automatic shadow uses, so a *later* attempt that happens to reuse
        # this exact (problem, baseline, request) triple is served from
        # cache instead of re-solving.
        from .stage3_cpsat_cache import cp_sat_cache_key, write_cp_sat_cache_entry
        from ...stage3_shadow import build_shadow_report

        try:
            cache_key = cp_sat_cache_key(problem, baseline_candidate, request)
            report = build_shadow_report(baseline_candidate, new_candidate, problem, engine="cp_sat")
            write_cp_sat_cache_entry(
                state, _current_run_id(state), cache_key, candidate=new_candidate, report=report
            )
        except Exception as exc:
            log_fn(f"optimize_plan: could not cache explicit cp_sat result ({exc})")

    checkpoint = dict(planning_checkpoint)
    checkpoint["plan"] = new_candidate
    checkpoint["source"] = f"stage3_optimizer_v2:{engine}"
    log_fn(
        f"optimize_plan: ran Stage 3 v2 optimizer (engine={engine}, "
        f"iterations={arguments.get('iterations', 4000)}, seed={arguments.get('seed', 0)})"
    )
    state.write_stage(StageName.PLANNING, checkpoint, status=StageStatus.DONE)
    return checkpoint, False


def _run_stage3_pareto_optimize(
    state: "Any",
    cfg: "dict[str, Any]",
    scraping: "dict[str, Any]",
    start: "Any",
    end: "Any",
    arguments: "dict[str, Any] | None",
    log_fn: "Any",
) -> "tuple[list[dict[str, Any]] | None, bool]":
    """Execute an accepted interactive ``optimize_plan`` decision with
    ``arguments.mode == "pareto"`` via the shared multi-objective Stage 3
    search (issue #264 P1 / issue #265 P1), instead of the single-objective
    v2 optimizer :func:`_run_stage3_v2_optimize` runs.

    Unlike that function, this never writes a new Stage 3 checkpoint
    itself -- a Pareto attempt can produce several genuinely different
    non-dominated candidates, and which one (if any) actually gets adopted
    is a later ``apply_candidate`` decision the caller resolves against
    ``candidate_ref`` (see :func:`_emit_stage3_pareto_decision`).

    Returns ``(portfolio, abort)`` -- *portfolio* is
    :func:`~tournament_scheduler.stage3_optimizer.optimize_candidate_pareto`'s
    ``"candidates"`` list (each still missing ``candidate_ref``, assigned by
    the caller), or ``None`` with *abort* ``True`` when there is no baseline
    candidate to search from.
    """
    from ...planning_contract import build_planning_problem, extract_candidate
    from ...pipeline.state import StageName
    from ...stage3_optimizer import optimize_candidate_pareto

    arguments = arguments or {}
    planning_checkpoint = state.read_stage(StageName.PLANNING)
    if not planning_checkpoint:
        log_fn("optimize_plan(pareto): no Stage 3 checkpoint (baseline) to search from -- aborting")
        return None, True
    try:
        baseline_candidate = extract_candidate(planning_checkpoint)
    except ValueError as exc:
        log_fn(f"optimize_plan(pareto): could not read baseline candidate: {exc}")
        return None, True

    problem = build_planning_problem(cfg, scraping, start.date(), end.date())

    result = optimize_candidate_pareto(
        baseline_candidate,
        problem,
        iterations_per_epoch=int(arguments.get("iterations_per_epoch", 2000)),
        seed=int(arguments.get("seed", 0)),
        move_dates=bool(arguments.get("move_dates", False)),
        date_swap_probability=float(arguments.get("date_swap_probability", 0.3)),
        move_dates_within_half=bool(arguments.get("move_dates_within_half", False)),
        move_hosts=bool(arguments.get("move_hosts", False)),
        move_slots=bool(arguments.get("move_slots", False)),
        max_archive_size=int(arguments.get("max_archive_size", 5)),
    )
    log_fn(
        "optimize_plan(pareto): ran multi-objective search "
        f"({result['search_summary']['epochs']} epochs, "
        f"archive_size={result['search_summary']['archive_size']})"
    )
    return result["candidates"], False
