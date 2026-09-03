from datetime import date
from types import SimpleNamespace

from tournament_scheduler.html.data_computation import canonical_rvv_club_name
from tournament_scheduler.models import Roster, SeasonPlan, Team, Tournament
from tournament_scheduler.warnings import hosting_fairness_breakdown


def test_missing_calendar_club_keeps_proportional_hosting_obligation():
    roster = Roster(
        teams=[
            Team(club="Jar", label="Jar 1", age_group="U10"),
            Team(club="Jar", label="Jar 2", age_group="U10"),
            Team(club="Sandefjord", label="Sandefjord", age_group="U10"),
        ]
    )
    planner = SimpleNamespace(
        roster=roster,
        available_calendar_clubs={"Jar"},
        events_by_club={"Jar": []},
        club_arenas={"Jar": "Jarahallen", "Sandefjord": "Sandefjord ishall"},
        fallback_host_substitutions=[],
    )
    plan = SeasonPlan(
        tournaments=[
            Tournament(date=date(2026, 9, 5), arena="Jarahallen", age_group="U10", host_club="Jar"),
            Tournament(date=date(2026, 10, 3), arena="Jarahallen", age_group="U10", host_club="Jar"),
            Tournament(
                date=date(2026, 11, 7),
                arena="Sandefjord ishall",
                age_group="U10",
                host_club="Sandefjord",
            ),
        ],
        start_date=date(2026, 9, 1),
        end_date=date(2027, 4, 30),
    )

    result = hosting_fairness_breakdown(planner, plan)
    rows = {(row["age_group"], row["club"]): row for row in result["age_group_breakdown"]}
    sandefjord = canonical_rvv_club_name("Sandefjord")

    assert "Sandefjord" in result["missing_calendar_clubs"]
    assert rows[("U10", "Jar")]["teams"] == 2
    assert rows[("U10", "Jar")]["actual"] == 2
    assert rows[("U10", "Jar")]["expected"] == 2.0
    assert rows[("U10", sandefjord)]["teams"] == 1
    assert rows[("U10", sandefjord)]["actual"] == 1
    assert rows[("U10", sandefjord)]["expected"] == 1.0
    assert result["max_deviation"] == 0
    assert "fortsatt med i avviksberegningen" in result["detail"]
