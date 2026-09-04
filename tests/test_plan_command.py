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
