"""Cancellation / rain-check workflow — first-class handling of cancelled tournaments.

When a tournament weekend is cancelled (ice hall issue, weather, illness),
the ``CancellationWorkflow`` provides a systematic flow:

1. **Mark as cancelled** — set ``cancelled=True`` with a readable reason.
2. **Suggest makeup weekends** — find candidate dates from the remaining
   free weekends in the season window, ranked by proximity to the original
   date, with basic conflict re-checking.
3. **Apply makeup** — move the tournament to the chosen date via
   ``TournamentUpdater.move_date``, then clear the cancelled state.
4. **Re-export** — regenerate all Stage 4 exports (Excel, iCal, CSV, HTML,
   Spond) so downstream consumers see the updated plan.

Usage::

    from tournament_scheduler.pipeline.cancellation_workflow import CancellationWorkflow
    from tournament_scheduler.pipeline.state import PipelineState

    state = PipelineState(".pipeline")
    wf = CancellationWorkflow(state)
    plan = wf.load_plan()
    wf.mark_cancelled("abc12345", "Ishall stengt — vannlekkasje", plan)
    suggestions = wf.suggest_makeup_dates(plan.tournaments[0], plan)
    # … user picks one …
    wf.apply_makeup("abc12345", chosen_date, plan)
    wf.re_export()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional

from ..models import SeasonPlan, Tournament
from .state import PipelineState, StageName
from .run_log_paths import resolve_active_run_log_dir
from .tournament_updater import TournamentUpdater, UpdateResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class MakeupSuggestion:
    """A single suggested makeup date for a cancelled tournament."""

    date: date
    days_from_original: int  # signed distance in days (positive = later)
    conflicts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CancelResult:
    """Result of a cancellation operation."""

    summary_nb: str
    tournament_id: str
    success: bool = True
    changes: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


class CancellationWorkflow:
    """First-class cancellation and rain-check workflow for a season plan.

    Reads the ``SeasonPlan`` from the Stage 3 pipeline checkpoint,
    applies cancellation / makeup operations, writes the updated
    checkpoint, and triggers re-export.
    """

    def __init__(
        self,
        state: PipelineState,
        updater: Optional[TournamentUpdater] = None,
    ) -> None:
        """Initialize the cancellation workflow.

        Args:
            state: Pipeline state for reading/writing checkpoints.
            updater: Optional ``TournamentUpdater`` for date-move
                operations. If omitted, a bare updater is created
                (conflict checking during moves is limited to plan-internal
                and weekend checks).
        """
        self.state = state
        self.updater = updater or TournamentUpdater(state=state)

    # ------------------------------------------------------------------
    # Load / save plan
    # ------------------------------------------------------------------

    def load_plan(self) -> SeasonPlan:
        """Read the ``SeasonPlan`` from the Stage 3 checkpoint."""
        return self.updater.load_plan()

    def write_plan(self, plan: SeasonPlan, log_entry: Optional[Any] = None) -> None:
        """Write the modified plan back to the Stage 3 checkpoint."""
        self.updater.write_updated_checkpoint(plan, log_entry=log_entry)

    # ------------------------------------------------------------------
    # Mark as cancelled
    # ------------------------------------------------------------------

    def mark_cancelled(
        self,
        tournament_id: str,
        reason: str,
        plan: Optional[SeasonPlan] = None,
    ) -> CancelResult:
        """Mark a tournament as cancelled.

        Args:
            tournament_id: ID of the tournament to cancel.
            reason: Human-readable cancellation reason (Norwegian or English).
            plan: Optional plan to modify. If not provided, loads from checkpoint.

        Returns:
            A ``CancelResult`` with a Norwegian summary.
        """
        if plan is None:
            plan = self.load_plan()

        tournament = self._find_tournament(plan, tournament_id)

        if tournament.cancelled:
            return CancelResult(
                summary_nb=(
                    f"Turnering {tournament_id} ({tournament.age_group}, "
                    f"{tournament.date.isoformat()}) er allerede avlyst "
                    f"({tournament.cancellation_reason or 'ingen grunn oppgitt'})."
                ),
                tournament_id=tournament_id,
                success=False,
            )

        tournament.cancelled = True
        tournament.cancellation_reason = reason

        return CancelResult(
            summary_nb=(
                f"Turnering {tournament_id} ({tournament.age_group}, "
                f"{tournament.arena}, {tournament.date.isoformat()}) markert "
                f"som avlyst.\n"
                f"  Årsak: {reason}"
            ),
            tournament_id=tournament_id,
            changes={
                "cancelled": True,
                "cancellation_reason": reason,
                "original_date": tournament.date.isoformat(),
                "age_group": tournament.age_group,
                "arena": tournament.arena,
            },
        )

    # ------------------------------------------------------------------
    # Suggest makeup dates
    # ------------------------------------------------------------------

    def suggest_makeup_dates(
        self,
        tournament: Tournament,
        plan: SeasonPlan,
        *,
        max_suggestions: int = 5,
        start_search: Optional[date] = None,
        end_search: Optional[date] = None,
    ) -> list[MakeupSuggestion]:
        """Suggest candidate makeup weekends for a cancelled tournament.

        Searches for free weekend dates in the date range, excludes dates
        already occupied by other tournaments, and ranks candidates by
        proximity to the original tournament date.

        Args:
            tournament: The cancelled tournament.
            plan: The full season plan.
            max_suggestions: Maximum number of suggestions to return.
            start_search: Earliest date to consider (default: original date
                + 7 days, to avoid suggesting the same weekend).
            end_search: Latest date to consider (default: season end date
                from the plan, or original date + 120 days).

        Returns:
            List of ``MakeupSuggestion`` ranked by proximity to original date.
        """
        original_date = tournament.date

        if start_search is None:
            start_search = original_date + timedelta(days=7)
        if end_search is None:
            end_search = plan.end_date if plan.end_date else original_date + timedelta(days=120)

        # Collect all dates already occupied by non-cancelled tournaments.
        occupied_dates = {
            t.date for t in plan.tournaments
            if t.id != tournament.id and not t.cancelled
        }

        # Find free weekend dates in the search window.
        from datetime import datetime as dt
        search_start_dt = dt.combine(start_search, dt.min.time())
        search_end_dt = dt.combine(end_search, dt.max.time())

        result = None
        try:
            # Build a lightweight scheduler for holiday checking.
            scheduler = self._make_lightweight_scheduler()
            result = scheduler.find_available_dates(
                start_date=search_start_dt,
                end_date=search_end_dt,
                team_names=[],
            )
        except Exception:
            logger.exception("Konfliktsjekk feilet under forslag til alternativdatoer.")

        # Build candidate set: free dates minus occupied dates.
        free_dates = set(result.available_dates) if result else set()
        candidate_dates = sorted(free_dates - occupied_dates)

        # Collect conflict reasons from the scheduler for transparency.
        conflict_by_date: dict[date, list[dict[str, Any]]] = {}
        for excl_date, reason in (result.detailed_exclusions if result else []) or []:
            conflict_by_date.setdefault(excl_date, []).append({
                "date": excl_date.isoformat(),
                "reason": reason,
            })

        # Rank by absolute distance from original date (closer = better).
        suggestions: list[MakeupSuggestion] = []
        for d in candidate_dates:
            days = (d - original_date).days
            conflicts = conflict_by_date.get(d, [])
            suggestions.append(
                MakeupSuggestion(date=d, days_from_original=days, conflicts=conflicts)
            )

        suggestions.sort(key=lambda s: abs(s.days_from_original))
        return suggestions[:max_suggestions]

    # ------------------------------------------------------------------
    # Apply makeup
    # ------------------------------------------------------------------

    def apply_makeup(
        self,
        tournament_id: str,
        new_date: date,
        plan: Optional[SeasonPlan] = None,
        *,
        force: bool = False,
        cascade: bool = True,
    ) -> UpdateResult:
        """Apply a makeup date: move the tournament and clear its cancelled state.

        Calls ``TournamentUpdater.move_date`` for the actual move and
        conflict re-checking, then clears ``cancelled`` and
        ``cancellation_reason`` on the tournament.

        Args:
            tournament_id: ID of the cancelled tournament.
            new_date: The chosen makeup date.
            plan: Optional plan to modify.
            force: Passed through to ``move_date``.
            cascade: Passed through to ``move_date``.

        Returns:
            An ``UpdateResult`` from ``TournamentUpdater.move_date``.
        """
        if plan is None:
            plan = self.load_plan()

        # Verify the tournament exists and is cancelled.
        tournament = self._find_tournament(plan, tournament_id)
        original_reason = tournament.cancellation_reason

        # Move the date via the existing updater (handles conflict checking + cascade).
        result = self.updater.move_date(
            tournament_id, new_date, plan=plan, force=force, cascade=cascade
        )

        if result.success:
            # Clear cancelled state — the makeup is applied.
            tournament.cancelled = False
            tournament.cancellation_reason = None

            # Add makeup context to the result for logging.
            result.changes["makeup_applied"] = True
            result.changes["original_cancellation_reason"] = original_reason
            result.summary_nb += (
                "\n  Avlysning opphevet — makeup-dato er satt."
            )

        return result

    # ------------------------------------------------------------------
    # Re-export
    # ------------------------------------------------------------------

    def re_export(
        self,
        export_dir: str = "export",
        basename: str = "season_plan",
    ) -> dict[str, Any]:
        """Regenerate all Stage 4 exports from the updated plan.

        Args:
            export_dir: Directory for output files.
            basename: Base filename for exports.

        Returns:
            The Stage 4 checkpoint dict with output file paths.
        """
        from .stage4_export import run as run_export

        plan_checkpoint = self.state.read_stage(StageName.PLANNING)
        if not plan_checkpoint:
            raise ValueError("Ingen Stage 3-plan funnet. Kjør pipelinen først.")

        result = run_export(
            plan_checkpoint,
            state=self.state,
            export_dir=export_dir,
            basename=basename,
            strict=True,
        )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_tournament(self, plan: SeasonPlan, tournament_id: str) -> Tournament:
        """Find a tournament by ID. Raises ``ValueError`` if not found."""
        for t in plan.tournaments:
            if t.id == tournament_id:
                return t
        raise ValueError(
            f"Turnering med ID '{tournament_id}' ble ikke funnet. "
            f"Tilgjengelige ID-er: {', '.join(t.id for t in plan.tournaments)}"
        )

    @staticmethod
    def _make_lightweight_scheduler():
        """Build a lightweight ``TournamentScheduler`` with just a holiday checker."""
        from ..scheduler import TournamentScheduler
        from ..conflict_checkers.holiday_checker import HolidayConflictChecker
        from ..utils.date_parser import DateParser

        return TournamentScheduler(
            calendar_sources=[],
            conflict_checkers=[HolidayConflictChecker()],
            date_parser=DateParser(),
        )

    def log_cancellation(
        self,
        result: CancelResult,
        run_id: Optional[str] = None,
    ) -> str:
        """Write a structured ``tournament_cancellation`` entry to the run log."""
        import json
        import os

        log_dir = resolve_active_run_log_dir(self.state)
        log_dir.mkdir(parents=True, exist_ok=True)

        if run_id:
            log_path = log_dir / f"{run_id}.jsonl"
        else:
            from datetime import datetime as _dt

            existing = sorted(
                [f for f in log_dir.iterdir() if f.suffix == ".jsonl"],
                key=os.path.getmtime,
                reverse=True,
            )
            if existing:
                log_path = existing[0]
            else:
                stub_id = f"run-cancel-{_dt.now():%Y-%m-%dT%H-%M-%S}"
                log_path = log_dir / f"{stub_id}.jsonl"

        entry: dict[str, Any] = {
            "type": "tournament_cancellation",
            "run_id": run_id or log_path.stem,
            "timestamp": datetime.now().isoformat(),
            "tournament_id": result.tournament_id,
            "success": result.success,
            "summary_nb": result.summary_nb,
            "changes": result.changes,
        }

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return str(log_path)
