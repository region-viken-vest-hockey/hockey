"""Tests for the cross-age hosting-coverage repair (issue #328)."""

from datetime import date

import pytest

from tournament_scheduler.hosting_cross_age_repair import (
    candidate_reallocation_slots,
    club_hosting_evidence,
    unresolved_with_evidence,
)
from tournament_scheduler.hosting_coverage import hosting_coverage_matrix
from tournament_scheduler.hosting_cross_age_repair_apply import attempt_cross_age_repairs
from tournament_scheduler.models import Roster, SeasonPlan, Team, Tournament
from tournament_scheduler.season_planner import SeasonPlanner
from tournament_scheduler.testing.canonical_input import OfflineScheduler


def _team(club, label, age_group, target=None):
    return Team(club=club, label=label, age_group=age_group, target_tournament_count=target)


def _tournament_dict(id_, on_date, arena, age_group, host_club, cancelled=False):
    return {
        "id": id_,
        "date": on_date.isoformat(),
        "arena": arena,
        "age_group": age_group,
        "host_club": host_club,
        "cancelled": cancelled,
    }


class TestClubHostingEvidence:
    def test_deficit_and_reusable_surplus_are_both_visible(self):
        teams = [
            {"club": "Kongsberg", "age_group": "U10"},
            {"club": "Jar", "age_group": "U10"},
            {"club": "Jar", "age_group": "U10"},
            {"club": "Kongsberg", "age_group": "JU12"},
            {"club": "Jar", "age_group": "JU12"},
            {"club": "Jar", "age_group": "JU12"},
        ]
        tournaments = [
            _tournament_dict("t1", date(2027, 1, 16), "Kongsberghallen", "JU12", "Kongsberg"),
            _tournament_dict("t2", date(2027, 2, 27), "Kongsberghallen", "JU12", "Kongsberg"),
            _tournament_dict("t3", date(2027, 1, 9), "Jarhallen", "U10", "Jar"),
        ]
        evidence = {(row["club"], row["age_group"]): row for row in club_hosting_evidence(teams, tournaments)}

        assert evidence[("Kongsberg", "U10")]["actual_hosting_count"] == 0
        assert evidence[("Kongsberg", "U10")]["coverage_satisfied"] is False
        assert evidence[("Kongsberg", "JU12")]["actual_hosting_count"] == 2
        assert evidence[("Kongsberg", "JU12")]["surplus_hosting_count"] == 1

    def test_no_surplus_means_no_candidates(self):
        teams = [{"club": "Kongsberg", "age_group": "U10"}, {"club": "Kongsberg", "age_group": "JU12"}]
        tournaments = [_tournament_dict("t1", date(2027, 1, 16), "Kongsberghallen", "JU12", "Kongsberg")]
        evidence = club_hosting_evidence(teams, tournaments)
        candidates = candidate_reallocation_slots("Kongsberg", "U10", tournaments, evidence)
        assert candidates == []

    def test_joint_registration_resolves_through_constituents(self):
        # By the time a real tournament exists, `host_club` is always a
        # resolved physical constituent, never the literal joint label
        # (see `hosting_coverage.hosting_coverage_matrix`'s own docstring).
        teams = [
            {"club": "Kongsberg/Tønsberg", "age_group": "U10"},
            {"club": "Kongsberg/Tønsberg", "age_group": "JU12"},
        ]
        tournaments = [
            _tournament_dict("t1", date(2027, 1, 16), "Kongsberghallen", "JU12", "Kongsberg"),
            _tournament_dict("t2", date(2027, 2, 27), "Kongsberghallen", "JU12", "Kongsberg"),
        ]
        evidence = {(row["club"], row["age_group"]): row for row in club_hosting_evidence(teams, tournaments)}
        assert evidence[("Kongsberg/Tønsberg", "JU12")]["actual_hosting_count"] == 2

    def test_candidates_ordered_by_surplus_then_latest_date_first(self):
        # A competing club is required for Kongsberg's own JU12 hosting to
        # ever register as *surplus* against its target -- with no other
        # club needing coverage, Kongsberg's target absorbs every tournament.
        teams = [
            {"club": "Kongsberg", "age_group": "JU12"},
            {"club": "Jar", "age_group": "JU12"},
            {"club": "Kongsberg", "age_group": "U10"},
        ]
        tournaments = [
            _tournament_dict("early", date(2027, 1, 9), "Kongsberghallen", "JU12", "Kongsberg"),
            _tournament_dict("late", date(2027, 2, 27), "Kongsberghallen", "JU12", "Kongsberg"),
        ]
        evidence = club_hosting_evidence(teams, tournaments)
        candidates = candidate_reallocation_slots("Kongsberg", "U10", tournaments, evidence)
        assert [c["tournament_id"] for c in candidates] == ["late", "early"]


class TestUnresolvedWithEvidence:
    def test_unresolved_rows_carry_candidate_reallocation_slots(self):
        teams = [
            {"club": "Kongsberg", "age_group": "U10"},
            {"club": "Kongsberg", "age_group": "JU12"},
            {"club": "Jar", "age_group": "JU12"},
        ]
        tournaments = [
            _tournament_dict("t1", date(2027, 1, 16), "Kongsberghallen", "JU12", "Kongsberg"),
            _tournament_dict("t2", date(2027, 2, 27), "Kongsberghallen", "JU12", "Kongsberg"),
        ]
        coverage_rows = hosting_coverage_matrix(teams, tournaments)
        evidence = club_hosting_evidence(teams, tournaments)
        unresolved = unresolved_with_evidence(coverage_rows, evidence, tournaments)
        row = next(r for r in unresolved if r["club"] == "Kongsberg" and r["age_group"] == "U10")
        assert [c["tournament_id"] for c in row["candidate_reallocation_slots"]] == ["t2", "t1"]


def _build_repair_scenario():
    """A minimal roster/plan reproducing the production Kongsberg pattern:
    Kongsberg hosts JU12 twice while carrying an unresolved U10 obligation,
    and has a surplus/duplicate JU12 assignment a repair could reuse.
    """
    teams = [
        _team("Kongsberg", "Kongsberg U10", "U10", target=2),
        _team("Jar", "Jar U10-1", "U10", target=2),
        _team("Jar", "Jar U10-2", "U10", target=2),
        # issue #328's own participation-target-safety check must never
        # block a repair merely because the donor age group's teams "need"
        # the tournament -- give the JU12 teams a target of 0 so removing
        # one of Kongsberg's two JU12 tournaments is always participation-safe.
        _team("Kongsberg", "Kongsberg JU12", "JU12", target=0),
        _team("Jar", "Jar JU12-1", "JU12", target=0),
        _team("Jar", "Jar JU12-2", "JU12", target=0),
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

    def _games_for(team_list):
        return planner.generate_round_robin_games(team_list, planner._parallel_games_for("JU12"))

    ju12_participants = [by_label["Kongsberg JU12"], by_label["Jar JU12-1"], by_label["Jar JU12-2"]]
    t1 = Tournament(
        id="t1",
        date=date(2027, 1, 16),
        arena="Kongsberghallen",
        age_group="JU12",
        teams=ju12_participants,
        games=_games_for(ju12_participants),
        host_club="Kongsberg",
        start_time="11:00",
    )
    t2 = Tournament(
        id="t2",
        date=date(2027, 2, 27),
        arena="Kongsberghallen",
        age_group="JU12",
        teams=ju12_participants,
        games=_games_for(ju12_participants),
        host_club="Kongsberg",
        start_time="11:00",
    )
    u10_participants = [by_label["Jar U10-1"], by_label["Jar U10-2"]]
    t3 = Tournament(
        id="t3",
        date=date(2027, 1, 9),
        arena="Jarhallen",
        age_group="U10",
        teams=u10_participants,
        games=planner.generate_round_robin_games(u10_participants, planner._parallel_games_for("U10")),
        host_club="Jar",
        start_time="11:00",
    )

    plan = SeasonPlan(tournaments=[t1, t2, t3], start_date=date(2026, 9, 1), end_date=date(2027, 4, 30))
    for tournament in plan.tournaments:
        planner._record_grouping(tournament.teams, None)
        planner._record_opponent_history(tournament.games)
        month_key = (tournament.date.year, tournament.date.month)
        planner._hosting_days_by_club_month.setdefault((tournament.host_club, month_key), set()).add(
            tournament.date
        )
    return planner, plan


class TestAttemptCrossAgeRepairs:
    def test_reuses_surplus_hosting_to_cover_the_deficit(self):
        planner, plan = _build_repair_scenario()

        log = attempt_cross_age_repairs(planner, plan)

        repaired = next(e for e in log if e["club"] == "Kongsberg" and e["age_group"] == "U10")
        assert repaired["status"] == "repaired"
        assert repaired["donor_tournament_id"] == "t2"

        coverage_teams = [{"club": t.club, "age_group": t.age_group} for t in planner.roster.teams]
        coverage_tournaments = [
            {"host_club": t.host_club, "age_group": t.age_group, "cancelled": t.cancelled} for t in plan.tournaments
        ]
        rows = {
            (row["club"], row["age_group"]): row for row in hosting_coverage_matrix(coverage_teams, coverage_tournaments)
        }
        assert rows[("Kongsberg", "U10")]["unresolved"] is False
        # The donor's JU12 slot is gone -- Kongsberg no longer double-hosts.
        assert sum(1 for t in plan.tournaments if t.host_club == "Kongsberg" and t.age_group == "JU12") == 1
        assert not any(t.id == "t2" for t in plan.tournaments)

    def test_no_repair_when_donor_removal_would_break_a_participation_target(self):
        planner, plan = _build_repair_scenario()
        # Every JU12 team now needs both tournaments -- removing either would
        # drop it below its own target, so no legal repair exists.
        for team in planner.roster.by_age_group("JU12"):
            team.target_tournament_count = 2

        log = attempt_cross_age_repairs(planner, plan)

        entry = next(e for e in log if e["club"] == "Kongsberg" and e["age_group"] == "U10")
        assert entry["status"] == "unresolved"
        assert entry["rejected_candidates"]
        assert all("participation target" in r["reason"] for r in entry["rejected_candidates"])
        assert len(plan.tournaments) == 3

    def test_no_repair_attempted_when_there_is_no_surplus(self):
        planner, plan = _build_repair_scenario()
        # Remove every Kongsberg JU12 hosting -- with Jar carrying more JU12
        # weight than Kongsberg, a single remaining Kongsberg JU12 slot would
        # still register as surplus against a rounded-down-to-zero target, so
        # a genuine "no candidates at all" case needs zero Kongsberg JU12
        # tournaments, not just fewer of them.
        plan.tournaments = [t for t in plan.tournaments if t.id not in ("t1", "t2")]

        log = attempt_cross_age_repairs(planner, plan)

        entry = next(e for e in log if e["club"] == "Kongsberg" and e["age_group"] == "U10")
        assert entry["status"] == "unresolved"
        assert entry["rejected_candidates"] == []
