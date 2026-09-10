"""Tournament updater — targeted modifications to an existing season plan.

Supports two operations after a season plan has been generated:

- **Team drop:** Remove a team from a tournament (e.g. they cannot attend)
  and regenerate the round-robin game schedule for the remaining teams.
- **Date move:** Change a tournament to a different weekend, re-running
  conflict checking and optionally cascading to displaced tournaments.

Both operations read a ``SeasonPlan`` from a Stage 3 checkpoint, apply
the modification, write an updated checkpoint, and log the change to the
active export folder for traceability via ``rvv-miniputt logs``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..club_registry import CLUB_REGISTRY, club_for_arena as _club_for_arena
from ..models import SeasonPlan, Team, Tournament
from ..scheduler import TournamentScheduler
from ..season_planner import SeasonPlanner
from .state import PipelineState, StageName, StageStatus
from .run_log_paths import resolve_active_run_log_dir
from .stage3_planning import _plan_to_dict
from .stage3_helpers import _tournament_from_dict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TournamentUpdateError(Exception):
    """Raised when a tournament update operation fails."""


class TournamentValidationError(TournamentUpdateError):
    """Raised when input validation fails for a tournament update."""


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class UpdateResult:
    """Result of a tournament update operation."""

    summary_nb: str  # Norwegian-language summary
    tournament_id: str
    operation: str  # "team_drop" or "date_move"
    changes: dict[str, Any] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    cascade: list[dict[str, Any]] = field(default_factory=list)
    success: bool = True
    post_patch_warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Updater
# ---------------------------------------------------------------------------


class TournamentUpdater:
    """Apply targeted updates to a ``SeasonPlan`` loaded from a checkpoint.

    Usage::

        updater = TournamentUpdater(state=PipelineState(".pipeline"))
        result = updater.drop_team(tournament_id="abc12345", team_label="Jar 1")
        updater.write_updated_checkpoint(result)
    """

    def __init__(
        self,
        state: PipelineState,
        scheduler: Optional[TournamentScheduler] = None,
    ) -> None:
        """Initialize the updater.

        Args:
            state: Pipeline state to read/write checkpoints.
            scheduler: Optional scheduler for date-move conflict re-checking.
                If omitted, date-move operations will skip conflict checking
                (conflict report will be empty).
        """
        self.state = state
        self.scheduler = scheduler
        self._log_dir: Path | None = None

    # ------------------------------------------------------------------
    # Load / save plan
    # ------------------------------------------------------------------

    def load_plan(self) -> SeasonPlan:
        """Read the ``SeasonPlan`` from the Stage 3 checkpoint."""
        data = self.state.read_stage(StageName.PLANNING)
        if not data or "plan" not in data:
            raise ValueError("Ingen gyldig Stage 3-plan funnet. Kjør pipelinen først.")

        plan_data = data["plan"]
        tournaments = [_tournament_from_dict(t) for t in plan_data.get("tournaments", [])]

        plan = SeasonPlan(
            tournaments=tournaments,
            start_date=_parse_date(plan_data.get("start_date")),
            end_date=_parse_date(plan_data.get("end_date")),
            manual_adjustments=dict(plan_data.get("manual_adjustments", {})),
            arena_day_collisions=list(plan_data.get("arena_day_collisions", [])),
            pairwise_matchup_score=float(plan_data.get("pairwise_matchup_score", 0.0)),
            diversity_score=float(plan_data.get("diversity_score", 0.0)),
            month_balance_score=float(plan_data.get("month_balance_score", 0.0)),
            fairness_gate=dict(plan_data.get("fairness_gate") or {}),
            game_count_spread=float(plan_data.get("game_count_spread", 0.0)),
            game_count_spread_by_age_group=dict(plan_data.get("game_count_spread_by_age_group") or {}),
            team_game_counts=dict(plan_data.get("team_game_counts") or {}),
        )
        return plan

    def plan_to_dict(self, plan: SeasonPlan) -> dict[str, Any]:
        """Convert a ``SeasonPlan`` back to a checkpoint-ready dict."""
        return _plan_to_dict(plan)

    # ------------------------------------------------------------------
    # Tournament lookup
    # ------------------------------------------------------------------

    def find_tournament(self, plan: SeasonPlan, tournament_id: str) -> Tournament:
        """Find a tournament by ID in the plan. Raises ``ValueError`` if not found."""
        for t in plan.tournaments:
            if t.id == tournament_id:
                return t
        raise ValueError(
            f"Turnering med ID '{tournament_id}' ble ikke funnet. "
            f"Tilgjengelige ID-er: {', '.join(t.id for t in plan.tournaments)}"
        )

    def find_tournament_index(
        self, plan: SeasonPlan, tournament_id: str
    ) -> int:
        """Return the index of a tournament by ID."""
        for i, t in enumerate(plan.tournaments):
            if t.id == tournament_id:
                return i
        raise ValueError(f"Turnering med ID '{tournament_id}' ble ikke funnet.")

    # ------------------------------------------------------------------
    # Operation: drop a team
    # ------------------------------------------------------------------

    def drop_team(
        self,
        tournament_id: str,
        team_label: str,
        plan: Optional[SeasonPlan] = None,
    ) -> UpdateResult:
        """Remove a team from a tournament and regenerate round-robin games.

        Args:
            tournament_id: ID of the tournament to modify.
            team_label: Label of the team to remove (e.g. ``"Jar 1"``).
            plan: Optional plan to modify. If not provided, loads from checkpoint.

        Returns:
            An ``UpdateResult`` with a Norwegian summary of what changed.
        """
        if plan is None:
            plan = self.load_plan()

        tournament = self.find_tournament(plan, tournament_id)
        original_team_count = len(tournament.teams)
        original_game_count = len(tournament.games)

        # Find and remove the team
        matching_teams = [t for t in tournament.teams if t.label == team_label]
        if not matching_teams:
            return UpdateResult(
                summary_nb=f"Lag '{team_label}' ble ikke funnet i turnering {tournament_id} "
                           f"({tournament.age_group}, {tournament.date.isoformat()}). "
                           f"Tilgjengelige lag: {', '.join(t.label for t in tournament.teams)}",
                tournament_id=tournament_id,
                operation="team_drop",
                success=False,
            )

        tournament.teams = [t for t in tournament.teams if t.label != team_label]

        if len(tournament.teams) < 2:
            return UpdateResult(
                summary_nb=f"Kan ikke droppe laget — turneringen har kun {len(tournament.teams)} lag igjen. "
                           f"En round-robin-turnering trenger minst 2 lag.",
                tournament_id=tournament_id,
                operation="team_drop",
                changes={
                    "team_removed": team_label,
                    "remaining_teams": [t.label for t in tournament.teams],
                },
                success=False,
            )

        # Regenerate round-robin games
        # Ensure host club's team is first so home team assignment is correct.
        _home_club = _club_for_arena(tournament.arena) or tournament.host_club
        _host_t = [t for t in tournament.teams if t.club == _home_club]
        _other_t = [t for t in tournament.teams if t.club != _home_club]
        ordered_teams = (_host_t + _other_t) if _host_t else list(tournament.teams)
        parallel_games = self._infer_parallel_games(tournament)
        tournament.games = SeasonPlanner.generate_round_robin_games(
            ordered_teams, parallel_games
        )

        return UpdateResult(
            summary_nb=(
                f"Droppet {team_label} fra turnering {tournament_id} "
                f"({tournament.age_group}, {tournament.date.isoformat()}).\n"
                f"  Lag: {original_team_count} → {len(tournament.teams)}\n"
                f"  Kamper: {original_game_count} → {len(tournament.games)}\n"
                f"  Gjenvaerende lag: {', '.join(t.label for t in tournament.teams)}"
            ),
            tournament_id=tournament_id,
            operation="team_drop",
            changes={
                "team_removed": team_label,
                "original_team_count": original_team_count,
                "new_team_count": len(tournament.teams),
                "original_game_count": original_game_count,
                "new_game_count": len(tournament.games),
                "remaining_teams": [t.label for t in tournament.teams],
                "parallel_games_used": parallel_games,
            },
            success=True,
        )

    # ------------------------------------------------------------------
    # Operation: move date
    # ------------------------------------------------------------------

    def move_date(
        self,
        tournament_id: str,
        new_date: date,
        plan: Optional[SeasonPlan] = None,
        *,
        force: bool = False,
        cascade: bool = True,
    ) -> UpdateResult:
        """Move a tournament to a different date.

        Args:
            tournament_id: ID of the tournament to move.
            new_date: The new date (weekend only — Saturday/Sunday is
                enforced at import time; callers should validate).
            plan: Optional plan to modify. If not provided, loads from
                checkpoint.
            force: If ``True``, apply the move even when conflicts are
                detected on the new date (conflicts are still reported).
            cascade: If ``True`` and the new date is occupied by another
                tournament in the plan, attempt to swap dates (the displaced
                tournament takes the original tournament's old date).

        Returns:
            An ``UpdateResult`` with a Norwegian summary and conflict report.
        """
        if plan is None:
            plan = self.load_plan()

        tournament = self.find_tournament(plan, tournament_id)
        original_date = tournament.date

        conflicts: list[dict[str, Any]] = []
        cascade_changes: list[dict[str, Any]] = []

        # --- Conflict checking (always runs weekend + plan-internal checks;
        # scheduler-based checks only when a TournamentScheduler is available) ---
        date_conflicts = self._check_date_conflicts(new_date, tournament, plan)
        conflicts.extend(date_conflicts)

        if conflicts and not force:
            conflict_lines = []
            for c in conflicts:
                conflict_lines.append(f"  - {c.get('reason', 'Ukjent konflikt')}")
            conflict_str = "\n".join(conflict_lines)

            return UpdateResult(
                summary_nb=(
                    f"Kan ikke flytte turnering {tournament_id} til "
                    f"{new_date.isoformat()} — følgende konflikter ble funnet:\n"
                    f"{conflict_str}\n\n"
                    f"Bruk --force for å flytte uansett."
                ),
                tournament_id=tournament_id,
                operation="date_move",
                changes={
                    "original_date": original_date.isoformat(),
                    "proposed_date": new_date.isoformat(),
                },
                conflicts=conflicts,
                success=False,
            )

        # --- Cascade handling: check if another tournament occupies the new date ---
        if cascade:
            displaced = [t for t in plan.tournaments if t.date == new_date and t.id != tournament_id]
            for d in displaced:
                # Swap dates: the displaced tournament gets the original date
                old_displaced_date = d.date
                d.date = original_date
                cascade_changes.append({
                    "displaced_tournament_id": d.id,
                    "original_date": old_displaced_date.isoformat(),
                    "new_date": original_date.isoformat(),
                    "age_group": d.age_group,
                })

        # Apply the date move
        tournament.date = new_date

        # Re-sort tournaments by date
        plan.tournaments.sort(key=lambda t: t.date)

        change_details: dict[str, Any] = {
            "original_date": original_date.isoformat(),
            "new_date": new_date.isoformat(),
            "age_group": tournament.age_group,
            "arena": tournament.arena,
        }

        summary_parts = [
            f"Flyttet turnering {tournament_id} ({tournament.age_group}, {tournament.arena})",
            f"  Dato: {original_date.isoformat()} → {new_date.isoformat()}",
        ]

        if cascade_changes:
            change_details["cascade"] = cascade_changes
            summary_parts.append("  Kaskade:")
            for cc in cascade_changes:
                summary_parts.append(
                    f"    Turnering {cc['displaced_tournament_id']} "
                    f"({cc['age_group']}): {cc['original_date']} → {cc['new_date']}"
                )

        if conflicts:
            change_details["conflicts_ignored"] = conflicts
            summary_parts.append("  (Konflikter ignorert pga. --force)")

        return UpdateResult(
            summary_nb="\n".join(summary_parts),
            tournament_id=tournament_id,
            operation="date_move",
            changes=change_details,
            conflicts=conflicts,
            cascade=cascade_changes,
            success=True,
        )

    # ------------------------------------------------------------------
    # Operation: set host club
    # ------------------------------------------------------------------

    def set_host_club(
        self,
        tournament_id: str,
        host_club: str,
        plan: Optional[SeasonPlan] = None,
    ) -> UpdateResult:
        """Change a tournament's host club (and arena when known)."""
        if plan is None:
            plan = self.load_plan()

        tournament = self.find_tournament(plan, tournament_id)
        original_host = tournament.host_club
        original_arena = tournament.arena
        tournament.host_club = host_club

        arena = self._arena_for_club(host_club)
        if arena:
            tournament.arena = arena

        return UpdateResult(
            summary_nb=(
                f"Oppdatert host for turnering {tournament_id} ({tournament.age_group})\n"
                f"  Vertsklubb: {original_host or 'ukjent'} → {host_club}\n"
                f"  Hall: {original_arena} → {tournament.arena}"
            ),
            tournament_id=tournament_id,
            operation="host_change",
            changes={
                "original_host_club": original_host,
                "new_host_club": host_club,
                "original_arena": original_arena,
                "new_arena": tournament.arena,
            },
            success=True,
        )

    # ------------------------------------------------------------------
    # Operation: add a tournament
    # ------------------------------------------------------------------

    def add_tournament(
        self,
        plan: SeasonPlan,
        age_group: str,
        team_labels: list[str],
        tournament_date: date,
        arena: str,
        host_club: Optional[str] = None,
        *,
        parallel_games: Optional[int] = None,
        force: bool = False,
    ) -> UpdateResult:
        """Add a new tournament to the season plan.

        Builds a new ``Tournament`` with the given parameters, resolves
        ``Team`` objects from the plan's existing roster, generates
        round-robin games, runs optional conflict checking, and appends
        the tournament to *plan*.

        Args:
            plan: The season plan to modify.
            age_group: Age group for the tournament (e.g. ``"U10"``).
            team_labels: Labels of teams to invite (e.g. ``["Jar 1", "Kongsberg 1"]``).
            tournament_date: Date of the tournament (weekend expected).
            arena: Host arena name (e.g. ``"Kongsberghallen"``).
            host_club: Club hosting the tournament. If ``None``, inferred
                from *arena* by scanning plan teams.
            parallel_games: Number of parallel games/rinks.
                If ``None``, inferred from team count.
            force: If ``True``, skip conflict checking (e.g. when the
                user is certain the date is free).

        Returns:
            An ``UpdateResult`` with a Norwegian summary.
        """
        # Resolve teams by scanning plan tournaments for matching labels.
        known_teams: dict[str, Team] = {}
        for t in plan.tournaments:
            for team in t.teams:
                if team.label not in known_teams:
                    known_teams[team.label] = team

        teams: list[Team] = []
        missing: list[str] = []
        for label in team_labels:
            stripped = label.strip()
            if stripped in known_teams:
                teams.append(known_teams[stripped])
            else:
                missing.append(stripped)

        if missing:
            return UpdateResult(
                summary_nb=(
                    f"Kan ikke legge til turnering — følgende lag ble ikke funnet "
                    f"i sesongplanen: {', '.join(missing)}.\n"
                    f"Kjente lag: {', '.join(sorted(known_teams.keys()))}"
                ),
                tournament_id="",
                operation="add_tournament",
                changes={"missing_teams": missing},
                success=False,
            )

        if len(teams) < 2:
            return UpdateResult(
                summary_nb=(
                    f"Kan ikke legge til turnering — trenger minst 2 lag, "
                    f"fikk {len(teams)}."
                ),
                tournament_id="",
                operation="add_tournament",
                changes={"team_count": len(teams)},
                success=False,
            )

        # Validate all teams are from the same age group.
        mismatched = [t for t in teams if t.age_group != age_group]
        if mismatched:
            return UpdateResult(
                summary_nb=(
                    f"Kan ikke legge til turnering — følgende lag har feil aldersgruppe "
                    f"(forventet {age_group}): {', '.join(t.label + ' (' + t.age_group + ')' for t in mismatched)}"
                ),
                tournament_id="",
                operation="add_tournament",
                changes={"mismatched_age_groups": [t.label for t in mismatched]},
                success=False,
            )

        # Resolve host_club from teams if not provided.
        resolved_host: Optional[str] = host_club
        if resolved_host is None:
            for team in teams:
                if team.club:
                    resolved_host = team.club
                    break

        # Conflict checking (unless forced).
        conflicts: list[dict[str, Any]] = []
        if not force:
            # Build a lightweight tournament stub for conflict checking.
            stub = Tournament(
                date=tournament_date,
                arena=arena,
                age_group=age_group,
                teams=teams,
            )
            conflicts = self._check_date_conflicts(tournament_date, stub, plan)

        if conflicts and not force:
            conflict_lines = []
            for c in conflicts:
                conflict_lines.append(f"  - {c.get('reason', 'Ukjent konflikt')}")
            conflict_str = "\n".join(conflict_lines)
            return UpdateResult(
                summary_nb=(
                    f"Kan ikke legge til turnering på {tournament_date.isoformat()} — "
                    f"følgende konflikter ble funnet:\n{conflict_str}\n\n"
                    f"Bruk --force for å legge til uansett."
                ),
                tournament_id="",
                operation="add_tournament",
                changes={
                    "age_group": age_group,
                    "team_labels": team_labels,
                    "date": tournament_date.isoformat(),
                    "arena": arena,
                },
                conflicts=conflicts,
                success=False,
            )

        # Determine parallel games.
        pg = parallel_games or self._infer_parallel_games_from_count(len(teams))

        # Build the tournament.
        import uuid
        new_id = uuid.uuid4().hex[:8]

        # Reorder teams so the arena-owning club is first (correct home team).
        _home_club_new = _club_for_arena(arena) or resolved_host
        _host_t_new = [t for t in teams if t.club == _home_club_new]
        _other_t_new = [t for t in teams if t.club != _home_club_new]
        ordered_teams_new = (_host_t_new + _other_t_new) if _host_t_new else list(teams)

        tournament = Tournament(
            id=new_id,
            date=tournament_date,
            arena=arena,
            age_group=age_group,
            host_club=resolved_host,
            teams=ordered_teams_new,
        )
        tournament.games = SeasonPlanner.generate_round_robin_games(
            tournament.teams, pg
        )

        plan.tournaments.append(tournament)
        plan.tournaments.sort(key=lambda t: t.date)

        return UpdateResult(
            summary_nb=(
                f"Lagt til turnering {new_id} ({age_group}, {arena})\n"
                f"  Dato: {tournament_date.isoformat()}\n"
                f"  Lag ({len(teams)}): {', '.join(t.label for t in teams)}\n"
                f"  Kamper ({len(tournament.games)}): {pg} parallelle baner"
            ),
            tournament_id=new_id,
            operation="add_tournament",
            changes={
                "age_group": age_group,
                "team_labels": [t.label for t in teams],
                "team_count": len(teams),
                "date": tournament_date.isoformat(),
                "arena": arena,
                "host_club": resolved_host,
                "parallel_games": pg,
                "game_count": len(tournament.games),
            },
            conflicts=conflicts,
            success=True,
        )

    # ------------------------------------------------------------------
    # Operation: remove a tournament
    # ------------------------------------------------------------------

    def remove_tournament(
        self,
        plan: SeasonPlan,
        tournament_id: str,
    ) -> UpdateResult:
        """Remove an entire tournament from the season plan.

        Unlike cancellation (which marks a tournament as cancelled but keeps
        it in the plan), this operation deletes the tournament entirely.

        Args:
            plan: The season plan to modify.
            tournament_id: ID of the tournament to remove.

        Returns:
            An ``UpdateResult`` with a Norwegian summary of what was removed.
        """
        tournament = self.find_tournament(plan, tournament_id)
        idx = self.find_tournament_index(plan, tournament_id)

        removed_data = {
            "id": tournament.id,
            "date": tournament.date.isoformat(),
            "age_group": tournament.age_group,
            "arena": tournament.arena,
            "team_count": len(tournament.teams),
            "teams": [t.label for t in tournament.teams],
            "game_count": len(tournament.games),
            "host_club": tournament.host_club,
        }

        del plan.tournaments[idx]

        return UpdateResult(
            summary_nb=(
                f"Fjernet turnering {tournament_id} ({tournament.age_group}, "
                f"{tournament.arena}, {tournament.date.isoformat()}).\n"
                f"  Lag: {', '.join(t.label for t in tournament.teams)}"
            ),
            tournament_id=tournament_id,
            operation="remove_tournament",
            changes=removed_data,
            success=True,
        )

    # ------------------------------------------------------------------
    # Checkpoint write
    # ------------------------------------------------------------------

    def write_updated_checkpoint(
        self,
        plan: SeasonPlan,
        log_entry: Optional[UpdateResult] = None,
    ) -> None:
        """Write an updated Stage 3 checkpoint with the modified plan.

        Args:
            plan: The modified season plan.
            log_entry: Optional update result to include in checkpoint metadata.
        """
        existing = self.state.read_stage(StageName.PLANNING)
        plan_dict = _plan_to_dict(plan)

        # Preserve existing metadata (LLM confidence etc.) and add update info
        checkpoint: dict[str, Any] = {
            "plan": plan_dict,
            "llm_confidence": existing.get("llm_confidence", 0.0),
            "llm_reasoning": existing.get("llm_reasoning", ""),
            "attempts": existing.get("attempts", 1),
            "llm_skipped": existing.get("llm_skipped", True),
        }

        if log_entry:
            checkpoint["last_update"] = {
                "tournament_id": log_entry.tournament_id,
                "operation": log_entry.operation,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "changes": log_entry.changes,
                "conflicts": log_entry.conflicts,
                "cascade": log_entry.cascade,
                "success": log_entry.success,
            }

        self.state.write_stage(StageName.PLANNING, checkpoint, status=StageStatus.DONE)

    def persist_update(self, plan: SeasonPlan, result: UpdateResult) -> str:
        """Persist a modified plan and append its structured log entry.

        Returns the log path written by :meth:`log_update`.
        """
        self.write_updated_checkpoint(plan, log_entry=result)
        return self.log_update(result)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log_update(self, result: UpdateResult, run_id: Optional[str] = None) -> str:
        """Write a structured ``tournament_update`` entry to the run log.

        Args:
            result: The update result to log.
            run_id: An optional run ID. If not provided, the most recent
                log file in the active export tree is reused when present,
                otherwise a new run file is started.

        Returns:
            The path to the log file that was appended to.
        """
        log_dir = self._log_dir or resolve_active_run_log_dir(self.state)
        log_dir.mkdir(parents=True, exist_ok=True)

        if run_id:
            log_path = log_dir / f"{run_id}.jsonl"
        else:
            # Find the most recent log file, or create a stub
            existing = sorted(
                [f for f in log_dir.iterdir() if f.suffix == ".jsonl"],
                key=os.path.getmtime,
                reverse=True,
            )
            if existing:
                log_path = existing[0]
            else:
                stub_id = f"run-update-{datetime.now():%Y-%m-%dT%H-%M-%S}"
                log_path = log_dir / f"{stub_id}.jsonl"

        entry: dict[str, Any] = {
            "type": "tournament_update",
            "run_id": run_id or log_path.stem,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "tournament_id": result.tournament_id,
            "operation": result.operation,
            "success": result.success,
            "summary_nb": result.summary_nb,
            "changes": result.changes,
            "conflicts": result.conflicts,
            "cascade": result.cascade,
        }

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return str(log_path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_date_conflicts(
        self,
        new_date: date,
        tournament: Tournament,
        plan: SeasonPlan,
    ) -> list[dict[str, Any]]:
        """Run conflict checkers on the proposed new date.

        Uses the configured ``self.scheduler`` (``TournamentScheduler``) to
        check for calendar and holiday conflicts on the specific date.
        Returns a list of conflict dictionaries with keys ``date``,
        ``checker``, and ``reason``.
        """
        conflicts: list[dict[str, Any]] = []

        # Check if the new date is a weekend
        if new_date.weekday() not in (5, 6):
            conflicts.append({
                "date": new_date.isoformat(),
                "checker": "date_validator",
                "reason": f"{new_date.isoformat()} er ikke en helgedag (lordag/sondag).",
            })

        # Check plan-internal conflicts: another tournament already on this date
        same_date = [
            t for t in plan.tournaments
            if t.date == new_date and t.id != tournament.id
        ]
        for other in same_date:
            conflicts.append({
                "date": new_date.isoformat(),
                "checker": "plan_internal",
                "reason": (
                    f"Turnering {other.id} ({other.age_group}, {other.arena}) "
                    f"er allerede planlagt pa {new_date.isoformat()}."
                ),
            })

        # Scheduler: run find_available_dates on a narrow window to detect
        # calendar/holiday conflicts for this specific date.
        if self.scheduler:
            from datetime import datetime as _dt, timedelta

            # Run the scheduler on a 3-day window centred on new_date so
            # conflict checkers that look at surrounding days also fire.
            window_start = _dt.combine(new_date - timedelta(days=1), _dt.min.time())
            window_end = _dt.combine(new_date + timedelta(days=1), _dt.max.time())

            try:
                sched_result = self.scheduler.find_available_dates(
                    start_date=window_start,
                    end_date=window_end,
                    team_names=[t.label for t in tournament.teams],
                )
                if new_date in sched_result.excluded_dates:
                    for excl_date, reason in sched_result.detailed_exclusions:
                        if excl_date == new_date:
                            conflicts.append({
                                "date": new_date.isoformat(),
                                "checker": "scheduler",
                                "reason": reason,
                            })
            except Exception as exc:
                conflicts.append({
                    "date": new_date.isoformat(),
                    "checker": "scheduler",
                    "reason": f"Feil ved konfliktsjekk: {exc}",
                })

        return conflicts

    def _infer_parallel_games(self, tournament: Tournament) -> int:
        """Infer the parallel-games count for a tournament.

        Uses the number of teams to estimate the original parallel-games
        setting when regenerating round-robin schedules.
        """
        return self._infer_parallel_games_from_count(len(tournament.teams))

    @staticmethod
    def _arena_for_club(club: str) -> Optional[str]:
        """Return the known home arena for `club`, if available."""
        entry = CLUB_REGISTRY.get(club)
        arena = getattr(entry, "arena", None) if entry else None
        return arena or None

    @staticmethod
    def _infer_parallel_games_from_count(team_count: int) -> int:
        """Infer parallel-games count from a raw team count."""
        if team_count < 3:
            return 1
        return max(1, min(4, team_count // 2))


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _parse_date(value: Any) -> Optional[date]:
    """Parse an ISO date string or return ``None``."""
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Oppdater en turnering i sesongplanen"
    )
    parser.add_argument("--work-dir", default=".pipeline",
                        help="Pipeline work directory")
    parser.add_argument("--tournament-id", required=True,
                        help="ID for turneringen som skal oppdateres")
    parser.add_argument("--drop-team", type=str, default=None,
                        help="Fjern et lag fra turneringen (team label)")
    parser.add_argument("--new-date", type=str, default=None,
                        help="Ny dato for turneringen (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true",
                        help="Tving flytting selv om det er konflikter")
    parser.add_argument("--no-cascade", action="store_true",
                        help="Ikke kaskader til andre turneringer ved datoflytting")

    cli_args = parser.parse_args()

    try:
        if not cli_args.drop_team and not cli_args.new_date:
            raise TournamentValidationError("Angi --drop-team eller --new-date.")

        if cli_args.drop_team and cli_args.new_date:
            raise TournamentValidationError("Kan ikke bruke --drop-team og --new-date samtidig.")

        state_obj = PipelineState(cli_args.work_dir)
        updater = TournamentUpdater(state=state_obj)

        try:
            season_plan = updater.load_plan()
        except ValueError as exc:
            raise TournamentUpdateError(str(exc)) from exc

        result: Optional[UpdateResult] = None

        if cli_args.drop_team:
            result = updater.drop_team(cli_args.tournament_id, cli_args.drop_team, plan=season_plan)
        elif cli_args.new_date:
            try:
                parsed_date = date.fromisoformat(cli_args.new_date)
            except ValueError:
                raise TournamentValidationError(
                    f"Ugyldig datoformat '{cli_args.new_date}'. Bruk YYYY-MM-DD."
                )
            result = updater.move_date(
                cli_args.tournament_id, parsed_date,
                plan=season_plan,
                force=cli_args.force,
                cascade=not cli_args.no_cascade,
            )

        if not result:
            raise TournamentUpdateError("Ingen operasjon utført.")

        if result.success:
            updater.write_updated_checkpoint(season_plan, log_entry=result)
            log_path = updater.log_update(result)
            print(result.summary_nb)
            print(f"\nPlan oppdatert i {cli_args.work_dir}/stage3_planning.json")
            print(f"Logget til {log_path}")
            sys.exit(0)
        else:
            print(result.summary_nb, file=sys.stderr)
            sys.exit(1)

    except TournamentUpdateError as exc:
        print(f"Feil: {exc}", file=sys.stderr)
        sys.exit(1)
