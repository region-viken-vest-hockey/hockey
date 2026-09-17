"""Tests for the bounded local-search repair provider (#348 Phase 4)."""

from copy import deepcopy

from tournament_scheduler.application.decisions import DecisionAction, decide
from tournament_scheduler.local_repair_options import (
    apply_local_repair_option,
    enumerate_local_repair_options,
)
from tournament_scheduler.search_neighborhood_repair import (
    apply_search_neighborhood_repair_option,
    build_search_neighborhood_decision_context,
    enumerate_search_neighborhood_repairs,
    findings_are_locally_searchable,
)


def _team(club, label, age="U10"):
    return {"club": club, "label": label, "age_group": age}


def _round_robin_games(teams):
    labels = [team["label"] for team in teams]
    rotation = list(labels)
    count = len(rotation)
    games = []
    for round_number in range(1, count):
        for index in range(count // 2):
            games.append(
                {
                    "home": rotation[index],
                    "away": rotation[count - 1 - index],
                    "parallel_slot": 0,
                    "round_number": round_number,
                }
            )
        rotation = [rotation[0]] + [rotation[-1]] + rotation[1:-1]
    return games


def _tournament(tid, host, teams, *, date, arena, age="U10", start="10:00"):
    return {
        "id": tid,
        "date": date,
        "age_group": age,
        "host_club": host,
        "arena": arena,
        "start_time": start,
        "duration_minutes": 120,
        "teams": teams,
        "games": _round_robin_games(teams),
    }


def _candidate():
    """t1 is missing its host team; t2 owns the only Host-club team.

    The host sibling already plays the same date, so a plain participant fill
    is illegal and t1's represented clubs have no trusted arena, so no rehost
    verifies either. A same-age one-for-one swap (t1's B1 <-> t2's Host 1) is
    the coupled repair only a bounded search finds.
    """
    t1_teams = [_team("B", "B1"), _team("C", "C1"), _team("D", "D1"), _team("E", "E1")]
    t2_teams = [
        _team("Host", "Host 1"),
        _team("F", "F1"),
        _team("G", "G1"),
        _team("H", "H1"),
    ]
    return {
        "schema_version": 1,
        "tournaments": [
            _tournament("t1", "Host", t1_teams, date="2026-09-05", arena="Host Arena"),
            _tournament("t2", "F", t2_teams, date="2026-09-05", arena="F Arena", start="13:00"),
        ],
    }


def _problem(**overrides):
    problem = {
        "teams": [
            _team("Host", "Host 1"),
            _team("B", "B1"),
            _team("C", "C1"),
            _team("D", "D1"),
            _team("E", "E1"),
            _team("F", "F1"),
            _team("G", "G1"),
            _team("H", "H1"),
        ],
        "parallel_games": {"U10": 2},
        "clubs": {"Host": "Host Arena", "F": "F Arena"},
        "club_calendar_status": {"Host": "known", "F": "known"},
        "club_busy_intervals": {},
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "christmas_split_date": "2027-01-01",
    }
    problem.update(overrides)
    return problem


def test_bounded_search_exposes_only_a_verified_participant_swap():
    candidate = _candidate()
    problem = _problem()

    repair_set = enumerate_search_neighborhood_repairs(candidate, problem, run_id="r1")

    assert repair_set["applicable"] is True
    assert repair_set["options"]
    option = repair_set["options"][0]
    assert option["action"] == "search_neighborhood"
    assert option["hard_feasible"] is True
    assert option["effects"]["hard_violation_delta_by_code"] == {"host_team_missing": -1}
    assert option["effects"]["changed_tournament_count"] == 2
    assert "weighted_objective_delta" in option["effects"]
    # The neighborhood is exactly the affected age group, so nothing outside
    # it could have moved.
    assert repair_set["repair_neighborhood"]["age_groups"] == ["U10"]
    assert repair_set["repair_neighborhood"]["tournament_ids"] == ["t1", "t2"]


def test_apply_moves_the_host_team_and_preserves_participation_totals():
    candidate = _candidate()
    original = deepcopy(candidate)
    problem = _problem()
    repair_set = enumerate_search_neighborhood_repairs(candidate, problem)
    option = repair_set["options"][0]

    applied = apply_search_neighborhood_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=repair_set["candidate_fingerprint"],
    )

    assert applied["ok"] and applied["verification"]["ok"]
    assert candidate == original
    t1 = next(t for t in applied["candidate"]["tournaments"] if t["id"] == "t1")
    t2 = next(t for t in applied["candidate"]["tournaments"] if t["id"] == "t2")
    assert any(team["club"] == "Host" for team in t1["teams"])
    assert not any(team["label"] == "Host 1" for team in t2["teams"])
    assert len(t1["teams"]) == 4 and len(t2["teams"]) == 4


def test_bounded_search_freezes_tournaments_outside_the_age_group():
    candidate = _candidate()
    outsider = _tournament(
        "u11",
        "Z",
        [_team("Z", "Z1", "U11"), _team("Y", "Y1", "U11"), _team("X", "X1", "U11")],
        date="2026-09-05",
        arena="Z Arena",
        age="U11",
    )
    candidate["tournaments"].append(outsider)
    problem = _problem()
    problem["teams"] = problem["teams"] + [
        _team("Z", "Z1", "U11"),
        _team("Y", "Y1", "U11"),
        _team("X", "X1", "U11"),
    ]
    problem["clubs"]["Z"] = "Z Arena"
    problem["club_calendar_status"]["Z"] = "known"

    repair_set = enumerate_search_neighborhood_repairs(candidate, problem)
    assert repair_set["options"]
    assert "u11" not in repair_set["repair_neighborhood"]["tournament_ids"]

    option = repair_set["options"][0]
    applied = apply_search_neighborhood_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=repair_set["candidate_fingerprint"],
    )
    assert applied["ok"]
    after_u11 = next(t for t in applied["candidate"]["tournaments"] if t["id"] == "u11")
    assert after_u11 == outsider


def test_non_searchable_finding_is_not_a_solver_problem():
    candidate = _candidate()
    # Rename a team so the candidate also fails with unregistered_team, which
    # a bounded participant/host search cannot repair.
    candidate["tournaments"][0]["teams"] = candidate["tournaments"][0]["teams"] + [
        _team("Q", "Q1")
    ]
    problem = _problem()

    repair_set = enumerate_search_neighborhood_repairs(candidate, problem)

    assert repair_set["applicable"] is False
    assert repair_set["options"] == []


def test_context_exposes_stable_option_ids_and_apply_action():
    candidate = _candidate()
    problem = _problem()
    context = build_search_neighborhood_decision_context(candidate, problem, run_id="run-1")

    assert context.capability == "search_neighborhood_repair"
    assert "apply_repair_option" in context.available_actions
    option_ids = context.action_parameters["apply_repair_option"]["option_id"]["enum"]
    assert option_ids == [option["option_id"] for option in context.facts["repair_options"]]
    assert context.facts["repair_neighborhood"]["age_groups"] == ["U10"]

    option = context.facts["repair_options"][0]
    decision = decide(
        context,
        DecisionAction(
            action_id="apply_repair_option",
            arguments={
                "option_id": option["option_id"],
                "candidate_fingerprint": context.facts["candidate_fingerprint"],
            },
        ),
    )
    assert decision.accepted


def test_dispatcher_runs_search_last_and_reports_family():
    candidate = _candidate()
    problem = _problem()

    repair_set = enumerate_local_repair_options(candidate, problem, run_id="r1")

    family = repair_set["families"]["search_neighborhood"]
    assert family["option_count"] >= 1
    option = next(o for o in repair_set["options"] if o["family"] == "search_neighborhood")

    applied = apply_local_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=repair_set["candidate_fingerprint"],
        run_id="r1",
    )
    assert applied["ok"] and applied["verification"]["ok"]
    assert applied["family"] == "search_neighborhood"


def test_dispatcher_skips_search_when_a_cheap_family_has_an_option():
    candidate = _candidate()
    # Make a direct fill legal: give Host 2 an available same-age registration
    # that does not already play t1's date.
    problem = _problem(
        teams=_problem()["teams"] + [_team("Host", "Host 2")],
        parallel_games={"U10": 2},
    )
    candidate["tournaments"][1]["teams"] = [
        _team("Host", "Host 1"),
        _team("F", "F1"),
        _team("G", "G1"),
        _team("H", "H1"),
    ]

    repair_set = enumerate_local_repair_options(candidate, problem, run_id="r1")

    assert repair_set["families"]["search_neighborhood"].get("skipped") == (
        "cheaper_family_has_options"
    )


def test_stale_fingerprint_does_not_mutate_candidate():
    candidate = _candidate()
    original = deepcopy(candidate)
    problem = _problem()

    applied = apply_search_neighborhood_repair_option(
        candidate,
        problem,
        option_id="anything",
        expected_fingerprint="not-the-fingerprint",
    )

    assert not applied["ok"]
    assert applied["reason"] == "stale_candidate_fingerprint"
    assert candidate == original


def test_findings_are_locally_searchable_requires_a_searchable_hard_finding():
    assert findings_are_locally_searchable(["host_team_missing"]) is True
    assert findings_are_locally_searchable(["host_team_missing", "unregistered_team"]) is False
    assert findings_are_locally_searchable(["unregistered_team"]) is False
    assert findings_are_locally_searchable([]) is False
