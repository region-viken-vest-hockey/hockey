"""Manual organizer adjustment loop for the final season plan.

Applies operator-provided locks/pins/host preferences to an already-generated
Stage 3 plan, then recalculates plan metadata and rechecks conflicts before
export.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from ..hosting_coverage import hosting_coverage_matrix, unresolved_from_matrix
from ..models import SeasonPlan, Tournament
from ..warnings import (
    scan_arena_day_collision_warnings,
    scan_game_count_warnings,
    scan_hosting_warnings,
    scan_month_load_warnings,
)
from .cancellation_workflow import CancellationWorkflow
from .stage1_config import load_effective_config
from .stage3_helpers import _build_club_arenas, _build_events_by_club, _build_parallel_games, _build_roster, _build_round_length, _make_planner
from .state import PipelineState, StageName
from .tournament_updater import TournamentUpdater, UpdateResult

logger = logging.getLogger(__name__)


@dataclass
class _NormalizedAdjustments:
    locked_dates: set[date] = field(default_factory=set)
    banned_dates: set[date] = field(default_factory=set)
    forced_host_clubs: list[str] = field(default_factory=list)
    excluded_host_clubs: set[str] = field(default_factory=set)
    pinned_tournament_ids: set[str] = field(default_factory=set)


class ManualAdjustmentWorkflow:
    """Apply manual organizer rules to an existing season plan."""

    def __init__(
        self,
        state: PipelineState,
        updater: Optional[TournamentUpdater] = None,
    ) -> None:
        self.state = state
        self.updater = updater or TournamentUpdater(state=state)

    def load_plan(self) -> SeasonPlan:
        return self.updater.load_plan()

    @staticmethod
    def merge_manual_adjustments(
        existing: dict[str, list[str]],
        requested: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        def _append_unique(base: list[str], extra: Optional[list[str]]) -> list[str]:
            result = list(base)
            for value in extra or []:
                if value not in result:
                    result.append(value)
            return result

        return {
            "locked_dates": _append_unique(existing.get("locked_dates", []), requested.get("locked_dates")),
            "banned_dates": _append_unique(existing.get("banned_dates", []), requested.get("banned_dates")),
            "pinned_tournament_ids": _append_unique(existing.get("pinned_tournament_ids", []), requested.get("pinned_tournament_ids")),
            "forced_host_clubs": _append_unique(existing.get("forced_host_clubs", []), requested.get("forced_host_clubs")),
            "excluded_host_clubs": _append_unique(existing.get("excluded_host_clubs", []), requested.get("excluded_host_clubs")),
        }

    def apply(self, plan: Optional[SeasonPlan] = None) -> UpdateResult:
        """Apply the manual adjustment rules stored on ``plan``."""
        if plan is None:
            plan = self.load_plan()

        adjustments = self._normalize_adjustments(plan.manual_adjustments)
        plan.manual_adjustments = self._serialise_adjustments(adjustments)

        summary_lines: list[str] = []
        move_changes: list[dict[str, Any]] = []
        host_changes: list[dict[str, Any]] = []
        conflict_report: list[dict[str, Any]] = []

        cancellation_wf = CancellationWorkflow(self.state, updater=self.updater)

        # 1) Move mutable tournaments off banned dates.
        for tournament in sorted(plan.tournaments, key=lambda t: (t.date, t.id)):
            if tournament.id in adjustments.pinned_tournament_ids:
                continue
            if tournament.date in adjustments.locked_dates:
                continue
            if tournament.date not in adjustments.banned_dates:
                continue

            replacement = self._find_replacement_date(
                cancellation_wf,
                tournament,
                plan,
                adjustments,
            )
            if replacement is None:
                return UpdateResult(
                    summary_nb=(
                        f"Fant ingen erstatningsdato for {tournament.id} "
                        f"({tournament.age_group}, {tournament.date.isoformat()})."
                    ),
                    tournament_id=tournament.id,
                    operation="manual_adjustment",
                    changes={"failed_tournament": tournament.id},
                    success=False,
                )

            move_result = self.updater.move_date(
                tournament.id,
                replacement,
                plan=plan,
                force=False,
                cascade=False,
            )
            if not move_result.success:
                return move_result

            original_date = tournament.date.isoformat()
            move_changes.append({
                "tournament_id": tournament.id,
                "from": original_date,
                "to": replacement.isoformat(),
            })
            summary_lines.append(
                f"Flyttet {tournament.id} ({tournament.age_group}) {original_date} → {replacement.isoformat()}"
            )

        # 2) Reapply host-club rules.
        for tournament in sorted(plan.tournaments, key=lambda t: (t.date, t.id)):
            if tournament.id in adjustments.pinned_tournament_ids:
                continue

            desired_host = self._choose_host_club(tournament, adjustments)
            if not desired_host or desired_host == tournament.host_club:
                continue

            host_result = self.updater.set_host_club(tournament.id, desired_host, plan=plan)
            if not host_result.success:
                return host_result

            host_changes.append({
                "tournament_id": tournament.id,
                "from": host_result.changes.get("original_host_club"),
                "to": host_result.changes.get("new_host_club"),
            })
            summary_lines.append(
                f"Vertsklubb justert for {tournament.id}: {host_changes[-1]['from'] or 'ukjent'} → {host_changes[-1]['to']}"
            )

        # 3) Recalculate metrics and conflicts.
        planner = self._make_metrics_planner()
        self._prime_planner(planner, plan)
        self._refresh_plan_metadata(planner, plan)

        post_patch_warnings = self._collect_post_patch_warnings(planner, plan)
        conflict_report = self._collect_conflicts(plan)
        success = not conflict_report

        if adjustments.locked_dates & adjustments.banned_dates:
            summary_lines.append(
                "Låste og bannlyste datoer overlapper; lås vinner, men regelen bør sjekkes manuelt."
            )

        summary = "\n".join(summary_lines) if summary_lines else "Ingen manuelle justeringer var nødvendige."
        if conflict_report:
            summary += "\nManuelle justeringer er brukt, men konflikter gjenstår før eksport."

        return UpdateResult(
            summary_nb=summary,
            tournament_id="",
            operation="manual_adjustment",
            changes={
                "manual_adjustments": plan.manual_adjustments,
                "moved": move_changes,
                "host_changes": host_changes,
                "fairness_gate": plan.fairness_gate,
                "conflict_count": len(conflict_report),
            },
            conflicts=conflict_report,
            post_patch_warnings=post_patch_warnings,
            success=success,
        )

    def _normalize_adjustments(self, raw: dict[str, Any]) -> _NormalizedAdjustments:
        def _parse_dates(values: Any) -> set[date]:
            result: set[date] = set()
            for value in values or []:
                if isinstance(value, date):
                    result.add(value)
                    continue
                if isinstance(value, str):
                    try:
                        result.add(date.fromisoformat(value))
                    except ValueError:
                        continue
            return result

        forced_hosts = [str(v) for v in (raw.get("forced_host_clubs") or []) if str(v).strip()]
        excluded_hosts = {str(v) for v in (raw.get("excluded_host_clubs") or []) if str(v).strip()}

        return _NormalizedAdjustments(
            locked_dates=_parse_dates(raw.get("locked_dates")),
            banned_dates=_parse_dates(raw.get("banned_dates")),
            forced_host_clubs=forced_hosts,
            excluded_host_clubs=excluded_hosts,
            pinned_tournament_ids={str(v) for v in (raw.get("pinned_tournament_ids") or []) if str(v).strip()},
        )

    def _serialise_adjustments(self, adjustments: _NormalizedAdjustments) -> dict[str, list[str]]:
        return {
            "locked_dates": sorted(d.isoformat() for d in adjustments.locked_dates),
            "banned_dates": sorted(d.isoformat() for d in adjustments.banned_dates),
            "forced_host_clubs": list(adjustments.forced_host_clubs),
            "excluded_host_clubs": sorted(adjustments.excluded_host_clubs),
            "pinned_tournament_ids": sorted(adjustments.pinned_tournament_ids),
        }

    def _find_replacement_date(
        self,
        cancellation_wf: CancellationWorkflow,
        tournament: Tournament,
        plan: SeasonPlan,
        adjustments: _NormalizedAdjustments,
    ) -> Optional[date]:
        suggestions = cancellation_wf.suggest_makeup_dates(tournament, plan)
        for suggestion in suggestions:
            if suggestion.date in adjustments.locked_dates:
                continue
            if suggestion.date in adjustments.banned_dates:
                continue
            return suggestion.date
        return None

    def _choose_host_club(self, tournament: Tournament, adjustments: _NormalizedAdjustments) -> Optional[str]:
        allowed_forced = [club for club in adjustments.forced_host_clubs if club not in adjustments.excluded_host_clubs]
        allowed = allowed_forced or [team.club for team in tournament.teams if team.club not in adjustments.excluded_host_clubs]
        if not allowed:
            return None

        if tournament.host_club in allowed:
            return tournament.host_club

        for team in tournament.teams:
            if team.club in allowed:
                return team.club

        return allowed[0]

    def _make_metrics_planner(self):
        cfg = load_effective_config(self.state)
        if not cfg:
            raise ValueError("Ingen Stage 1-konfigurasjon funnet. Kjør pipelinen først.")

        roster = _build_roster(cfg)
        pg_config = _build_parallel_games(cfg)
        round_length_config = _build_round_length(cfg)
        events_by_club = _build_events_by_club(self.state.read_stage(StageName.SCRAPING))
        club_arenas = _build_club_arenas(cfg)

        return _make_planner(
            roster,
            pg_config,
            club_arenas,
            cfg.get("maxHostingDeviation", 1),
            round_length_config,
            events_by_club,
            cfg.get("fairness_thresholds", {}),
            cfg.get("target_tournament_count"),
            cfg.get("participation_targets_by_age_group"),
        )

    def _prime_planner(self, planner, plan: SeasonPlan) -> None:
        planner._opponent_history = {}
        planner._month_counts = {}
        planner._running_game_counts = {}
        planner._team_game_counts = {}
        planner._team_last_date = {}

        for tournament in plan.tournaments:
            planner._record_month(tournament.date)
            planner._record_opponent_history(tournament.games)

    def _refresh_plan_metadata(self, planner, plan: SeasonPlan) -> None:
        plan.arena_counts = planner._arena_counts(plan.tournaments)
        plan.diversity_score = planner._diversity_score(plan.tournaments)
        plan.pairwise_matchup_score = planner._pairwise_matchup_score(plan.tournaments)

        if plan.start_date and plan.end_date:
            expected_per_month = planner._expected_monthly_load(
                plan.start_date,
                plan.end_date,
                len(plan.tournaments),
            )
            plan.month_balance_score = planner._month_balance_score(expected_per_month)
        else:
            plan.month_balance_score = 0.0

        planner._compute_game_counts(plan.tournaments)
        plan.team_game_counts = dict(planner._team_game_counts)
        plan.team_last_game_dates = dict(planner._team_last_date)

        # Recompute per-age-group spread so the critic and warnings stay in sync.
        skipped_set = {e["age_group"] for e in plan.skipped_age_groups}
        age_group_counts: dict = {}
        for team in planner.roster.teams:
            if team.age_group in skipped_set:
                continue
            key = planner._team_key(team)
            count = planner._team_game_counts.get(key, 0)
            ag = team.age_group
            if ag not in age_group_counts:
                age_group_counts[ag] = {}
            age_group_counts[ag][key] = age_group_counts[ag].get(key, 0) + count
        per_age_group_spreads: dict = {}
        for ag, counts in age_group_counts.items():
            if counts:
                per_age_group_spreads[ag] = max(counts.values()) - min(counts.values())
        plan.game_count_spread_by_age_group = per_age_group_spreads
        if per_age_group_spreads:
            plan.game_count_spread = max(per_age_group_spreads.values())
        elif planner._team_game_counts:
            plan.game_count_spread = (
                max(planner._team_game_counts.values()) - min(planner._team_game_counts.values())
            )
        else:
            plan.game_count_spread = 0

        plan.fairness_gate = planner._build_fairness_gate(plan)

    def _collect_conflicts(self, plan: SeasonPlan) -> list[dict[str, Any]]:
        conflicts: list[dict[str, Any]] = []
        for tournament in plan.tournaments:
            date_conflicts = self.updater._check_date_conflicts(tournament.date, tournament, plan)
            conflicts.extend(date_conflicts)
        return conflicts

    def _collect_post_patch_warnings(self, planner, plan: SeasonPlan) -> list[str]:
        """Re-run fairness checks after manual patches and return deduplicated warning strings.

        Uses the already-primed planner (populated by ``_prime_planner`` and
        ``_refresh_plan_metadata``) so game-count and month tracking is current.
        """
        warnings: list[str] = []

        # --- game count spread / early-finish warnings ---
        scan_game_count_warnings(planner, plan.start_date, plan.end_date)
        for key, count, value, kind in planner._game_count_warnings:
            if kind == "spread":
                warnings.append(
                    f"Spilltallspredning: {key} har {count} kamper (spredning {value})"
                )
            elif kind == "early_finish":
                warnings.append(
                    f"Sesongdekning: {key} har {count} kamper, {value} dagers opphold i sesongdekningen "
                    "(før første, mellom to, eller etter siste turnering)"
                )

        # --- hosting deviation warnings ---
        planner._hosting_warnings = []
        scan_hosting_warnings(planner, plan)
        warnings.extend(planner._hosting_warnings)

        # --- month load warnings ---
        expected_per_month: float = 0.0
        if plan.start_date and plan.end_date:
            expected_per_month = planner._expected_monthly_load(
                plan.start_date,
                plan.end_date,
                len(plan.tournaments),
            )
        scan_month_load_warnings(planner, expected_per_month, plan.start_date)
        _NORWEGIAN_MONTHS = {
            1: "januar", 2: "februar", 3: "mars", 4: "april",
            5: "mai", 6: "juni", 7: "juli", 8: "august",
            9: "september", 10: "oktober", 11: "november", 12: "desember",
        }
        for year, month, count, expected, deviation in planner._month_load_warnings:
            month_name = _NORWEGIAN_MONTHS.get(month, str(month))
            direction = "overbelastet" if deviation > 0 else "underbelastet"
            tournament_text = "turnering" if count == 1 else "turneringer"
            warnings.append(
                f"Månedslast: {month_name} {year}: {count} {tournament_text} "
                f"(forventet ~{expected:.1f}, {direction} med {abs(deviation):.0%})"
            )

        # --- arena day collision warnings ---
        warnings.extend(scan_arena_day_collision_warnings(plan))

        # --- club x age-group hosting coverage (issue #266 P0) ---
        # A manual patch (e.g. cancelling a club's only tournament in an age
        # group) can turn a previously-covered obligation into an unresolved
        # one -- recompute against the patched plan so `manual_schedule.html`
        # (rendered from `plan.unresolved_hosting_obligations`) stays correct
        # after operator edits, not just after the original Stage 3 run.
        coverage_teams = [
            {"club": team.club, "age_group": team.age_group} for team in planner.roster.teams
        ]
        coverage_tournaments = [
            {"host_club": t.host_club, "age_group": t.age_group}
            for t in plan.tournaments
            if not t.cancelled
        ]
        unresolved_rows = unresolved_from_matrix(hosting_coverage_matrix(coverage_teams, coverage_tournaments))
        plan.unresolved_hosting_obligations = [
            {
                "club": row["club"],
                "age_group": row["age_group"],
                "reason": (
                    f"{row['club']} har lag i {row['age_group']}, men har ingen hjemmeturnering "
                    "i denne aldersgruppen etter manuell justering."
                ),
            }
            for row in unresolved_rows
        ]
        for item in plan.unresolved_hosting_obligations:
            warnings.append(
                f"Uløst vertskapskrav: {item['club']} / {item['age_group']} har ingen hjemmeturnering"
            )

        # Deduplicate while preserving order.
        seen: set[str] = set()
        result: list[str] = []
        for w in warnings:
            if w not in seen:
                seen.add(w)
                result.append(w)
        return result
