"""Tests for tournament_scheduler.pipeline.run_manifest (RunManifest)."""

import json

import pytest

from tournament_scheduler.pipeline.capability_result import CapabilityResult
from tournament_scheduler.pipeline.run_manifest import (
    RUN_MANIFEST_SCHEMA_VERSION,
    RunManifest,
    timing_summary,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus


@pytest.fixture()
def manifest(tmp_path):
    return RunManifest(tmp_path / "pipeline")


class TestRunManifestLifecycle:
    def test_creates_work_dir(self, tmp_path):
        work_dir = tmp_path / "newdir"
        assert not work_dir.exists()
        RunManifest(work_dir)
        assert work_dir.exists()

    def test_start_run_writes_expected_shape(self, manifest):
        data = manifest.start_run("Produce the best season plan")
        assert data["schema_version"] == RUN_MANIFEST_SCHEMA_VERSION
        assert data["objective"] == "Produce the best season plan"
        assert data["final_outcome"] == "in_progress"
        assert data["current_capability"] is None
        assert data["active_capability"] is None
        assert data["last_completed_capability"] is None
        assert data["next_recommended_capability"] == "config"
        assert data["capabilities"] == []
        assert data["run_id"]
        assert data["started_at"] == data["updated_at"]
        assert manifest.exists()

    def test_start_run_generates_unique_run_ids(self, manifest):
        first = manifest.start_run("objective A")
        second = manifest.start_run("objective B")
        assert first["run_id"] != second["run_id"]

    def test_records_input_fingerprint(self, manifest):
        data = manifest.start_run("objective", input_fingerprint={"path": "input.xlsx", "sha256": "abc123"})
        assert data["input_fingerprint"] == {"path": "input.xlsx", "sha256": "abc123"}

    def test_set_current_capability(self, manifest):
        manifest.start_run("objective")
        manifest.set_current_capability("scraping")
        data = manifest.read()
        assert data["current_capability"] == "scraping"
        assert data["active_capability"] == "scraping"

    def test_active_capability_survives_interruption(self, manifest):
        """An interrupted run (no finalize call) can identify the capability
        that was active when execution stopped (issue #15)."""
        manifest.start_run("objective")
        manifest.record_capability(CapabilityResult.ok("config ready", capability="config"))
        manifest.set_current_capability("scraping")
        # Simulate a crash: no record_capability("scraping") call, no finalize.
        data = manifest.read()
        assert data["active_capability"] == "scraping"
        assert data["last_completed_capability"] == "config"
        assert data["final_outcome"] == "in_progress"


class TestRunManifestCapabilityOutcomes:
    @pytest.mark.parametrize("status", ["ok", "warning", "blocked", "failed"])
    def test_record_capability_for_each_status(self, manifest, status):
        manifest.start_run("objective")
        result = CapabilityResult(status=status, summary=f"stage returned {status}", capability="scraping")
        entry = manifest.record_capability(result)

        assert entry["status"] == status
        assert entry["recorded_at"]

        data = manifest.read()
        assert len(data["capabilities"]) == 1
        assert data["capabilities"][0]["status"] == status
        assert data["current_capability"] == "scraping"
        assert data["last_completed_capability"] == "scraping"
        # Recording a result always ends the "in progress" window for that
        # capability, whatever the outcome (issue #15).
        assert data["active_capability"] is None
        if status in ("blocked", "failed"):
            assert data["next_recommended_capability"] == "scraping"
        else:
            assert data["next_recommended_capability"] == "planning"

    def test_record_capability_appends_history(self, manifest):
        manifest.start_run("objective")
        manifest.record_capability(CapabilityResult.ok("config ready", capability="config"))
        manifest.record_capability(CapabilityResult.warning("1 source blocked", capability="scraping"))
        data = manifest.read()
        assert [c["capability"] for c in data["capabilities"]] == ["config", "scraping"]
        assert data["last_completed_capability"] == "scraping"
        assert data["next_recommended_capability"] == "planning"

    def test_next_recommended_capability_follows_full_sequence(self, manifest):
        manifest.start_run("objective")
        assert manifest.read()["next_recommended_capability"] == "config"
        for capability, following in (
            ("config", "scraping"),
            ("scraping", "planning"),
            ("planning", "export"),
        ):
            manifest.record_capability(CapabilityResult.ok("done", capability=capability))
            assert manifest.read()["next_recommended_capability"] == following
        manifest.record_capability(CapabilityResult.ok("done", capability="export"))
        assert manifest.read()["next_recommended_capability"] is None

    @pytest.mark.parametrize("outcome", ["ok", "warning", "blocked", "failed"])
    def test_finalize_sets_terminal_outcome(self, manifest, outcome):
        manifest.start_run("objective")
        manifest.set_current_capability("export")
        manifest.finalize(outcome)
        data = manifest.read()
        assert data["final_outcome"] == outcome
        assert data["ended_at"] is not None
        # A finalized run always has active_capability: null (issue #15),
        # even when finalize is called directly after an abort without an
        # intervening record_capability call.
        assert data["active_capability"] is None

    def test_finalize_rejects_in_progress(self, manifest):
        manifest.start_run("objective")
        with pytest.raises(ValueError):
            manifest.finalize("in_progress")

    def test_finalize_rejects_unknown_outcome(self, manifest):
        manifest.start_run("objective")
        with pytest.raises(ValueError):
            manifest.finalize("not-a-real-outcome")


class TestRunManifestJsonOnDisk:
    def test_manifest_file_is_valid_json(self, manifest):
        manifest.start_run("objective")
        manifest.record_capability(CapabilityResult.ok("done", capability="config"))
        raw = json.loads(manifest.path.read_text(encoding="utf-8"))
        assert raw["objective"] == "objective"


class TestRunManifestBackwardCompatibility:
    def test_read_with_no_manifest_synthesizes_from_legacy_checkpoints(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        state = PipelineState(work_dir)
        state.write_stage(StageName.CONFIG, {"teams": ["A", "B"]}, status=StageStatus.DONE)
        state.write_stage(StageName.SCRAPING, {"sources": []}, status=StageStatus.DONE)

        manifest = RunManifest(work_dir)
        assert not manifest.exists()
        data = manifest.read()

        assert data["schema_version"] == RUN_MANIFEST_SCHEMA_VERSION
        assert data["synthesized_from_legacy_checkpoints"] is True
        assert data["run_id"] == "legacy"
        assert [c["capability"] for c in data["capabilities"]] == ["config", "scraping"]
        assert all(c["status"] == "ok" for c in data["capabilities"])
        assert data["current_capability"] == "scraping"
        assert data["last_completed_capability"] == "scraping"
        assert data["active_capability"] is None
        assert data["next_recommended_capability"] == "planning"

    def test_synthesized_manifest_reflects_failed_stage(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        state = PipelineState(work_dir)
        state.write_stage(StageName.CONFIG, {"teams": ["A"]}, status=StageStatus.DONE)
        state.write_stage(StageName.SCRAPING, {"sources": []}, status=StageStatus.FAILED)
        state.mark_failed(StageName.SCRAPING, error="network error")

        data = RunManifest(work_dir).read()
        capability_by_name = {c["capability"]: c for c in data["capabilities"]}
        assert capability_by_name["scraping"]["status"] == "failed"
        assert data["final_outcome"] == "failed"

    def test_read_with_no_checkpoints_at_all(self, tmp_path):
        data = RunManifest(tmp_path / "pipeline").read()
        assert data["capabilities"] == []
        assert data["final_outcome"] == "in_progress"

    def test_read_with_corrupt_manifest_file_falls_back(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        manifest = RunManifest(work_dir)
        manifest.path.write_text("not valid json{{{", encoding="utf-8")
        data = manifest.read()
        assert data["synthesized_from_legacy_checkpoints"] is True

    def test_legacy_on_disk_manifest_backfills_new_fields_in_progress(self, tmp_path):
        """A manifest written before issue #15 (no active_capability etc.)
        that is still in progress backfills active_capability from the old
        current_capability field."""
        work_dir = tmp_path / "pipeline"
        manifest = RunManifest(work_dir)
        manifest.path.write_text(
            json.dumps(
                {
                    "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
                    "run_id": "old-run",
                    "objective": "objective",
                    "current_capability": "scraping",
                    "input_fingerprint": {},
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "ended_at": None,
                    "final_outcome": "in_progress",
                    "capabilities": [{"capability": "config", "status": "ok"}],
                    "pending_questions": [],
                    "action_log": [],
                }
            ),
            encoding="utf-8",
        )
        data = manifest.read()
        assert data["active_capability"] == "scraping"
        assert data["last_completed_capability"] == "config"
        assert data["next_recommended_capability"] == "scraping"

    def test_legacy_on_disk_manifest_clears_stale_active_capability_once_finalized(self, tmp_path):
        """This is the exact issue #15 bug, applied retroactively: a manifest
        written before the fix, whose current_capability was never cleared
        by finalize(), must not report an active capability once read again."""
        work_dir = tmp_path / "pipeline"
        manifest = RunManifest(work_dir)
        manifest.path.write_text(
            json.dumps(
                {
                    "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
                    "run_id": "old-run",
                    "objective": "objective",
                    "current_capability": "export",
                    "input_fingerprint": {},
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "ended_at": "2026-01-01T00:05:00+00:00",
                    "final_outcome": "ok",
                    "capabilities": [{"capability": "export", "status": "ok"}],
                    "pending_questions": [],
                    "action_log": [],
                }
            ),
            encoding="utf-8",
        )
        data = manifest.read()
        assert data["active_capability"] is None
        assert data["last_completed_capability"] == "export"
        assert data["next_recommended_capability"] is None


class TestRunManifestTiming:
    """issue #310: end-to-end timing instrumentation and CP-SAT invocation
    telemetry, so a production-sized run's time is diagnosable from the
    manifest alone."""

    def test_record_timing_accumulates_additively(self, manifest):
        manifest.start_run("objective")
        manifest.record_timing("stage3_cp_sat_seconds", 2.5)
        manifest.record_timing("stage3_cp_sat_seconds", 1.5)
        data = manifest.read()
        assert data["timing"]["stage3_cp_sat_seconds"] == pytest.approx(4.0)

    def test_record_timing_keeps_other_keys_independent(self, manifest):
        manifest.start_run("objective")
        manifest.record_timing("stage1_seconds", 3.0)
        manifest.record_timing("stage3_baseline_seconds", 5.0)
        data = manifest.read()
        assert data["timing"]["stage1_seconds"] == pytest.approx(3.0)
        assert data["timing"]["stage3_baseline_seconds"] == pytest.approx(5.0)

    def test_record_cp_sat_invocation_appends_and_counts(self, manifest):
        manifest.start_run("objective")
        manifest.record_cp_sat_invocation({"source": "automatic_shadow", "cache_hit": False, "runtime_seconds": 1.2})
        manifest.record_cp_sat_invocation({"source": "automatic_shadow", "cache_hit": True, "runtime_seconds": 0.0})
        data = manifest.read()
        assert len(data["cp_sat_invocations"]) == 2
        assert data["cp_sat_invocations"][0]["cache_hit"] is False
        assert data["cp_sat_invocations"][1]["cache_hit"] is True
        assert "recorded_at" in data["cp_sat_invocations"][0]

    def test_timing_summary_derives_totals_without_separate_accumulation(self, manifest):
        manifest.start_run("objective")
        manifest.record_timing("stage1_seconds", 1.0)
        manifest.record_timing("stage2_seconds", 2.0)
        manifest.record_timing("stage3_baseline_seconds", 3.0)
        manifest.record_timing("stage3_cp_sat_seconds", 4.0)
        manifest.record_timing("stage4_export_seconds", 5.0)
        data = manifest.read()
        summary = timing_summary(data)
        assert summary["stage3_total_seconds"] == pytest.approx(7.0)
        assert summary["run_total_seconds"] == pytest.approx(15.0)

    def test_timing_summary_handles_missing_timing_dict(self, manifest):
        manifest.start_run("objective")
        data = manifest.read()
        summary = timing_summary(data)
        assert summary == {"stage3_total_seconds": 0.0, "run_total_seconds": 0.0}
