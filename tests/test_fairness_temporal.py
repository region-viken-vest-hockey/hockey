"""issue #312: per-team temporal fairness metrics in `build_fairness_gate`.

Regression coverage for the two soft fairness metrics that measure *when*
teams finish, not just how many games they play:

- ``team_finish_gap``: how far a team's last tournament trails the last
  tournament played by any team in its own age group (never the overall
  season end date).
- ``team_intra_season_gap``: the largest gap between a team's own
  consecutive tournaments during the season.
"""

from __future__ import annotations

from datetime import date, datetime

from tournament_scheduler.models import Game, Roster, SeasonPlan, Team, Tournament
from tournament_scheduler.season_planner import SeasonPlanner
from tournament_scheduler.testing.canonical_input import OfflineScheduler


def _tournament(day: date, teams: list[Team], arena: str = "Kongsberghallen", host_club: str = "Kongsberg") -> Tournament:
    games = [
        Game(home=teams[i], away=teams[i + 1], parallel_slot=0, round_number=1)
        for i in range(len(teams) - 1)
    ]
    return Tournament(date=day, arena=arena, age_group=teams[0].age_group, teams=teams, games=games, host_club=host_club)


def _build_gate(teams: list[Team], plan: SeasonPlan) -> dict:
    planner = SeasonPlanner(
        scheduler=OfflineScheduler([]),
        roster=Roster(teams=teams),
        club_arenas={"Kongsberg": "Kongsberghallen", "Tonsberg": "Tonsberghallen", "Filler": "Fillerhallen"},
        parallel_games_for_age_group={"U9": 3},
    )
    planner._compute_game_counts(plan.tournaments)
    return planner._build_fairness_gate(plan)


def _metric(gate: dict, key: str) -> dict:
    return next(m for m in gate["metrics"] if m["key"] == key)


def test_team_finish_gap_warns_for_team_finishing_weeks_early_with_equal_game_count():
    team_a = Team(club="Kongsberg", label="Kongsberg A", age_group="U9")
    team_b = Team(club="Tonsberg", label="Tonsberg A", age_group="U9")
    team_c = Team(club="Filler", label="Filler A", age_group="U9")

    plan = SeasonPlan(
        tournaments=[
            _tournament(date(2026, 10, 4), [team_a, team_b]),
            _tournament(date(2026, 11, 8), [team_a, team_b]),
            _tournament(date(2027, 1, 10), [team_a, team_c]),
            _tournament(date(2027, 2, 7), [team_a, team_c]),
            _tournament(date(2027, 2, 21), [team_b, team_c]),
            _tournament(date(2027, 4, 4), [team_b, team_c]),
        ],
        start_date=datetime(2026, 9, 1),
        end_date=datetime(2027, 4, 30),
    )
    gate = _build_gate([team_a, team_b, team_c], plan)

    # Team A and Team B both play exactly four tournaments -- the existing
    # game-count-spread metric alone must not distinguish them.
    assert _metric(gate, "game_count_spread")["value"] == 0

    finish_gap = _metric(gate, "team_finish_gap")
    assert finish_gap["status"] == "warn"
    assert finish_gap["value"] == 8.0  # Feb 7 -> Apr 4 is 56 days
    assert "Kongsberg A" in finish_gap["detail"]
    assert "2027-02-07" in finish_gap["detail"]
    assert "2027-04-04" in finish_gap["detail"]


def test_team_finish_gap_passes_when_all_teams_finish_together():
    team_a = Team(club="Kongsberg", label="Kongsberg A", age_group="U9")
    team_b = Team(club="Tonsberg", label="Tonsberg A", age_group="U9")

    plan = SeasonPlan(
        tournaments=[
            _tournament(date(2026, 10, 4), [team_a, team_b]),
            _tournament(date(2027, 2, 7), [team_a, team_b]),
        ],
        start_date=datetime(2026, 9, 1),
        end_date=datetime(2027, 4, 30),
    )
    gate = _build_gate([team_a, team_b], plan)

    finish_gap = _metric(gate, "team_finish_gap")
    assert finish_gap["status"] == "pass"
    assert finish_gap["value"] == 0.0


def test_team_intra_season_gap_warns_for_excessive_mid_season_hole_with_close_finish():
    team_x = Team(club="Kongsberg", label="Kongsberg A", age_group="U9")
    team_y = Team(club="Tonsberg", label="Tonsberg A", age_group="U9")
    team_z = Team(club="Filler", label="Filler A", age_group="U9")

    plan = SeasonPlan(
        tournaments=[
            _tournament(date(2026, 10, 4), [team_x, team_y]),
            _tournament(date(2026, 11, 8), [team_x, team_y]),
            _tournament(date(2026, 12, 6), [team_y, team_z]),
            _tournament(date(2027, 2, 7), [team_y, team_z]),
            _tournament(date(2027, 4, 4), [team_x, team_y, team_z]),
        ],
        start_date=datetime(2026, 9, 1),
        end_date=datetime(2027, 4, 30),
    )
    gate = _build_gate([team_x, team_y, team_z], plan)

    # Both teams finish on the same date, so the finish-gap metric sees no
    # problem -- this case is only caught by the intra-season-gap metric.
    finish_gap = _metric(gate, "team_finish_gap")
    assert finish_gap["status"] == "pass"
    assert finish_gap["value"] == 0.0

    intra_gap = _metric(gate, "team_intra_season_gap")
    assert intra_gap["status"] == "warn"
    assert intra_gap["value"] == 21.0  # Nov 8 -> Apr 4 is 147 days
    assert "Kongsberg A" in intra_gap["detail"]
    assert "2026-11-08" in intra_gap["detail"]
    assert "2027-04-04" in intra_gap["detail"]


def test_temporal_metrics_are_measurements_not_policy_gate_authority():
    team_a = Team(club="Kongsberg", label="Kongsberg A", age_group="U9")
    team_b = Team(club="Tonsberg", label="Tonsberg A", age_group="U9")
    team_c = Team(club="Filler", label="Filler A", age_group="U9")

    plan = SeasonPlan(
        tournaments=[
            _tournament(date(2026, 10, 4), [team_a, team_b]),
            _tournament(date(2027, 2, 7), [team_a, team_c]),
            _tournament(date(2027, 4, 25), [team_b, team_c]),
        ],
        start_date=datetime(2026, 9, 1),
        end_date=datetime(2027, 4, 30),
    )
    gate = _build_gate([team_a, team_b, team_c], plan)

    finish_gap = _metric(gate, "team_finish_gap")
    assert finish_gap["status"] == "warn"
    assert finish_gap["provenance"] == "measurement"

    policy_keys = {m["key"] for m in gate["policy_gate"]["metrics"]}
    measurement_keys = {m["key"] for m in gate["measurements"]}
    assert "team_finish_gap" not in policy_keys
    assert "team_intra_season_gap" not in policy_keys
    assert "team_finish_gap" in measurement_keys
    assert "team_intra_season_gap" in measurement_keys


def test_cancelled_tournaments_do_not_count_toward_temporal_metrics():
    team_a = Team(club="Kongsberg", label="Kongsberg A", age_group="U9")
    team_b = Team(club="Tonsberg", label="Tonsberg A", age_group="U9")

    late_tournament = _tournament(date(2027, 4, 25), [team_a, team_b])
    late_tournament.cancelled = True

    plan = SeasonPlan(
        tournaments=[
            _tournament(date(2026, 10, 4), [team_a, team_b]),
            _tournament(date(2027, 1, 10), [team_a, team_b]),
            late_tournament,
        ],
        start_date=datetime(2026, 9, 1),
        end_date=datetime(2027, 4, 30),
    )
    gate = _build_gate([team_a, team_b], plan)

    finish_gap = _metric(gate, "team_finish_gap")
    assert finish_gap["value"] == 0.0

    intra_gap = _metric(gate, "team_intra_season_gap")
    assert intra_gap["value"] == 14.0  # Oct 4 -> Jan 10 is 98 days
