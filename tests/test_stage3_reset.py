"""Regression tests for the canonical Stage 3 engineering reset capability."""

from __future__ import annotations

import argparse
import json

from tournament_scheduler.application.stage3_session_store import Stage3SessionStore
from tournament_scheduler.cli.pipeline_orchestrator.stage3_reset_command import _cmd_stage3_reset
from tournament_scheduler.pipeline.evidence_bundle import stage3_attempt_log_path
from tournament_scheduler.pipeline.run_manifest import RunManifest
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus


def _seed_upstream(tmp_path) -> tuple[PipelineState, str, str]:
    state = PipelineState(tmp_path)
    RunManifest(tmp_path).start_run(
        "production planning",
        run_id="run-before-reset",
        input_fingerprint={"sha256": "input-fingerprint"},
    )
    state.write_stage(
        StageName.CONFIG,
        {"input_path": "input.xlsx", "start_date": "2026-10-09", "end_date": "2027-03-28"},
        status=StageStatus.DONE,
    )
    state.write_stage(
        StageName.SCRAPING,
        {"sources": [{"name": "Jar", "events": 10}], "blocked": []},
        status=StageStatus.DONE,
    )
    config_before = state.checkpoint_path(StageName.CONFIG).read_text(encoding="utf-8")
    scraping_before = state.checkpoint_path(StageName.SCRAPING).read_text(encoding="utf-8")
    return state, config_before, scraping_before


def test_reset_preserves_stage1_stage2_and_starts_clean_stage3_lineage(tmp_path, capsys):
    state, config_before, scraping_before = _seed_upstream(tmp_path)
    state.write_stage(
        StageName.PLANNING,
        {"plan": {"tournaments": [{"id": "old-stage3"}]}},
        status=StageStatus.DONE,
    )
    state.write_stage(
        StageName.EXPORT,
        {"output_files": {"html": "old.html"}},
        status=StageStatus.DONE,
    )

    store = Stage3SessionStore(tmp_path)
    store.bind_candidate(
        {"plan": {"tournaments": [{"id": "old-stage3"}]}},
        run_id="run-before-reset",
        source="baseline",
    )
    stage3_attempt_log_path(tmp_path).write_text("[]", encoding="utf-8")
    (tmp_path / "stage3_cpsat_cache.json").write_text(
        json.dumps({"run_id": "run-before-reset", "entries": {}}), encoding="utf-8"
    )

    rc = _cmd_stage3_reset(
        argparse.Namespace(work_dir=str(tmp_path), json=True)
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["old_run_id"] == "run-before-reset"
    assert payload["new_run_id"] and payload["new_run_id"] != "run-before-reset"
    assert payload["resume_from"] == 3
    assert "--resume-from 3" in payload["next_command"]

    # Frozen upstream facts are byte-for-byte preserved.
    assert state.checkpoint_path(StageName.CONFIG).read_text(encoding="utf-8") == config_before
    assert state.checkpoint_path(StageName.SCRAPING).read_text(encoding="utf-8") == scraping_before
    assert state.status(StageName.CONFIG) == StageStatus.DONE
    assert state.status(StageName.SCRAPING) == StageStatus.DONE

    # Candidate/export lineage and Stage 3 transient evidence are gone.
    assert not state.checkpoint_path(StageName.PLANNING).exists()
    assert not state.checkpoint_path(StageName.EXPORT).exists()
    assert not stage3_attempt_log_path(tmp_path).exists()
    assert not (tmp_path / "stage3_cpsat_cache.json").exists()

    fresh_session = store.load(expected_run_id=payload["new_run_id"])
    assert fresh_session.status == "new"
    assert fresh_session.candidate is None
    assert fresh_session.pending_decision is None

    manifest = RunManifest(tmp_path).read()
    assert manifest["run_id"] == payload["new_run_id"]
    assert manifest["objective"] == "production planning"
    assert manifest["input_fingerprint"] == {"sha256": "input-fingerprint"}
    assert manifest["final_outcome"] == "in_progress"


def test_reset_refuses_when_stage2_is_not_complete(tmp_path, capsys):
    state = PipelineState(tmp_path)
    RunManifest(tmp_path).start_run("production planning", run_id="run-before-reset")
    state.write_stage(
        StageName.CONFIG,
        {"input_path": "input.xlsx", "start_date": "2026-10-09", "end_date": "2027-03-28"},
        status=StageStatus.DONE,
    )
    state.write_stage(
        StageName.PLANNING,
        {"plan": {"tournaments": [{"id": "must-survive-rejected-reset"}]}},
        status=StageStatus.DONE,
    )

    rc = _cmd_stage3_reset(
        argparse.Namespace(work_dir=str(tmp_path), json=True)
    )
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "scraping" in payload["error"]

    # Validation happens before destructive work.
    assert state.checkpoint_path(StageName.PLANNING).exists()
    assert RunManifest(tmp_path).read()["run_id"] == "run-before-reset"
