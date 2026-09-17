"""Tests for responsibility-preserving host placement repair options (#369/#348)."""

from copy import deepcopy

from tournament_scheduler.application.decisions import DecisionAction, decide
from tournament_scheduler.host_placement_repair import (
    apply_host_placement_repair_option,
    build_host_placement_decision_context,
    enumerate_host_placement_repairs,
)
from tournament_scheduler.local_repair_options import apply_local_repair_option
from tournament_scheduler.planning_contract import verify_candidate

MANUAL_REASON = "Ingen verifisert ledig istid for H 2026-01-10 — turneringen må plasseres manuelt."


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


def _tournament(tid, host, teams, *, date, arena, start="10:00"):
    return {
        "id": tid,
        "date": date,
        "age_group": "U10",
        "host_club": host,
        "arena": arena,
        "start_time": start,
        "duration_minutes": 90,
        "teams": teams,
        "games": _round_robin_games(teams),
    }


def _pool():
    return [
        _team("H", "H1"),
        _team("A", "A1"),
        _team("A", "A2"),
        _team("B", "B1"),
        _team("B", "B2"),
        _team("B", "B3"),
        _team("C", "C1"),
        _team("C", "C2"),
        _team("C", "C3"),
        _team("D", "D1"),
        _team("D", "D2"),
        _team("D", "D3"),
    ]


def _problem(**overrides):
    problem = {
        "teams": _pool(),
        "parallel_games": {"U10": 2},
        "clubs": {"H": "H Arena", "A": "A Arena", "B": "B Arena", "C": "C Arena", "D": "D Arena"},
        "club_calendar_status": {club: "known" for club in "HABCD"},
        "club_busy_intervals": {},
        "start_date": "2026-01-01",
        "end_date": "2026-06-30",
        "christmas_split_date": "2026-03-01",
        "allow_cross_half_moves": False,
    }
    problem.update(overrides)
    return problem


def _manual_candidate(*, manual_reason=MANUAL_REASON, extra_tournaments=()):
    manual = _tournament(
        "t1",
        "H",
        [_team("H", "H1"), _team("B", "B1"), _team("C", "C1"), _team("D", "D1")],
        date="2026-01-10",
        arena="H Arena",
    )
    if manual_reason is not None:
        manual["manual_booking_reason"] = manual_reason
    return {
        "schema_version": 1,
        "unresolved_tournament_placements": [
            {"age_group": "U10", "date": "2026-01-10", "category": "manual_tournament_placement"}
        ],
        "tournaments": [
            manual,
            _tournament(
                "t2",
                "A",
                [_team("A", "A1"), _team("B", "B2"), _team("C", "C2"), _team("D", "D2")],
                date="2026-01-10",
                arena="A Arena",
            ),
            _tournament(
                "t3",
                "A",
                [_team("A", "A2"), _team("B", "B3"), _team("C", "C3"), _team("D", "D3")],
                date="2026-01-17",
                arena="A Arena",
            ),
            *extra_tournaments,
        ],
    }


def test_same_host_start_time_option_is_exposed_when_another_time_is_free():
    candidate = _manual_candidate()
    problem = _problem()

    repair_set = enumerate_host_placement_repairs(candidate, problem, run_id="r1")

    time_options = [o for o in repair_set["options"] if o["action"] == "move_same_host_start_time"]
    assert time_options
    assert time_options[0]["arguments"]["date"] == "2026-01-10"
    assert time_options[0]["arguments"]["start_time"] != "10:00"
    assert time_options[0]["evidence"]["responsible_host"] == "H"


def test_movable_busy_slot_option_reports_host_confirmation():
    """issue #373: a repair into the host's own movable_busy interval is valid
    but must be surfaced as requiring host confirmation, not as free ice."""
    candidate = _manual_candidate()
    problem = _problem(
        club_busy_intervals={
            "H": [
                {
                    "date": "2026-01-10",
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

    repair_set = enumerate_host_placement_repairs(candidate, problem, run_id="r1")

    time_options = [o for o in repair_set["options"] if o["action"] == "move_same_host_start_time"]
    assert time_options
    evidence = time_options[0]["evidence"]
    assert evidence["availability"] == "movable_busy"
    assert evidence["requires_host_confirmation"] is True
    assert evidence["calendar_event"] == "Åpen ishall"
    assert evidence["host_action_required"] == "host-controlled open ice"


def test_same_host_date_option_is_exposed_when_current_date_is_blocked():
    candidate = _manual_candidate()
    problem = _problem(
        club_busy_intervals={
            "H": [{"date": "2026-01-10", "start": "09:00", "end": "20:00", "kind": "external"}]
        }
    )

    repair_set = enumerate_host_placement_repairs(candidate, problem, run_id="r1")

    assert [o for o in repair_set["options"] if o["action"] == "move_same_host_start_time"] == []
    date_options = [o for o in repair_set["options"] if o["action"] == "move_same_host_date"]
    assert date_options
    assert date_options[0]["arguments"]["date"] == "2026-01-17"
    assert date_options[0]["arguments"]["start_time"] == "10:00"


def test_calendar_untrusted_host_is_rejected_with_explicit_reason():
    candidate = _manual_candidate()
    problem = _problem(club_calendar_status={club: "unknown" for club in "HABCD"})

    repair_set = enumerate_host_placement_repairs(candidate, problem)

    assert repair_set["options"] == []
    assert any(
        rejection["reason"] == "calendar_evidence_not_trusted"
        for rejection in repair_set["rejected_candidates"]
    )


def test_pinned_tournament_is_rejected_with_explicit_reason():
    candidate = _manual_candidate()
    problem = _problem()
    problem["manual_adjustments"] = {"pinned_tournament_ids": ["t1"]}

    repair_set = enumerate_host_placement_repairs(candidate, problem)

    assert repair_set["options"] == []
    assert {entry["reason"] for entry in repair_set["rejected_candidates"]} == {
        "manual_restriction_forbids_mutation"
    }


def test_cross_half_date_is_not_offered_without_explicit_permission():
    candidate = _manual_candidate()
    # Block every same-date start time so only a date move could repair it.
    problem = _problem(
        club_busy_intervals={
            "H": [{"date": "2026-01-10", "start": "09:00", "end": "20:00", "kind": "external"}]
        }
    )
    # Replace the only cross-half candidate date with an after-split date.
    candidate["tournaments"][2]["date"] = "2026-04-11"

    repair_set = enumerate_host_placement_repairs(candidate, problem)

    assert [o for o in repair_set["options"] if o["action"] == "move_same_host_date"] == []


def test_selected_option_preserves_host_and_clears_manual_state():
    candidate = _manual_candidate()
    original = deepcopy(candidate)
    problem = _problem()
    context = build_host_placement_decision_context(candidate, problem, run_id="run-1")
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

    applied = apply_host_placement_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=context.facts["candidate_fingerprint"],
        run_id="run-1",
    )

    assert applied["ok"] and applied["verification"]["ok"]
    assert candidate == original
    repaired = applied["candidate"]["tournaments"][0]
    assert repaired["host_club"] == "H"
    assert repaired["manual_booking_reason"] is None
    assert applied["candidate"]["unresolved_tournament_placements"] == []
    # The obligation was never transferred to the other club's arena.
    assert repaired["arena"] == "H Arena"


def test_moved_tournament_keeps_full_verified_schedule():
    candidate = _manual_candidate()
    problem = _problem(
        club_busy_intervals={
            "H": [{"date": "2026-01-10", "start": "09:00", "end": "20:00", "kind": "external"}]
        }
    )
    repair_set = enumerate_host_placement_repairs(candidate, problem)
    option = next(o for o in repair_set["options"] if o["action"] == "move_same_host_date")

    applied = apply_host_placement_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=repair_set["candidate_fingerprint"],
    )

    assert applied["ok"]
    repaired = applied["candidate"]["tournaments"][0]
    assert repaired["date"] == "2026-01-17"
    assert repaired["start_time"] == "10:00"
    assert applied["verification"]["ok"]


def test_context_offers_keep_baseline_and_stable_option_ids():
    candidate = _manual_candidate()
    problem = _problem()
    context = build_host_placement_decision_context(candidate, problem, run_id="run-1")

    assert context.capability == "host_placement_repair"
    assert "keep_baseline" in context.available_actions
    option_ids = context.action_parameters["apply_repair_option"]["option_id"]["enum"]
    assert option_ids == [option["option_id"] for option in context.facts["repair_options"]]


def test_dispatcher_applies_host_placement_option_and_reports_family():
    candidate = _manual_candidate()
    problem = _problem()
    context = build_host_placement_decision_context(candidate, problem, run_id="run-1")
    option = context.facts["repair_options"][0]

    applied = apply_local_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=context.facts["candidate_fingerprint"],
        run_id="run-1",
    )

    assert applied["ok"] and applied["verification"]["ok"]
    assert applied["family"] == "host_placement"
    assert applied["candidate"]["tournaments"][0]["manual_booking_reason"] is None


def test_stale_fingerprint_does_not_mutate_candidate():
    candidate = _manual_candidate()
    original = deepcopy(candidate)
    problem = _problem()

    applied = apply_host_placement_repair_option(
        candidate,
        problem,
        option_id="anything",
        expected_fingerprint="not-the-fingerprint",
    )

    assert not applied["ok"]
    assert applied["reason"] == "stale_candidate_fingerprint"
    assert candidate == original


def test_healthy_candidate_has_no_placement_findings():
    candidate = _manual_candidate(manual_reason=None)
    problem = _problem()

    repair_set = enumerate_host_placement_repairs(candidate, problem)

    assert repair_set["options"] == []
    assert repair_set["rejected_candidates"] == []
    assert verify_candidate(deepcopy(candidate), problem)["ok"]


def _swap_problem():
    problem = _problem()
    # A shared physical hall: both hosts use "H Arena", but each has its own
    # calendar. `ice_time_minutes` is required for the verifier's arena
    # interval collision check.
    problem["clubs"] = {"H": "H Arena", "A": "H Arena", "B": "B Arena", "C": "C Arena", "D": "D Arena"}
    problem["ice_time_minutes"] = {"U10": 90}
    problem["club_busy_intervals"] = {
        "H": [{"date": "2026-01-10", "start": "09:00", "end": "20:00", "kind": "external"}]
    }
    return problem


def _swap_candidate():
    manual = _tournament(
        "t1",
        "H",
        [_team("H", "H1"), _team("B", "B1"), _team("C", "C1"), _team("D", "D1")],
        date="2026-01-10",
        arena="H Arena",
    )
    manual["manual_booking_reason"] = MANUAL_REASON
    donor = _tournament(
        "t2",
        "A",
        [_team("A", "A1"), _team("B", "B2"), _team("C", "C2"), _team("D", "D2")],
        date="2026-01-17",
        arena="H Arena",
    )
    return {
        "schema_version": 1,
        "unresolved_tournament_placements": [
            {"age_group": "U10", "date": "2026-01-10", "category": "manual_tournament_placement"}
        ],
        "tournaments": [manual, donor],
    }


def test_compatible_placement_swap_is_offered_when_no_same_host_slot_exists():
    candidate = _swap_candidate()
    problem = _swap_problem()

    repair_set = enumerate_host_placement_repairs(candidate, problem, run_id="r1")

    assert [o for o in repair_set["options"] if o["action"] == "move_same_host_start_time"] == []
    assert [o for o in repair_set["options"] if o["action"] == "move_same_host_date"] == []
    swap = next(o for o in repair_set["options"] if o["action"] == "swap_compatible_tournament_placement")
    assert swap["arguments"]["date"] == "2026-01-17"
    assert swap["arguments"]["swap_tournament_id"] == "t2"
    assert swap["arguments"]["swap_tournament_date"] == "2026-01-10"
    reasons = {r["reason"] for r in repair_set["rejected_candidates"]}
    assert "arena_interval_conflict" in reasons


def test_selected_swap_exchanges_dates_without_transferring_responsibility():
    candidate = _swap_candidate()
    original = deepcopy(candidate)
    problem = _swap_problem()
    repair_set = enumerate_host_placement_repairs(candidate, problem)
    swap = next(o for o in repair_set["options"] if o["action"] == "swap_compatible_tournament_placement")

    applied = apply_host_placement_repair_option(
        candidate,
        problem,
        option_id=swap["option_id"],
        expected_fingerprint=repair_set["candidate_fingerprint"],
    )

    assert applied["ok"] and applied["verification"]["ok"]
    assert candidate == original
    by_id = {t["id"]: t for t in applied["candidate"]["tournaments"]}
    assert by_id["t1"]["host_club"] == "H"
    assert by_id["t1"]["date"] == "2026-01-17"
    assert by_id["t1"]["manual_booking_reason"] is None
    assert by_id["t2"]["host_club"] == "A"
    assert by_id["t2"]["date"] == "2026-01-10"
    assert applied["candidate"]["unresolved_tournament_placements"] == []


def test_swap_does_not_use_an_untrusted_or_manual_donor():
    candidate = _swap_candidate()
    problem = _swap_problem()
    problem["club_calendar_status"]["A"] = "unknown"

    repair_set = enumerate_host_placement_repairs(candidate, problem)

    assert [o for o in repair_set["options"] if o["action"] == "swap_compatible_tournament_placement"] == []
    assert any(
        r["reason"] == "donor_calendar_evidence_not_trusted"
        for r in repair_set["rejected_candidates"]
    )
