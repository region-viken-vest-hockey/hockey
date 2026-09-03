"""Season planning / optimization engine.

`SeasonPlanner` now acts as a small orchestration facade around focused
helper modules for participant selection, host assignment, fairness
scoring, game generation, warning scans, and the rules report.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tournament_scheduler.fairness_model import SeasonFairnessModel
from tournament_scheduler.game_generation import (
    arena_counts as _arena_counts,
    best_round_subset as _best_round_subset,
    diversity_score as _diversity_score,
    generate_round_robin_games as _generate_round_robin_games,
    month_balance_score as _month_balance_score,
    pairwise_matchup_score as _pairwise_matchup_score,
    rebalance_rounds as _rebalance_rounds,
)
from tournament_scheduler.fairness_scoring import (
    DEFAULT_FAIRNESS_THRESHOLDS,
    build_fairness_gate as _build_fairness_gate,
)
from tournament_scheduler.host_assignment import (
    assign_hosts as _assign_hosts,
    default_target_count as _default_target_count,
    find_slot_for_tournament as _find_slot_for_tournament,
    hosting_targets_for_age_group as _hosting_targets_for_age_group,
    proportional_integer_targets as _proportional_integer_targets,
)
from tournament_scheduler.models import (
    AGE_GROUP_OVERLAP,
    CalendarEvent,
    DatePreference,
    Game,
    Roster,
    SeasonPlan,
    Team,
    Tournament,
    overlapping_age_groups,
    team_key,
)
from tournament_scheduler.club_registry import club_for_arena as _club_for_arena
from tournament_scheduler.arena_conflicts import find_arena_interval_collisions
from tournament_scheduler.participant_selection import (
    age_group_deficit_spread as _age_group_deficit_spread,
    cap_per_club_deficit_aware as _cap_per_club_deficit_aware,
    deficit_score as _deficit_score,
    expected_average_for as _expected_average_for,
    max_club_teams_for as _max_club_teams_for,
    max_teams_for as _max_teams_for,
    normalized_invite_count as _normalized_invite_count,
    next_age_group as _next_age_group,
    participant_limit_for as _participant_limit_for,
    participant_selection_score as _participant_selection_score,
    pick_least_recently_grouped as _pick_least_recently_grouped,
    pick_scored_participants as _pick_scored_participants,
    pick_spread_dates as _pick_spread_dates,
    select_participants as _select_participants,
    target_tournaments_for_age_group as _target_tournaments_for_age_group,
)
from tournament_scheduler.rules_report import rules_report as _rules_report
from tournament_scheduler.scheduler import TournamentScheduler
from tournament_scheduler.utils.slot_finder import matchday_duration_minutes
from tournament_scheduler.warnings import (
    _club_calendar_available,
    compute_game_counts as _compute_game_counts,
    hosting_fairness_breakdown as _hosting_fairness_breakdown,
    scan_club_load_warnings as _scan_club_load_warnings,
    scan_feasibility_warnings as _scan_feasibility_warnings,
    scan_game_count_warnings as _scan_game_count_warnings,
    scan_hosting_warnings as _scan_hosting_warnings,
    scan_month_load_warnings as _scan_month_load_warnings,
    scan_per_team_share_warnings as _scan_per_team_share_warnings,
)


MIN_TEAMS_PER_TOURNAMENT = 3
DEFAULT_TOURNAMENT_START_TIME = "10:00"
ARENA_DAY_SEQUENCE_BUFFER_MINUTES = 5

def _normalize_penalty_hints(raw: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """Accept flat or structured planning hints and return numeric penalties."""
    if not isinstance(raw, dict):
        return {}
    if isinstance(raw.get("penalty_hints"), dict):
        raw = raw["penalty_hints"]

    normalized: Dict[str, float] = {}
    for key, value in raw.items():
        try:
            normalized[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return normalized


class SeasonPlanner:
    """Greedy season-plan builder on top of `TournamentScheduler`."""

    def __init__(
        self,
        scheduler: TournamentScheduler,
        roster: Roster,
        club_arenas: Dict[str, str],
        parallel_games_for_age_group: Optional[Dict[str, int]] = None,
        round_length_for_age_group: Optional[Dict[str, int]] = None,
        target_tournament_count: Optional[int] = None,
        target_tournament_counts_by_age_group: Optional[Dict[str, Dict[str, int]]] = None,
        max_club_teams_per_tournament: int = 1,
        deficit_cap_expansion: int = 1,
        max_game_count_spread: int = 2,
        max_early_finish_gap_days: int = 60,
        max_hosting_deviation: int = 1,
        max_hosting_days_per_month: int | None = None,
        max_month_deviation_ratio: float = 0.5,
        events_by_club: Optional[Dict[str, List[CalendarEvent]]] = None,
        fairness_thresholds: Optional[Dict[str, float]] = None,
        fairness_model: Optional[SeasonFairnessModel] = None,
        date_preferences: Optional[List[DatePreference]] = None,
        preferanse_vekt_by_age_group: Optional[Dict[str, float]] = None,
        seed: Optional[int] = None,
        penalty_hints: Optional[Dict[str, float]] = None,
    ):
        self.scheduler = scheduler
        self.roster = roster
        self.club_arenas = club_arenas
        self.parallel_games_for_age_group = parallel_games_for_age_group or {}
        self.round_length_for_age_group = round_length_for_age_group or {}
        self.target_tournament_count = target_tournament_count
        self.target_tournament_counts_by_age_group = {
            age_group: dict(targets)
            for age_group, targets in (target_tournament_counts_by_age_group or {}).items()
        }
        self.max_club_teams_per_tournament = max_club_teams_per_tournament
        self.deficit_cap_expansion = deficit_cap_expansion
        self.max_game_count_spread = max_game_count_spread
        self.max_early_finish_gap_days = max_early_finish_gap_days
        self.max_hosting_deviation = max_hosting_deviation
        self.max_hosting_days_per_month = max_hosting_days_per_month

        duplicate_labels = {
            label for label, count in Counter(team.label for team in roster.teams).items() if count > 1
        }
        self._duplicate_team_labels = duplicate_labels
        self._club_load_warnings: List[Tuple[str, str, str, int]] = []
        self._hosting_warnings: List[str] = []
        self._game_count_warnings: List[Tuple[str, int, int, str]] = []
        self._grouped_with: Dict[str, Set[str]] = {}
        self._team_game_counts: Dict[str, int] = {}
        self._running_game_counts: Dict[str, int] = {}
        self._club_cap_overrides: int = 0
        self._team_last_date: Dict[str, date] = {}
        self._invite_counts: Dict[str, int] = {self._team_key(team): 0 for team in roster.teams}
        club_age_group_counts: Dict[Tuple[str, str], int] = {}
        for team in roster.teams:
            key = (team.club, team.age_group)
            club_age_group_counts[key] = club_age_group_counts.get(key, 0) + 1
        self._club_age_group_team_counts: Dict[str, int] = {
            self._team_key(team): club_age_group_counts[(team.club, team.age_group)]
            for team in roster.teams
        }
        self._opponent_history: Dict[frozenset, int] = {}
        self._month_counts: Dict[Tuple[int, int], int] = {}
        self._month_load_warnings: List[Tuple[int, int, int, float, float]] = []
        self._per_team_share_warnings: List[Tuple[str, str, str, int, float]] = []
        self._feasibility_warnings: List[str] = []
        self._tournament_participations: Dict[str, int] = {self._team_key(team): 0 for team in roster.teams}
        # Tracks distinct hosting dates per (club, year, month) during date selection
        # so _score_candidate_date can penalise dates that would exceed
        # max_hosting_days_per_month for the predicted host club.
        self._hosting_days_by_club_month: Dict[Tuple[str, Tuple[int, int]], Set[date]] = {}

        self.max_month_deviation_ratio = max_month_deviation_ratio
        self.events_by_club = events_by_club or {}
        self.available_calendar_clubs = set(self.events_by_club.keys())
        self.fairness_thresholds = dict(DEFAULT_FAIRNESS_THRESHOLDS)
        if fairness_thresholds:
            self.fairness_thresholds.update(fairness_thresholds)
        self.penalty_hints: Dict[str, float] = _normalize_penalty_hints(penalty_hints)
        if self.penalty_hints:
            _hint_keys_log: list[str] = []
            # Relax thresholds for metrics that failed, capped at 2x original
            host_score = self.penalty_hints.get("hosting_deviation_score")
            if host_score is not None and host_score < 100:
                old = self.max_hosting_deviation
                self.max_hosting_deviation = max(2, int(round(old * 1.5)))
                _hint_keys_log.append(f"maks_vertskap_avvik: {old}→{self.max_hosting_deviation} (score={host_score})")
            spread_score = self.penalty_hints.get("game_count_spread_score")
            if spread_score is not None and spread_score < 100:
                old = self.max_game_count_spread
                self.max_game_count_spread = max(4, int(round(old * 1.5)))
                _hint_keys_log.append(f"maks_kamper_spredning: {old}→{self.max_game_count_spread} (score={spread_score})")
            for key, min_key, factor in [
                ("diversity_score", "min_diversity_score", 0.7),
                ("pairwise_matchup_score", "min_pairwise_matchup_score", 0.7),
                ("month_balance_score", "min_month_balance_score", 0.7),
            ]:
                score_val = self.penalty_hints.get(key)
                if score_val is not None and score_val < 75:
                    old = self.fairness_thresholds.get(min_key, DEFAULT_FAIRNESS_THRESHOLDS.get(min_key, 0.75))
                    new_val = max(0.3, old * factor)
                    self.fairness_thresholds[min_key] = new_val
                    _hint_keys_log.append(f"{min_key}: {old:.2f}→{new_val:.2f} (score={score_val})")
            if _hint_keys_log:
                print(f"[penalty_hints] {', '.join(_hint_keys_log)}")
        self.fairness_model = fairness_model or SeasonFairnessModel()
        self._fallback_host_substitutions: List[Tuple[date, str, str, str]] = []
        self.date_preferences: List[DatePreference] = date_preferences or []
        self.preferanse_vekt_by_age_group: Dict[str, float] = preferanse_vekt_by_age_group or {}
        self._rng: random.Random = random.Random(seed)

    def _team_target_tournament_count(self, team: Team) -> int:
        if team.target_tournament_count is not None:
            return team.target_tournament_count
        if self.target_tournament_count is not None:
            return self.target_tournament_count

        age_group = team.age_group
        team_count = max(1, len(self.roster.by_age_group(age_group)))
        capacity = max(1, self._max_teams_for(age_group))
        tournament_count = self._target_tournaments_for_age_group(age_group) or 1
        return max(1, math.ceil(tournament_count * capacity / team_count))

    def _team_at_target(self, team: Team) -> bool:
        key = self._team_key(team)
        return self._tournament_participations.get(key, 0) >= self._team_target_tournament_count(team)

    def _team_key(self, team: Team) -> str:
        return team_key(team, self._duplicate_team_labels)

    def build_plan(self, start_date: datetime, end_date: datetime) -> SeasonPlan:
        print("[plan] Henter tilgjengelige datoer...", flush=True)
        scheduling_result = self.scheduler.find_available_dates(start_date, end_date)
        free_dates = sorted(scheduling_result.available_dates)
        print(f"[plan] Fant {len(free_dates)} fridager i vinduet {start_date.date()}–{end_date.date()}", flush=True)

        plan = SeasonPlan(tournaments=[], start_date=start_date.date(), end_date=end_date.date())

        age_groups = self.roster.age_groups()
        if not age_groups:
            print("[plan] Ingen aldersgrupper i rosteren — returnerer tom plan.", flush=True)
            return plan

        self._rng.shuffle(age_groups)
        print(f"[plan] Aldergrupper: {', '.join(age_groups)}", flush=True)

        # Reset hosting-day tracking so each build_plan call starts clean.
        self._hosting_days_by_club_month = {}

        target_counts = {age_group: self._target_tournaments_for_age_group(age_group) for age_group in age_groups}
        print(f"[plan] Mål per aldersgruppe: {', '.join(f'{ag}={target_counts[ag]}' for ag in age_groups)}", flush=True)

        season_start_date = start_date.date()
        season_end_date = end_date.date()
        if self._has_split_tournament_targets() and self._christmas_split_date(season_start_date, season_end_date) is not None:
            print("[plan] Bruker delt før/etter-jul-planlegging...", flush=True)
            scheduled = self._build_split_date_schedule(
                age_groups,
                free_dates,
                season_start_date,
                season_end_date,
                target_counts,
            )
            print(f"[plan] Delt dato-plan klar ({len(scheduled)} turneringer)", flush=True)
        else:
            print("[plan] Bygger grov dato-plan...", flush=True)
            baseline_scheduled, _ = self._build_greedy_date_schedule(
                age_groups,
                free_dates,
                season_start_date,
                season_end_date,
                target_counts,
            )
            print(f"[plan] Grov dato-plan klar ({len(baseline_scheduled)} turneringer)", flush=True)
            print("[plan] Bygger sesong-optimalisert dato-plan...", flush=True)
            optimized_scheduled, _ = self._build_global_date_schedule(
                age_groups,
                free_dates,
                season_start_date,
                season_end_date,
                target_counts,
            )
            print(f"[plan] Optimalisert dato-plan klar ({len(optimized_scheduled)} turneringer)", flush=True)
            print("[plan] Optimalisering: ferdig — kjører finjustering og forbedringsanalyse...", flush=True)
            baseline_score = self._score_date_schedule(baseline_scheduled, season_start_date, season_end_date)
            optimized_score = self._score_date_schedule(optimized_scheduled, season_start_date, season_end_date)
            scheduled = optimized_scheduled if optimized_score <= baseline_score else baseline_scheduled
            print(
                f"[plan] Valgte {'optimalisert' if scheduled is optimized_scheduled else 'grov'} plan (score {optimized_score:.1f} vs {baseline_score:.1f})",
                flush=True,
            )

        self._month_counts = {}
        self._hosting_days_by_club_month = {}
        self._tournament_participations = {self._team_key(team): 0 for team in self.roster.teams}
        self._running_game_counts = {}
        self._opponent_history = {}
        self._invite_counts = {self._team_key(team): 0 for team in self.roster.teams}
        self._grouped_with = {}
        self._team_last_date = {}
        self._team_game_counts = {}
        self._club_cap_overrides = 0
        scheduled.sort(key=lambda item: (item[0], item[1]))
        print("[plan] Fordeler verter og tidspunkter (kan ta litt tid)...", flush=True)
        host_assignments = self._assign_hosts(scheduled)
        print(f"[plan] Verter fordelt ({len(host_assignments)}/{len(scheduled)})", flush=True)
        collisions: List[Tuple[date, str, str]] = []
        self._fallback_host_substitutions = []

        scheduled_age_groups_by_date: Dict[date, List[str]] = {}
        scheduled_counts = Counter(age_group for _, age_group in scheduled)
        host_targets_by_age = {
            age_group: _hosting_targets_for_age_group(self, age_group, count)
            for age_group, count in scheduled_counts.items()
        }
        host_counts_by_age: Dict[str, Dict[str, int]] = {age_group: {} for age_group in scheduled_counts}
        print("[plan] Bygger turneringer, verter og kamper...", flush=True)
        reserved_events_by_club: Dict[str, List[CalendarEvent]] = {}
        slot_failures: List[Dict[str, str]] = []
        for index, ((tournament_date, age_group), original_host_club) in enumerate(zip(scheduled, host_assignments), start=1):
            if index == 1 or index % 10 == 0:
                print(f"[plan] Ferdigstiller turnering {index}/{len(scheduled)} ({age_group} {tournament_date})", flush=True)
            self._record_month(tournament_date)
            collision = self._check_overlap_collision(tournament_date, age_group, scheduled_age_groups_by_date)
            if collision:
                collisions.append((tournament_date, age_group, collision))
            scheduled_age_groups_by_date.setdefault(tournament_date, []).append(age_group)

            participants = self._select_participants(age_group)

            if len(participants) < MIN_TEAMS_PER_TOURNAMENT:
                plan.skipped_age_groups.append(
                    {
                        "age_group": age_group,
                        "team_count": len(participants),
                        "reason": f"Kun {len(participants)} lag konfigurert; minimum er {MIN_TEAMS_PER_TOURNAMENT}",
                    }
                )
                continue

            self._record_grouping(participants)
            parallel_games = self._parallel_games_for(age_group)
            provisional_games = self.generate_round_robin_games(participants, parallel_games)

            candidate_hosts = self._ordered_host_candidates(
                age_group=age_group,
                original_host=original_host_club,
                tournament_date=tournament_date,
                host_targets_by_age=host_targets_by_age,
                host_counts_by_age=host_counts_by_age,
            )
            slot_search_active = bool(self.events_by_club or reserved_events_by_club)
            slot = self._find_slot_for_tournament(
                tournament_date,
                original_host_club,
                age_group,
                provisional_games,
                candidate_hosts=candidate_hosts,
                reserved_events_by_club=reserved_events_by_club,
            )

            final_host_club = original_host_club
            start_time = DEFAULT_TOURNAMENT_START_TIME
            if slot is not None:
                final_host_club, slot_start, _slot_end = slot
                start_time = slot_start
                if final_host_club != original_host_club:
                    self._fallback_host_substitutions.append(
                        (tournament_date, age_group, original_host_club, final_host_club)
                    )

            arena = self.club_arenas.get(final_host_club, final_host_club)
            home_club = _club_for_arena(arena) or final_host_club
            host_teams = [t for t in participants if t.club == home_club]
            other_teams = [t for t in participants if t.club != home_club]
            if host_teams:
                participants = host_teams + other_teams

            games = self.generate_round_robin_games(participants, parallel_games)
            self._record_opponent_history(games)

            # A club without scraped calendar data can still host its fair share
            # of tournaments — the assigned start time is just a provisional
            # placeholder because the planner cannot validate hall availability.
            # Mark it so the club knows the istid must be booked by hand (see
            # the "Må planlegges manuelt" export view).
            manual_booking_reason: Optional[str] = None
            if not _club_calendar_available(final_host_club, self.available_calendar_clubs):
                manual_booking_reason = (
                    f"Kalender utilgjengelig for {final_host_club} — "
                    "istid må bookes/verifiseres manuelt."
                )

            ag_weight = self.preferanse_vekt_by_age_group.get(age_group, 0.0)
            date_pref_total = sum(
                p.vekt for p in self.date_preferences if p.fra <= tournament_date <= p.til
            )
            tournament = Tournament(
                date=tournament_date,
                arena=arena,
                age_group=age_group,
                teams=participants,
                games=games,
                host_club=final_host_club,
                start_time=start_time,
                preferanse_vekt=ag_weight,
                scoring_weight_term=ag_weight + date_pref_total,
                manual_booking_reason=manual_booking_reason,
            )
            plan.tournaments.append(tournament)
            reservation = self._reservation_event_for_tournament(tournament)
            if reservation is not None:
                reserved_events_by_club.setdefault(final_host_club, []).append(reservation)
            if slot is None and slot_search_active:
                slot_failures.append(
                    self._slot_failure_collision(
                        tournament,
                        candidate_hosts=candidate_hosts,
                        reason="Ingen gyldig ledig arenatid ble funnet etter kildebookinger og planlagte turneringer.",
                    )
                )
            # Record actual host so the tracking dict reflects committed assignments.
            month_key = (tournament_date.year, tournament_date.month)
            self._hosting_days_by_club_month.setdefault(
                (final_host_club, month_key), set()
            ).add(tournament_date)
            host_counts_by_age.setdefault(age_group, {})
            host_counts_by_age[age_group][final_host_club] = host_counts_by_age[age_group].get(final_host_club, 0) + 1

        expected_per_month = self._expected_monthly_load(start_date.date(), end_date.date(), len(scheduled))
        sequence_failures = self._sequence_same_arena_day_start_times(plan)
        interval_collisions = find_arena_interval_collisions(
            plan.tournaments,
            self.round_length_for_age_group,
        )

        plan.arena_counts = self._arena_counts(plan.tournaments)
        plan.diversity_score = self._diversity_score(plan.tournaments)
        plan.pairwise_matchup_score = self._pairwise_matchup_score(plan.tournaments)
        plan.month_balance_score = self._month_balance_score(expected_per_month)
        plan.arena_day_collisions = interval_collisions + sequence_failures + slot_failures
        self._arena_day_collisions = list(plan.arena_day_collisions)
        if plan.arena_day_collisions:
            plan.arena_counts["_arena_day_collisions"] = len(plan.arena_day_collisions)
        else:
            plan.arena_counts.pop("_arena_day_collisions", None)
        if self.date_preferences:
            plan.date_preference_weights = [
                {"fra": p.fra.isoformat(), "til": p.til.isoformat(), "vekt": p.vekt}
                for p in self.date_preferences
            ]

        self._compute_game_counts(plan.tournaments)
        skipped_age_groups_set = {entry["age_group"] for entry in plan.skipped_age_groups}
        public_team_game_counts: Dict[str, int] = {}
        public_team_last_dates: Dict[str, date] = {}
        # age_group -> {public_key -> game_count} for per-group spread calc
        age_group_counts: Dict[str, Dict[str, int]] = {}
        for team in self.roster.teams:
            if team.age_group in skipped_age_groups_set:
                continue
            key = self._team_key(team)
            public_key = team_key(team, self._duplicate_team_labels)
            count = self._team_game_counts.get(key, 0)
            public_team_game_counts[public_key] = public_team_game_counts.get(public_key, 0) + count
            ag = team.age_group
            if ag not in age_group_counts:
                age_group_counts[ag] = {}
            age_group_counts[ag][public_key] = age_group_counts[ag].get(public_key, 0) + count
            last = self._team_last_date.get(key)
            if last is not None and (public_key not in public_team_last_dates or last > public_team_last_dates[public_key]):
                public_team_last_dates[public_key] = last
        plan.team_game_counts = public_team_game_counts
        plan.team_last_game_dates = public_team_last_dates
        per_age_group_spreads: Dict[str, int] = {}
        for ag, counts in age_group_counts.items():
            if counts:
                per_age_group_spreads[ag] = max(counts.values()) - min(counts.values())
        plan.game_count_spread_by_age_group = per_age_group_spreads
        if per_age_group_spreads:
            plan.game_count_spread = max(per_age_group_spreads.values())
        elif public_team_game_counts:
            plan.game_count_spread = max(public_team_game_counts.values()) - min(public_team_game_counts.values())

        plan.fairness_gate = self._build_fairness_gate(plan)
        self._scan_club_load_warnings(plan.tournaments)
        self._scan_hosting_warnings(plan)
        self._scan_game_count_warnings(plan.start_date, plan.end_date)
        self._scan_per_team_share_warnings(skipped_age_groups=plan.skipped_age_groups)
        self._scan_month_load_warnings(expected_per_month, plan.start_date)
        self._scan_feasibility_warnings(free_dates)

        if collisions:
            plan.arena_counts["_age_group_overlap_collisions"] = len(collisions)
            self._collisions = collisions
        else:
            self._collisions = []

        return plan

    @property
    def collisions(self) -> List[Tuple[date, str, str]]:
        return getattr(self, "_collisions", [])

    @property
    def fallback_host_substitutions(self) -> List[Tuple[date, str, str, str]]:
        return getattr(self, "_fallback_host_substitutions", [])

    @property
    def club_load_warnings(self) -> List[Tuple[str, str, str, int]]:
        return list(self._club_load_warnings)

    @property
    def hosting_warnings(self) -> List[str]:
        return list(self._hosting_warnings)

    @property
    def game_count_warnings(self) -> List[Tuple[str, int, int, str]]:
        return list(self._game_count_warnings)

    @property
    def per_team_share_warnings(self) -> List[Tuple[str, str, str, int, float]]:
        return list(self._per_team_share_warnings)

    @property
    def club_cap_overrides(self) -> int:
        return self._club_cap_overrides

    @property
    def feasibility_warnings(self) -> List[str]:
        return list(self._feasibility_warnings)

    @property
    def month_load_warnings(self) -> List[Tuple[int, int, int, float, float]]:
        return list(self._month_load_warnings)

    def _build_greedy_date_schedule(
        self,
        age_groups: Sequence[str],
        free_dates: Sequence[date],
        window_start: date,
        window_end: date,
        target_counts: Dict[str, int],
    ) -> Tuple[List[Tuple[date, str]], float]:
        """Build the existing per-age-group greedy date list on temporary state."""
        saved_month_counts = self._month_counts
        saved_hosting_days = self._hosting_days_by_club_month
        try:
            self._month_counts = {}
            self._hosting_days_by_club_month = {}
            scheduled: List[Tuple[date, str]] = []
            planned_age_groups_by_date: Dict[date, List[str]] = {}
            predicted_host_total: Dict[str, int] = {club: 0 for club in self.club_arenas}
            total_score = 0.0

            for age_group in age_groups:
                target_count = target_counts.get(age_group, 0)
                if target_count <= 0:
                    continue

                bucket_score = self._expected_monthly_load(window_start, window_end, target_count)
                bucket_span = (window_end - window_start).days / target_count if target_count else 0.0
                chosen_dates = self._pick_spread_dates(
                    free_dates,
                    window_start,
                    window_end,
                    [age_group],
                    planned_age_groups_by_date,
                    target_count=target_count,
                )
                for slot_index, tournament_date in enumerate(chosen_dates):
                    bucket_center = window_start + timedelta(days=int((slot_index + 0.5) * bucket_span))
                    half_span_days = max(1.0, bucket_span / 2)
                    predicted_participants = self._select_participants(age_group)
                    spread_penalty = abs((tournament_date - bucket_center).days) / half_span_days
                    diversity_penalty = self._score_candidate_date(
                        tournament_date,
                        age_group,
                        predicted_participants,
                        bucket_score,
                        tournament_weight=self.preferanse_vekt_by_age_group.get(age_group, 0.0),
                    )
                    same_day_penalty = len(planned_age_groups_by_date.get(tournament_date, [])) * 50.0
                    overlap_penalty = 0.0
                    for existing in planned_age_groups_by_date.get(tournament_date, []):
                        if (
                            age_group in overlapping_age_groups(existing)
                            or existing in overlapping_age_groups(age_group)
                        ):
                            overlap_penalty += 100.0
                    total_score += spread_penalty + diversity_penalty + same_day_penalty + overlap_penalty
                    scheduled.append((tournament_date, age_group))
                    planned_age_groups_by_date.setdefault(tournament_date, []).append(age_group)
                    self._record_month(tournament_date)
                    if predicted_host_total:
                        predicted_host = min(predicted_host_total, key=predicted_host_total.__getitem__)
                        month_key = (tournament_date.year, tournament_date.month)
                        club_month_key = (predicted_host, month_key)
                        self._hosting_days_by_club_month.setdefault(club_month_key, set()).add(tournament_date)
                        predicted_host_total[predicted_host] += 1

            return sorted(scheduled, key=lambda item: (item[0], item[1])), total_score
        finally:
            self._month_counts = saved_month_counts
            self._hosting_days_by_club_month = saved_hosting_days

    def _build_global_date_schedule(
        self,
        age_groups: Sequence[str],
        free_dates: Sequence[date],
        window_start: date,
        window_end: date,
        target_counts: Dict[str, int],
        *,
        continue_from_current_state: bool = False,
    ) -> Tuple[List[Tuple[date, str]], float]:
        """Rebuild the date list with a season-wide best-first optimisation pass."""
        saved_state = None
        if not continue_from_current_state:
            saved_state = (
                self._month_counts,
                self._hosting_days_by_club_month,
                self._tournament_participations,
                self._running_game_counts,
                self._opponent_history,
                self._invite_counts,
                self._grouped_with,
                self._team_last_date,
                self._team_game_counts,
                self._club_cap_overrides,
            )
            self._month_counts = {}
            self._hosting_days_by_club_month = {}
            self._tournament_participations = {self._team_key(team): 0 for team in self.roster.teams}
            self._running_game_counts = {}
            self._opponent_history = {}
            self._invite_counts = {self._team_key(team): 0 for team in self.roster.teams}
            self._grouped_with = {}
            self._team_last_date = {}
            self._team_game_counts = {}
            self._club_cap_overrides = 0
        else:
            saved_state = None

        remaining_by_age_group = {ag: count for ag, count in target_counts.items() if count > 0}
        used_dates_by_age_group: Dict[str, Set[date]] = {ag: set() for ag in remaining_by_age_group}
        scheduled_age_groups_by_date: Dict[date, List[str]] = {}
        predicted_host_total: Dict[str, int] = {club: 0 for club in self.club_arenas}
        scheduled: List[Tuple[date, str]] = []
        total_score = 0.0
        total_days = max(1, (window_end - window_start).days)
        expected_per_month = self._expected_monthly_load(
            window_start,
            window_end,
            sum(remaining_by_age_group.values()),
        )

        total_remaining = sum(remaining_by_age_group.values())
        placed_count = 0
        while any(remaining_by_age_group.values()):
            best_choice: Optional[Tuple[float, date, str, List[Team]]] = None
            if placed_count == 0 or placed_count % 10 == 0:
                print(
                    f"[plan] Optimalisering: {placed_count}/{total_remaining} turneringer plassert...",
                    flush=True,
                )
            for age_group in age_groups:
                remaining = remaining_by_age_group.get(age_group, 0)
                if remaining <= 0:
                    continue

                target_count = max(1, target_counts.get(age_group, 1))
                slot_index = target_count - remaining
                bucket_span = total_days / target_count
                bucket_center = window_start + timedelta(days=int((slot_index + 0.5) * bucket_span))
                half_span_days = max(1.0, bucket_span / 2)
                predicted_participants = list(self._select_participants(age_group))

                for tournament_date in free_dates:
                    if tournament_date in used_dates_by_age_group.setdefault(age_group, set()):
                        continue

                    spread_penalty = abs((tournament_date - bucket_center).days) / half_span_days
                    same_day_penalty = len(scheduled_age_groups_by_date.get(tournament_date, [])) * 50.0
                    overlap_penalty = 0.0
                    for existing in scheduled_age_groups_by_date.get(tournament_date, []):
                        if (
                            age_group in overlapping_age_groups(existing)
                            or existing in overlapping_age_groups(age_group)
                        ):
                            overlap_penalty += 100.0

                    diversity_penalty = self._score_candidate_date(
                        tournament_date,
                        age_group,
                        predicted_participants,
                        expected_per_month,
                        tournament_weight=self.preferanse_vekt_by_age_group.get(age_group, 0.0),
                    )
                    score = spread_penalty + same_day_penalty + overlap_penalty + diversity_penalty
                    if best_choice is None or score < best_choice[0] or (
                        score == best_choice[0]
                        and (tournament_date, age_group) < (best_choice[1], best_choice[2])
                    ):
                        best_choice = (score, tournament_date, age_group, predicted_participants)

            if best_choice is None:
                break

            score, tournament_date, age_group, participants = best_choice
            total_score += score
            scheduled.append((tournament_date, age_group))
            used_dates_by_age_group.setdefault(age_group, set()).add(tournament_date)
            scheduled_age_groups_by_date.setdefault(tournament_date, []).append(age_group)
            if participants:
                self._record_grouping(participants)
                parallel_games = self._parallel_games_for(age_group)
                games = self.generate_round_robin_games(participants, parallel_games)
                self._record_opponent_history(games)
            self._record_month(tournament_date)
            if predicted_host_total:
                predicted_host = min(predicted_host_total, key=predicted_host_total.__getitem__)
                month_key = (tournament_date.year, tournament_date.month)
                club_month_key = (predicted_host, month_key)
                self._hosting_days_by_club_month.setdefault(club_month_key, set()).add(tournament_date)
                predicted_host_total[predicted_host] += 1
            remaining_by_age_group[age_group] -= 1
            placed_count += 1

        print(f"[plan] Optimalisering: ferdig ({placed_count}/{total_remaining})", flush=True)
        print("[plan] Optimalisering: starter forbedringsanalyse...", flush=True)
        repaired_schedule, repaired_score = self._repair_date_schedule(
            scheduled,
            free_dates,
            window_start,
            window_end,
        )
        print(f"[plan] Optimalisering: forbedring ferdig ({len(repaired_schedule)}/{total_remaining})", flush=True)
        if saved_state is not None:
            (
                self._month_counts,
                self._hosting_days_by_club_month,
                self._tournament_participations,
                self._running_game_counts,
                self._opponent_history,
                self._invite_counts,
                self._grouped_with,
                self._team_last_date,
                self._team_game_counts,
                self._club_cap_overrides,
            ) = saved_state
        return repaired_schedule, repaired_score

    def _repair_date_schedule(
        self,
        scheduled: Sequence[Tuple[date, str]],
        free_dates: Sequence[date],
        window_start: date,
        window_end: date,
        max_passes: int = 3,
    ) -> Tuple[List[Tuple[date, str]], float]:
        """Apply a small hill-climbing repair pass to a tentative date schedule.

        The planner is mostly greedy, so this helper gives it a narrow chance to
        revisit earlier choices when a later collision or repeat penalty makes a
        local swap score better.
        """
        if not scheduled:
            return [], 0.0

        best_schedule = sorted(scheduled, key=lambda item: (item[0], item[1]))
        best_score = self._score_date_schedule(best_schedule, window_start, window_end)
        free_dates_sorted = sorted(set(free_dates))
        if len(best_schedule) < 2 or len(free_dates_sorted) < 2:
            return best_schedule, best_score

        total_passes = max(1, max_passes)
        for pass_index in range(1, total_passes + 1):
            print(f"[plan] Optimalisering: forbedringsrunde {pass_index}/{total_passes}...", flush=True)
            improved = False
            age_group_dates: Dict[str, Set[date]] = {}
            for tournament_date, age_group in best_schedule:
                age_group_dates.setdefault(age_group, set()).add(tournament_date)

            for index, (current_date, age_group) in enumerate(best_schedule):
                used_dates = age_group_dates.get(age_group, set())
                for candidate_date in free_dates_sorted:
                    if candidate_date == current_date or candidate_date in used_dates:
                        continue

                    candidate_schedule = list(best_schedule)
                    candidate_schedule[index] = (candidate_date, age_group)
                    candidate_schedule.sort(key=lambda item: (item[0], item[1]))

                    # month_penalty + overlap_penalty*100 is an exact lower bound on the
                    # full score (repeat_penalty >= 0), computable without replaying
                    # participant selection. Skip the expensive full replay whenever the
                    # bound alone already rules out both acceptance branches below.
                    lower_bound = self._position_penalty_lower_bound(
                        candidate_schedule, window_start, window_end
                    )
                    if lower_bound > best_score + 1e-9:
                        continue

                    candidate_score = self._score_date_schedule(
                        candidate_schedule,
                        window_start,
                        window_end,
                    )
                    if candidate_score + 1e-9 < best_score or (
                        abs(candidate_score - best_score) <= 1e-9 and candidate_schedule < best_schedule
                    ):
                        best_schedule = candidate_schedule
                        best_score = candidate_score
                        improved = True
                        break
                if improved:
                    break

            if not improved:
                print(f"[plan] Optimalisering: forbedringsrunde {pass_index}/{total_passes} ga ingen forbedring", flush=True)
                break
            print(f"[plan] Optimalisering: forbedringsrunde {pass_index}/{total_passes} ga forbedring", flush=True)

        return best_schedule, best_score

    def _position_penalty_lower_bound(
        self,
        scheduled: Sequence[Tuple[date, str]],
        window_start: date,
        window_end: date,
    ) -> float:
        """Cheap lower bound on `_score_date_schedule`, from positions alone.

        `month_penalty` and `overlap_penalty` depend only on which dates and
        age groups are scheduled, not on which teams get picked to play, so
        they can be computed directly without replaying participant
        selection. Since `repeat_penalty` is always >= 0, this sum is a exact
        lower bound on the full score — used to skip a full
        `_score_date_schedule` replay for candidates that cannot possibly
        beat the current best.
        """
        if not scheduled:
            return 0.0

        month_counts: Dict[Tuple[int, int], int] = {}
        by_date: Dict[date, List[str]] = {}
        for tournament_date, age_group in scheduled:
            month_key = (tournament_date.year, tournament_date.month)
            month_counts[month_key] = month_counts.get(month_key, 0) + 1
            by_date.setdefault(tournament_date, []).append(age_group)

        expected_per_month = self._expected_monthly_load(window_start, window_end, len(scheduled))
        month_penalty = 0.0
        if month_counts:
            month_penalty = sum(abs(count - expected_per_month) for count in month_counts.values())
            month_penalty /= max(1, len(month_counts))

        overlap_penalty = 0.0
        for groups in by_date.values():
            for i, age_group in enumerate(groups):
                for other in groups[i + 1 :]:
                    if (
                        age_group in overlapping_age_groups(other)
                        or other in overlapping_age_groups(age_group)
                    ):
                        overlap_penalty += 1.0

        return month_penalty + overlap_penalty * 100.0

    def _score_date_schedule(
        self,
        scheduled: Sequence[Tuple[date, str]],
        window_start: date,
        window_end: date,
    ) -> float:
        """Score a fully chosen date list by replaying the season state."""
        if not scheduled:
            return 0.0

        saved_state = (
            self._month_counts,
            self._hosting_days_by_club_month,
            self._tournament_participations,
            self._running_game_counts,
            self._opponent_history,
            self._invite_counts,
            self._grouped_with,
            self._team_last_date,
            self._team_game_counts,
            self._club_cap_overrides,
        )
        try:
            self._month_counts = {}
            self._hosting_days_by_club_month = {}
            self._tournament_participations = {self._team_key(team): 0 for team in self.roster.teams}
            self._running_game_counts = {}
            self._opponent_history = {}
            self._invite_counts = {self._team_key(team): 0 for team in self.roster.teams}
            self._grouped_with = {}
            self._team_last_date = {}
            self._team_game_counts = {}
            self._club_cap_overrides = 0

            by_date: Dict[date, List[str]] = {}
            sorted_schedule = sorted(scheduled, key=lambda item: (item[0], item[1]))
            for tournament_date, age_group in sorted_schedule:
                participants = list(self._select_participants(age_group))
                if participants:
                    self._record_grouping(participants)
                    parallel_games = self._parallel_games_for(age_group)
                    games = self.generate_round_robin_games(participants, parallel_games)
                    self._record_opponent_history(games)
                self._record_month(tournament_date)
                by_date.setdefault(tournament_date, []).append(age_group)

            expected_per_month = self._expected_monthly_load(window_start, window_end, len(sorted_schedule))
            month_penalty = 0.0
            if self._month_counts:
                month_penalty = sum(abs(count - expected_per_month) for count in self._month_counts.values())
                month_penalty /= max(1, len(self._month_counts))

            overlap_penalty = 0.0
            for groups in by_date.values():
                for i, age_group in enumerate(groups):
                    for other in groups[i + 1 :]:
                        if (
                            age_group in overlapping_age_groups(other)
                            or other in overlapping_age_groups(age_group)
                        ):
                            overlap_penalty += 1.0

            repeat_penalty = sum(max(0, count - 1) for count in self._opponent_history.values())
            return month_penalty + overlap_penalty * 100.0 + repeat_penalty
        finally:
            (
                self._month_counts,
                self._hosting_days_by_club_month,
                self._tournament_participations,
                self._running_game_counts,
                self._opponent_history,
                self._invite_counts,
                self._grouped_with,
                self._team_last_date,
                self._team_game_counts,
                self._club_cap_overrides,
            ) = saved_state

    def _reservation_event_for_tournament(self, tournament: Tournament) -> Optional[CalendarEvent]:
        round_length = self.round_length_for_age_group.get(tournament.age_group)
        if not round_length or not tournament.games or not tournament.start_time:
            return None
        try:
            hour, minute = (int(part) for part in tournament.start_time.split(":", 1))
            start_at = datetime.combine(tournament.date, datetime.min.time()).replace(hour=hour, minute=minute)
        except (TypeError, ValueError):
            return None
        duration_minutes = matchday_duration_minutes(round_length, max(g.round_number for g in tournament.games))
        if duration_minutes <= 0:
            return None
        return CalendarEvent(
            date=tournament.date.strftime("%d.%m.%Y"),
            name=f"Planlagt turnering {tournament.id} {tournament.age_group}",
            datetime=start_at,
            duration_hours=duration_minutes / 60.0,
            location=tournament.arena,
        )

    def _slot_failure_collision(
        self,
        tournament: Tournament,
        *,
        candidate_hosts: Sequence[str],
        reason: str,
    ) -> Dict[str, str]:
        round_length = self.round_length_for_age_group.get(tournament.age_group)
        interval = "ukjent"
        if round_length and tournament.start_time and tournament.games:
            try:
                hour, minute = (int(part) for part in tournament.start_time.split(":", 1))
                start_at = datetime.combine(tournament.date, datetime.min.time()).replace(hour=hour, minute=minute)
                duration_minutes = matchday_duration_minutes(round_length, max(g.round_number for g in tournament.games))
                end_at = start_at + timedelta(minutes=duration_minutes)
                interval = f"{start_at.strftime('%Y-%m-%d %H:%M')}–{end_at.strftime('%Y-%m-%d %H:%M')}"
            except (TypeError, ValueError):
                interval = f"{tournament.date.isoformat()} {tournament.start_time}"
        candidates = ", ".join(candidate_hosts) if candidate_hosts else str(tournament.host_club or tournament.arena)
        message = (
            f"Arena conflict {tournament.arena} {tournament.date.isoformat()}: "
            f"{tournament.id} ({tournament.age_group}) {interval}. {reason} Kandidater: {candidates}."
        )
        return {
            "date": tournament.date.isoformat(),
            "arena": tournament.arena,
            "tournament_id": tournament.id,
            "age_group": tournament.age_group,
            "interval": interval,
            "conflicting_tournament_id": "unplaced",
            "conflicting_age_group": tournament.age_group,
            "conflicting_interval": "ingen gyldig slot",
            "message": message,
        }

    def _sequence_same_arena_day_start_times(self, plan: SeasonPlan) -> List[Dict[str, str]]:
        groups: Dict[Tuple[date, str], List[Tournament]] = {}
        sequence_failures: List[Dict[str, str]] = []
        for tournament in plan.tournaments:
            if tournament.cancelled:
                continue
            groups.setdefault((tournament.date, tournament.arena), []).append(tournament)

        # Hard limit: tournaments must start between 10:00 and 16:00.
        _LATEST_START_MINUTES = 16 * 60  # 16:00
        _EARLIEST_START_MINUTES = 10 * 60  # 10:00

        for (_tournament_date, _arena), tournaments in groups.items():
            if len(tournaments) < 2:
                continue

            ordered = sorted(
                tournaments,
                key=lambda t: (t.start_time or DEFAULT_TOURNAMENT_START_TIME, t.age_group, t.id),
            )
            cursor_minutes: Optional[int] = None
            for tournament in ordered:
                start_time = tournament.start_time or DEFAULT_TOURNAMENT_START_TIME
                try:
                    requested = datetime.strptime(start_time, "%H:%M")
                    requested_minutes = requested.hour * 60 + requested.minute
                except ValueError:
                    requested_minutes = _EARLIEST_START_MINUTES

                if cursor_minutes is None:
                    cursor_minutes = requested_minutes
                else:
                    cursor_minutes = max(cursor_minutes, requested_minutes)

                if cursor_minutes > _LATEST_START_MINUTES:
                    overflow_time = f"{cursor_minutes // 60:02d}:{cursor_minutes % 60:02d}"
                    sequence_failures.append(
                        {
                            "date": tournament.date.isoformat(),
                            "arena": tournament.arena,
                            "tournament_id": tournament.id,
                            "age_group": tournament.age_group,
                            "interval": f"{tournament.date.isoformat()} {overflow_time}",
                            "conflicting_tournament_id": "sequence_overflow",
                            "conflicting_age_group": tournament.age_group,
                            "conflicting_interval": "seneste gyldige start er 16:00",
                            "message": (
                                f"Arena conflict {tournament.arena} {tournament.date.isoformat()}: "
                                f"{tournament.id} ({tournament.age_group}) måtte starte {overflow_time}, "
                                "etter seneste gyldige start 16:00."
                            ),
                        }
                    )

                tournament.start_time = f"{cursor_minutes // 60:02d}:{cursor_minutes % 60:02d}"

                round_length = self.round_length_for_age_group.get(tournament.age_group)
                duration_minutes = (
                    matchday_duration_minutes(round_length, max(g.round_number for g in tournament.games))
                    if round_length and tournament.games
                    else 0
                )
                cursor_minutes += duration_minutes + ARENA_DAY_SEQUENCE_BUFFER_MINUTES

        return sequence_failures

    def _ordered_host_candidates(
        self,
        age_group: str,
        original_host: str,
        tournament_date: date,
        host_targets_by_age: Dict[str, Dict[str, int]],
        host_counts_by_age: Dict[str, Dict[str, int]],
    ) -> List[str]:
        candidates: List[str] = []
        seen: Set[str] = set()
        for team in self.roster.by_age_group(age_group):
            if team.club in seen:
                continue
            candidates.append(team.club)
            seen.add(team.club)
        for club in self.roster.clubs():
            if club in seen:
                continue
            candidates.append(club)
            seen.add(club)
        if original_host not in seen:
            candidates.insert(0, original_host)
            seen.add(original_host)

        target_counts = host_targets_by_age.get(age_group, {})
        actual_counts = host_counts_by_age.setdefault(age_group, {})

        def score(club: str) -> Tuple[int, int, int, int]:
            actual_minus_target = actual_counts.get(club, 0) - target_counts.get(club, 0)
            return (
                0 if club == original_host else 1,
                actual_minus_target,
                -target_counts.get(club, 0),
                candidates.index(club),
            )

        return sorted(candidates, key=score)

    @staticmethod
    def _expected_monthly_load(window_start: date, window_end: date, tournament_count: int) -> float:
        if tournament_count <= 0:
            return 0.0
        months_spanned = (window_end.year - window_start.year) * 12 + (window_end.month - window_start.month) + 1
        return tournament_count / max(1, months_spanned)

    @staticmethod
    def _christmas_split_date(window_start: date, window_end: date) -> Optional[date]:
        split = date(window_start.year, 12, 24)
        if window_start <= split <= window_end:
            return split
        return None

    def _has_split_tournament_targets(self) -> bool:
        return any(
            targets.get("before_christmas") is not None or targets.get("after_christmas") is not None
            for targets in self.target_tournament_counts_by_age_group.values()
        )

    def _split_tournament_counts_for_age_groups(
        self,
        age_groups: Sequence[str],
        free_dates: Sequence[date],
        split_date: date,
    ) -> tuple[Dict[str, int], Dict[str, int]]:
        before_dates = [d for d in free_dates if d < split_date]
        after_dates = [d for d in free_dates if d >= split_date]
        before_weight = max(1, len(before_dates))
        after_weight = max(1, len(after_dates))

        before_counts: Dict[str, int] = {}
        after_counts: Dict[str, int] = {}
        for age_group in age_groups:
            total = self._target_tournaments_for_age_group(age_group)
            if total <= 0:
                before_counts[age_group] = 0
                after_counts[age_group] = 0
                continue

            targets = self.target_tournament_counts_by_age_group.get(age_group, {})
            split_before_weight = targets.get("before_christmas") or 0
            split_after_weight = targets.get("after_christmas") or 0
            if split_before_weight > 0 and split_after_weight > 0:
                total_weight = split_before_weight + split_after_weight
                before = int(round(total * split_before_weight / total_weight))
            else:
                before = int(round(total * before_weight / (before_weight + after_weight)))

            before = max(0, min(total, before))
            after = max(0, total - before)
            before_counts[age_group] = before
            after_counts[age_group] = after

        return before_counts, after_counts

    def _build_split_date_schedule(
        self,
        age_groups: Sequence[str],
        free_dates: Sequence[date],
        window_start: date,
        window_end: date,
        target_counts: Dict[str, int],
    ) -> List[Tuple[date, str]]:
        split_date = self._christmas_split_date(window_start, window_end)
        if split_date is None:
            optimized, _ = self._build_global_date_schedule(
                age_groups,
                free_dates,
                window_start,
                window_end,
                target_counts,
            )
            return optimized

        before_counts, after_counts = self._split_tournament_counts_for_age_groups(age_groups, free_dates, split_date)
        before_dates = [d for d in free_dates if d < split_date]
        after_dates = [d for d in free_dates if d >= split_date]

        self._month_counts = {}
        self._hosting_days_by_club_month = {}
        self._tournament_participations = {self._team_key(team): 0 for team in self.roster.teams}
        self._running_game_counts = {}
        self._opponent_history = {}
        self._invite_counts = {self._team_key(team): 0 for team in self.roster.teams}
        self._grouped_with = {}
        self._team_last_date = {}
        self._team_game_counts = {}
        self._club_cap_overrides = 0

        scheduled_before, _ = self._build_global_date_schedule(
            age_groups,
            before_dates,
            window_start,
            split_date - timedelta(days=1),
            before_counts,
            continue_from_current_state=True,
        )
        scheduled_after, _ = self._build_global_date_schedule(
            age_groups,
            after_dates,
            split_date,
            window_end,
            after_counts,
            continue_from_current_state=True,
        )
        return sorted(scheduled_before + scheduled_after, key=lambda item: (item[0], item[1]))

    def _record_month(self, tournament_date: date) -> None:
        key = (tournament_date.year, tournament_date.month)
        self._month_counts[key] = self._month_counts.get(key, 0) + 1

    def _score_candidate_date(
        self,
        candidate_date: date,
        age_group: str,
        candidate_participants: Sequence[Team],
        expected_per_month: float,
        tournament_weight: float = 0.0,
        date_preferences: Optional[List[DatePreference]] = None,
    ) -> float:
        # Penalise dates where all clubs would exceed the monthly hosting-day cap.
        # Predict the most likely host as the club with the fewest distinct hosting
        # days in the candidate month so far (i.e. least loaded). If even that club
        # already has max_hosting_days_per_month days (none of which is the candidate
        # date itself), return a large penalty to steer date selection away.
        if self.club_arenas and self.max_hosting_days_per_month is not None and self.max_hosting_days_per_month > 0:
            month_key = (candidate_date.year, candidate_date.month)
            min_days = None
            for club in self.club_arenas:
                days = self._hosting_days_by_club_month.get((club, month_key), set())
                day_count = len(days - {candidate_date})
                if min_days is None or day_count < min_days:
                    min_days = day_count
            if min_days is not None and min_days >= self.max_hosting_days_per_month:
                return 1e6

        repeat_penalty = 0.0
        participants = list(candidate_participants)
        if len(participants) >= 2:
            pair_count = 0
            repeat_total = 0
            for i, team_a in enumerate(participants):
                for team_b in participants[i + 1 :]:
                    pair = frozenset((self._team_key(team_a), self._team_key(team_b)))
                    repeat_total += self._opponent_history.get(pair, 0)
                    pair_count += 1
            if pair_count:
                repeat_penalty = repeat_total / pair_count

        month_penalty = max(0.0, self.month_load_ratio(candidate_date, expected_per_month) - 1.0)

        # Compute the subjective weight term (positive = penalise, negative = reward).
        # Cap magnitude to 2x the larger organic penalty so weights influence but
        # cannot completely override structural constraints.
        prefs = date_preferences if date_preferences is not None else self.date_preferences
        date_pref_total = sum(
            p.vekt for p in prefs if p.fra <= candidate_date <= p.til
        )
        raw_weight = tournament_weight + date_pref_total
        cap = max(abs(repeat_penalty), abs(month_penalty)) * 2
        if cap > 0.0:
            if raw_weight > cap:
                raw_weight = cap
            elif raw_weight < -cap:
                raw_weight = -cap

        return repeat_penalty + month_penalty + raw_weight

    def month_load_ratio(self, tournament_date: date, expected_per_month: float) -> float:
        if expected_per_month <= 0:
            return 0.0
        key = (tournament_date.year, tournament_date.month)
        return self._month_counts.get(key, 0) / expected_per_month

    def _parallel_games_for(self, age_group: str) -> int:
        return max(1, self.parallel_games_for_age_group.get(age_group, 1))

    def _check_overlap_collision(
        self,
        tournament_date: date,
        age_group: str,
        scheduled_by_date: Dict[date, List[str]],
    ) -> Optional[str]:
        for existing in scheduled_by_date.get(tournament_date, []):
            if age_group in overlapping_age_groups(existing) or existing in overlapping_age_groups(age_group):
                return existing
        return None

    def _record_grouping(self, participants: Sequence[Team]) -> None:
        labels = [self._team_key(team) for team in participants]
        games_added = max(0, len(participants) - 1)
        for team in participants:
            key = self._team_key(team)
            self._invite_counts[key] = self._invite_counts.get(key, 0) + 1
            self._tournament_participations[key] = self._tournament_participations.get(key, 0) + 1
            grouped = self._grouped_with.setdefault(key, set())
            grouped.update(label for label in labels if label != key)
            self._running_game_counts[key] = self._running_game_counts.get(key, 0) + games_added

    def _record_opponent_history(self, games: Sequence[Game]) -> None:
        for game in games:
            if game.home is None or game.away is None:
                continue
            pair = frozenset((self._team_key(game.home), self._team_key(game.away)))
            self._opponent_history[pair] = self._opponent_history.get(pair, 0) + 1


SeasonPlanner._pick_spread_dates = _pick_spread_dates
SeasonPlanner._target_tournaments_for_age_group = _target_tournaments_for_age_group
SeasonPlanner._assign_hosts = _assign_hosts
SeasonPlanner._find_slot_for_tournament = _find_slot_for_tournament
SeasonPlanner._next_age_group = _next_age_group
SeasonPlanner._select_participants = _select_participants
SeasonPlanner._cap_per_club_deficit_aware = _cap_per_club_deficit_aware
SeasonPlanner._participant_limit_for = _participant_limit_for
SeasonPlanner._max_teams_for = _max_teams_for
SeasonPlanner._max_club_teams_for = _max_club_teams_for
SeasonPlanner._age_group_deficit_spread = _age_group_deficit_spread
SeasonPlanner._expected_average_for = _expected_average_for
SeasonPlanner._deficit_score = _deficit_score
SeasonPlanner._normalized_invite_count = _normalized_invite_count
SeasonPlanner._pick_least_recently_grouped = _pick_least_recently_grouped
SeasonPlanner._pick_scored_participants = _pick_scored_participants
SeasonPlanner._participant_selection_score = staticmethod(_participant_selection_score)
SeasonPlanner._hosting_targets_for_age_group = _hosting_targets_for_age_group
SeasonPlanner._proportional_integer_targets = staticmethod(_proportional_integer_targets)
SeasonPlanner._default_target_count = staticmethod(_default_target_count)
SeasonPlanner._build_fairness_gate = _build_fairness_gate
SeasonPlanner._compute_game_counts = _compute_game_counts
SeasonPlanner._scan_game_count_warnings = _scan_game_count_warnings
SeasonPlanner._scan_per_team_share_warnings = _scan_per_team_share_warnings
SeasonPlanner._scan_feasibility_warnings = _scan_feasibility_warnings
SeasonPlanner._scan_month_load_warnings = _scan_month_load_warnings
SeasonPlanner._scan_club_load_warnings = _scan_club_load_warnings
SeasonPlanner._hosting_fairness_breakdown = _hosting_fairness_breakdown
SeasonPlanner._scan_hosting_warnings = _scan_hosting_warnings
SeasonPlanner.generate_round_robin_games = staticmethod(_generate_round_robin_games)
SeasonPlanner._rebalance_rounds = staticmethod(_rebalance_rounds)
SeasonPlanner._best_round_subset = staticmethod(_best_round_subset)
SeasonPlanner._arena_counts = staticmethod(_arena_counts)
SeasonPlanner._diversity_score = _diversity_score
SeasonPlanner._pairwise_matchup_score = _pairwise_matchup_score
SeasonPlanner._month_balance_score = _month_balance_score
SeasonPlanner.rules_report = _rules_report
SeasonPlanner.DEFAULT_TOURNAMENT_START_TIME = DEFAULT_TOURNAMENT_START_TIME
SeasonPlanner.ARENA_DAY_SEQUENCE_BUFFER_MINUTES = ARENA_DAY_SEQUENCE_BUFFER_MINUTES
