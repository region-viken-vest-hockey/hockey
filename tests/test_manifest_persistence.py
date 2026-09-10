"""Tests for reliable, observable run-manifest persistence (issue #14).

Covers: atomic writes, corrupted-manifest recovery with a visible
diagnostic and backup, the operator-state health check, visible (not
silently swallowed) warnings from the CLI-facing manifest wrappers, the
final-outcome downgrade when persistence degraded, and the
durable-persistence gate on approval-required operator actions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tournament_scheduler.pipeline.capability_result import CapabilityResult
from tournament_scheduler.pipeline.escalation import Question, raise_question
from tournament_scheduler.pipeline.operator_action import (
    ActionRegistry,
    ApprovalRequiredError,
    OperatorAction,
    PersistenceUnavailableError,
    RiskLevel,
)
from tournament_scheduler.pipeline.run_manifest import ManifestPersistenceError, RunManifest, is_durable


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------


class TestAtomicWrites:
    def test_normal_write_leaves_no_temp_files_behind(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective")
        leftovers = list(tmp_path.glob("*.tmp-*"))
        assert leftovers == []

    def test_write_replaces_content_atomically(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("first objective")
        manifest.start_run("second objective")
        assert manifest.read()["objective"] == "second objective"

    def test_write_failure_raises_manifest_persistence_error(self, tmp_path, monkeypatch):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective")

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom)
        with pytest.raises(ManifestPersistenceError):
            manifest.start_run("new objective")

    def test_write_failure_preserves_the_previous_valid_manifest(self, tmp_path, monkeypatch):
        manifest = RunManifest(tmp_path)
        manifest.start_run("first objective")

        original_write_text = Path.write_text

        def _boom(self, *args, **kwargs):
            if self.name.startswith("run_manifest.json.tmp-"):
                raise OSError("disk full")
            return original_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", _boom)
        with pytest.raises(ManifestPersistenceError):
            manifest.start_run("second objective")

        # Re-read with a fresh instance (no monkeypatch involved) — the
        # original content must be untouched.
        assert RunManifest(tmp_path).read()["objective"] == "first objective"

    def test_write_failure_does_not_leave_a_dangling_temp_file(self, tmp_path, monkeypatch):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective")

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom)
        with pytest.raises(ManifestPersistenceError):
            manifest.start_run("second objective")

        assert list(tmp_path.glob("*.tmp-*")) == []


# ---------------------------------------------------------------------------
# Corrupted-manifest recovery
# ---------------------------------------------------------------------------


class TestCorruptedManifestRecovery:
    def test_invalid_json_is_backed_up_and_flagged(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.path.write_text("not valid json{{{", encoding="utf-8")

        data = manifest.read()

        assert data["synthesized_from_legacy_checkpoints"] is True
        recovery = data["manifest_recovery"]
        assert recovery is not None
        assert recovery["reason"] == "invalid_json"
        assert recovery["backup_path"] is not None
        assert Path(recovery["backup_path"]).read_text(encoding="utf-8") == "not valid json{{{"

    def test_valid_json_that_is_not_an_object_is_flagged(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.path.write_text("[1, 2, 3]", encoding="utf-8")

        data = manifest.read()
        assert data["manifest_recovery"]["reason"] == "not_a_json_object"

    def test_healthy_manifest_has_no_recovery_flag(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective")
        assert manifest.read()["manifest_recovery"] is None

    def test_corruption_downgrades_outcome_from_ok_to_warning(self, tmp_path):
        from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus

        state = PipelineState(tmp_path)
        for stage in (StageName.CONFIG, StageName.SCRAPING, StageName.PLANNING, StageName.EXPORT):
            state.write_stage(stage, {"x": 1}, status=StageStatus.DONE)

        manifest = RunManifest(tmp_path)
        manifest.path.write_text("{broken", encoding="utf-8")
        data = manifest.read()
        assert data["final_outcome"] == "warning"

    def test_backup_file_is_unique_per_recovery(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.path.write_text("broken 1", encoding="utf-8")
        first = manifest.read()["manifest_recovery"]["backup_path"]

        manifest.path.write_text("broken 2", encoding="utf-8")
        second = manifest.read()["manifest_recovery"]["backup_path"]

        assert first != second
        assert Path(first).read_text(encoding="utf-8") == "broken 1"
        assert Path(second).read_text(encoding="utf-8") == "broken 2"


# ---------------------------------------------------------------------------
# Operator-state health check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_healthy_workspace_reports_healthy_and_writable(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective")
        result = manifest.check_health()
        assert result == {"healthy": True, "writable": True, "manifest_recovery": None, "detail": ""}

    def test_fresh_workspace_with_no_manifest_yet_is_still_healthy(self, tmp_path):
        result = RunManifest(tmp_path).check_health()
        assert result["healthy"] is True
        assert result["writable"] is True

    def test_corrupted_manifest_reports_unhealthy_but_writable(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.path.write_text("not json", encoding="utf-8")
        result = manifest.check_health()
        assert result["healthy"] is False
        assert result["writable"] is True
        assert "recovered" in result["detail"].lower()

    def test_write_failure_reports_unwritable(self, tmp_path, monkeypatch):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective")

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom)
        result = manifest.check_health()
        assert result["healthy"] is False
        assert result["writable"] is False

    def test_is_durable_convenience_wrapper(self, tmp_path):
        assert is_durable(tmp_path) is True

    def test_is_durable_false_on_write_failure(self, tmp_path, monkeypatch):
        RunManifest(tmp_path).start_run("objective")

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom)
        assert is_durable(tmp_path) is False


# ---------------------------------------------------------------------------
# Visible warnings instead of silent swallowing (pipeline_orchestrator.py)
# ---------------------------------------------------------------------------


class TestVisibleWarnings:
    def test_manifest_start_run_failure_prints_a_warning(self, tmp_path, capsys, monkeypatch):
        from tournament_scheduler.cli.pipeline_orchestrator.manifest import _manifest_start_run

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom)
        _manifest_start_run(str(tmp_path), str(tmp_path / "input.xlsx"))

        out = capsys.readouterr().out
        assert "Kunne ikke" in out
        assert "disk full" in out

    def test_manifest_start_run_failure_writes_a_log_line(self, tmp_path, monkeypatch):
        from tournament_scheduler.cli.pipeline_orchestrator.manifest import _manifest_start_run

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom)
        _manifest_start_run(str(tmp_path), str(tmp_path / "input.xlsx"))

        log_path = tmp_path / "logs" / "manifest_warnings.log"
        assert log_path.exists()
        assert "disk full" in log_path.read_text(encoding="utf-8")

    def test_manifest_record_failure_is_visible(self, tmp_path, capsys, monkeypatch):
        from tournament_scheduler.cli.pipeline_orchestrator.manifest import _manifest_record

        RunManifest(tmp_path).start_run("objective")

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom)
        _manifest_record(str(tmp_path), "config", "ok", "fine")

        assert "Kunne ikke" in capsys.readouterr().out

    def test_manifest_finalize_failure_is_visible(self, tmp_path, capsys, monkeypatch):
        from tournament_scheduler.cli.pipeline_orchestrator.manifest import _manifest_finalize

        RunManifest(tmp_path).start_run("objective")

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom)
        _manifest_finalize(str(tmp_path), "ok")

        assert "Kunne ikke" in capsys.readouterr().out

    def test_raise_escalation_questions_failure_is_visible(self, tmp_path, capsys):
        from tournament_scheduler.cli.pipeline_orchestrator.operator_run import _raise_escalation_questions

        with patch(
            "tournament_scheduler.pipeline.run_manifest.RunManifest.read", side_effect=RuntimeError("boom")
        ):
            _raise_escalation_questions(str(tmp_path))

        assert "Kunne ikke" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Final outcome downgrade when persistence degraded
# ---------------------------------------------------------------------------


class TestOutcomeDowngradeOnDegradedPersistence:
    def test_ok_outcome_downgrades_to_warning_after_a_manifest_failure(self, tmp_path, monkeypatch):
        from tournament_scheduler.cli.pipeline_orchestrator.manifest import (
            _MANIFEST_DEGRADED_WORK_DIRS,
            _manifest_finalize,
            _manifest_start_run,
        )

        _MANIFEST_DEGRADED_WORK_DIRS.discard(str(Path(tmp_path).resolve()))
        RunManifest(tmp_path).start_run("objective")

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom)
        _manifest_start_run(str(tmp_path), str(tmp_path / "input.xlsx"))  # fails, marks degraded

        monkeypatch.undo()
        _manifest_finalize(str(tmp_path), "ok")

        assert RunManifest(tmp_path).read()["final_outcome"] == "warning"
        _MANIFEST_DEGRADED_WORK_DIRS.discard(str(Path(tmp_path).resolve()))

    def test_clean_run_stays_ok(self, tmp_path):
        from tournament_scheduler.cli.pipeline_orchestrator.manifest import _manifest_finalize

        RunManifest(tmp_path).start_run("objective")
        _manifest_finalize(str(tmp_path), "ok")
        assert RunManifest(tmp_path).read()["final_outcome"] == "ok"


# ---------------------------------------------------------------------------
# Approval-gated actions require durable persistence
# ---------------------------------------------------------------------------


class TestApprovalRequiresDurablePersistence:
    def _registry_with_destructive_action(self) -> ActionRegistry:
        registry = ActionRegistry()
        registry.register(
            OperatorAction(
                action_id="destructive_test_action",
                description="test",
                capability="test",
                risk_level=RiskLevel.DESTRUCTIVE.value,
                requires_approval=True,
            ),
            lambda **kwargs: CapabilityResult.ok("done", capability="test"),
        )
        return registry

    def test_approved_action_runs_when_manifest_is_durable(self, tmp_path):
        RunManifest(tmp_path).start_run("objective")
        registry = self._registry_with_destructive_action()
        action = registry.build("destructive_test_action", work_dir=str(tmp_path))
        result = registry.execute(action, approved=True)
        assert result.status == "ok"

    def test_approved_action_refuses_to_run_when_manifest_is_not_writable(self, tmp_path, monkeypatch):
        RunManifest(tmp_path).start_run("objective")

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom)

        registry = self._registry_with_destructive_action()
        action = registry.build("destructive_test_action", work_dir=str(tmp_path))
        with pytest.raises(PersistenceUnavailableError):
            registry.execute(action, approved=True)

    def test_unapproved_action_still_raises_approval_required_first(self, tmp_path, monkeypatch):
        # The approval check must fail before the durability check even runs.
        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom)

        registry = self._registry_with_destructive_action()
        action = registry.build("destructive_test_action", work_dir=str(tmp_path))
        with pytest.raises(ApprovalRequiredError):
            registry.execute(action, approved=False)

    def test_action_without_work_dir_argument_is_not_gated(self, tmp_path):
        # An approval-required action with no work_dir argument (shouldn't
        # normally happen for real registered actions, but must not crash).
        registry = ActionRegistry()
        registry.register(
            OperatorAction(
                action_id="no_work_dir_action",
                description="test",
                capability="test",
                risk_level=RiskLevel.EXTERNAL.value,
                requires_approval=True,
            ),
            lambda **kwargs: CapabilityResult.ok("done", capability="test"),
        )
        action = registry.build("no_work_dir_action")
        result = registry.execute(action, approved=True)
        assert result.status == "ok"


# ---------------------------------------------------------------------------
# CLI: rvv-miniputt operator health
# ---------------------------------------------------------------------------


class TestOperatorHealthCli:
    def test_healthy_workspace_returns_0(self, tmp_path, capsys):
        from tournament_scheduler.cli.rvv_cli import _cmd_operator_health

        RunManifest(tmp_path).start_run("objective")
        args = argparse.Namespace(work_dir=str(tmp_path), json=False)
        rc = _cmd_operator_health(args)
        assert rc == 0
        assert "sunn" in capsys.readouterr().out

    def test_corrupted_workspace_returns_1_and_shows_backup_path(self, tmp_path, capsys):
        from tournament_scheduler.cli.rvv_cli import _cmd_operator_health

        RunManifest(tmp_path).path.write_text("broken", encoding="utf-8")
        args = argparse.Namespace(work_dir=str(tmp_path), json=False)
        rc = _cmd_operator_health(args)
        assert rc == 1
        assert "gjenopprettet" in capsys.readouterr().out

    def test_json_output(self, tmp_path, capsys):
        from tournament_scheduler.cli.rvv_cli import _cmd_operator_health

        RunManifest(tmp_path).start_run("objective")
        args = argparse.Namespace(work_dir=str(tmp_path), json=True)
        rc = _cmd_operator_health(args)
        assert rc == 0
        printed = json.loads(capsys.readouterr().out)
        assert printed["healthy"] is True

    def test_dispatch_routes_health(self):
        from tournament_scheduler.cli.rvv_cli import _cmd_operator

        with patch("tournament_scheduler.cli.rvv_cli._cmd_operator_health", return_value=0) as mock_h:
            _cmd_operator(argparse.Namespace(operator_command="health"))
        mock_h.assert_called_once()

    def test_operator_health_parses(self):
        from tournament_scheduler.cli.args import build_parser

        args = build_parser().parse_args(["operator", "health", "--json"])
        assert args.operator_command == "health"
        assert args.json is True


# ---------------------------------------------------------------------------
# Backward compatibility with pre-#14 manifests
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_existing_manifest_without_manifest_recovery_key_reads_fine(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective")
        # Simulate a manifest written before issue #14 (no manifest_recovery key).
        raw = json.loads(manifest.path.read_text(encoding="utf-8"))
        raw.pop("manifest_recovery", None)
        manifest.path.write_text(json.dumps(raw), encoding="utf-8")

        data = manifest.read()
        assert data["manifest_recovery"] is None
        assert data["objective"] == "objective"

    def test_pending_questions_still_carry_forward_after_recovery_machinery_added(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective")
        question = Question(type="credentials", capability="scraping", summary="x")
        raise_question(str(tmp_path), question)

        manifest.start_run("second objective")
        assert len(manifest.read()["pending_questions"]) == 1
