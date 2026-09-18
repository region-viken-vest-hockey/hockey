"""Regression coverage for placement-preserving roster repairs.

A tournament whose host/date/arena/slot are already legal must be repaired by
reselecting a conflicting participant first. Moving the tournament is the
higher-cost change and must only happen when no roster substitution verifies.
"""

from copy import deepcopy

from tournament_scheduler.local_repair_options import (
    apply_local_repair_option,
    enumerate_local_repair_options,
)
from tournament_scheduler.placement_preserving_roster_repair import (
    apply_placement_preserving_roster_repair_option,
    build_placement_preserving_roster_decision_context,
    enumerate_placement_preserving_roster_repairs,
)
from tournament_scheduler.planning_contract import verify_candidate


def _team(club, label, age="U11", target=None):
    row = {"club": club, "label": label, "age_group": age}
    if target is not None:
        row["target_tournament_count"] = target
    return row


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


def _problem(teams=None):
    teams = teams or [
        _team("Kongsberg", "Kongsberg"),
        _team("Jar", "Jar Oransje"),
        _team("Jar", "Jar Hvit", target=3),
        _team("Jar", "Jar Blå", target=1),
        _team("Holmen", "Holmen 1"),
        _team("Holmen", "Holmen 2"),
        _team("Ringerike", "Ringerike"),
        _team("Tønsberg", "Tønsberg Grå"),
        _team("Skien", "Skien"),
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
        "club_busy_intervals": {},
        "start_date": "2026-10-09",
        "end_date": "2027-03-28",
        "christmas_split_date": "2026-12-24",
        "allow_cross_half_moves": False,
    }


def _double_booked_candidate():
    kong = _tournament(
        "a-u11",
        "Kongsberg",
        [
            _team("Kongsberg", "Kongsberg"),
            _team("Jar", "Jar Oransje"),
            _team("Holmen", "Holmen 1"),
            _team("Tønsberg", "Tønsberg Grå"),
        ],
        "2026-11-21",
        "Kongsberghallen",
    )
    ring = _tournament(
        "b-u11",
        "Ringerike",
        [
            _team("Ringerike", "Ringerike"),
            _team("Jar", "Jar Oransje"),
            _team("Holmen", "Holmen 2"),
            _team("Skien", "Skien"),
        ],
        "2026-11-21",
        "Ringerike Arena",
    )
    return {"schema_version": 1, "tournaments": [kong, ring]}


def test_double_booked_participant_is_repaired_without_moving_placement():
    candidate = _double_booked_candidate()
    problem = _problem()

    assert not verify_candidate(candidate, problem)["ok"]
    repair_set = enumerate_placement_preserving_roster_repairs(
        candidate, problem, run_id="r1"
    )

    options = [option for option in repair_set["options"] if option["tournament_id"] == "a-u11"]
    assert options, repair_set["rejected_candidates"]
    option = options[0]
    assert option["action"] == "replace_participants"
    assert option["evidence"]["placement_unchanged"] is True
    assert option["evidence"]["host_club"] == "Kongsberg"
    assert option["evidence"]["date"] == "2026-11-21"
    assert option["evidence"]["arena"] == "Kongsberghallen"
    assert option["evidence"]["start_time"] == "10:00"
    assert [team["label"] for team in option["arguments"]["removed_teams"]] == [
        "Jar Oransje"
    ]
    assert option["arguments"]["added_teams"][0]["club"] == "Jar"

    applied = apply_placement_preserving_roster_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=repair_set["candidate_fingerprint"],
        run_id="r1",
    )
    assert applied["ok"], applied
    assert applied["verification"]["ok"]
    repaired = next(
        t for t in applied["candidate"]["tournaments"] if t["id"] == "a-u11"
    )
    assert repaired["host_club"] == "Kongsberg"
    assert repaired["date"] == "2026-11-21"
    assert repaired["arena"] == "Kongsberghallen"
    assert repaired["start_time"] == "10:00"
    assert "Jar Oransje" not in {team["label"] for team in repaired["teams"]}
    # The unrelated tournament is byte-identical.
    other = next(
        t for t in applied["candidate"]["tournaments"] if t["id"] == "b-u11"
    )
    assert [team["label"] for team in other["teams"]] == [
        "Ringerike",
        "Jar Oransje",
        "Holmen 2",
        "Skien",
    ]


def test_replacement_prefers_under_target_participation():
    candidate = _double_booked_candidate()
    problem = _problem()

    repair_set = enumerate_placement_preserving_roster_repairs(
        candidate, problem, run_id="r1"
    )
    option = next(
        entry for entry in repair_set["options"] if entry["tournament_id"] == "a-u11"
    )

    # Jar Hvit (target 3) is further below target than Jar Blå (target 1), so
    # the ranked alternatives expose it first.
    assert option["arguments"]["added_teams"][0]["label"] == "Jar Hvit"


def test_generic_repair_boundary_applies_option_atomically():
    candidate = _double_booked_candidate()
    original = deepcopy(candidate)
    problem = _problem()

    repair_set = enumerate_local_repair_options(candidate, problem, run_id="r1")
    option = next(
        entry
        for entry in repair_set["options"]
        if entry["family"] == "placement_preserving_roster"
        and entry["tournament_id"] == "b-u11"
    )

    applied = apply_local_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=repair_set["candidate_fingerprint"],
        run_id="r1",
    )

    assert candidate == original
    assert applied["ok"], applied
    assert applied["family"] == "placement_preserving_roster"
    assert applied["verification"]["ok"]
    repaired = next(
        t for t in applied["candidate"]["tournaments"] if t["id"] == "b-u11"
    )
    assert repaired["date"] == "2026-11-21"
    assert repaired["arena"] == "Ringerike Arena"
    assert "Jar Oransje" not in {team["label"] for team in repaired["teams"]}


def test_conflicting_host_representative_without_host_replacement_is_rejected():
    problem = _problem(
        teams=[
            _team("Jar", "Jar Oransje"),
            _team("Kongsberg", "Kongsberg"),
            _team("Holmen", "Holmen 1"),
            _team("Holmen", "Holmen 2"),
            _team("Ringerike", "Ringerike"),
            _team("Tønsberg", "Tønsberg Grå"),
            _team("Skien", "Skien"),
            _team("Frisk Asker", "Frisk Asker"),
        ]
    )
    host_tournament = _tournament(
        "a-u11",
        "Jar",
        [
            _team("Jar", "Jar Oransje"),
            _team("Kongsberg", "Kongsberg"),
            _team("Holmen", "Holmen 1"),
            _team("Tønsberg", "Tønsberg Grå"),
        ],
        "2026-11-21",
        "Jar Arena",
    )
    other = _tournament(
        "b-u11",
        "Ringerike",
        [
            _team("Ringerike", "Ringerike"),
            _team("Jar", "Jar Oransje"),
            _team("Holmen", "Holmen 2"),
            _team("Skien", "Skien"),
        ],
        "2026-11-21",
        "Ringerike Arena",
    )
    candidate = {"schema_version": 1, "tournaments": [host_tournament, other]}

    repair_set = enumerate_placement_preserving_roster_repairs(
        candidate, problem, run_id="r1"
    )

    assert not any(
        option["tournament_id"] == "a-u11" for option in repair_set["options"]
    )
    assert any(
        rejection.get("tournament_id") == "a-u11"
        and rejection.get("reason") == "replacement_roster_loses_host_representation"
        for rejection in repair_set["rejected_candidates"]
    )


def test_no_registered_replacement_reports_explicit_rejection():
    problem = _problem(
        teams=[
            _team("Kongsberg", "Kongsberg"),
            _team("Jar", "Jar Oransje"),
            _team("Jar", "Jar Hvit"),
            _team("Holmen", "Holmen 1"),
            _team("Holmen", "Holmen 2"),
            _team("Ringerike", "Ringerike"),
            _team("Tønsberg", "Tønsberg Grå"),
        ]
    )
    first = _tournament(
        "a-u11",
        "Kongsberg",
        [
            _team("Kongsberg", "Kongsberg"),
            _team("Jar", "Jar Oransje"),
            _team("Holmen", "Holmen 1"),
            _team("Tønsberg", "Tønsberg Grå"),
        ],
        "2026-11-21",
        "Kongsberghallen",
    )
    second = _tournament(
        "b-u11",
        "Ringerike",
        [
            _team("Ringerike", "Ringerike"),
            _team("Jar", "Jar Oransje"),
            _team("Jar", "Jar Hvit"),
            _team("Holmen", "Holmen 2"),
        ],
        "2026-11-21",
        "Ringerike Arena",
    )
    candidate = {"schema_version": 1, "tournaments": [first, second]}

    repair_set = enumerate_placement_preserving_roster_repairs(
        candidate, problem, run_id="r1"
    )

    assert repair_set["options"] == []
    assert any(
        rejection.get("reason") == "no_verified_placement_preserving_roster_repair"
        for rejection in repair_set["rejected_candidates"]
    )


def test_decision_context_exposes_only_legal_option_ids():
    candidate = _double_booked_candidate()
    problem = _problem()

    context = build_placement_preserving_roster_decision_context(
        candidate, problem, run_id="r1", candidate_ref="cand-1"
    )

    enum = context.action_parameters["apply_repair_option"]["option_id"]["enum"]
    assert enum
    assert enum == [option["option_id"] for option in context.facts["repair_options"]]
    assert "apply_repair_option" in context.available_actions


def test_duplicate_team_in_one_tournament_keeps_one_occurrence():
    problem = _problem()
    tournament = _tournament(
        "a-u11",
        "Kongsberg",
        [
            _team("Kongsberg", "Kongsberg"),
            _team("Jar", "Jar Oransje"),
            _team("Jar", "Jar Oransje"),
            _team("Holmen", "Holmen 1"),
        ],
        "2026-11-21",
        "Kongsberghallen",
    )
    candidate = {"schema_version": 1, "tournaments": [tournament]}

    repair_set = enumerate_placement_preserving_roster_repairs(
        candidate, problem, run_id="r1"
    )

    option = repair_set["options"][0]
    roster_labels = [team["label"] for team in option["arguments"]["roster"]]
    assert roster_labels.count("Jar Oransje") == 1
    assert len(roster_labels) == 4
    assert option["effects"]["roster_size_delta"] == 0
    assert option["effects"]["hard_violation_delta_by_code"][
        "duplicate_team_in_tournament"
    ] == -1


def test_provider_stays_silent_without_a_participant_conflict():
    problem = _problem()
    tournament = _tournament(
        "a-u11",
        "Kongsberg",
        [
            _team("Kongsberg", "Kongsberg"),
            _team("Jar", "Jar Oransje"),
            _team("Holmen", "Holmen 1"),
            _team("Tønsberg", "Tønsberg Grå"),
        ],
        "2026-11-21",
        "Kongsberghallen",
    )
    candidate = {"schema_version": 1, "tournaments": [tournament]}

    repair_set = enumerate_placement_preserving_roster_repairs(
        candidate, problem, run_id="r1"
    )

    assert repair_set["options"] == []
    assert repair_set["rejected_candidates"] == []
