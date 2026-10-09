"""
Verdict workflow for the RVV Miniputt pipeline.

This module provides the VerdictWorkflow class for managing verdicts
on tournaments in the pipeline.
"""

from __future__ import annotations

from typing import Any

from ..models import SeasonPlan, Tournament
from ..pipeline.state import PipelineState, StageName
from ..serialization.season_plan import season_plan_from_dict


class VerdictWorkflow:
    """Workflow for recording verdicts on tournaments."""

    def __init__(self, state: PipelineState) -> None:
        self.state = state

    def load_plan(self) -> SeasonPlan:
        """Load the SeasonPlan from the pipeline state."""
        checkpoint = self.state.read_stage(StageName.PLANNING)
        if not isinstance(checkpoint, dict) or not checkpoint:
            raise ValueError("No plan checkpoint found in pipeline state")
        plan_dict = checkpoint.get("plan")
        if not isinstance(plan_dict, dict):
            raise ValueError("Plan checkpoint missing 'plan' key")
        return season_plan_from_dict(plan_dict)

    def _find_tournament(self, plan: SeasonPlan, tournament_id: str) -> Tournament:
        """Find a tournament by ID in the plan."""
        for tournament in plan.tournaments:
            if tournament.id == tournament_id:
                return tournament
        raise ValueError(f"Tournament {tournament_id!r} not found in plan")

    def write_plan(self, plan: SeasonPlan, log_entry: str = "") -> None:
        """Write the plan back to the pipeline state."""
        from ..serialization.season_plan import season_plan_to_dict

        checkpoint = {"plan": season_plan_to_dict(plan)}
        self.state.write_stage(StageName.PLANNING, checkpoint, status="DONE")
        if log_entry:
            self.state.write_stage(StageName.PLANNING, {"log": log_entry}, status="DONE")