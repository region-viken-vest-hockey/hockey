"""Tests for the conflict-aware manual-placement candidate-weekend slice (#369)."""

from datetime import date, timedelta

from tournament_scheduler.candidate_weekends import (
    AVAILABILITY_FREE,
    AVAILABILITY_MOVABLE,
    AVAILABILITY_UNKNOWN,
    enumerate_candidate_weekends,
    season_weekend_dates,
)


def _problem(**overrides):
    problem = {
        "start_date": "2026-11-01",
        "end_date": "2026-11-30",
        "club_calendar_status": {"Kongsberg": "known"},
        "club_busy_intervals": {},
    }
    problem.update(overrides)
    return problem


def _team(club, label, age_group="U11"):
    return {"club": club, "label": label, "age_group": age_group}


def _enumerate(problem, **overrides):
    kwargs = {
        "host_club": "Kongsberg",
        "team_keys": {"K1", "K2", "B1"},
        "candidate_dates": [date(2026, 11, 21), date(2026, 11, 22)],
        "duration_minutes": 90,
        "preferred_start_time": "11:00",
        "occupancy": {},
        "team_labels": {"K1": "Kongsberg 1", "B1": "Bærum 1"},
        "current_roster": [_team("Kongsberg", "Kongsberg 1")],
    }
    kwargs.update(overrides)
    return enumerate_candidate_weekends(problem, **kwargs)


def test_verified_free_weekend_with_current_roster_is_top_ranked():
    result = _enumerate(_problem())

    assert result["status"] == "suggestions"
    assert result["bounded_date_set_exhausted"] is True
    assert [c["date"] for c in result["candidate_weekends"]] == ["2026-11-21", "2026-11-22"]
    best = result["candidate_weekends"][0]
    assert best["availability"] == AVAILABILITY_FREE
    assert best["requires_host_confirmation"] is False
    assert best["roster_source"] == "current"
    assert best["rank"] == 0
    assert best["end_time"] == "12:30"


def test_fixed_busy_date_is_rejected_with_deterministic_reason():
    problem = _problem(
        club_busy_intervals={
            "Kongsberg": [
                {
                    "date": "2026-11-21",
                    "start": "10:00",
                    "end": "18:00",
                    "kind": "external",
                    "availability": "fixed_busy",
                    "calendar_event": "Seriekamp",
                }
            ]
        }
    )

    result = _enumerate(problem)

    assert [c["date"] for c in result["candidate_weekends"]] == ["2026-11-22"]
    assert result["rejected_candidate_dates"] == [
        {"host_club": "Kongsberg", "date": "2026-11-21", "reason": "fixed_busy", "usable": False}
    ]


def test_movable_busy_weekend_is_usable_but_requires_host_confirmation():
    problem = _problem(
        club_busy_intervals={
            "Kongsberg": [
                {
                    "date": "2026-11-21",
                    "start": "09:00",
                    "end": "20:00",
                    "kind": "club_controlled",
                    "availability": "movable_busy",
                    "calendar_event": "Åpen ishall",
                    "reason": "host-controlled open ice",
                }
            ]
        }
    )

    result = _enumerate(problem)

    candidate = next(c for c in result["candidate_weekends"] if c["date"] == "2026-11-21")
    assert candidate["availability"] == AVAILABILITY_MOVABLE
    assert candidate["requires_host_confirmation"] is True
    assert candidate["calendar_event"] == "Åpen ishall"


def test_unknown_calendar_is_surfaced_as_confirmation_gated_opportunity():
    problem = _problem(club_calendar_status={"Kongsberg": "unknown"})

    result = _enumerate(problem)

    candidate = result["candidate_weekends"][0]
    assert candidate["availability"] == AVAILABILITY_UNKNOWN
    assert candidate["requires_host_confirmation"] is True
    assert candidate["rank"] == 4


def test_team_date_collision_is_rejected_when_no_replacement_roster_exists():
    result = _enumerate(
        _problem(),
        occupancy={"2026-11-21": {"K1"}},
        candidate_dates=[date(2026, 11, 21)],
    )

    assert result["candidate_weekends"] == []
    assert result["rejected_candidate_dates"] == [
        {
            "host_club": "Kongsberg",
            "date": "2026-11-21",
            "reason": "team_already_plays",
            "usable": False,
            "team_conflicts": ["Kongsberg 1"],
            "availability": AVAILABILITY_FREE,
        }
    ]
    assert result["status"] == "bounded_date_set_exhausted"


def test_team_date_collision_with_hard_valid_replacement_roster_is_suggested():
    def replacement(_candidate_date):
        return {"K3", "K4", "B1"}, [_team("Kongsberg", "Kongsberg 3"), _team("Bærum", "Bærum 1")]

    result = _enumerate(
        _problem(),
        occupancy={"2026-11-21": {"K1"}},
        candidate_dates=[date(2026, 11, 21)],
        replacement_roster=replacement,
    )

    candidate = result["candidate_weekends"][0]
    assert candidate["roster_source"] == "alternate"
    assert candidate["rank"] == 2
    assert candidate["roster"] == [
        {"club": "Kongsberg", "label": "Kongsberg 3", "age_group": "U11"},
        {"club": "Bærum", "label": "Bærum 1", "age_group": "U11"},
    ]


def test_replacement_roster_that_still_collides_is_rejected():
    def replacement(_candidate_date):
        return {"K1", "K4"}, [_team("Kongsberg", "Kongsberg 1")]

    result = _enumerate(
        _problem(),
        occupancy={"2026-11-21": {"K1"}},
        candidate_dates=[date(2026, 11, 21)],
        replacement_roster=replacement,
    )

    assert result["candidate_weekends"] == []
    assert result["rejected_candidate_dates"][0]["reason"] == "replacement_roster_still_conflicts"


def test_bounded_date_budget_reports_search_budget_exhausted():
    dates = [date(2026, 11, 1) + timedelta(days=index) for index in range(5)]
    result = _enumerate(
        _problem(),
        candidate_dates=dates,
        max_dates=2,
    )

    assert result["search_budget_exhausted"] is True
    assert result["status"] == "suggestions"
    assert len(result["dates_considered"]) == 2


def test_budget_exhausted_without_usable_candidate_says_so():
    problem = _problem(
        club_busy_intervals={
            "Kongsberg": [
                {
                    "date": "2026-11-21",
                    "start": "10:00",
                    "end": "18:00",
                    "kind": "external",
                    "availability": "fixed_busy",
                }
            ]
        }
    )
    result = _enumerate(
        problem,
        candidate_dates=[date(2026, 11, 21), date(2026, 11, 22)],
        max_dates=1,
        team_keys=set(),
    )

    assert result["status"] == "search_budget_exhausted"
    assert result["search_budget_exhausted"] is True


def test_season_weekend_dates_are_saturdays_and_sundays_in_window():
    dates = season_weekend_dates(
        {"start_date": "2026-11-01", "end_date": "2026-11-15"}
    )
    assert all(d.weekday() >= 5 for d in dates)
    assert dates[0] == date(2026, 11, 1)
    assert dates[-1] == date(2026, 11, 15)


def test_alternate_start_time_avoids_fixed_busy_instead_of_rejecting_date():
    problem = _problem(
        club_busy_intervals={
            "Kongsberg": [
                {
                    "date": "2026-11-21",
                    "start": "10:00",
                    "end": "12:00",
                    "kind": "external",
                    "availability": "fixed_busy",
                }
            ]
        }
    )

    result = _enumerate(
        problem,
        candidate_dates=[date(2026, 11, 21)],
        candidate_start_times=["11:00", "13:00"],
    )

    candidate = result["candidate_weekends"][0]
    assert candidate["start_time"] == "13:00"
    assert candidate["end_time"] == "14:30"
