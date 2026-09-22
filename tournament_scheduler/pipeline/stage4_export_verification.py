"""Reconstructs the planning_problem contract for Stage 4's hard-verification gate."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .state import PipelineState, StageName


def _build_export_verification_problem(
    effective_config: dict[str, Any], state: PipelineState
) -> dict[str, Any] | None:
    """Best-effort ``planning_problem`` for the Stage 4 hard-verification gate.

    Reconstructs the same problem contract Stage 3 was given so
    ``verify_candidate`` can check the problem-dependent hard invariants
    (arena interval conflicts, banned/locked dates, excluded host clubs,
    capacity, window bounds) at the actual export chokepoint, not only in
    the evidence bundle after export has already happened. Returns
    ``None`` (degrading to self-consistency-only verification) when the
    inputs can't be reconstructed, matching every other best-effort
    ``problem`` builder in this pipeline (e.g.
    ``cli.pipeline_orchestrator.verification._mid_planning_decision_problem``).
    """
    start_raw = effective_config.get("start_date")
    end_raw = effective_config.get("end_date")
    if not effective_config or not start_raw or not end_raw:
        return None
    try:
        from ..calendar_bookings import project_associations_into_problem
        from ..canonical_banned_dates import project_banned_dates_into_problem
        from ..canonical_baseline import resolve_canonical_state
        from ..canonical_holiday_exceptions import project_exceptions_into_problem
        from ..operator_waivers import load_active_waivers
        from ..planning_contract import build_planning_problem

        start = datetime.strptime(str(start_raw), "%Y-%m-%d")
        end = datetime.strptime(str(end_raw), "%Y-%m-%d")
        scraping_result = state.read_stage(StageName.SCRAPING)
        # Baseline-aware verification (issue #355): when the season has been
        # promoted to canonical state, fold its approval/placement locks into
        # the problem so this export gate hard-rejects a candidate that moved
        # or dropped approved/booked work -- not only self-consistency.
        canonical_state = resolve_canonical_state(effective_config, start.date(), end.date())
        canonical_baseline = canonical_state["baseline"] if canonical_state else None
        problem = build_planning_problem(
            effective_config,
            scraping_result,
            start.date(),
            end.date(),
            waivers=load_active_waivers(state.work_dir),
            canonical_baseline=canonical_baseline,
        )
        if canonical_state:
            # Mirror season_maintenance.load_context's projections so this
            # export chokepoint honours the same canonical banned dates,
            # holiday-date exceptions and calendar-booking associations that
            # season repair/apply-repair already verified the candidate
            # against, instead of re-deriving a stricter problem from
            # nothing but the frozen pipeline config.
            decisions = canonical_state["decisions"]
            problem = project_exceptions_into_problem(problem, decisions)
            problem = project_banned_dates_into_problem(problem, decisions)
            problem = project_associations_into_problem(problem, decisions) or problem
        return problem
    except Exception:
        return None
