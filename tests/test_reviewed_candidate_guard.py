"""A plain full-pipeline run must not silently invalidate a reviewed candidate.

After Stage 4 exports a hard-valid, unpromoted candidate and the semantic audit
has reviewed it, the normal improvement path is ``stage3 refine``. A plain run
that restarts/revalidates Stage 1 would clear the Stage 3 session and destroy
that exact candidate. The repository owns the lifecycle predicate; the CLI
refuses by default and only proceeds with an explicit ``--new-full-run``
opt-in.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from tournament_scheduler.application.candidate_refinement import (
    reviewed_unpromoted_candidate,
)
from tournament_scheduler.application.stage3_session_store import finalize_stage3_plan
from tournament_scheduler.cli.pipeline_orchestrator.new_run_guard import guard_new_full_run
from tournament_scheduler.pipeline.export_lifecycle import (
    SUPERSEDED_STATUS,
    write_draft_manifest,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus

PLAN = {"plan": {"tournaments": [{"id": "t1", "date": "2026-10-05"}]}}


def _reviewed_candidate(tmp_path, *, export_fingerprint: str = "export-fp-1"):
    state = PipelineState(str(tmp_path))
    state.write_stage(StageName.PLANNING, PLAN, status=StageStatus.DONE)
    export_dir = Path(tmp_path) / "export" / "2026-09-18T1200"
    export_dir.mkdir(parents=True)
    write_draft_manifest(
        export_dir,
        export_id=export_dir.name,
        generated_at="2026-09-18T12:00:00",
        export_fingerprint=export_fingerprint,
        source_run_id="run-1",
    )
    state.write_stage(
        StageName.EXPORT,
        {"export_dir": str(export_dir), "export_fingerprint": export_fingerprint},
        status=StageStatus.DONE,
    )
    session = finalize_stage3_plan(
        tmp_path, PLAN, action_id="keep_baseline", rationale="reviewed export", run_id="run-1"
    )
    return state, export_dir, session


def _args(tmp_path, *, new_full_run: bool = False):
    return SimpleNamespace(work_dir=str(tmp_path), new_full_run=new_full_run)


def test_predicate_describes_reviewed_unpromoted_candidate(tmp_path):
    _state, export_dir, session = _reviewed_candidate(tmp_path)
    reviewed = reviewed_unpromoted_candidate(tmp_path, season_root=Path(tmp_path) / "season")
    assert reviewed is not None
    assert reviewed["candidate_fingerprint"] == session.finalized_fingerprint
    assert reviewed["export_dir"] == str(export_dir)
    assert reviewed["export_id"] == export_dir.name
    assert reviewed["published"] is False


def test_predicate_is_clear_once_candidate_is_promoted(tmp_path):
    _state, _export_dir, session = _reviewed_candidate(tmp_path)
    season_dir = Path(tmp_path) / "season" / "2026-2027"
    season_dir.mkdir(parents=True)
    (season_dir / "schedule.json").write_text(
        json.dumps(
            {
                "promoted_from": {
                    "stage4_export_fingerprint": "export-fp-1",
                    "stage3_fingerprint": session.finalized_fingerprint,
                }
            }
        ),
        encoding="utf-8",
    )
    assert reviewed_unpromoted_candidate(tmp_path, season_root=Path(tmp_path) / "season") is None


def test_predicate_is_clear_when_export_is_superseded(tmp_path):
    _state, export_dir, _session = _reviewed_candidate(tmp_path)
    from tournament_scheduler.pipeline.export_lifecycle import read_export_manifest

    manifest = read_export_manifest(export_dir)
    manifest["lifecycle_status"] = SUPERSEDED_STATUS
    (export_dir / "export_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert reviewed_unpromoted_candidate(tmp_path, season_root=Path(tmp_path) / "season") is None


def test_guard_refuses_plain_new_run_and_names_the_safe_paths(tmp_path, capsys):
    _reviewed_candidate(tmp_path)
    assert guard_new_full_run(_args(tmp_path)) is True
    out = capsys.readouterr().out
    assert "stage3 refine" in out
    assert "--new-full-run" in out


def test_guard_allows_explicit_new_full_run_opt_in(tmp_path):
    _reviewed_candidate(tmp_path)
    assert guard_new_full_run(_args(tmp_path, new_full_run=True)) is False


def test_guard_allows_run_when_no_reviewed_candidate_exists(tmp_path):
    assert guard_new_full_run(_args(tmp_path)) is False


def test_interactive_plain_run_refuses_before_touching_reviewed_candidate(tmp_path, capsys):
    from unittest.mock import patch

    from tournament_scheduler.application.stage3_session_store import Stage3SessionStore
    from tournament_scheduler.cli.pipeline_orchestrator.run_command_interactive import (
        _cmd_run_interactive,
    )

    _reviewed_candidate(tmp_path)
    args = SimpleNamespace(
        work_dir=str(tmp_path),
        input="input.xlsx",
        export_dir="export",
        resume_from="1",
        non_strict=False,
        force_refresh=False,
        allow_missing_sources=False,
        timestamped_export=True,
        iterations=1,
        interactive=True,
        decision_action=None,
        decision_action_file=None,
        new_full_run=False,
    )
    with patch(
        "tournament_scheduler.cli.pipeline_orchestrator.run_command_interactive._run_stage1"
    ) as run_stage1:
        assert _cmd_run_interactive(args) == 1
    run_stage1.assert_not_called()
    # The reviewed session is still finalized and untouched.
    assert Stage3SessionStore(tmp_path).load().is_finalized() is True


def test_plain_run_refuses_before_starting_stage1(tmp_path):
    from unittest.mock import patch

    from tournament_scheduler.cli.pipeline_orchestrator.run_command import _cmd_run

    _reviewed_candidate(tmp_path)
    args = SimpleNamespace(
        work_dir=str(tmp_path),
        input="input.xlsx",
        non_strict=False,
        resume_from="1",
        interactive=False,
        new_full_run=False,
    )
    with patch(
        "tournament_scheduler.cli.pipeline_orchestrator.run_command._run_stage1"
    ) as run_stage1:
        assert _cmd_run(args) == 1
    run_stage1.assert_not_called()
