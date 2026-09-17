"""Tests for deterministic underfilled-roster repair options (issue #347/#348)."""

from copy import deepcopy

from tournament_scheduler.application.decisions import DecisionAction, decide
from tournament_scheduler.local_repair_options import apply_local_repair_option
from tournament_scheduler.planning_contract import verify_candidate
from tournament_scheduler.underfilled_roster_repair import (
    apply_underfilled_roster_repair_option,
    build_underfilled_roster_decision_context,
    enumerate_underfilled_roster_repairs,
)


def _team(club, label=None, age="U10", target=None):
    row = {"club": club, "label": label or club, "age_group": age}
    if target is not None:
        row["target_tournament_count"] = target
    return row


def _round_robin_games(labels):
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


def _tournament(tid, host, teams, *, date="2026-01-10", age_group="U10", arena=None):
    return {
        "id": tid,
        "date": date,
        "age_group": age_group,
        "host_club": host,
        "arena": arena or f"{host} Arena",
        "start_time": "10:00",
        "duration_minutes": 90,
        "teams": teams,
        "games": _round_robin_games([team["label"] for team in teams]),
    }


def _clubs(*names):
    return {name: f"{name} Arena" for name in names}


def _statuses(*names):
    return {name: "known" for name in names}


def _underfilled_problem(*, extra_teams=(), **overrides):
    clubs = ("A", "B", "C", "D", "E", "F")
    problem = {
        "teams": [
            _team("A", "A1"),
            _team("B", "B1"),
            _team("C", "C1"),
            _team("D", "D1"),
            _team("E", "E1"),
            _team("F", "F1"),
            *extra_teams,
        ],
        "parallel_games": {"U10": 2},
        "club_arenas": _clubs(*clubs),
        "club_calendar_status": _statuses(*clubs),
        "club_busy_intervals": {},
    }
    problem.update(overrides)
    return problem


def _underfilled_candidate():
    return {
        "schema_version": 1,
        "tournaments": [_tournament("t1", "B", [_team("B", "B1"), _team("C", "C1"), _team("D", "D1")])],
    }


def test_single_slot_fill_exposes_legal_under_target_candidates():
    candidate = _underfilled_candidate()
    problem = _underfilled_problem()

    repair_set = enumerate_underfilled_roster_repairs(candidate, problem, run_id="r1")

    assert not verify_candidate(deepcopy(candidate), problem)["ok"]
    fill_labels = {
        option["arguments"]["add_team"]["label"]
        for option in repair_set["options"]
        if option["action"] == "fill_participant"
    }
    assert fill_labels == {"A1", "E1", "F1"}
    assert all(option["hard_feasible"] for option in repair_set["options"])


def test_ranking_prefers_team_with_larger_participation_deficit():
    """A team behind its target outranks a team already close to target."""
    candidate = _underfilled_candidate()
    # A1, A2, B2 and E1 already have one participation; F1 has none.
    candidate["tournaments"].append(
        _tournament(
            "t2",
            "A",
            [_team("A", "A1"), _team("A", "A2"), _team("E", "E1"), _team("B", "B2")],
            date="2026-01-17",
        )
    )
    problem = _underfilled_problem(
        extra_teams=[_team("A", "A2"), _team("B", "B2")],
        target_tournament_count=2,
    )

    repair_set = enumerate_underfilled_roster_repairs(candidate, problem, run_id="r1")

    fill_options = [o for o in repair_set["options"] if o["action"] == "fill_participant"]
    assert fill_options[0]["arguments"]["add_team"]["label"] == "F1"
    assert fill_options[0]["effects"]["participation_deficit"] == 2


def test_lower_participation_sibling_team_is_selectable_when_legal():
    candidate = _underfilled_candidate()
    # Club G has two teams; only the stronger one already participates.
    candidate["tournaments"].append(
        _tournament("t2", "A", [_team("A", "A1"), _team("G", "G1"), _team("E", "E1"), _team("F", "F1")], date="2026-01-17")
    )
    problem = _underfilled_problem(
        extra_teams=[_team("G", "G1"), _team("G", "G2")],
        target_tournament_count=2,
    )

    repair_set = enumerate_underfilled_roster_repairs(candidate, problem, run_id="r1")

    fill_labels = {
        o["arguments"]["add_team"]["label"]
        for o in repair_set["options"]
        if o["action"] == "fill_participant"
    }
    assert "G2" in fill_labels


def test_same_date_team_is_rejected_with_explicit_reason():
    candidate = _underfilled_candidate()
    candidate["tournaments"].append(
        _tournament("t2", "A", [_team("A", "A1"), _team("E", "E1"), _team("F", "F1"), _team("B", "B2")], date="2026-01-10")
    )
    problem = _underfilled_problem(extra_teams=[_team("B", "B2")])

    repair_set = enumerate_underfilled_roster_repairs(candidate, problem)

    assert any(
        rejection["reason"] == "already_plays_same_date"
        and rejection["team"]["label"] == "A1"
        for rejection in repair_set["rejected_candidates"]
    )
    fill_labels = {
        o["arguments"]["add_team"]["label"]
        for o in repair_set["options"]
        if o["action"] == "fill_participant"
    }
    assert "A1" not in fill_labels


def test_team_at_participation_max_is_rejected_with_explicit_reason():
    candidate = _underfilled_candidate()
    # A1, E1, F1 and B2 already play once, so none may take another.
    candidate["tournaments"].append(
        _tournament(
            "t2",
            "A",
            [_team("A", "A1"), _team("E", "E1"), _team("F", "F1"), _team("B", "B2")],
            date="2026-01-17",
        )
    )
    problem = _underfilled_problem(extra_teams=[_team("B", "B2")], target_tournament_count=1)

    repair_set = enumerate_underfilled_roster_repairs(candidate, problem)

    assert any(
        rejection["reason"] == "at_participation_max"
        for rejection in repair_set["rejected_candidates"]
    )
    assert [o for o in repair_set["options"] if o["action"] == "fill_participant"] == []


def test_hard_per_club_cap_is_rejected_by_the_canonical_verifier():
    teams = [_team("B", f"B{index}") for index in range(1, 4)]
    candidate = {"schema_version": 1, "tournaments": [_tournament("t1", "B", teams)]}
    problem = {
        "teams": [_team("B", f"B{index}") for index in range(1, 5)],
        "parallel_games": {"U10": 2},
        "club_arenas": _clubs("B"),
        "club_calendar_status": _statuses("B"),
        "club_busy_intervals": {},
    }

    repair_set = enumerate_underfilled_roster_repairs(candidate, problem)

    # B4 is registered, under target and not same-date, but a fourth B team
    # would exceed the hard per-club cap: the canonical verifier's own code
    # must be the reason, never a hand-rolled rule.
    assert [o for o in repair_set["options"] if o["action"] == "fill_participant"] == []
    assert any(
        rejection["reason"] == "club_hard_max_exceeded"
        for rejection in repair_set["rejected_candidates"]
    )


def test_selected_fill_is_atomic_verified_and_regenerates_games():
    candidate = _underfilled_candidate()
    original = deepcopy(candidate)
    problem = _underfilled_problem()
    context = build_underfilled_roster_decision_context(candidate, problem, run_id="run-1")
    option = next(o for o in context.facts["repair_options"] if o["action"] == "fill_participant")

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

    applied = apply_underfilled_roster_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=context.facts["candidate_fingerprint"],
        run_id="run-1",
    )

    assert applied["ok"] and applied["verification"]["ok"]
    assert candidate == original
    assert applied["before_fingerprint"] != applied["after_fingerprint"]
    repaired = applied["candidate"]["tournaments"][0]
    assert len(repaired["teams"]) == 4
    assert len(repaired["games"]) == 6
    assert applied["effects"]["bye_team_not_allowed"] == -1


def test_bounded_same_age_swap_repairs_when_direct_fill_is_blocked():
    """When every direct fill is blocked, a verified same-age relocation that
    keeps both rosters legal is exposed instead of escalating."""
    candidate = {
        "schema_version": 1,
        "tournaments": [
            _tournament("t1", "B", [_team("B", "B1", target=1)], date="2026-01-10"),
            _tournament(
                "t2",
                "X",
                [_team("X", "X1", target=1), _team("S", "S1", target=1)],
                date="2026-01-17",
            ),
            _tournament(
                "t3",
                "A",
                [_team("A", "A2", target=1), _team("U", "U1", target=2)],
                date="2026-01-10",
            ),
        ],
    }
    problem = {
        "teams": [
            _team("A", "A2", target=1),
            _team("B", "B1", target=1),
            _team("S", "S1", target=1),
            _team("U", "U1", target=2),
            _team("X", "X1", target=1),
        ],
        "parallel_games": {"U10": 1},
        "club_arenas": _clubs("A", "B", "S", "U", "X"),
        "club_calendar_status": _statuses("A", "B", "S", "U", "X"),
        "club_busy_intervals": {},
    }
    original = deepcopy(candidate)

    repair_set = enumerate_underfilled_roster_repairs(candidate, problem, run_id="r1")

    assert [o for o in repair_set["options"] if o["action"] == "fill_participant"] == []
    swap = next(o for o in repair_set["options"] if o["action"] == "swap_participant")
    assert swap["arguments"]["add_team"]["label"] == "S1"
    assert swap["arguments"]["from_tournament_id"] == "t2"
    assert swap["arguments"]["donor_add_team"]["label"] == "U1"

    applied = apply_underfilled_roster_repair_option(
        candidate,
        problem,
        option_id=swap["option_id"],
        expected_fingerprint=repair_set["candidate_fingerprint"],
        run_id="r1",
    )

    assert applied["ok"] and applied["verification"]["ok"]
    assert candidate == original
    by_id = {t["id"]: [team["label"] for team in t["teams"]] for t in applied["candidate"]["tournaments"]}
    assert by_id["t1"] == ["B1", "S1"]
    assert by_id["t2"] == ["X1", "U1"]
    assert by_id["t3"] == ["A2", "U1"]


def test_pinned_tournament_reports_manual_restriction_for_every_candidate():
    candidate = _underfilled_candidate()
    problem = _underfilled_problem()
    problem["manual_adjustments"] = {"pinned_tournament_ids": ["t1"]}

    repair_set = enumerate_underfilled_roster_repairs(candidate, problem)

    assert repair_set["options"] == []
    assert {entry["reason"] for entry in repair_set["rejected_candidates"]} == {
        "manual_restriction_forbids_mutation"
    }


def test_input_constrained_pool_is_not_guessed_at():
    """A pool that cannot support the effective shape is reported as a fact,
    not silently 'repaired' by adding whatever is left."""
    candidate = {"schema_version": 1, "tournaments": [_tournament("t1", "B", [_team("B", "B1"), _team("C", "C1")])]}
    problem = {
        "teams": [_team("B", "B1"), _team("C", "C1"), _team("D", "D1")],
        "parallel_games": {"U10": 2},
        "club_arenas": _clubs("B", "C", "D"),
        "club_calendar_status": _statuses("B", "C", "D"),
        "club_busy_intervals": {},
    }

    repair_set = enumerate_underfilled_roster_repairs(candidate, problem)

    assert repair_set["options"] == []
    assert any(
        rejection["reason"] == "registered_pool_too_small"
        for rejection in repair_set["rejected_candidates"]
    )


def test_no_legal_option_returns_rejection_evidence_and_escalation_actions():
    candidate = _underfilled_candidate()
    candidate["tournaments"].append(
        _tournament(
            "t2",
            "A",
            [_team("A", "A1"), _team("E", "E1"), _team("F", "F1"), _team("B", "B2")],
            date="2026-01-17",
        )
    )
    problem = _underfilled_problem(extra_teams=[_team("B", "B2")], target_tournament_count=1)

    context = build_underfilled_roster_decision_context(candidate, problem, run_id="run-1")

    assert context.capability == "underfilled_roster_repair"
    assert context.facts["repair_options"] == []
    assert context.available_actions == ("optimize_plan", "request_operator")
    assert "apply_repair_option" not in context.available_actions
    assert context.warnings


def test_context_offers_stable_option_ids_with_matching_enum():
    candidate = _underfilled_candidate()
    problem = _underfilled_problem()
    context = build_underfilled_roster_decision_context(candidate, problem, run_id="run-1")

    assert "apply_repair_option" in context.available_actions
    option_ids = context.action_parameters["apply_repair_option"]["option_id"]["enum"]
    assert option_ids == [option["option_id"] for option in context.facts["repair_options"]]


def test_dispatcher_applies_underfilled_option_and_reports_family():
    candidate = _underfilled_candidate()
    problem = _underfilled_problem()
    context = build_underfilled_roster_decision_context(candidate, problem, run_id="run-1")
    option = context.facts["repair_options"][0]

    applied = apply_local_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=context.facts["candidate_fingerprint"],
        run_id="run-1",
    )

    assert applied["ok"] and applied["verification"]["ok"]
    assert applied["family"] == "underfilled_roster"


def test_stale_fingerprint_does_not_mutate_candidate():
    candidate = _underfilled_candidate()
    original = deepcopy(candidate)
    problem = _underfilled_problem()

    applied = apply_underfilled_roster_repair_option(
        candidate,
        problem,
        option_id="anything",
        expected_fingerprint="not-the-fingerprint",
    )

    assert not applied["ok"]
    assert applied["reason"] == "stale_candidate_fingerprint"
    assert candidate == original
