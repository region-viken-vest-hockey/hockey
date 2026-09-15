from datetime import date

from tournament_scheduler.models import Roster, SeasonPlan, Team, Tournament
from tournament_scheduler.participant_roster_repair import attempt_underfilled_roster_repairs
from tournament_scheduler.season_planner import SeasonPlanner


class FakeScheduler:
    def find_available_dates(self, start, end):  # pragma: no cover - not used by these unit tests
        raise AssertionError("not used")


def _planner(teams, *, target=2, max_club=2):
    return SeasonPlanner(
        scheduler=FakeScheduler(),
        roster=Roster(list(teams)),
        club_arenas={team.club: f"{team.club}hallen" for team in teams},
        parallel_games_for_age_group={"U10": 3},
        rounds_per_tournament_for_age_group={"U10": 5},
        target_tournament_count=target,
        max_club_teams_per_tournament=max_club,
    )


def _tournament(tournament_date, teams, tid="t1"):
    return Tournament(
        id=tid,
        date=tournament_date,
        arena="Arena",
        age_group="U10",
        host_club=teams[0].club if teams else None,
        teams=list(teams),
    )


def _regenerate(planner, tournament):
    tournament.games = planner._generate_tournament_games(
        tournament.age_group,
        tournament.teams,
        planner._parallel_games_for(tournament.age_group),
    )


def test_underfilled_tournament_direct_fill_adds_legal_under_target_team():
    teams = [Team(f"Club {i}", f"Club {i} U10", "U10") for i in range(1, 7)]
    planner = _planner(teams)
    underfilled = _tournament(date(2026, 1, 10), teams[:5])
    _regenerate(planner, underfilled)
    plan = SeasonPlan(tournaments=[underfilled], start_date=date(2025, 9, 1), end_date=date(2026, 4, 1))
    planner.recompute_tournament_participations(plan)

    repairs = attempt_underfilled_roster_repairs(planner, plan)

    assert [team.label for team in underfilled.teams] == [team.label for team in teams]
    assert repairs[0]["status"] == "repaired_by_direct_fill"
    assert repairs[0]["added_teams"] == [{"club": "Club 6", "label": "Club 6 U10", "age_group": "U10"}]
    assert underfilled.games


def test_direct_fill_ranking_prefers_team_with_larger_participation_deficit():
    teams = [Team(f"Club {i}", f"Club {i} U10", "U10") for i in range(1, 8)]
    planner = _planner(teams, target=3)
    underfilled = _tournament(date(2026, 1, 10), teams[:5])
    older = _tournament(date(2025, 10, 5), [teams[5], teams[0], teams[1]], tid="older")
    _regenerate(planner, underfilled)
    _regenerate(planner, older)
    plan = SeasonPlan(tournaments=[underfilled, older], start_date=date(2025, 9, 1), end_date=date(2026, 4, 1))
    planner.recompute_tournament_participations(plan)

    repairs = attempt_underfilled_roster_repairs(planner, plan)

    assert underfilled.teams[-1] == teams[6]
    assert repairs[0]["added_teams"][0]["label"] == "Club 7 U10"


def test_direct_fill_can_select_lower_participation_sibling_team_when_legal():
    jar1 = Team("Jar", "Jar U10-1", "U10")
    jar2 = Team("Jar", "Jar U10-2", "U10")
    others = [Team(f"Club {i}", f"Club {i} U10", "U10") for i in range(1, 6)]
    teams = [jar1, jar2, *others]
    planner = _planner(teams, target=3)
    underfilled = _tournament(date(2026, 1, 10), [jar1, *others[:4]])
    older = _tournament(date(2025, 10, 5), [others[4], jar1, others[0]], tid="older")
    _regenerate(planner, underfilled)
    _regenerate(planner, older)
    plan = SeasonPlan(tournaments=[underfilled, older], start_date=date(2025, 9, 1), end_date=date(2026, 4, 1))
    planner.recompute_tournament_participations(plan)

    attempt_underfilled_roster_repairs(planner, plan)

    assert underfilled.teams[-1] == jar2


def test_direct_fill_rejects_same_date_participation_and_records_reason():
    teams = [Team(f"Club {i}", f"Club {i} U10", "U10") for i in range(1, 7)]
    planner = _planner(teams, target=2)
    underfilled = _tournament(date(2026, 1, 10), teams[:5])
    same_day = _tournament(date(2026, 1, 10), [teams[5], teams[0], teams[1]], tid="same-day")
    _regenerate(planner, underfilled)
    _regenerate(planner, same_day)
    plan = SeasonPlan(tournaments=[underfilled, same_day], start_date=date(2025, 9, 1), end_date=date(2026, 4, 1))
    planner.recompute_tournament_participations(plan)

    repairs = attempt_underfilled_roster_repairs(planner, plan)

    assert len(underfilled.teams) == 5
    assert repairs[0]["status"] == "targeted_repair_impossible"
    assert repairs[0]["rejected_candidates"][0]["reason"] == "already_plays_same_date"


def test_direct_fill_rejects_team_at_participation_max():
    teams = [Team(f"Club {i}", f"Club {i} U10", "U10") for i in range(1, 7)]
    planner = _planner(teams, target=1)
    underfilled = _tournament(date(2026, 1, 10), teams[:5])
    older = _tournament(date(2025, 10, 5), [teams[5], teams[0], teams[1]], tid="older")
    _regenerate(planner, underfilled)
    _regenerate(planner, older)
    plan = SeasonPlan(tournaments=[underfilled, older], start_date=date(2025, 9, 1), end_date=date(2026, 4, 1))
    planner.recompute_tournament_participations(plan)

    repairs = attempt_underfilled_roster_repairs(planner, plan)

    assert len(underfilled.teams) == 5
    assert any(row["reason"] == "at_participation_max" for row in repairs[0]["rejected_candidates"])


def test_local_swap_fill_is_attempted_when_direct_addition_is_impossible():
    jar = [Team("Jar", f"Jar U10-{i}", "U10") for i in range(1, 5)]
    club4 = Team("Club 4", "Club 4 U10", "U10")
    club5 = Team("Club 5", "Club 5 U10", "U10")
    club6 = Team("Club 6", "Club 6 U10", "U10")
    teams = [*jar, club4, club5, club6]
    planner = _planner(teams, target=1)
    target = _tournament(date(2026, 1, 10), [jar[0], jar[1], jar[2], club4, club5])
    donor = _tournament(date(2025, 10, 5), [club6, club4, club5], tid="donor")
    _regenerate(planner, target)
    _regenerate(planner, donor)
    plan = SeasonPlan(tournaments=[target, donor], start_date=date(2025, 9, 1), end_date=date(2026, 4, 1))
    planner.recompute_tournament_participations(plan)

    repairs = attempt_underfilled_roster_repairs(planner, plan)

    assert repairs[0]["status"] == "repaired_by_local_swap"
    assert club6 in target.teams
    assert jar[3] in donor.teams
    assert club6 not in donor.teams
    assert len(target.teams) == 6
    assert len(donor.teams) == 3


def test_direct_fill_rejects_hard_club_cap():
    jar = [Team("Jar", f"Jar U10-{i}", "U10") for i in range(1, 5)]
    others = [Team(f"Club {i}", f"Club {i} U10", "U10") for i in range(1, 3)]
    teams = [*jar, *others]
    planner = _planner(teams, target=2)
    underfilled = _tournament(date(2026, 1, 10), [jar[0], jar[1], jar[2], *others])
    _regenerate(planner, underfilled)
    plan = SeasonPlan(tournaments=[underfilled], start_date=date(2025, 9, 1), end_date=date(2026, 4, 1))
    planner.recompute_tournament_participations(plan)

    repairs = attempt_underfilled_roster_repairs(planner, plan)

    assert len(underfilled.teams) == 5
    assert repairs[0]["rejected_candidates"] == [
        {"club": "Jar", "label": "Jar U10-4", "age_group": "U10", "reason": "hard_club_cap"}
    ]
