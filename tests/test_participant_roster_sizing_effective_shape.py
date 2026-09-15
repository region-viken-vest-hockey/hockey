from types import SimpleNamespace

from tournament_scheduler.models import Roster, Team
from tournament_scheduler.participant_roster_sizing import (
    plan_roster_sizes_for_age_group,
    target_tournaments_for_age_group,
)


def _planner(team_count: int, *, rounds: int = 5, parallel_games: int = 3):
    teams = [Team(f"Club {idx}", f"Team {idx}", "U10") for idx in range(1, team_count + 1)]
    return SimpleNamespace(
        roster=Roster(teams),
        rounds_per_tournament_for_age_group={"U10": rounds},
        parallel_games_for_age_group={"U10": parallel_games},
        participation_targets_by_age_group={},
        target_tournament_count=1,
    )


def test_configured_rounds_size_to_preferred_no_bye_shape_when_pool_supports_it():
    planner = _planner(8)

    assert target_tournaments_for_age_group(planner, "U10") == 1
    assert plan_roster_sizes_for_age_group(planner, "U10") == [6]


def test_configured_rounds_adapt_to_small_odd_registered_pool():
    planner = _planner(5)

    assert target_tournaments_for_age_group(planner, "U10") == 1
    assert plan_roster_sizes_for_age_group(planner, "U10") == [5]


def test_configured_rounds_adapt_to_small_even_registered_pool():
    planner = _planner(4)

    assert target_tournaments_for_age_group(planner, "U10") == 1
    assert plan_roster_sizes_for_age_group(planner, "U10") == [4]
