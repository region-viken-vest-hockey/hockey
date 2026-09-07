from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from tournament_scheduler.models import CalendarEvent, Roster, SchedulingResult, SeasonPlan, Team, team_key
from tournament_scheduler.pipeline.input_workbook import load_workbook_config
from tournament_scheduler.scheduler import TournamentScheduler
from tournament_scheduler.season_planner import SeasonPlanner


def canonical_input_path(path: str | Path | None = None) -> Path:
    """Return the canonical ``input.xlsx`` path used by docs/tests."""
    if path is not None:
        return Path(path)
    return Path(__file__).resolve().parents[2] / "input.xlsx"


def load_canonical_input_data(path: str | Path | None = None) -> dict[str, Any]:
    """Load the canonical workbook config from ``input.xlsx``."""
    return load_workbook_config(canonical_input_path(path))


def canonical_input_has_teams(path: str | Path | None = None) -> bool:
    """Return whether the canonical workbook contains a populated ``Lag`` sheet.

    The repository's operational ``input.xlsx`` is allowed to be an empty
    pre-registration workbook. Slow/integration tests that exercise the real
    season roster should treat that state as an unavailable fixture, not as a
    product failure.
    """
    try:
        data = load_canonical_input_data(path)
    except Exception:
        return False
    return bool(data.get("teams"))


def load_canonical_roster(path: str | Path | None = None) -> tuple[Roster, dict[str, int]]:
    """Load the canonical roster + per-age-group parallel-game config."""
    data = load_canonical_input_data(path)
    roster = Roster(teams=[Team(**team) for team in data["teams"]])
    return roster, data.get("parallel_games", {})


def load_canonical_season_window(path: str | Path | None = None) -> tuple[datetime, datetime]:
    """Load the canonical season start/end from ``input.xlsx``."""
    data = load_canonical_input_data(path)
    return (
        datetime.fromisoformat(data["start_date"]),
        datetime.fromisoformat(data["end_date"]),
    )


def all_weekend_dates(start: datetime, end: datetime) -> list[date]:
    """Return all Saturday/Sunday dates inside a season window."""
    dates: list[date] = []
    current = start.date()
    while current <= end.date():
        if current.weekday() in (5, 6):
            dates.append(current)
        current += timedelta(days=1)
    return dates


class OfflineScheduler:
    """Deterministic scheduler for canonical tests with no calendar I/O."""

    def __init__(self, free_dates: list[date]):
        self.free_dates = free_dates
        self._real_scheduler = TournamentScheduler(
            calendar_sources=[], conflict_checkers=[], date_parser=None
        )

    def find_available_dates(self, start_date, end_date, **kwargs):
        return SchedulingResult(
            available_dates=list(self.free_dates),
            excluded_dates=[],
            exclusion_breakdown={},
            detailed_exclusions=[],
            total_weekends_checked=len(self.free_dates),
        )

    def find_arena_slot_for_date(
        self,
        check_date,
        host_club,
        required_minutes,
        events_by_club,
        preferred_start="11:00",
        club_calendar_status=None,
    ):
        return self._real_scheduler.find_arena_slot_for_date(
            check_date,
            host_club,
            required_minutes,
            events_by_club,
            preferred_start=preferred_start,
            club_calendar_status=club_calendar_status,
        )


def _normalize_cached_team_game_counts(plan_data: dict[str, Any], roster: Roster) -> dict[str, int]:
    """Rebuild per-team game counts from cached Stage 3 tournament data.

    Older cached plans in ``.pipeline/stage3_planning.json`` may still store
    club-aggregated labels or otherwise stale counts. The tests that consume
    the canonical cached plan need the current per-team key format, so we
    recompute it from the raw tournament/game payload instead of trusting the
    serialized summary fields.
    """
    duplicate_labels = {
        label for label, count in Counter(team.label for team in roster.teams).items() if count > 1
    }
    by_age_group_and_label = {
        (team.age_group, team.label): team_key(team, duplicate_labels)
        for team in roster.teams
    }
    counts: dict[str, int] = {key: 0 for key in by_age_group_and_label.values()}

    for tournament in plan_data.get("tournaments", []) or []:
        age_group = str(tournament.get("age_group", ""))
        for game in tournament.get("games", []) or []:
            for side in ("home", "away"):
                raw_label = str(game.get(side, ""))
                key = by_age_group_and_label.get((age_group, raw_label))
                if key is None:
                    matches = [
                        team_key(team, duplicate_labels)
                        for team in roster.teams
                        if team.age_group == age_group and team.label == raw_label
                    ]
                    if len(matches) == 1:
                        key = matches[0]
                if key is not None:
                    counts[key] = counts.get(key, 0) + 1

    return {key: count for key, count in counts.items() if count > 0}


def _age_group_spreads(roster: Roster, counts: dict[str, int]) -> dict[str, int]:
    duplicate_labels = {
        label for label, count in Counter(team.label for team in roster.teams).items() if count > 1
    }
    spreads: dict[str, int] = {}
    for age_group in roster.age_groups():
        team_counts = [
            counts.get(team_key(team, duplicate_labels), 0)
            for team in roster.by_age_group(age_group)
        ]
        if team_counts:
            spreads[age_group] = max(team_counts) - min(team_counts)
    return spreads


def build_canonical_planner(
    path: str | Path | None = None,
    *,
    events_by_club: dict[str, list[CalendarEvent]] | None = None,
    free_dates: list[date] | None = None,
    **planner_kwargs: Any,
) -> tuple[SeasonPlanner, datetime, datetime]:
    """Build a SeasonPlanner from the canonical ``input.xlsx`` test fixture."""
    data = load_canonical_input_data(path)
    roster = Roster(teams=[Team(**team) for team in data["teams"]])
    parallel_games = data.get("parallel_games", {})
    start, end = load_canonical_season_window(path)
    clubs = sorted({team.club for team in roster.teams})
    planner = SeasonPlanner(
        scheduler=OfflineScheduler(free_dates or all_weekend_dates(start, end)),
        roster=roster,
        club_arenas={club: f"{club}hallen" for club in clubs},
        parallel_games_for_age_group=parallel_games,
        target_tournament_counts_by_age_group=data.get("target_tournament_counts_by_age_group"),
        events_by_club=events_by_club or {},
        **planner_kwargs,
    )

    cached_plan_path = Path(__file__).resolve().parents[2] / ".pipeline" / "stage3_planning.json"
    if cached_plan_path.exists():
        cached = json.loads(cached_plan_path.read_text(encoding="utf-8"))
        plan_data = cached.get("data", {}).get("plan", {})
        normalized_counts = _normalize_cached_team_game_counts(plan_data, roster)
        age_group_spreads = _age_group_spreads(roster, normalized_counts)
        plan = SeasonPlan(
            tournaments=[],
            start_date=datetime.fromisoformat(plan_data["start_date"]).date() if plan_data.get("start_date") else start.date(),
            end_date=datetime.fromisoformat(plan_data["end_date"]).date() if plan_data.get("end_date") else end.date(),
            diversity_score=plan_data.get("diversity_score", 0.0),
            pairwise_matchup_score=plan_data.get("pairwise_matchup_score", 0.0),
            month_balance_score=plan_data.get("month_balance_score", 0.0),
            arena_counts=plan_data.get("arena_counts", {}),
            team_game_counts=normalized_counts,
            game_count_spread=max(age_group_spreads.values()) if age_group_spreads else 0,
            game_count_spread_by_age_group=age_group_spreads,
            fairness_gate=plan_data.get("fairness_gate", {}),
            skipped_age_groups=plan_data.get("skipped_age_groups", []),
            arena_day_collisions=plan_data.get("arena_day_collisions", []),
        )
        planner._team_game_counts = dict(plan.team_game_counts)
        planner._team_last_date = {}
        planner._scan_per_team_share_warnings(skipped_age_groups=plan.skipped_age_groups)
        planner._canonical_plan = plan
        return planner, start, end

    return planner, start, end
