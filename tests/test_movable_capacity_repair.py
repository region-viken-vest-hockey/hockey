"""Regression coverage for movable-capacity participant/date repair."""

from copy import deepcopy

from tournament_scheduler.local_repair_options import (
    apply_local_repair_option,
    enumerate_local_repair_options,
)
from tournament_scheduler.movable_capacity_repair import (
    enumerate_movable_capacity_repairs,
)


def _team(club, label, age="U11"):
    return {"club": club, "label": label, "age_group": age}


def _games(teams):
    labels = [team["label"] for team in teams]
    return [
        {"home": labels[0], "away": labels[1], "parallel_slot": 0, "round_number": 1},
        {"home": labels[2], "away": labels[3], "parallel_slot": 1, "round_number": 1},
        {"home": labels[0], "away": labels[2], "parallel_slot": 0, "round_number": 2},
        {"home": labels[1], "away": labels[3], "parallel_slot": 1, "round_number": 2},
        {"home": labels[0], "away": labels[3], "parallel_slot": 0, "round_number": 3},
        {"home": labels[1], "away": labels[2], "parallel_slot": 1, "round_number": 3},
    ]


def _tournament(tid, host, teams, date, arena):
    return {
        "id": tid,
        "date": date,
        "age_group": "U11",
        "host_club": host,
        "arena": arena,
        "start_time": "10:00",
        "duration_minutes": 120,
        "teams": teams,
        "games": _games(teams),
    }


def _problem():
    teams = [
        _team("Kongsberg", "Kongsberg"),
        _team("Jar", "Jar Oransje"),
        _team("Jar", "Jar Hvit"),
        _team("Holmen", "Holmen 1"),
        _team("Holmen", "Holmen 2"),
        _team("Ringerike", "Ringerike"),
        _team("Tønsberg", "Tønsberg Grå"),
        _team("Tønsberg", "Tønsberg Rød"),
    ]
    clubs = {team["club"]: f"{team['club']} Arena" for team in teams}
    clubs["Kongsberg"] = "Kongsberghallen"
    return {
        "teams": teams,
        "parallel_games": {"U11": 2},
        "rounds_per_tournament": {"U11": 3},
        "round_length_minutes": {"U11": 20},
        "ice_time_minutes": {"U11": 120},
        "clubs": clubs,
        "club_calendar_status": {club: "known" for club in clubs},
        "club_busy_intervals": {
            "Kongsberg": [
                {
                    "date": "2026-11-21",
                    "start": "09:00",
                    "end": "14:00",
                    "kind": "club_controlled",
                    "availability": "movable_busy",
                    "classification_source": "configured",
                    "calendar_event": "Åpen ishall",
                    "reason": "host-controlled open ice",
                }
            ]
        },
        "start_date": "2026-10-09",
        "end_date": "2027-03-28",
        "christmas_split_date": "2026-12-24",
        "allow_cross_half_moves": False,
    }


def _candidate():
    manual = _tournament(
        "k-u11",
        "Kongsberg",
        [
            _team("Kongsberg", "Kongsberg"),
            _team("Jar", "Jar Oransje"),
            _team("Holmen", "Holmen 1"),
            _team("Tønsberg", "Tønsberg Grå"),
        ],
        "2026-11-14",
        "Kongsberghallen",
    )
    manual["manual_booking_reason"] = (
        "Ingen verifisert ledig istid for Kongsberg 2026-11-14 — "
        "turneringen må plasseres manuelt."
    )
    occupied = _tournament(
        "other-u11",
        "Ringerike",
        [
            _team("Jar", "Jar Oransje"),
            _team("Holmen", "Holmen 2"),
            _team("Ringerike", "Ringerike"),
            _team("Tønsberg", "Tønsberg Rød"),
        ],
        "2026-11-21",
        "Ringerike Arena",
    )
    return {
        "schema_version": 1,
        "unresolved_tournament_placements": [
            {
                "age_group": "U11",
                "date": "2026-11-14",
                "category": "manual_tournament_placement",
            }
        ],
        "tournaments": [manual, occupied],
    }


def test_kongsberg_movable_weekend_can_reselect_conflicting_participant():
    candidate = _candidate()
    problem = _problem()

    repair_set = enumerate_movable_capacity_repairs(candidate, problem, run_id="r1")

    option = next(
        option
        for option in repair_set["options"]
        if option["action"] == "move_to_movable_capacity_reselect_participants"
        and option["arguments"]["date"] == "2026-11-21"
    )
    assert option["evidence"]["availability"] == "movable_busy"
    assert option["evidence"]["requires_host_confirmation"] is True
    assert option["evidence"]["calendar_event"] == "Åpen ishall"
    assert {team["label"] for team in option["arguments"]["removed_teams"]} == {
        "Jar Oransje"
    }
    assert "Jar Oransje" not in {
        team["label"] for team in option["arguments"]["roster"]
    }


def test_generic_repair_boundary_applies_movable_capacity_option_atomically():
    candidate = _candidate()
    original = deepcopy(candidate)
    problem = _problem()

    repair_set = enumerate_local_repair_options(candidate, problem, run_id="r1")
    option = next(
        option
        for option in repair_set["options"]
        if option["family"] == "movable_capacity"
        and option["arguments"]["date"] == "2026-11-21"
    )

    applied = apply_local_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=repair_set["candidate_fingerprint"],
        run_id="r1",
    )

    assert candidate == original
    assert applied["ok"]
    assert applied["family"] == "movable_capacity"
    assert applied["verification"]["ok"]
    repaired = next(
        tournament
        for tournament in applied["candidate"]["tournaments"]
        if tournament["id"] == "k-u11"
    )
    assert repaired["host_club"] == "Kongsberg"
    assert repaired["arena"] == "Kongsberghallen"
    assert repaired["date"] == "2026-11-21"
    assert repaired["manual_booking_reason"] is None
    assert "Jar Oransje" not in {team["label"] for team in repaired["teams"]}
    assert applied["candidate"]["unresolved_tournament_placements"] == []
