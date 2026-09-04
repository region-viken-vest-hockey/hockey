"""Tests for ``rvv-miniputt run --interactive`` (issue #260 Phase 5).

Covers the DecisionContext emission / DecisionAction validation loop added
around the existing ``_run_stageN`` helpers, not the helpers themselves
(already covered elsewhere) — this module mocks them out so tests run fast
and don't touch real pipeline stages.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tournament_scheduler.cli.pipeline_orchestrator import (
    _cmd_run_interactive,
    _decision_summary_for_checkpoint,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus


def _args(**overrides: Any) -> SimpleNamespace:
    base = dict(
        work_dir=None,
        input="input.xlsx",
        export_dir="export",
        resume_from="1",
        non_strict=False,
        force_refresh=False,
        allow_missing_sources=False,
        manual_bookup_login=False,
        manual_bookup_login_timeout=None,
        timestamped_export=True,
        iterations=1,
        interactive=True,
        decision_action=None,
        decision_action_file=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture()
def state(tmp_path) -> PipelineState:
    return PipelineState(str(tmp_path))


class TestDecisionSummaryForCheckpoint:
    def test_stage1_uses_effective_config_not_raw_checkpoint(self):
        # Stage 1's own checkpoint never stores sources/dates (they come
        # from input.xlsx via load_effective_config) — a caller that reads
        # the raw checkpoint instead would silently see sources=0.
        raw_checkpoint = {"teams": [{"club": "A", "label": "A1", "age_group": "U10"}]}
        effective_config = {
            "sources": [{"name": "x"}],
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "age_groups": ["U10"],
            "clubs": ["A"],
        }
        summary = _decision_summary_for_checkpoint(1, raw_checkpoint, effective_config=effective_config)
        assert summary["sources"] == 1
        assert summary["start_date"] == "2026-09-01"

    def test_stage2_counts_blocked_and_scanned(self):
        checkpoint = {"sources": [{"name": "a"}, {"name": "b"}], "blocked": ["b"]}
        summary = _decision_summary_for_checkpoint(2, checkpoint)
        assert summary["sources_scanned"] == 2
        assert summary["blocked"] == ["b"]

    def test_stage4_lists_output_file_kinds(self):
        checkpoint = {"output_files": {"excel": "a.xlsx", "ical": "a.ics"}, "errors": []}
        summary = _decision_summary_for_checkpoint(4, checkpoint)
        assert set(summary["files_written"]) == {"excel", "ical"}


class TestCmdRunInteractive:
    def test_emits_valid_decision_context_json(self, state, tmp_path, capsys):
        args = _args(work_dir=str(tmp_path), resume_from="1")
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=({"start_date": "2026-09-01", "end_date": "2027-04-30"}, False),
        ), patch(
            "tournament_scheduler.pipeline.stage1_config.load_effective_config",
            return_value={"sources": [{"name": "x"}], "start_date": "2026-09-01", "end_date": "2027-04-30"},
        ):
            exit_code = _cmd_run_interactive(args)
            out = capsys.readouterr().out

        assert exit_code == 2
        payload = json.loads(out)
        assert payload["capability"] == "config"
        assert payload["available_actions"] == ["proceed", "abort", "retry_stage", "request_operator"]
        assert payload["facts"]["sources"] == 1

    def test_abort_decision_stops_before_target_stage_runs(self, state, tmp_path):
        state.write_stage(StageName.CONFIG, {}, status=StageStatus.DONE)
        args = _args(
            work_dir=str(tmp_path),
            resume_from="2",
            decision_action=json.dumps({"action_id": "abort", "rationale": "test"}),
        )
        with patch(
            "tournament_scheduler.pipeline.stage1_config.load_effective_config",
            return_value={"sources": [], "start_date": "2026-09-01", "end_date": "2027-04-30"},
        ), patch("tournament_scheduler.cli.pipeline_orchestrator._run_stage2") as run_stage2:
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 1
        run_stage2.assert_not_called()

    def test_unknown_action_rejected_before_any_stage_runs(self, state, tmp_path):
        state.write_stage(StageName.CONFIG, {}, status=StageStatus.DONE)
        args = _args(
            work_dir=str(tmp_path),
            resume_from="2",
            decision_action=json.dumps({"action_id": "not_a_real_action"}),
        )
        with patch(
            "tournament_scheduler.pipeline.stage1_config.load_effective_config",
            return_value={"sources": [], "start_date": "2026-09-01", "end_date": "2027-04-30"},
        ), patch("tournament_scheduler.cli.pipeline_orchestrator._run_stage2") as run_stage2:
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 1
        run_stage2.assert_not_called()

    def test_decision_action_without_resume_from_2_or_more_is_rejected(self, state, tmp_path):
        args = _args(
            work_dir=str(tmp_path),
            resume_from="1",
            decision_action=json.dumps({"action_id": "proceed"}),
        )
        exit_code = _cmd_run_interactive(args)
        assert exit_code == 1

    def test_retry_stage_reruns_previous_stage_instead_of_target(self, state, tmp_path):
        state.write_stage(StageName.CONFIG, {}, status=StageStatus.DONE)
        args = _args(
            work_dir=str(tmp_path),
            resume_from="2",
            decision_action=json.dumps({"action_id": "retry_stage", "arguments": {"stage": "1"}}),
        )
        with patch(
            "tournament_scheduler.pipeline.stage1_config.load_effective_config",
            return_value={"sources": [], "start_date": "2026-09-01", "end_date": "2027-04-30"},
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=({"start_date": "2026-09-01", "end_date": "2027-04-30"}, False),
        ) as run_stage1, patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2"
        ) as run_stage2:
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 2
        run_stage1.assert_called_once()
        run_stage2.assert_not_called()

    def test_proceed_decision_records_audit_trail_in_manifest(self, state, tmp_path):
        state.write_stage(StageName.CONFIG, {}, status=StageStatus.DONE)
        args = _args(
            work_dir=str(tmp_path),
            resume_from="2",
            decision_action=json.dumps({"action_id": "proceed", "rationale": "looks fine"}),
        )
        with patch(
            "tournament_scheduler.pipeline.stage1_config.load_effective_config",
            return_value={"sources": [], "start_date": "2026-09-01", "end_date": "2027-04-30"},
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ):
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 2
        manifest_path = tmp_path / "run_manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        decisions = manifest.get("decision_log", [])
        assert len(decisions) == 1
        assert decisions[0]["action"]["action_id"] == "proceed"
        assert decisions[0]["result"]["accepted"] is True
