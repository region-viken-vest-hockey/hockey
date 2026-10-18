"""Tests for Stage 2 structured progress (issue #363)."""

import time
from datetime import UTC, datetime
from unittest.mock import patch

from tournament_scheduler.pipeline import stage2_scraping
from tournament_scheduler.pipeline.stage2_progress import (
    STAGE2_PROGRESS_FILENAME,
    Stage2ProgressTracker,
    read_stage2_progress,
)
from tournament_scheduler.pipeline.stage2_scraping import SOURCE_ICAL, run
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus


def _source_by_name(snapshot, name):
    return next(s for s in snapshot["sources"] if s["name"] == name)


class TestStage2ProgressTracker:
    def test_new_tracker_reports_all_sources_pending(self, tmp_path):
        tracker = Stage2ProgressTracker(tmp_path, ["A", "B"])

        snapshot = read_stage2_progress(tmp_path)

        assert snapshot["total"] == 2
        assert snapshot["completed"] == 0
        assert snapshot["finished"] is False
        assert [s["state"] for s in snapshot["sources"]] == ["pending", "pending"]
        assert tracker.path == tmp_path / STAGE2_PROGRESS_FILENAME

    def test_sources_completing_at_different_times_update_counts(self, tmp_path):
        tracker = Stage2ProgressTracker(tmp_path, ["A", "B", "C"])

        tracker.mark_active("A")
        tracker.mark_active("B")
        tracker.mark_result("A", {"blocked": False, "event_count": 12})
        snapshot = read_stage2_progress(tmp_path)

        assert snapshot["completed"] == 1
        assert _source_by_name(snapshot, "A")["state"] == "completed"
        assert _source_by_name(snapshot, "A")["event_count"] == 12
        assert _source_by_name(snapshot, "B")["state"] == "active"
        assert _source_by_name(snapshot, "C")["state"] == "pending"

    def test_blocked_and_failed_sources_are_visible_immediately(self, tmp_path):
        tracker = Stage2ProgressTracker(tmp_path, ["Blocked", "Crashed"])

        tracker.mark_active("Blocked")
        tracker.mark_result("Blocked", {"blocked": True, "block_reason": "login required"})
        tracker.mark_active("Crashed")
        tracker.mark_failed("Crashed", "timeout")
        snapshot = read_stage2_progress(tmp_path)

        assert snapshot["completed"] == 2
        assert _source_by_name(snapshot, "Blocked")["state"] == "blocked"
        assert _source_by_name(snapshot, "Blocked")["block_reason"] == "login required"
        assert _source_by_name(snapshot, "Crashed")["state"] == "failed"
        assert _source_by_name(snapshot, "Crashed")["block_reason"] == "timeout"

    def test_cached_source_counts_as_completed_without_active_phase(self, tmp_path):
        tracker = Stage2ProgressTracker(tmp_path, ["Cached"])

        tracker.mark_cached("Cached", event_count=7)
        snapshot = read_stage2_progress(tmp_path)

        assert snapshot["completed"] == 1
        assert _source_by_name(snapshot, "Cached")["state"] == "cached"
        assert _source_by_name(snapshot, "Cached")["event_count"] == 7

    def test_repeated_polls_of_active_source_show_advancing_elapsed(self, tmp_path):
        """A poller reading only the persisted file can tell a slow source from a stuck one."""
        tracker = Stage2ProgressTracker(tmp_path, ["Slow"])
        tracker.mark_active("Slow")
        now = datetime.now(tz=UTC)

        first = _source_by_name(read_stage2_progress(tmp_path), "Slow")
        time.sleep(0.05)
        second = _source_by_name(read_stage2_progress(tmp_path), "Slow")

        assert first["state"] == second["state"] == "active"
        assert first["started_at"] == second["started_at"]
        started = datetime.fromisoformat(first["started_at"])
        first_elapsed = (now - started).total_seconds()
        second_elapsed = (datetime.now(tz=UTC) - started).total_seconds()
        assert second_elapsed > first_elapsed >= 0

    def test_finished_source_drops_started_at_and_keeps_duration(self, tmp_path):
        tracker = Stage2ProgressTracker(tmp_path, ["A"])
        tracker.mark_active("A")
        tracker.mark_result("A", {"blocked": False, "event_count": 2})

        source = _source_by_name(read_stage2_progress(tmp_path), "A")

        assert "started_at" not in source
        assert source["elapsed_s"] is not None

    def test_finish_marks_snapshot_finished(self, tmp_path):
        tracker = Stage2ProgressTracker(tmp_path, ["A"])
        tracker.mark_result("A", {"blocked": False, "event_count": 1})

        tracker.finish()

        assert read_stage2_progress(tmp_path)["finished"] is True

    def test_missing_snapshot_reads_as_none(self, tmp_path):
        assert read_stage2_progress(tmp_path) is None

    def test_persistence_failure_does_not_raise(self, tmp_path):
        missing_dir = tmp_path / "does-not-exist"
        tracker = Stage2ProgressTracker(missing_dir, ["A"])

        tracker.mark_result("A", {"blocked": False, "event_count": 0})

        assert tracker.snapshot()["completed"] == 1


def _config(sources):
    return {
        "start_date": "2025-09-01",
        "end_date": "2025-12-01",
        "teams": [{"club": "Kongsberg", "label": "Kongsberg U10", "age_group": "U10"}],
        "sources": sources,
    }


class TestRunWritesStage2Progress:
    def test_progress_is_observable_mid_run_and_finalised_after(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        sources = [
            {"name": "First", "type": SOURCE_ICAL, "url": "https://first.example.com"},
            {"name": "Second", "type": SOURCE_ICAL, "url": "https://second.example.com"},
        ]
        observed_mid_run = {}

        def fake_scrape(source_cfg, *, start_date, end_date, calendar_cache=None):
            name = source_cfg["name"]
            if name == "Second":
                observed_mid_run["snapshot"] = read_stage2_progress(state.work_dir)
            return {"name": name, "url": "", "blocked": False, "event_count": 3, "events": []}

        with patch.object(stage2_scraping, "_scrape_source", side_effect=fake_scrape):
            run(
                _config(sources), state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False, max_workers=1,
            )

        mid = observed_mid_run["snapshot"]
        assert _source_by_name(mid, "First")["state"] == "completed"
        assert _source_by_name(mid, "Second")["state"] == "active"
        assert mid["completed"] == 1

        final = read_stage2_progress(state.work_dir)
        assert final["finished"] is True
        assert final["completed"] == 2
        assert _source_by_name(final, "Second")["event_count"] == 3
        assert state.is_done(StageName.SCRAPING)

    def test_crashing_source_is_recorded_as_failed_without_stopping_run(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        sources = [
            {"name": "Good", "type": SOURCE_ICAL, "url": "https://good.example.com"},
            {"name": "Crashy", "type": SOURCE_ICAL, "url": "https://crash.example.com"},
        ]

        def fake_scrape(source_cfg, *, start_date, end_date, calendar_cache=None):
            if source_cfg["name"] == "Crashy":
                raise RuntimeError("boom")
            return {"name": source_cfg["name"], "url": "", "blocked": False, "event_count": 1, "events": []}

        with patch.object(stage2_scraping, "_scrape_source", side_effect=fake_scrape):
            result = run(
                _config(sources), state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False, allow_missing_sources=True,
            )

        final = read_stage2_progress(state.work_dir)
        assert _source_by_name(final, "Crashy")["state"] == "failed"
        assert _source_by_name(final, "Crashy")["block_reason"] == "boom"
        assert _source_by_name(final, "Good")["state"] == "completed"
        assert "Crashy" in result["blocked"]
        assert state.read_envelope(StageName.SCRAPING)["status"] == StageStatus.DONE.value
