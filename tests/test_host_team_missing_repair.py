from copy import deepcopy

from tournament_scheduler.application.decisions import DecisionAction, decide
from tournament_scheduler.host_team_missing_repair import (
    apply_host_team_missing_repair_option,
    build_host_team_missing_decision_context,
    candidate_fingerprint,
    enumerate_host_team_missing_repairs,
)
from tournament_scheduler.planning_contract import verify_candidate


def _team(club, label=None, age="U12", target=None):
    row = {"club": club, "label": label or club, "age_group": age}
    if target is not None:
        row["target_tournament_count"] = target
    return row


def _game(home, away, round_number):
    return {"home": home, "away": away, "round_number": round_number, "parallel_slot": 0}


def _tournament(tid, host, teams, *, arena=None, start="10:00"):
    labels = [team["label"] for team in teams]
    games = [
        _game(labels[0], labels[3], 1),
        _game(labels[1], labels[2], 1),
        _game(labels[0], labels[2], 2),
        _game(labels[3], labels[1], 2),
        _game(labels[0], labels[1], 3),
        _game(labels[2], labels[3], 3),
    ]
    return {
        "id": tid,
        "date": "2026-01-10",
        "age_group": "U12",
        "host_club": host,
        "arena": arena or f"{host} Arena",
        "start_time": start,
        "duration_minutes": 120,
        "teams": teams,
        "games": games,
    }


def _problem(extra_teams=(), **overrides):
    teams = [
        _team("Host", "Host 1"),
        _team("Host", "Host 2"),
        _team("B"),
        _team("C"),
        _team("D"),
        _team("E"),
        *extra_teams,
    ]
    problem = {
        "teams": teams,
        "parallel_games": {"U12": 2},
        "club_arenas": {"Host": "Host Arena", "B": "B Arena", "C": "C Arena", "D": "D Arena", "E": "E Arena"},
        "club_calendar_status": {"Host": "known", "B": "known", "C": "known", "D": "known", "E": "known"},
        "club_busy_intervals": {},
    }
    problem.update(overrides)
    return problem


def _invalid_candidate():
    teams = [_team("B"), _team("C"), _team("D"), _team("E")]
    return {"schema_version": 1, "tournaments": [_tournament("t1", "Host", teams)]}


def test_host_club_sibling_teams_expose_replacement_options_with_deficit_effects():
    candidate = _invalid_candidate()
    problem = _problem(target_tournament_count=1)

    repair_set = enumerate_host_team_missing_repairs(candidate, problem, run_id="r1")

    assert not verify_candidate(candidate, problem)["ok"]
    options = repair_set["options"]
    host_options = [o for o in options if o["action"] == "replace_participant" and o["arguments"]["add_team"]["club"] == "Host"]
    assert {o["arguments"]["add_team"]["label"] for o in host_options} == {"Host 1", "Host 2"}
    assert all("participation_deficit" in o["effects"] for o in host_options)


def test_same_date_host_team_is_rejected_with_explicit_reason():
    candidate = _invalid_candidate()
    candidate["tournaments"].append(_tournament("t2", "B", [_team("Host", "Host 1"), _team("B"), _team("C"), _team("D")], arena="B Arena"))
    problem = _problem()

    repair_set = enumerate_host_team_missing_repairs(candidate, problem)

    assert any(r["reason"] == "already_plays_same_date" and r["team"]["label"] == "Host 1" for r in repair_set["rejected_candidates"])


def test_team_at_participation_max_is_rejected_with_explicit_reason():
    candidate = _invalid_candidate()
    candidate["tournaments"].append(_tournament("t2", "B", [_team("Host", "Host 1"), _team("B"), _team("C"), _team("D")], arena="B Arena"))
    candidate["tournaments"][1]["date"] = "2026-01-17"
    problem = _problem(target_tournament_count=1)

    repair_set = enumerate_host_team_missing_repairs(candidate, problem)

    assert any(r["reason"] == "at_participation_max" and r["team"]["label"] == "Host 1" for r in repair_set["rejected_candidates"])


def test_rehost_option_exposes_arena_and_time_evidence():
    candidate = _invalid_candidate()
    problem = _problem()

    repair_set = enumerate_host_team_missing_repairs(candidate, problem)

    option = next(o for o in repair_set["options"] if o["action"] == "rehost" and o["arguments"]["host_club"] == "B")
    assert option["arguments"]["arena"] == "B Arena"
    assert option["evidence"]["start_time"] == "10:00"
    assert option["evidence"]["end_time"] == "12:00"


def test_represented_club_external_conflict_is_rejected_with_reason():
    candidate = _invalid_candidate()
    problem = _problem(club_busy_intervals={"B": [{"date": "2026-01-10", "start": "09:00", "end": "18:00"}]})

    repair_set = enumerate_host_team_missing_repairs(candidate, problem)

    assert any(r["reason"] == "external_calendar_conflict" and r["host_club"] == "B" for r in repair_set["rejected_candidates"])


def test_harness_selects_stable_option_id_and_participant_repair_is_atomic_and_verified():
    candidate = _invalid_candidate()
    original = deepcopy(candidate)
    problem = _problem()
    context = build_host_team_missing_decision_context(candidate, problem, run_id="run-1")
    option = next(o for o in context.facts["repair_options"] if o["action"] == "replace_participant")

    result = decide(context, DecisionAction(action_id="apply_repair_option", arguments={"option_id": option["option_id"], "candidate_fingerprint": context.facts["candidate_fingerprint"]}))
    assert result.accepted

    applied = apply_host_team_missing_repair_option(candidate, problem, option_id=option["option_id"], expected_fingerprint=context.facts["candidate_fingerprint"], run_id="run-1")
    assert applied["ok"]
    assert applied["verification"]["ok"]
    assert candidate == original
    assert applied["before_fingerprint"] != applied["after_fingerprint"]
    repaired_tournament = applied["candidate"]["tournaments"][0]
    assert any(team["club"] == "Host" for team in repaired_tournament["teams"])
    assert len(repaired_tournament["games"]) == 6


def test_selected_rehost_recomputes_placement_and_passes_full_verifier():
    candidate = _invalid_candidate()
    problem = _problem()
    repair_set = enumerate_host_team_missing_repairs(candidate, problem)
    option = next(o for o in repair_set["options"] if o["action"] == "rehost" and o["arguments"]["host_club"] == "B")

    applied = apply_host_team_missing_repair_option(candidate, problem, option_id=option["option_id"], expected_fingerprint=repair_set["candidate_fingerprint"])

    assert applied["ok"]
    tournament = applied["candidate"]["tournaments"][0]
    assert tournament["host_club"] == "B"
    assert tournament["arena"] == "B Arena"
    assert applied["verification"]["ok"]


def test_stale_option_or_failed_application_does_not_mutate_candidate():
    candidate = _invalid_candidate()
    original = deepcopy(candidate)
    problem = _problem()
    repair_set = enumerate_host_team_missing_repairs(candidate, problem)
    wrong_fingerprint = candidate_fingerprint({"different": True})

    stale = apply_host_team_missing_repair_option(candidate, problem, option_id=repair_set["options"][0]["option_id"], expected_fingerprint=wrong_fingerprint)

    assert not stale["ok"]
    assert stale["reason"] == "stale_candidate_fingerprint"
    assert candidate == original
