"""Tests for tournament_scheduler.pipeline.state (PipelineState)."""

import hashlib
from pathlib import Path

import pytest

from tournament_scheduler.pipeline.state import (
    PipelineState,
    StageName,
    StageStatus,
)


@pytest.fixture()
def tmp_state(tmp_path):
    return PipelineState(tmp_path / "pipeline")


class TestPipelineState:
    def test_creates_work_dir(self, tmp_path):
        work_dir = tmp_path / "newdir"
        assert not work_dir.exists()
        PipelineState(work_dir)
        assert work_dir.exists()

    def test_write_and_read_stage(self, tmp_state):
        data = {"teams": ["A", "B"], "start_date": "2025-09-01"}
        tmp_state.write_stage(StageName.CONFIG, data)
        assert tmp_state.read_stage(StageName.CONFIG) == data

    def test_initial_status_is_pending(self, tmp_state):
        assert tmp_state.status(StageName.CONFIG) == StageStatus.PENDING

    def test_mark_done(self, tmp_state):
        tmp_state.write_stage(StageName.CONFIG, {})
        tmp_state.mark_done(StageName.CONFIG)
        assert tmp_state.is_done(StageName.CONFIG)

    def test_mark_failed(self, tmp_state):
        tmp_state.write_stage(StageName.CONFIG, {})
        tmp_state.mark_failed(StageName.CONFIG, error="something broke")
        assert tmp_state.is_failed(StageName.CONFIG)

    def test_failed_stage_invalidates_downstream_checkpoints(self, tmp_state):
        tmp_state.write_stage(StageName.SCRAPING, {"sources": ["a"]}, status=StageStatus.DONE)
        tmp_state.write_stage(StageName.PLANNING, {"plan": {"id": 1}}, status=StageStatus.DONE)
        tmp_state.write_stage(StageName.EXPORT, {"output_files": {"excel": "x.xlsx"}}, status=StageStatus.DONE)

        tmp_state.write_stage(StageName.SCRAPING, {"sources": ["new"]}, status=StageStatus.FAILED)
        tmp_state.mark_failed(StageName.SCRAPING, error="stage 2 failed")

        assert tmp_state.is_failed(StageName.SCRAPING)
        assert tmp_state.is_failed(StageName.PLANNING)
        assert tmp_state.is_failed(StageName.EXPORT)
        assert tmp_state.is_stale(StageName.PLANNING)
        assert tmp_state.is_stale(StageName.EXPORT)
        assert tmp_state.read_envelope(StageName.PLANNING)["stale_from"] == StageName.SCRAPING.value
        assert tmp_state.read_envelope(StageName.EXPORT)["stale_reason"]

    def test_upstream_config_change_invalidates_downstream_checkpoints(self, tmp_state):
        tmp_state.write_stage(StageName.CONFIG, {"teams": ["A"]}, status=StageStatus.DONE)
        tmp_state.write_stage(StageName.SCRAPING, {"sources": ["a"]}, status=StageStatus.DONE)
        tmp_state.write_stage(StageName.PLANNING, {"plan": {"id": 1}}, status=StageStatus.DONE)

        tmp_state.write_stage(StageName.CONFIG, {"teams": ["B"]}, status=StageStatus.DONE)

        assert tmp_state.is_done(StageName.CONFIG)
        assert tmp_state.is_failed(StageName.SCRAPING)
        assert tmp_state.is_failed(StageName.PLANNING)
        assert tmp_state.is_stale(StageName.SCRAPING)
        assert tmp_state.is_stale(StageName.PLANNING)
        assert tmp_state.read_envelope(StageName.SCRAPING)["stale_from"] == StageName.CONFIG.value

    def test_input_workbook_fingerprint_change_invalidates_config_and_downstream(self, tmp_path):
        input_file = tmp_path / "input.xlsx"
        input_file.write_bytes(b"old workbook bytes")
        old_sha = hashlib.sha256(b"old workbook bytes").hexdigest()
        state = PipelineState(tmp_path / "pipeline")
        state.write_stage(
            StageName.CONFIG,
            {
                "input_path": str(input_file),
                "input_fingerprint": {
                    "algorithm": "sha256",
                    "path": str(input_file),
                    "sha256": old_sha,
                },
                "effective_config_fingerprint": {
                    "algorithm": "sha256",
                    "sha256": "old-effective",
                },
            },
            status=StageStatus.DONE,
        )
        state.write_stage(StageName.SCRAPING, {"sources": ["a"]}, status=StageStatus.DONE)
        state.write_stage(StageName.PLANNING, {"plan": {"id": 1}}, status=StageStatus.DONE)
        state.write_stage(StageName.EXPORT, {"output_files": {"excel": "plan.xlsx"}}, status=StageStatus.DONE)

        input_file.write_bytes(b"new workbook bytes")

        assert state.invalidate_if_config_fingerprint_changed() is True
        assert state.is_failed(StageName.CONFIG)
        assert state.is_stale(StageName.CONFIG)
        assert state.is_failed(StageName.SCRAPING)
        assert state.is_stale(StageName.SCRAPING)
        assert state.is_failed(StageName.PLANNING)
        assert state.is_stale(StageName.PLANNING)
        assert state.is_failed(StageName.EXPORT)
        assert state.is_stale(StageName.EXPORT)
        assert state.read_envelope(StageName.SCRAPING)["stale_reason"].startswith("Input workbook changed")

    def test_read_envelope_contains_status(self, tmp_state):
        tmp_state.write_stage(StageName.SCRAPING, {"x": 1}, status=StageStatus.RUNNING)
        env = tmp_state.read_envelope(StageName.SCRAPING)
        assert env["status"] == StageStatus.RUNNING.value
        assert env["stage"] == StageName.SCRAPING.value

    def test_checkpoint_filename(self):
        assert StageName.CONFIG.filename == "stage1_config.json"
        assert StageName.SCRAPING.filename == "stage2_scraping.json"
        assert StageName.PLANNING.filename == "stage3_planning.json"
        assert StageName.EXPORT.filename == "stage4_export.json"

    def test_resolve_resume_from_aliases(self, tmp_state):
        assert tmp_state.resolve_resume_from("1") == StageName.CONFIG
        assert tmp_state.resolve_resume_from("scraping") == StageName.SCRAPING
        assert tmp_state.resolve_resume_from("plan") == StageName.PLANNING
        assert tmp_state.resolve_resume_from("export") == StageName.EXPORT

    def test_resolve_resume_from_invalid(self, tmp_state):
        with pytest.raises(ValueError, match="Unknown stage"):
            tmp_state.resolve_resume_from("unknown")

    def test_stages_to_run_full(self, tmp_state):
        stages = tmp_state.stages_to_run()
        assert stages == list(StageName)

    def test_stages_to_run_resume_skips_done(self, tmp_state):
        tmp_state.write_stage(StageName.CONFIG, {})
        tmp_state.mark_done(StageName.CONFIG)
        stages = tmp_state.stages_to_run(StageName.SCRAPING)
        assert StageName.CONFIG not in stages
        assert StageName.SCRAPING in stages

    def test_stages_to_run_blocks_if_prev_not_done(self, tmp_state):
        # CONFIG not done, cannot resume from SCRAPING
        with pytest.raises(ValueError, match="not done yet"):
            tmp_state.stages_to_run(StageName.SCRAPING)

    def test_summary(self, tmp_state):
        summary = tmp_state.summary()
        assert set(summary.keys()) == set(StageName)
        assert all(v == StageStatus.PENDING for v in summary.values())


class TestWriteEnvelopeErrorHandling:
    """_write_envelope centralises file-write error handling for all five write methods."""

    def _patch_write_text(self, monkeypatch, exc):
        """Patch Path.write_text to raise *exc*."""
        monkeypatch.setattr(Path, "write_text", lambda *args, **kwargs: (_ for _ in ()).throw(exc))

    # ------------------------------------------------------------------ #
    # OSError injection
    # ------------------------------------------------------------------ #

    def test_write_stage_oserror_raises_runtime_error(self, tmp_path, monkeypatch):
        state = PipelineState(tmp_path / "pipeline")
        monkeypatch.setattr(Path, "write_text", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(RuntimeError, match="Failed to write checkpoint"):
            state.write_stage(StageName.CONFIG, {})

    def test_write_judgment_oserror_raises_runtime_error(self, tmp_path, monkeypatch):
        state = PipelineState(tmp_path / "pipeline")
        # write_judgment reads the envelope first; pre-populate so read succeeds
        state.write_stage(StageName.CONFIG, {})
        monkeypatch.setattr(Path, "write_text", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(RuntimeError, match="Failed to write checkpoint"):
            state.write_judgment(StageName.CONFIG, verdict="PROCEED")

    def test_write_approval_oserror_raises_runtime_error(self, tmp_path, monkeypatch):
        state = PipelineState(tmp_path / "pipeline")
        state.write_stage(StageName.CONFIG, {})
        monkeypatch.setattr(Path, "write_text", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(RuntimeError, match="Failed to write checkpoint"):
            state.write_approval(StageName.CONFIG, decision="GO")

    def test_set_status_oserror_raises_runtime_error(self, tmp_path, monkeypatch):
        state = PipelineState(tmp_path / "pipeline")
        state.write_stage(StageName.CONFIG, {})
        monkeypatch.setattr(Path, "write_text", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(RuntimeError, match="Failed to write checkpoint"):
            state.mark_done(StageName.CONFIG)

    def test_invalidate_downstream_oserror_raises_runtime_error(self, tmp_path, monkeypatch):
        state = PipelineState(tmp_path / "pipeline")
        # Create checkpoints for config and scraping so _invalidate_downstream finds them
        state.write_stage(StageName.CONFIG, {})
        state.write_stage(StageName.SCRAPING, {})
        # Allow the first write (config mark_done) but fail the downstream invalidation write
        call_count = {"n": 0}
        original = Path.write_text

        def _fail_on_second(self_, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] > 1:
                raise OSError("disk full")
            return original(self_, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", _fail_on_second)
        with pytest.raises(RuntimeError, match="Failed to write checkpoint"):
            state.mark_done(StageName.CONFIG)

    # ------------------------------------------------------------------ #
    # ValueError injection (covers json.JSONDecodeError / bad data)
    # ------------------------------------------------------------------ #

    def test_write_stage_valueerror_raises_runtime_error(self, tmp_path, monkeypatch):
        state = PipelineState(tmp_path / "pipeline")
        monkeypatch.setattr(Path, "write_text", lambda *a, **kw: (_ for _ in ()).throw(ValueError("bad json")))
        with pytest.raises(RuntimeError, match="Failed to write checkpoint"):
            state.write_stage(StageName.CONFIG, {})

    # ------------------------------------------------------------------ #
    # Error message contains the file path
    # ------------------------------------------------------------------ #

    def test_runtime_error_message_contains_path(self, tmp_path, monkeypatch):
        state = PipelineState(tmp_path / "pipeline")
        monkeypatch.setattr(Path, "write_text", lambda *a, **kw: (_ for _ in ()).throw(OSError("no space")))
        with pytest.raises(RuntimeError) as exc_info:
            state.write_stage(StageName.CONFIG, {})
        assert "stage1_config.json" in str(exc_info.value)
