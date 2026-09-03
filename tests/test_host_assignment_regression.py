from collections import Counter
from datetime import date, timedelta
from types import SimpleNamespace

from tournament_scheduler.host_assignment import assign_hosts, hosting_targets_for_age_group
from tournament_scheduler.models import Roster, Team


def _team(club: str, label: str, age_group: str) -> Team:
    return Team(club=club, label=label, age_group=age_group)


def test_host_assignment_is_proportional_within_age_group():
    """A larger club must carry a larger hosting share in that age group."""
    roster = Roster(
        teams=[
            _team("Jar", "Jar 1", "U10"),
            _team("Jar", "Jar 2", "U10"),
            _team("Jar", "Jar 3", "U10"),
            _team("Jutul", "Jutul 1", "U10"),
            _team("Jutul", "Jutul 2", "U10"),
            _team("Kongsberg", "Kongsberg", "U10"),
            # A club that only has another age group must never become U10 host.
            _team("Holmen", "Holmen", "U11"),
        ]
    )
    planner = SimpleNamespace(roster=roster)
    scheduled = [(date(2026, 9, 5) + timedelta(days=7 * i), "U10") for i in range(6)]

    assert hosting_targets_for_age_group(planner, "U10", 6) == {
        "Jar": 3,
        "Jutul": 2,
        "Kongsberg": 1,
    }

    hosts = assign_hosts(planner, scheduled)
    assert Counter(hosts) == Counter({"Jar": 3, "Jutul": 2, "Kongsberg": 1})


def test_zero_target_club_does_not_steal_large_club_hosting_share():
    """Rounded target zero must stay zero while other clubs are below target."""
    roster = Roster(
        teams=[
            _team("Jar", "Jar 1", "U10"),
            _team("Jar", "Jar 2", "U10"),
            _team("Jar", "Jar 3", "U10"),
            _team("Jar", "Jar 4", "U10"),
            _team("Jutul", "Jutul", "U10"),
            _team("Kongsberg", "Kongsberg", "U10"),
        ]
    )
    planner = SimpleNamespace(roster=roster)
    scheduled = [(date(2026, 9, 5) + timedelta(days=7 * i), "U10") for i in range(4)]

    targets = hosting_targets_for_age_group(planner, "U10", 4)
    assert targets == {"Jar": 3, "Jutul": 1, "Kongsberg": 0}
    assert Counter(assign_hosts(planner, scheduled)) == Counter({"Jar": 3, "Jutul": 1})


def test_club_without_team_in_age_group_cannot_host_that_age_group():
    """Regression for the old plan where Jutul hosted JU10 without a JU10 team."""
    roster = Roster(
        teams=[
            _team("Jar", "Jar 1", "JU10"),
            _team("Jar", "Jar 2", "JU10"),
            _team("Skien", "Skien", "JU10"),
            _team("Jutul", "Jutul", "U10"),
        ]
    )
    planner = SimpleNamespace(roster=roster)
    scheduled = [(date(2026, 9, 5) + timedelta(days=7 * i), "JU10") for i in range(3)]

    hosts = assign_hosts(planner, scheduled)
    assert "Jutul" not in hosts
    assert Counter(hosts) == Counter({"Jar": 2, "Skien": 1})
