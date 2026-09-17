"""Tests for the ``rvv-miniputt stage3 session`` inspection facade."""

from __future__ import annotations

import argparse
import json

from tournament_scheduler.cli.pipeline_orchestrator.stage3_session_command import _cmd_stage3_session
from tournament_scheduler.pipeline.run_manifest import RunManifest
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus


def _args(work_dir: str, *, as_json: bool = True) -> argparse.Namespace:
    return argparse.Namespace(work_dir=work_dir, json=as_json, stage3_command="session")


def _candidate(seed: int) -> dict:
    return {"tournaments": [{"id": f"t{seed}", "date": "2026-10-05"}]}


def test_session_view_without_state_reports_new(tmp_path, capsys):
    rc = _cmd_stage3_session(_args(str(tmp_path)))
    assert rc == 0

    view = json.loads(capsys.readouterr().out)
    assert view["status"] == "new"
    assert view["pending_decision"] is None
    assert view["legal_transitions"] == ["create_baseline"]


def test_session_view_migrates_legacy_state_into_one_facade(tmp_path, capsys):
    state = PipelineState(str(tmp_path))
    state.write_stage(StageName.PLANNING, {"plan": _candidate(1), "warnings": []}, status=StageStatus.DONE)
    RunManifest(str(tmp_path)).start_run("objective", run_id="run-1")
    (tmp_path / "stage3_interactive_state.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "attempts_used": 2,
                "best_attempt": 1,
                "best_plan": {"plan": _candidate(1), "warnings": []},
                "pending_candidate": {"plan": _candidate(2), "warnings": []},
                "pending_attempt": 2,
                "last_context": {
                    "capability": "stage3_interactive",
                    "candidate_ref": "stage3_interactive:attempt_2",
                    "available_actions": ["keep_baseline", "optimize_plan"],
                },
            }
        ),
        encoding="utf-8",
    )

    rc = _cmd_stage3_session(_args(str(tmp_path)))
    assert rc == 0

    view = json.loads(capsys.readouterr().out)
    assert view["run_id"] == "run-1"
    assert view["status"] == "awaiting_adoption"
    assert view["candidate_revision"] == 2
    assert view["pending_decision"]["capability"] == "stage3_interactive"
    assert view["pending_decision"]["scope"] == "candidate"
    assert "run_search" in view["legal_transitions"]


def test_session_human_output_lists_status_and_transitions(tmp_path, capsys):
    rc = _cmd_stage3_session(_args(str(tmp_path), as_json=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Stage 3-sesjon" in out
    assert "status:" in out


def test_stage4_handoff_guard_blocks_mismatched_checkpoint(tmp_path):
    """No side-state may silently replace the finalized Stage 3 candidate."""
    from unittest.mock import patch

    from tournament_scheduler.application.stage3_session_store import finalize_stage3_plan
    from tournament_scheduler.cli.pipeline_orchestrator.run_command_interactive import _cmd_run_interactive

    state = PipelineState(str(tmp_path))
    state.write_stage(StageName.CONFIG, {"start_date": "2026-09-01", "end_date": "2027-04-30"}, status=StageStatus.DONE)
    # Stage 3 finalized plan A ...
    finalize_stage3_plan(
        str(tmp_path),
        {"plan": _candidate(1)},
        action_id="keep_baseline",
        rationale="reviewed",
        run_id="",
    )
    # ... but the checkpoint has been replaced by plan B.
    state.write_stage(StageName.PLANNING, {"plan": _candidate(2)}, status=StageStatus.DONE)

    args = argparse.Namespace(
        work_dir=str(tmp_path),
        input="input.xlsx",
        resume_from="4",
        non_strict=False,
        decision_action=None,
        decision_action_file=None,
        export_dir="export",
        iterations=1,
    )
    with patch(
        "tournament_scheduler.cli.pipeline_orchestrator.run_command_interactive._run_stage1",
        return_value=({"start_date": "2026-09-01", "end_date": "2027-04-30"}, False),
    ), patch(
        "tournament_scheduler.cli.pipeline_orchestrator.run_command_interactive._run_stage2",
        return_value=({"sources": [], "blocked": []}, False, False),
    ), patch(
        "tournament_scheduler.cli.pipeline_orchestrator.run_command_interactive._run_stage4_export",
    ) as run_stage4:
        exit_code = _cmd_run_interactive(args)

    assert exit_code == 1
    run_stage4.assert_not_called()
