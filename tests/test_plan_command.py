from __future__ import annotations

import argparse
import json

import pytest

from tournament_scheduler.cli.plan_command import (
    _cmd_plan_decide,
    _cmd_plan_decision_context,
    _parse_weight_overrides,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName
from tournament_scheduler.stage3_ab import build_ab_report
from tournament_scheduler.stage3_optimizer import optimize_candidate


def test_parse_weight_overrides_empty():
    assert _parse_weight_overrides(None) == ({}, {})
    assert _parse_weight_overrides([]) == ({}, {})


def test_parse_weight_overrides_parses_multiple():
    weights, per_age_group = _parse_weight_overrides(["gap_under_7=8.0", "same_club_pairing=1.5"])
    assert weights == {"gap_under_7": 8.0, "same_club_pairing": 1.5}
    assert per_age_group == {}


def test_parse_weight_overrides_parses_per_age_group():
    weights, per_age_group = _parse_weight_overrides(
        ["gap_under_7=8.0", "JU12:same_club_pairing=1.5", "JU12:gap_under_7=2.0", "JU14:pair_repeat=4.0"]
    )
    assert weights == {"gap_under_7": 8.0}
    assert per_age_group == {
        "JU12": {"same_club_pairing": 1.5, "gap_under_7": 2.0},
        "JU14": {"pair_repeat": 4.0},
    }


def test_parse_weight_overrides_rejects_malformed():
    with pytest.raises(ValueError):
        _parse_weight_overrides(["gap_under_7"])
    with pytest.raises(ValueError):
        _parse_weight_overrides(["JU12:gap_under_7"])


def _team(club: str, label: str, age_group: str) -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(t_id: str, date_str: str, arena: str, age_group: str, teams: list[dict]) -> dict:
    game_pairs = [(a["label"], b["label"]) for i, a in enumerate(teams) for b in teams[i + 1 :]]
    return {
        "id": t_id,
        "date": date_str,
        "arena": arena,
        "age_group": age_group,
        "host_club": teams[0]["club"] if teams else None,
        "teams": teams,
        "games": [
            {"home": home, "away": away, "parallel_slot": 0, "round_number": 1}
            for home, away in game_pairs
        ],
    }


def _clustered_candidate() -> dict:
    teams = {f"T{i}": _team(f"Club{i}", f"T{i}", "U10") for i in range(1, 9)}
    group_a = [teams["T1"], teams["T2"], teams["T3"], teams["T4"]]
    group_b = [teams["T5"], teams["T6"], teams["T7"], teams["T8"]]
    return {
        "schema_version": 1,
        "tournaments": [
            _tournament("t1", "2026-01-05", "Arena1", "U10", group_a),
            _tournament("t2", "2026-02-04", "Arena1", "U10", group_a),
            _tournament("t3", "2026-03-06", "Arena5", "U10", group_b),
            _tournament("t4", "2026-04-05", "Arena5", "U10", group_b),
        ],
    }


def _decision_context_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        ab_report="",
        work_dir="",
        run_id=None,
        baseline_ref=None,
        candidate_ref=None,
        objective=None,
        output=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _decide_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        ab_report="",
        action="keep_baseline",
        rationale="test rationale",
        target=None,
        candidate=None,
        question=None,
        output_dir=None,
        problem=None,
        iterations=3000,
        seed=1,
        weights=None,
        move_dates=False,
        work_dir="",
        run_id=None,
        baseline_ref=None,
        candidate_ref=None,
        objective=None,
        json=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestPlanDecisionContextCommand:
    def test_prints_decision_context_json(self, tmp_path, capsys):
        old_candidate = _clustered_candidate()
        new_candidate = optimize_candidate(old_candidate, iterations=3000, seed=1)
        report = build_ab_report(old_candidate, new_candidate)
        report_path = tmp_path / "ab_report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")

        args = _decision_context_args(ab_report=str(report_path), work_dir=str(tmp_path))
        exit_code = _cmd_plan_decision_context(args)
        assert exit_code == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["capability"] == "stage3_optimize"
        assert payload["available_actions"] == [
            "apply_candidate",
            "keep_baseline",
            "optimize_plan",
            "request_operator",
        ]


class TestPlanDecideCommand:
    def test_apply_candidate_writes_stage3_checkpoint_and_records_decision(self, tmp_path, capsys):
        work_dir = tmp_path / "pipeline"
        work_dir.mkdir()
        old_candidate = _clustered_candidate()
        PipelineState(str(work_dir)).write_stage(
            StageName.PLANNING, {"plan": old_candidate, "warnings": []}
        )
        new_candidate = optimize_candidate(old_candidate, iterations=3000, seed=1)
        report = build_ab_report(old_candidate, new_candidate)
        report_path = tmp_path / "ab_report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        candidate_path = tmp_path / "new_candidate.json"
        candidate_path.write_text(json.dumps(new_candidate), encoding="utf-8")

        args = _decide_args(
            ab_report=str(report_path),
            action="apply_candidate",
            candidate=str(candidate_path),
            work_dir=str(work_dir),
        )
        exit_code = _cmd_plan_decide(args)
        assert exit_code == 0

        checkpoint = PipelineState(str(work_dir)).read_stage(StageName.PLANNING)
        assert checkpoint["plan"]["tournaments"] == new_candidate["tournaments"]

        from tournament_scheduler.pipeline.run_manifest import RunManifest

        manifest = RunManifest(str(work_dir)).read()
        assert manifest["decision_log"][-1]["action"]["action_id"] == "apply_candidate"
        assert manifest["decision_log"][-1]["result"]["accepted"] is True

    def test_apply_candidate_blocked_by_invalid_new_candidate(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        work_dir.mkdir()
        old_candidate = _clustered_candidate()
        PipelineState(str(work_dir)).write_stage(
            StageName.PLANNING, {"plan": old_candidate, "warnings": []}
        )
        teams = [_team(f"Club{i}", f"T{i}", "U10") for i in range(1, 5)]
        invalid_candidate = {
            "schema_version": 1,
            "tournaments": [
                _tournament("t1", "2026-01-05", "Arena1", "U10", teams),
                _tournament("t2", "2026-01-05", "Arena1", "U10", teams),
            ],
        }
        report = build_ab_report(old_candidate, invalid_candidate)
        report_path = tmp_path / "ab_report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        candidate_path = tmp_path / "invalid_candidate.json"
        candidate_path.write_text(json.dumps(invalid_candidate), encoding="utf-8")

        args = _decide_args(
            ab_report=str(report_path),
            action="apply_candidate",
            candidate=str(candidate_path),
            work_dir=str(work_dir),
        )
        exit_code = _cmd_plan_decide(args)
        assert exit_code == 1

        # The Stage 3 checkpoint must not have been touched by a rejected decision.
        checkpoint = PipelineState(str(work_dir)).read_stage(StageName.PLANNING)
        assert checkpoint["plan"]["tournaments"] == old_candidate["tournaments"]

    def test_keep_baseline_is_recorded_without_touching_checkpoint(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        work_dir.mkdir()
        old_candidate = _clustered_candidate()
        PipelineState(str(work_dir)).write_stage(
            StageName.PLANNING, {"plan": old_candidate, "warnings": []}
        )
        new_candidate = optimize_candidate(old_candidate, iterations=3000, seed=1)
        report = build_ab_report(old_candidate, new_candidate)
        report_path = tmp_path / "ab_report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")

        args = _decide_args(
            ab_report=str(report_path), action="keep_baseline", work_dir=str(work_dir)
        )
        exit_code = _cmd_plan_decide(args)
        assert exit_code == 0

        checkpoint = PipelineState(str(work_dir)).read_stage(StageName.PLANNING)
        assert checkpoint["plan"]["tournaments"] == old_candidate["tournaments"]

    def test_optimize_plan_requires_output_dir(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        work_dir.mkdir()
        old_candidate = _clustered_candidate()
        PipelineState(str(work_dir)).write_stage(
            StageName.PLANNING, {"plan": old_candidate, "warnings": []}
        )
        new_candidate = optimize_candidate(old_candidate, iterations=3000, seed=1)
        report = build_ab_report(old_candidate, new_candidate)
        report_path = tmp_path / "ab_report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")

        args = _decide_args(
            ab_report=str(report_path), action="optimize_plan", work_dir=str(work_dir)
        )
        exit_code = _cmd_plan_decide(args)
        assert exit_code == 1

    def test_optimize_plan_executes_optimizer_and_writes_new_ab_report(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        work_dir.mkdir()
        old_candidate = _clustered_candidate()
        PipelineState(str(work_dir)).write_stage(
            StageName.PLANNING, {"plan": old_candidate, "warnings": []}
        )
        new_candidate = optimize_candidate(old_candidate, iterations=3000, seed=1)
        report = build_ab_report(old_candidate, new_candidate)
        report_path = tmp_path / "ab_report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        output_dir = tmp_path / "optimize_plan_out"

        args = _decide_args(
            ab_report=str(report_path),
            action="optimize_plan",
            work_dir=str(work_dir),
            output_dir=str(output_dir),
            seed=2,
        )
        exit_code = _cmd_plan_decide(args)
        assert exit_code == 0

        assert (output_dir / "old_candidate.json").exists()
        assert (output_dir / "new_candidate.json").exists()
        new_report = json.loads((output_dir / "ab_report.json").read_text(encoding="utf-8"))
        assert new_report["old"]["verification"]["ok"]
        assert "dominates_baseline" in new_report

        # The Stage 3 checkpoint itself must be untouched — optimize_plan only
        # produces a new candidate for the next decision, it never applies it.
        checkpoint = PipelineState(str(work_dir)).read_stage(StageName.PLANNING)
        assert checkpoint["plan"]["tournaments"] == old_candidate["tournaments"]

    def test_optimize_plan_can_continue_from_a_supplied_candidate(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        work_dir.mkdir()
        old_candidate = _clustered_candidate()
        PipelineState(str(work_dir)).write_stage(
            StageName.PLANNING, {"plan": old_candidate, "warnings": []}
        )
        first_pass = optimize_candidate(old_candidate, iterations=3000, seed=1)
        first_candidate_path = tmp_path / "first_candidate.json"
        first_candidate_path.write_text(json.dumps(first_pass), encoding="utf-8")
        report = build_ab_report(old_candidate, first_pass)
        report_path = tmp_path / "ab_report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        output_dir = tmp_path / "optimize_plan_out"

        args = _decide_args(
            ab_report=str(report_path),
            action="optimize_plan",
            work_dir=str(work_dir),
            candidate=str(first_candidate_path),
            output_dir=str(output_dir),
            seed=5,
        )
        exit_code = _cmd_plan_decide(args)
        assert exit_code == 0

        new_report = json.loads((output_dir / "ab_report.json").read_text(encoding="utf-8"))
        old_candidate_out = json.loads((output_dir / "old_candidate.json").read_text(encoding="utf-8"))
        # Always compared against the true Stage 3 baseline, not whatever
        # candidate we resumed the search from.
        assert old_candidate_out["tournaments"] == old_candidate["tournaments"]
        assert "dominates_baseline" in new_report


class TestDecisionLoopEndToEnd:
    """Proves the full LLM -> optimizer -> verifier -> LLM decision loop end-to-end.

    A DecisionContext built from an ab_report offers optimize_plan; deciding
    optimize_plan runs the real Stage 3 v2 optimizer/verifier and writes a
    new ab_report; a second DecisionContext built from that new report is
    what an LLM/agent controller would see next, and deciding apply_candidate
    against it is what finally writes the Stage 3 checkpoint. Nothing here
    is a Python quality heuristic auto-choosing the outcome.
    """

    def test_optimize_then_apply_loop(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        work_dir.mkdir()
        old_candidate = _clustered_candidate()
        PipelineState(str(work_dir)).write_stage(
            StageName.PLANNING, {"plan": old_candidate, "warnings": []}
        )

        # Step 1: an initial ab_report (as if from 'plan ab') gives the LLM/agent
        # a DecisionContext offering optimize_plan among other actions.
        first_pass = optimize_candidate(old_candidate, iterations=3000, seed=1)
        first_report = build_ab_report(old_candidate, first_pass)
        first_report_path = tmp_path / "ab_report_1.json"
        first_report_path.write_text(json.dumps(first_report), encoding="utf-8")

        context_1 = _cmd_plan_decision_context(
            _decision_context_args(ab_report=str(first_report_path), work_dir=str(work_dir))
        )
        assert context_1 == 0

        # Step 2: the LLM/agent chooses optimize_plan with adjusted settings.
        # 'plan decide' executes the real optimizer/verifier and writes a new
        # ab_report — it does not just record a note for a human to act on.
        optimize_output_dir = tmp_path / "optimize_out"
        decide_optimize_exit = _cmd_plan_decide(
            _decide_args(
                ab_report=str(first_report_path),
                action="optimize_plan",
                work_dir=str(work_dir),
                output_dir=str(optimize_output_dir),
                seed=9,
                move_dates=True,
            )
        )
        assert decide_optimize_exit == 0

        second_report_path = optimize_output_dir / "ab_report.json"
        assert second_report_path.exists()
        second_candidate_path = optimize_output_dir / "new_candidate.json"
        assert second_candidate_path.exists()

        # Stage 3 checkpoint must still be untouched after optimize_plan alone.
        checkpoint_after_optimize = PipelineState(str(work_dir)).read_stage(StageName.PLANNING)
        assert checkpoint_after_optimize["plan"]["tournaments"] == old_candidate["tournaments"]

        # Step 3: the new ab_report becomes the next DecisionContext an
        # LLM/agent controller would see — the loop back into the LLM.
        context_2 = _cmd_plan_decision_context(
            _decision_context_args(ab_report=str(second_report_path), work_dir=str(work_dir))
        )
        assert context_2 == 0

        # Step 4: this time the LLM/agent chooses apply_candidate. The
        # deterministic verifier still gates it — a rejected candidate could
        # never reach here since build_ab_report/verify_candidate already ran
        # inside optimize_plan and the new candidate passed hard constraints.
        second_report = json.loads(second_report_path.read_text(encoding="utf-8"))
        assert second_report["new"]["verification"]["ok"]

        decide_apply_exit = _cmd_plan_decide(
            _decide_args(
                ab_report=str(second_report_path),
                action="apply_candidate",
                work_dir=str(work_dir),
                candidate=str(second_candidate_path),
            )
        )
        assert decide_apply_exit == 0

        final_checkpoint = PipelineState(str(work_dir)).read_stage(StageName.PLANNING)
        second_candidate = json.loads(second_candidate_path.read_text(encoding="utf-8"))
        assert final_checkpoint["plan"]["tournaments"] == second_candidate["tournaments"]

        from tournament_scheduler.pipeline.run_manifest import RunManifest

        manifest = RunManifest(str(work_dir)).read()
        actions = [entry["action"]["action_id"] for entry in manifest["decision_log"]]
        assert actions == ["optimize_plan", "apply_candidate"]
