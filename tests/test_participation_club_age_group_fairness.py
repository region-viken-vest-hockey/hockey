"""Club x age-group participation fairness over booked reality."""

from __future__ import annotations

from tournament_scheduler.participation.club_age_group_fairness import (
    build_club_age_group_participation_report,
)

SPLIT = "2027-01-01"


def _team(club: str, label: str, age_group: str = "U11") -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(tid: str, date: str, teams: list[dict], *, cancelled: bool = False, age_group: str = "U11") -> dict:
    row = {"id": tid, "date": date, "age_group": age_group, "teams": teams}
    if cancelled:
        row["cancelled"] = True
    return row


def _row(report: dict, club: str, age_group: str = "U11") -> dict:
    return next(row for row in report["rows"] if row["club"] == club and row["age_group"] == age_group)


def test_multi_team_club_is_normalized_by_registered_teams_not_sibling_label():
    teams = [_team("Frisk", "Frisk 1"), _team("Frisk", "Frisk 2"), _team("Frisk", "Frisk 3"), _team("Jar", "Jar 1")]
    tournaments = [
        _tournament("t1", "2026-10-10", [teams[0], teams[1], teams[3]]),
        _tournament("t2", "2026-10-17", [teams[0], teams[2], teams[3]]),
    ]
    report = build_club_age_group_participation_report(
        teams=teams,
        tournaments=tournaments,
        booking_states={"t1": "booked", "t2": "booked"},
        split_date=SPLIT,
    )

    frisk = _row(report, "Frisk")
    jar = _row(report, "Jar")
    assert frisk["registered_teams"] == 3
    assert frisk["booked_participations"]["total"] == 4
    assert frisk["booked_per_team"] == round(4 / 3, 4)
    assert jar["booked_participations"]["total"] == 2
    assert jar["booked_per_team"] == 2.0
    assert report["lowest_booked_per_team_by_age_group"]["U11"]["club"] == "Frisk"


def test_cancelled_tournaments_are_excluded_and_counted_separately():
    teams = [_team("Jar", "Jar 1")]
    tournaments = [
        _tournament("t1", "2026-10-10", teams, cancelled=True),
        _tournament("t2", "2026-10-17", teams),
    ]
    report = build_club_age_group_participation_report(
        teams=teams,
        tournaments=tournaments,
        booking_states={"t1": "booked", "t2": "booked"},
        split_date=SPLIT,
    )

    jar = _row(report, "Jar")
    assert jar["booked_participations"]["total"] == 1
    assert jar["cancelled_excluded"] == 1
    assert jar["provisional"] is False


def test_unconfirmed_participations_are_reported_separately_and_mark_provisional():
    teams = [_team("Jutul", "Jutul 1"), _team("Jutul", "Jutul 2")]
    tournaments = [
        _tournament("t1", "2026-10-10", [teams[0]]),
        _tournament("t2", "2026-10-17", [teams[1]]),
    ]
    report = build_club_age_group_participation_report(
        teams=teams,
        tournaments=tournaments,
        booking_states={"t1": "booked", "t2": "ambiguous"},
        split_date=SPLIT,
    )

    jutul = _row(report, "Jutul")
    assert jutul["booked_participations"]["total"] == 1
    assert jutul["unconfirmed_participations"]["total"] == 1
    assert jutul["provisional"] is True
    assert jutul["provisional_reasons"] == ["unconfirmed_participations"]
    assert report["provisional"] is True


def test_booked_population_excludes_non_booked_states_entirely():
    teams = [_team("Holmen", "Holmen 1")]
    tournaments = [
        _tournament("t1", "2026-10-10", teams),
        _tournament("t2", "2026-10-17", teams),
        _tournament("t3", "2026-10-24", teams),
    ]
    report = build_club_age_group_participation_report(
        teams=teams,
        tournaments=tournaments,
        booking_states={"t1": "booked", "t2": "action_required", "t3": "not_booked"},
        split_date=SPLIT,
    )

    holmen = _row(report, "Holmen")
    assert holmen["booked_participations"]["total"] == 1
    assert holmen["unconfirmed_participations"]["total"] == 2
    assert report["tournament_booking_states"] == {"action_required": 1, "booked": 1, "not_booked": 1}


def test_before_and_after_christmas_coverage_is_split_by_planning_half():
    teams = [_team("Skien", "Skien 1")]
    tournaments = [
        _tournament("t1", "2026-12-12", teams),
        _tournament("t2", "2027-01-16", teams),
        _tournament("t3", "2027-02-06", teams),
    ]
    report = build_club_age_group_participation_report(
        teams=teams,
        tournaments=tournaments,
        booking_states={"t1": "booked", "t2": "booked", "t3": "booked"},
        targets_by_age_group={"U11": {"before_christmas": 4, "after_christmas": 5}},
        split_date=SPLIT,
    )

    skien = _row(report, "Skien")
    assert skien["booked_participations"]["before_christmas"] == 1
    assert skien["booked_participations"]["after_christmas"] == 2
    assert skien["booked_participations"]["unsplit"] == 0
    assert skien["target_per_team"] == {"before_christmas": 4, "after_christmas": 5}


def test_without_christmas_split_participations_are_unsplit():
    teams = [_team("Ringerike", "Ringerike 1")]
    report = build_club_age_group_participation_report(
        teams=teams,
        tournaments=[_tournament("t1", "2026-10-10", teams)],
        booking_states={"t1": "booked"},
        split_date=None,
    )

    ringerike = _row(report, "Ringerike")
    assert ringerike["booked_participations"]["unsplit"] == 1
    assert ringerike["booked_participations"]["before_christmas"] == 0


def test_age_group_filter_limits_rows_and_ignores_other_groups():
    teams = [_team("Jar", "Jar U11", "U11"), _team("Jar", "Jar U10", "U10")]
    tournaments = [
        _tournament("t1", "2026-10-10", [teams[0]], age_group="U11"),
        _tournament("t2", "2026-10-17", [teams[1]], age_group="U10"),
    ]
    report = build_club_age_group_participation_report(
        teams=teams,
        tournaments=tournaments,
        booking_states={"t1": "booked", "t2": "booked"},
        split_date=SPLIT,
        age_group="U11",
    )

    assert [row["age_group"] for row in report["rows"]] == ["U11"]
    assert report["age_group"] == "U11"


def test_participation_without_registered_team_is_flagged_not_silently_normalized():
    report = build_club_age_group_participation_report(
        teams=[],
        tournaments=[_tournament("t1", "2026-10-10", [_team("Ghost", "Ghost 1")])],
        booking_states={"t1": "booked"},
        split_date=SPLIT,
    )

    ghost = _row(report, "Ghost")
    assert ghost["registered_teams"] == 0
    assert ghost["booked_per_team"] is None
    assert ghost["provisional_reasons"] == ["participation_without_registered_team"]
    assert "U11" not in report["lowest_booked_per_team_by_age_group"]
