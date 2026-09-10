"""issue #305: wording/unit fixes for specific `build_fairness_gate` metrics.

These are unit tests over the metrics returned by `fairness_scoring.build_fairness_gate`
(consumed by `rules_model.build_rules_model` for the Regler page):

- `arena_day_collisions` must describe interval-overlap semantics, not a
  same-arena/same-day ban (same arena + same day is fine as long as the
  reserved time intervals don't overlap).
- `same_weekend_club_load` groups by ISO (year, week) -- an actual calendar
  week, not a weekend -- so its label must say "uke".
- `game_count_spread` must report the raw per-age-group games spread
  (bounded [0, N]) rather than a value normalized/capped to [0, 1] compared
  against a raw-games threshold.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from tournament_scheduler.models import Game, Roster, SeasonPlan, Team, Tournament
from tournament_scheduler.season_planner import SeasonPlanner
from tournament_scheduler.testing.canonical_input import OfflineScheduler


def _build_planner_and_plan():
    jar_a = Team(club="Jar", label="Jar A", age_group="U10")
    jar_b = Team(club="Jar", label="Jar B", age_group="U10")
    jutul = Team(club="Jutul", label="Jutul A", age_group="U10")
    roster = Roster(teams=[jar_a, jar_b, jutul])
    planner = SeasonPlanner(
        scheduler=OfflineScheduler([]),
        roster=roster,
        club_arenas={"Jar": "Jarhallen", "Jutul": "Jutulhallen"},
        parallel_games_for_age_group={"U10": 3},
    )
    plan = SeasonPlan(
        tournaments=[
            Tournament(
                date=date(2026, 9, 5),
                arena="Jarhallen",
                age_group="U10",
                teams=[jar_a, jutul],
                games=[Game(home=jar_a, away=jutul, parallel_slot=0, round_number=1)],
                host_club="Jar",
            )
        ],
        start_date=datetime(2026, 9, 1),
        end_date=datetime(2026, 12, 1),
        diversity_score=1.0,
        pairwise_matchup_score=1.0,
        month_balance_score=1.0,
        game_count_spread=1,
    )
    planner._compute_game_counts(plan.tournaments)
    planner._scan_hosting_warnings(plan)
    return planner, plan


def test_arena_day_collisions_wording_describes_interval_overlap():
    planner, plan = _build_planner_and_plan()
    gate = planner._build_fairness_gate(plan)
    metric = next(m for m in gate["metrics"] if m["key"] == "arena_day_collisions")

    assert "dobbeltbooking av samme arena samme dag" not in metric["detail"]
    assert "tidsintervaller" in metric["detail"]


def test_same_weekend_club_load_label_says_uke():
    planner, plan = _build_planner_and_plan()
    gate = planner._build_fairness_gate(plan)
    metric = next(m for m in gate["metrics"] if m["key"] == "same_weekend_club_load")

    assert metric["label"] == "Klubblast per uke"


def test_game_count_spread_reports_raw_games_not_normalized_ratio():
    planner, plan = _build_planner_and_plan()
    # Jar A plays 1 game, Jar B plays 0 -> raw per-age-group spread is 1.
    gate = planner._build_fairness_gate(plan)
    metric = next(m for m in gate["metrics"] if m["key"] == "game_count_spread")

    assert metric["value"] == 1
    assert metric["unit"] == " kamper"
