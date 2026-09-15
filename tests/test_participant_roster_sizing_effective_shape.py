from types import SimpleNamespace

from tournament_scheduler.models import Roster, Team
from datetime import date

from tournament_scheduler.participant_roster_sizing import (
    fixed_cohort_participants,
    fixed_cohort_shape_for,
    plan_roster_sizes_for_age_group,
    rebalance_roster_sizes_across_dates,
    target_tournaments_for_age_group,
)


def _planner(team_count: int, *, rounds: int = 5, parallel_games: int = 3):
    teams = [Team(f"Club {idx}", f"Team {idx}", "U10") for idx in range(1, team_count + 1)]
    planner = SimpleNamespace(
        roster=Roster(teams),
        rounds_per_tournament_for_age_group={"U10": rounds},
        parallel_games_for_age_group={"U10": parallel_games},
        participation_targets_by_age_group={},
        target_tournament_count=1,
    )
    planner._team_key = lambda team: team.label
    planner._team_at_target = lambda team, period=None: False
    return planner


def test_capacity_sizes_roster_to_parallel_game_capacity():
    # U8-shaped: 4 parallel games => 8 teams, independent of 5 rounds.
    planner = _planner(8, rounds=5, parallel_games=4)

    assert target_tournaments_for_age_group(planner, "U10") == 1
    assert plan_roster_sizes_for_age_group(planner, "U10") == [8]


def test_capacity_adapts_to_smaller_registered_pool():
    # Capacity 8 but only 6 real teams: use the six real teams, never invent
    # the missing two.
    planner = _planner(6, rounds=5, parallel_games=4)

    assert plan_roster_sizes_for_age_group(planner, "U10") == [6]


def test_full_pool_at_capacity_is_a_fixed_cohort():
    planner = _planner(8, rounds=5, parallel_games=4)

    shape = fixed_cohort_shape_for(planner, "U10")

    assert shape is not None
    assert shape.effective_team_count == 8
    assert shape.effective_round_count == 5
    assert fixed_cohort_participants(planner, "U10", planned_roster_size=6) == planner.roster.by_age_group("U10")


def test_configured_rounds_size_to_preferred_no_bye_shape_when_pool_supports_it():
    planner = _planner(8)

    assert target_tournaments_for_age_group(planner, "U10") == 1
    assert plan_roster_sizes_for_age_group(planner, "U10") == [6]


def test_configured_rounds_adapt_to_small_odd_registered_pool():
    planner = _planner(5)

    assert target_tournaments_for_age_group(planner, "U10") == 1
    assert plan_roster_sizes_for_age_group(planner, "U10") == [5]


def test_odd_full_pool_effective_shape_is_fixed_cohort_and_cannot_be_shrunk():
    planner = _planner(5)

    shape = fixed_cohort_shape_for(planner, "U10")

    assert shape is not None
    assert shape.effective_team_count == 5
    assert fixed_cohort_participants(planner, "U10", planned_roster_size=4) == planner.roster.by_age_group("U10")


def test_even_full_pool_effective_shape_is_fixed_cohort():
    planner = _planner(6)

    shape = fixed_cohort_shape_for(planner, "U10")

    assert shape is not None
    assert shape.effective_team_count == 6


def test_date_rebalancing_preserves_legal_odd_effective_slot_size():
    slot_date = date(2026, 9, 5)

    sizes_by_date, evidence = rebalance_roster_sizes_across_dates(
        [(slot_date, 1)], [5], capacity=6, distinct_team_count=5
    )

    assert evidence == []
    assert sizes_by_date[slot_date] == [5]


def test_configured_rounds_adapt_to_small_even_registered_pool():
    planner = _planner(4)

    assert target_tournaments_for_age_group(planner, "U10") == 1
    assert plan_roster_sizes_for_age_group(planner, "U10") == [4]
