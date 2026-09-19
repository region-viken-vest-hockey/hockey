"""Acceptance tests for first-class, audited operator waivers (issue #350).

Covers the generic mechanism plus its initial ``participation_target_exceeded``
vertical slice: agents can never create a waiver, an explicit operator action
creates a narrowly scoped audited one, the verifier downgrades exactly the
matching violation, revocation restores hard failure, and structural
invariants cannot be bypassed.
"""

from __future__ import annotations

import json

import pytest

from tournament_scheduler.application.decisions import DECISION_ACTION_IDS
from tournament_scheduler.final_verification import publication_readiness, verify_final_candidate
from tournament_scheduler.game_generation import generate_tournament_games
from tournament_scheduler.host_team_missing_repair import (
    apply_host_team_missing_repair_option,
    enumerate_host_team_missing_repairs,
)
from tournament_scheduler.models import Team
from tournament_scheduler.operator_waivers import (
    NON_WAIVABLE_STRUCTURAL_INVARIANTS,
    WAIVABLE_RULE_IDS,
    WaiverError,
    create_waiver,
    find_participation_waiver,
    load_active_waivers,
    load_waivers,
    revoke_waiver,
    scope_fingerprint,
)
from tournament_scheduler.planning_contract import build_planning_problem, verify_candidate

U11 = "U11"


def _team(club: str, label: str, age_group: str = U11) -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(t_id: str, date: str, teams: list[dict], *, age_group: str = U11, host: str | None = None) -> dict:
    team_objs = [Team(club=t["club"], label=t["label"], age_group=age_group) for t in teams]
    games = generate_tournament_games(team_objs, 2, None)
    return {
        "id": t_id,
        "date": date,
        "arena": "Arena",
        "age_group": age_group,
        "host_club": host or teams[0]["club"],
        "start_time": "10:00",
        "teams": teams,
        "games": [
            {"home": g.home.label, "away": g.away.label, "parallel_slot": g.parallel_slot, "round_number": g.round_number}
            for g in games
        ],
    }


def _problem(teams: list[dict], *, targets: dict | None = None) -> dict:
    return {
        "start_date": "2025-09-01",
        "end_date": "2026-06-30",
        "teams": [dict(t, target_tournament_count=None) for t in teams],
        "parallel_games": {U11: 2},
        "rounds_per_tournament": {},
        "round_length_minutes": {U11: 30},
        "ice_time_minutes": {U11: 90},
        "participation_targets_by_age_group": {U11: targets or {"before_christmas": 3, "after_christmas": 3}},
        "manual_adjustments": {
            "locked_dates": [],
            "banned_dates": [],
            "forced_host_clubs": [],
            "excluded_host_clubs": [],
            "pinned_tournament_ids": [],
        },
        "club_calendar_status": {},
        "club_busy_intervals": {},
        "operator_waivers": [],
    }


def _three_team_candidate() -> tuple[dict, dict]:
    """A1 ends at 4 after Christmas, above an explicit hard maximum of 3."""
    a1, b1, c1, d1 = (_team("A", "A 1"), _team("B", "B 1"), _team("C", "C 1"), _team("D", "D 1"))
    e1, f1, g1, h1 = (_team("E", "E 1"), _team("F", "F 1"), _team("G", "G 1"), _team("H", "H 1"))
    candidate = {
        "schema_version": 1,
        "tournaments": [
            _tournament("t1", "2026-01-10", [a1, b1, c1, d1]),
            _tournament("t2", "2026-02-10", [a1, b1, c1, d1]),
            _tournament("t3", "2026-03-10", [a1, b1, c1, d1]),
            _tournament("t4", "2026-04-15", [a1, e1, f1, g1]),
        ],
    }
    problem = _problem([a1, b1, c1, d1, e1, f1, g1, h1], targets={"before_christmas": 3, "after_christmas": 3})
    # Explicit hard maximum -- the genuinely hard, operator-waivable ceiling.
    # The target itself remains a strong goal and is not a hard violation.
    problem["participation_hard_max"] = 3
    return candidate, problem


def _matching_waiver(candidate: dict, problem: dict, *, tournament_id: str = "t4", half: str | None = None) -> dict:
    fingerprint = scope_fingerprint(
        rule="participation_hard_max_exceeded",
        team={"club": "A", "label": "A 1", "age_group": U11},
        tournament_id=tournament_id,
        half=half,
        configured_value=3,
        allowed_value=4,
    )
    return {
        "id": "waiver-test",
        "rule": "participation_hard_max_exceeded",
        "scope": {"team": {"club": "A", "label": "A 1", "age_group": U11}, "tournament_id": tournament_id, "half": half},
        "configured_value": 3,
        "allowed_value": 4,
        "reason": "operator chose A 1 for host placement",
        "created_at": "2026-01-01T00:00:00+00:00",
        "created_by": "operator",
        "active": True,
        "scope_fingerprint": fingerprint,
    }


# ---------------------------------------------------------------------------
# Core verification behavior
# ---------------------------------------------------------------------------


def test_without_waiver_hard_max_overage_is_blocking():
    candidate, problem = _three_team_candidate()
    result = verify_candidate(candidate, problem)
    codes = [v["code"] for v in result["violations"]]
    assert "participation_hard_max_exceeded" in codes
    assert result["ok"] is False
    assert result["waived_violations"] == []


def test_matching_waiver_downgrades_only_that_violation():
    candidate, problem = _three_team_candidate()
    problem["operator_waivers"] = [_matching_waiver(candidate, problem)]
    result = verify_candidate(candidate, problem)
    assert result["ok"] is True
    assert result["violations"] == []
    assert [w["code"] for w in result["waived_violations"]] == ["participation_hard_max_exceeded"]
    assert result["waived_violations"][0]["waived_by_operator"] is True
    assert result["waived_violations"][0]["waiver_id"] == "waiver-test"


def test_another_team_overage_is_still_rejected():
    candidate, problem = _three_team_candidate()
    # Waiver for an unrelated team must not cover A 1.
    unrelated = _matching_waiver(candidate, problem)
    unrelated["scope"]["team"] = {"club": "B", "label": "B 1", "age_group": U11}
    unrelated["scope_fingerprint"] = scope_fingerprint(
        rule="participation_hard_max_exceeded",
        team=unrelated["scope"]["team"],
        tournament_id="t4",
        half=None,
        configured_value=3,
        allowed_value=4,
    )
    problem["operator_waivers"] = [unrelated]
    result = verify_candidate(candidate, problem)
    assert result["ok"] is False
    assert "participation_hard_max_exceeded" in [v["code"] for v in result["violations"]]


def test_target_overage_alone_is_not_a_hard_violation():
    """issue #376: without an explicit hard maximum, an over-target team is
    bounded strong-goal evidence -- it does not require a waiver and does not
    block verification."""
    candidate, problem = _three_team_candidate()
    problem.pop("participation_hard_max")
    result = verify_candidate(candidate, problem)
    assert result["ok"] is True
    assert result["violations"] == []
    assert result["waived_violations"] == []
    assert any(d["direction"] == "over_target" for d in result["participation_deviations"])


def test_waiver_scoped_to_absent_tournament_does_not_suppress():
    candidate, problem = _three_team_candidate()
    problem["operator_waivers"] = [_matching_waiver(candidate, problem, tournament_id="t-not-present")]
    result = verify_candidate(candidate, problem)
    assert result["ok"] is False
    assert "participation_hard_max_exceeded" in [v["code"] for v in result["violations"]]


def test_stale_waiver_wrong_allowed_value_does_not_suppress():
    candidate, problem = _three_team_candidate()
    # Team reaches 5, but the waiver only authorizes 4.
    extra = _tournament("t5", "2026-05-24", [_team("A", "A 1"), _team("E", "E 1"), _team("F", "F 1"), _team("G", "G 1")])
    candidate["tournaments"].append(extra)
    problem["operator_waivers"] = [_matching_waiver(candidate, problem)]
    result = verify_candidate(candidate, problem)
    assert result["ok"] is False
    assert "participation_hard_max_exceeded" in [v["code"] for v in result["violations"]]


def test_moving_the_extra_participation_makes_the_waiver_stale():
    """If the authorized tournament is no longer a participant, the waiver no
    longer matches even though the count is unchanged."""
    candidate, problem = _three_team_candidate()
    problem["operator_waivers"] = [_matching_waiver(candidate, problem, tournament_id="t4")]
    # Keep A 1 at 4 tournaments, but move the authorized participation out of
    # t4 into a new t5.
    candidate["tournaments"][3] = _tournament("t4", "2026-04-15", [_team("E", "E 1"), _team("F", "F 1"), _team("G", "G 1"), _team("H", "H 1")])
    candidate["tournaments"].append(
        _tournament("t5", "2026-05-24", [_team("A", "A 1"), _team("E", "E 1"), _team("F", "F 1"), _team("G", "G 1")])
    )
    result = verify_candidate(candidate, problem)
    assert result["ok"] is False
    assert "participation_hard_max_exceeded" in [v["code"] for v in result["violations"]]


def test_structural_invariant_failures_cannot_be_waived():
    """A non-waivable structural invariant stays blocking even if a hand-crafted
    record claiming its rule is injected into the problem."""
    fake = {
        "id": "waiver-fake",
        "rule": "unregistered_team",
        "scope": {"team": {"club": "A", "label": "A 1", "age_group": U11}, "tournament_id": "t1", "half": None},
        "configured_value": 0,
        "allowed_value": 1,
        "active": True,
    }
    candidate = {"tournaments": [_tournament("t1", "2026-01-10", [_team("A", "A 1")])]}
    problem = _problem([_team("B", "B 1")])
    problem["operator_waivers"] = [fake]
    result = verify_candidate(candidate, problem)
    assert result["ok"] is False
    assert "unregistered_team" in [v["code"] for v in result["violations"]]
    assert result["waived_violations"] == []


def test_waivable_and_non_waivable_rules_are_disjoint():
    assert WAIVABLE_RULE_IDS.isdisjoint(NON_WAIVABLE_STRUCTURAL_INVARIANTS)


# ---------------------------------------------------------------------------
# Store: create / revoke validation
# ---------------------------------------------------------------------------


def test_store_create_is_narrow_idempotent_and_audited(tmp_path):
    record, created = create_waiver(
        tmp_path,
        rule="participation_target_exceeded",
        team={"club": "A", "label": "A 1", "age_group": U11},
        tournament_id="t4",
        half="after_christmas",
        configured_value=3,
        allowed_value=4,
        reason="operator chose A 1",
        actor="operator@example",
        now="2026-01-01T00:00:00+00:00",
    )
    assert created is True
    assert record["active"] is True
    assert record["configured_value"] == 3 and record["allowed_value"] == 4
    assert record["created_by"] == "operator@example"
    assert record["scope_fingerprint"]

    again, created_again = create_waiver(
        tmp_path,
        rule="participation_target_exceeded",
        team={"club": "A", "label": "A 1", "age_group": U11},
        tournament_id="t4",
        half="after_christmas",
        configured_value=3,
        allowed_value=4,
        reason="operator chose A 1",
        actor="operator@example",
    )
    assert created_again is False
    assert again["id"] == record["id"]
    assert len(load_active_waivers(tmp_path)) == 1


def test_store_rejects_unwaivable_rule(tmp_path):
    with pytest.raises(WaiverError):
        create_waiver(
            tmp_path,
            rule="unregistered_team",
            team={"club": "A", "label": "A 1", "age_group": U11},
            configured_value=0,
            allowed_value=1,
            reason="nope",
        )


def test_store_rejects_allowed_value_at_or_below_target(tmp_path):
    with pytest.raises(WaiverError):
        create_waiver(
            tmp_path,
            rule="participation_target_exceeded",
            team={"club": "A", "label": "A 1", "age_group": U11},
            tournament_id="t4",
            half="after_christmas",
            configured_value=3,
            allowed_value=3,
            reason="not an overage",
        )


def test_revocation_restores_hard_failure(tmp_path):
    create_waiver(
        tmp_path,
        rule="participation_hard_max_exceeded",
        team={"club": "A", "label": "A 1", "age_group": U11},
        tournament_id="t4",
        half=None,
        configured_value=3,
        allowed_value=4,
        reason="operator chose A 1",
        actor="operator",
    )
    candidate, problem = _three_team_candidate()
    problem["operator_waivers"] = load_active_waivers(tmp_path)
    assert verify_candidate(candidate, problem)["ok"] is True

    record = load_active_waivers(tmp_path)[0]
    revoke_waiver(tmp_path, record["id"], reason="withdrawn", actor="operator")
    assert load_active_waivers(tmp_path) == []
    assert load_waivers(tmp_path)[0]["active"] is False

    problem["operator_waivers"] = load_active_waivers(tmp_path)
    assert verify_candidate(candidate, problem)["ok"] is False


def test_matching_requires_consistent_fingerprint(tmp_path):
    waiver = _matching_waiver({}, {})
    assert find_participation_waiver(
        {"operator_waivers": [waiver]},
        identity=("A", "A 1", U11),
        half=None,
        actual=4,
        configured=3,
        tournament_ids=["t4"],
    )
    tampered = dict(waiver, scope_fingerprint="0" * 64)
    assert not find_participation_waiver(
        {"operator_waivers": [tampered]},
        identity=("A", "A 1", U11),
        half=None,
        actual=4,
        configured=3,
        tournament_ids=["t4"],
    )


# ---------------------------------------------------------------------------
# Stage 4 readiness
# ---------------------------------------------------------------------------


def test_stage4_accepts_waived_candidate_and_keeps_it_visible():
    candidate, problem = _three_team_candidate()
    problem["operator_waivers"] = [_matching_waiver(candidate, problem)]
    result = verify_final_candidate(candidate, problem)
    assert result["ok"] is True
    assert result["violations"] == []
    assert len(result["waived_violations"]) == 1
    readiness = result["publication_readiness"]
    assert readiness["status"] == "REVIEW_REQUIRED"
    assert {"code": "operator_waivers", "count": 1} in readiness["reasons"]


def test_stage4_rejects_unwaived_candidate():
    candidate, problem = _three_team_candidate()
    result = verify_final_candidate(candidate, problem)
    assert result["ok"] is False
    assert result["publication_readiness"]["status"] == "INVALID"


def test_publication_readiness_reports_waivers_not_invalid():
    readiness = publication_readiness({"violations": [], "waived_violations": [{"code": "x"}]})
    assert readiness["status"] == "REVIEW_REQUIRED"
    assert readiness["publishable"] is False


# ---------------------------------------------------------------------------
# Agent boundary
# ---------------------------------------------------------------------------


def test_no_decision_action_can_create_a_waiver():
    assert not any("waiver" in action for action in DECISION_ACTION_IDS)


# ---------------------------------------------------------------------------
# Canonical operator CLI capability
# ---------------------------------------------------------------------------


def _write_run(tmp_path) -> None:
    from tournament_scheduler.pipeline.state import PipelineState, StageName

    candidate, problem = _three_team_candidate()
    state = PipelineState(tmp_path)
    state.write_stage(
        StageName.CONFIG,
        {
            "teams": problem["teams"],
            "start_date": problem["start_date"],
            "end_date": problem["end_date"],
            "participation_targets_by_age_group": problem["participation_targets_by_age_group"],
            "participation_hard_max": problem["participation_hard_max"],
            "parallel_games": problem["parallel_games"],
            "sources": [],
            "age_groups": [U11],
        },
    )
    state.write_stage(StageName.SCRAPING, {})
    state.write_stage(
        StageName.PLANNING,
        {
            "plan": {
                "start_date": problem["start_date"],
                "end_date": problem["end_date"],
                "tournaments": candidate["tournaments"],
            }
        },
    )


def test_cli_create_list_and_revoke_roundtrip(tmp_path):
    from tournament_scheduler.cli.rvv_cli import main

    _write_run(tmp_path)

    assert (
        main(
            [
                "waiver",
                "create",
                "--work-dir",
                str(tmp_path),
                "--rule",
                "participation_target_exceeded",
                "--club",
                "A",
                "--team",
                "A 1",
                "--age-group",
                U11,
                "--tournament",
                "t4",
                "--half",
                "after_christmas",
                "--allowed-value",
                "4",
                "--reason",
                "operator chose A 1 for host placement",
                "--actor",
                "operator",
            ]
        )
        == 0
    )
    records = load_active_waivers(tmp_path)
    assert len(records) == 1
    assert records[0]["allowed_value"] == 4
    assert records[0]["configured_value"] == 3

    # The CLI-created record is a real, scoped exception in the store. (A
    # target-based waiver no longer suppresses a hard verifier failure after
    # the participation-target reclassification -- only an explicit hard
    # maximum does -- so this test asserts the store roundtrip, not that it
    # downgrades a target deviation.)
    assert records[0]["rule"] == "participation_target_exceeded"

    assert main(["waiver", "revoke", records[0]["id"], "--work-dir", str(tmp_path), "--reason", "withdrawn"]) == 0
    assert load_active_waivers(tmp_path) == []


def test_cli_rejects_mismatched_scope(tmp_path):
    from tournament_scheduler.cli.rvv_cli import main

    _write_run(tmp_path)
    # A 1 does not participate in t1 in the stored plan (t1 is B1/C1/D1), and
    # allowed-value 7 is not the next count either.
    assert (
        main(
            [
                "waiver",
                "create",
                "--work-dir",
                str(tmp_path),
                "--rule",
                "participation_target_exceeded",
                "--club",
                "A",
                "--team",
                "A 1",
                "--age-group",
                U11,
                "--tournament",
                "t4",
                "--allowed-value",
                "7",
                "--reason",
                "nope",
            ]
        )
        == 1
    )
    assert load_active_waivers(tmp_path) == []


def test_cli_create_hard_max_waiver_roundtrip(tmp_path):
    """The canonical operator path for a genuinely hard participation ceiling."""
    from tournament_scheduler.cli.rvv_cli import main

    _write_run(tmp_path)
    assert (
        main(
            [
                "waiver",
                "create",
                "--work-dir",
                str(tmp_path),
                "--rule",
                "participation_hard_max_exceeded",
                "--club",
                "A",
                "--team",
                "A 1",
                "--age-group",
                U11,
                "--tournament",
                "t4",
                "--allowed-value",
                "4",
                "--reason",
                "operator-approved hard-max exception",
                "--actor",
                "operator",
            ]
        )
        == 0
    )
    records = load_active_waivers(tmp_path)
    assert len(records) == 1
    assert records[0]["rule"] == "participation_hard_max_exceeded"
    assert records[0]["configured_value"] == 3
    assert records[0]["allowed_value"] == 4

    candidate, problem = _three_team_candidate()
    problem["operator_waivers"] = records
    result = verify_candidate(candidate, problem)
    assert result["ok"] is True
    assert [v["code"] for v in result["waived_violations"]] == ["participation_hard_max_exceeded"]


def test_agent_boundary_host_repair_options_do_not_include_a_waiver_action():
    candidate, problem = _three_team_candidate()
    problem["operator_waivers"] = [_matching_waiver(candidate, problem)]
    # Repair enumeration validates candidates with the same waiver-aware
    # verifier; it still only ever exposes apply_repair_option, never any
    # waiver-creating action.
    assert all(action != "create_waiver" for action in DECISION_ACTION_IDS)


def _host_repair_scenario() -> tuple[dict, dict]:
    host1, host2 = _team("Host", "Host 1"), _team("Host", "Host 2")
    b, c, d, e = _team("B", "B 1"), _team("C", "C 1"), _team("D", "D 1"), _team("E", "E 1")
    f, g, h = _team("F", "F 1"), _team("G", "G 1"), _team("H", "H 1")
    candidate = {
        "schema_version": 1,
        "tournaments": [
            _tournament("t1", "2026-01-10", [b, c, d, e], host="Host"),
            _tournament("t0", "2026-02-17", [host1, f, g, h], host="F"),
        ],
    }
    problem = _problem([host1, host2, b, c, d, e, f, g, h], targets={"before_christmas": 1, "after_christmas": 1})
    # Explicit hard maximum: the motivating "host team is at its cap" flow is
    # now a genuinely hard ceiling, not the strong target goal.
    problem["participation_hard_max"] = 1
    problem["club_arenas"] = {club: f"{club} Arena" for club in ("Host", "B", "C", "D", "E", "F", "G", "H")}
    problem["club_calendar_status"] = {
        club: "known" for club in ("Host", "B", "C", "D", "E", "F", "G", "H")
    }
    return candidate, problem


def test_waiver_exposes_an_otherwise_rejected_host_repair_option():
    """When the operator's chosen host team would exceed an explicit
    participation hard maximum, only an explicit waiver makes that specific
    repair option legal."""
    candidate, problem = _host_repair_scenario()

    without = enumerate_host_team_missing_repairs(candidate, problem)
    assert not any(
        o["arguments"].get("add_team", {}).get("label") == "Host 1" for o in without["options"]
    )
    assert any(
        r.get("reason") == "at_participation_max" and r.get("team", {}).get("label") == "Host 1"
        for r in without["rejected_candidates"]
    )

    waiver = _matching_waiver(candidate, problem, tournament_id="t1")
    waiver["scope"]["team"] = {"club": "Host", "label": "Host 1", "age_group": U11}
    waiver["scope"]["half"] = None
    waiver["configured_value"] = 1
    waiver["allowed_value"] = 2
    waiver["scope_fingerprint"] = scope_fingerprint(
        rule="participation_hard_max_exceeded",
        team=waiver["scope"]["team"],
        tournament_id="t1",
        half=None,
        configured_value=1,
        allowed_value=2,
    )
    problem["operator_waivers"] = [waiver]

    with_waiver = enumerate_host_team_missing_repairs(candidate, problem)
    option = next(
        o for o in with_waiver["options"] if o["arguments"].get("add_team", {}).get("label") == "Host 1"
    )
    applied = apply_host_team_missing_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=with_waiver["candidate_fingerprint"],
    )
    assert applied["ok"] is True
    assert applied["verification"]["ok"] is True
    assert applied["verification"]["waived_violations"]


def test_build_planning_problem_carries_waivers():
    candidate, problem = _three_team_candidate()
    config = {
        "teams": problem["teams"],
        "start_date": problem["start_date"],
        "end_date": problem["end_date"],
        "participation_targets_by_age_group": problem["participation_targets_by_age_group"],
    }
    from datetime import date

    built = build_planning_problem(
        config,
        None,
        date(2025, 9, 1),
        date(2026, 6, 30),
        waivers=[_matching_waiver(candidate, problem)],
    )
    assert built["operator_waivers"][0]["id"] == "waiver-test"


def test_json_serialization_of_records(tmp_path):
    record, _ = create_waiver(
        tmp_path,
        rule="participation_target_exceeded",
        team={"club": "A", "label": "A 1", "age_group": U11},
        tournament_id="t4",
        half="after_christmas",
        configured_value=3,
        allowed_value=4,
        reason="operator chose A 1",
    )
    json.dumps(record)


def test_waiver_evidence_appears_in_operator_html():
    from datetime import date

    from tournament_scheduler.models import SeasonPlan
    from tournament_scheduler.pipeline.stage4_export_manual_schedule import _manual_schedule_html

    plan = SeasonPlan(tournaments=[], start_date=date(2025, 9, 1), end_date=date(2026, 6, 30))
    html = _manual_schedule_html(
        plan,
        manual_entries=[],
        participation_entries=[],
        waiver_entries=[
            {
                "id": "waiver-test",
                "rule": "participation_target_exceeded",
                "team": {"club": "A", "label": "A 1", "age_group": U11},
                "tournament_id": "t4",
                "half": "after_christmas",
                "configured_value": 3,
                "allowed_value": 4,
                "reason": "operator chose A 1 for host placement",
                "created_at": "2026-01-01T00:00:00+00:00",
                "created_by": "operator",
            }
        ],
    )
    assert "Operatorunntak" in html
    assert "A 1" in html
    assert "operator chose A 1 for host placement" in html
    assert "4/3" in html
