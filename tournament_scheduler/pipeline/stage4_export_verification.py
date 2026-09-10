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
        from ..planning_contract import build_planning_problem

        start = datetime.strptime(str(start_raw), "%Y-%m-%d")
        end = datetime.strptime(str(end_raw), "%Y-%m-%d")
        scraping_result = state.read_stage(StageName.SCRAPING)
        return build_planning_problem(effective_config, scraping_result, start.date(), end.date())
    except Exception:
        return None
