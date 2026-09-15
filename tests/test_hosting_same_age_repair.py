"""Tests for the same-age hosting-coverage repair (issue #329)."""

from datetime import date

from tournament_scheduler.hosting_coverage import hosting_coverage_matrix
from tournament_scheduler.hosting_same_age_repair import same_age_reallocation_candidates
from tournament_scheduler.hosting_same_age_repair_apply import attempt_same_age_repairs
from tournament_scheduler.models import Roster, SeasonPlan, Team, Tournament
from tournament_scheduler.season_planner import SeasonPlanner
from tournament_scheduler.testing.canonical_input import OfflineScheduler


def _team(club, label, age_group, target=None):
    return Team(club=club, label=label, age_group=age_group, target_tournament_count=target)


def _tournament_dict(id_, on_date, arena, age_group, host_club, team_clubs, cancelled=False):
    return {
        "id": id_,
        "date": on_date.isoformat(),
        "arena": arena,
        "age_group": age_group,
        "host_club": host_club,
        "cancelled": cancelled,
        "teams": [{"club": club} for club in team_clubs],
    }


class TestSameAgeReallocationCandidates:
    def test_finds_tournament_where_club_already_participates(self):
        tournaments = [
            _tournament_dict("t1", date(2027, 1, 16), "Jarhallen", "U10", "Jar", ["Kongsberg", "Jar", "Jar"]),
        ]
        candidates = same_age_reallocation_candidates("Kongsberg", "U10", tournaments)
        assert [c["tournament_id"] for c in candidates] == ["t1"]

    def test_excludes_tournaments_the_club_already_hosts(self):
        tournaments = [
            _tournament_dict("t1", date(2027, 1, 16), "Kongsberghallen", "U10", "Kongsberg", ["Kongsberg", "Jar"]),
        ]
        assert same_age_reallocation_candidates("Kongsberg", "U10", tournaments) == []

    def test_excludes_a_different_age_group(self):
        tournaments = [
            _tournament_dict("t1", date(2027, 1, 16), "Jarhallen", "JU12", "Jar", ["Kongsberg", "Jar"]),
        ]
        assert same_age_reallocation_candidates("Kongsberg", "U10", tournaments) == []

    def test_no_candidates_when_club_does_not_participate(self):
        tournaments = [
            _tournament_dict("t1", date(2027, 1, 16), "Jarhallen", "U10", "Jar", ["Jar", "Jar"]),
        ]
        assert same_age_reallocation_candidates("Kongsberg", "U10", tournaments) == []

    def test_joint_registration_resolves_through_constituents(self):
        tournaments = [
            _tournament_dict(
                "t1", date(2027, 1, 16), "Jarhallen", "U10", "Jar", ["Kongsberg/Tønsberg", "Jar", "Jar"]
            ),
        ]
        candidates = same_age_reallocation_candidates("Kongsberg", "U10", tournaments)
        assert [c["tournament_id"] for c in candidates] == ["t1"]

    def test_candidates_ordered_latest_date_first(self):
        tournaments = [
            _tournament_dict("early", date(2027, 1, 9), "Jarhallen", "U10", "Jar", ["Kongsberg", "Jar"]),
            _tournament_dict("late", date(2027, 2, 27), "Jarhallen", "U10", "Jar", ["Kongsberg", "Jar"]),
        ]
        candidates = same_age_reallocation_candidates("Kongsberg", "U10", tournaments)
        assert [c["tournament_id"] for c in candidates] == ["late", "early"]


def _build_repair_scenario():
    """A minimal roster/plan reproducing the production Kongsberg pattern:
    Kongsberg has a registered U10 team that already plays in a Jar-hosted
    U10 tournament but hosts nothing anywhere -- no surplus hosting exists
    for the cross-age repair to reuse, so only a same-age host swap can
    resolve the obligation.
    """
    teams = [
        _team("Kongsberg", "Kongsberg U10", "U10", target=2),
        _team("Jar", "Jar U10-1", "U10", target=2),
        _team("Jar", "Jar U10-2", "U10", target=2),
        _team("Jar", "Jar JU12-1", "JU12", target=0),
        _team("Jar", "Jar JU12-2", "JU12", target=0),
        _team("Jar", "Jar JU12-3", "JU12", target=0),
    ]
    roster = Roster(teams=teams)
    club_arenas = {"Kongsberg": "Kongsberghallen", "Jar": "Jarhallen"}
    planner = SeasonPlanner(
        scheduler=OfflineScheduler(free_dates=[]),
        roster=roster,
        club_arenas=club_arenas,
        parallel_games_for_age_group={"U10": 3, "JU12": 3},
        round_length_for_age_group={"U10": 30, "JU12": 30},
    )
    by_label = {t.label: t for t in teams}

    u10_participants = [by_label["Kongsberg U10"], by_label["Jar U10-1"], by_label["Jar U10-2"]]
    t1 = Tournament(
        id="t1",
        date=date(2027, 1, 16),
        arena="Jarhallen",
        age_group="U10",
        teams=u10_participants,
        games=planner.generate_round_robin_games(u10_participants, planner._parallel_games_for("U10")),
        host_club="Jar",
        start_time="11:00",
    )
    ju12_participants = [by_label["Jar JU12-1"], by_label["Jar JU12-2"], by_label["Jar JU12-3"]]
    t2 = Tournament(
        id="t2",
        date=date(2027, 1, 9),
        arena="Jarhallen",
        age_group="JU12",
        teams=ju12_participants,
        games=planner.generate_round_robin_games(ju12_participants, planner._parallel_games_for("JU12")),
        host_club="Jar",
        start_time="11:00",
    )

    plan = SeasonPlan(tournaments=[t1, t2], start_date=date(2026, 9, 1), end_date=date(2027, 4, 30))
    for tournament in plan.tournaments:
        planner._record_grouping(tournament.teams, None)
        planner._record_opponent_history(tournament.games)
        month_key = (tournament.date.year, tournament.date.month)
        planner._hosting_days_by_club_month.setdefault((tournament.host_club, month_key), set()).add(
            tournament.date
        )
    return planner, plan


class TestAttemptSameAgeRepairs:
    def test_reassigns_host_to_the_deficit_club_without_touching_participants(self):
        planner, plan = _build_repair_scenario()

        log = attempt_same_age_repairs(planner, plan)

        repaired = next(e for e in log if e["club"] == "Kongsberg" and e["age_group"] == "U10")
        assert repaired["status"] == "repaired"
        assert repaired["donor_tournament_id"] == "t1"

        new_tournament = next(t for t in plan.tournaments if t.id == repaired["tournament_id"])
        assert new_tournament.host_club == "Kongsberg"
        assert new_tournament.age_group == "U10"
        assert {t.club for t in new_tournament.teams} == {"Kongsberg", "Jar"}
        assert not any(t.id == "t1" for t in plan.tournaments)

        coverage_teams = [{"club": t.club, "age_group": t.age_group} for t in planner.roster.teams]
        coverage_tournaments = [
            {"host_club": t.host_club, "age_group": t.age_group, "cancelled": t.cancelled} for t in plan.tournaments
        ]
        rows = {
            (row["club"], row["age_group"]): row
            for row in hosting_coverage_matrix(coverage_teams, coverage_tournaments)
        }
        assert rows[("Kongsberg", "U10")]["unresolved"] is False

    def test_no_repair_attempted_when_club_does_not_participate_anywhere(self):
        planner, plan = _build_repair_scenario()
        # Strip Kongsberg out of the U10 tournament entirely -- no legal
        # same-age candidate can exist without an existing participation.
        plan.tournaments[0].teams = [t for t in plan.tournaments[0].teams if t.club != "Kongsberg"]

        log = attempt_same_age_repairs(planner, plan)

        entry = next(e for e in log if e["club"] == "Kongsberg" and e["age_group"] == "U10")
        assert entry["status"] == "unresolved"
        assert entry["rejected_candidates"] == []
