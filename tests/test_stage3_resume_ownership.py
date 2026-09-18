"""Regression coverage for Stage 3 resume ownership.

A pending Stage 3 decision belongs to exactly one pipeline-stage owner. The
``Stage3Session`` -- not each CLI/harness adapter -- decides whether an answer
is submitted with ``--resume-from 3`` (in-Stage-3 sub-decisions) or
``--resume-from 4`` (post-plan candidate decisions), and a transport must
reject a wrong resume number with a precise lifecycle error instead of
building an unrelated stage context.
"""

from __future__ import annotations

import argparse
import json

from tournament_scheduler.application.stage3_session import (
    Stage3Session,
    resume_stage_for_capability,
)
from tournament_scheduler.application.stage3_session_store import Stage3SessionStore, status_for_session


def _candidate() -> dict:
    return {"tournaments": [{"id": "t1", "date": "2026-10-05"}]}


def _plan() -> dict:
    return {"plan": _candidate(), "warnings": []}


def _session_with_pending(capability: str) -> Stage3Session:
    session = Stage3Session(run_id="")
    session.candidate = _plan()
    session.candidate_fingerprint = "fp-current"
    session.candidate_revision = 2
    session.set_pending(
        capability=capability,
        context={
            "capability": capability,
            "available_actions": [
                "apply_repair_option",
                "apply_candidate",
                "keep_baseline",
                "request_operator",
            ],
        },
    )
    return session


def test_in_stage3_subdecisions_are_answered_with_resume_from_3():
    assert _session_with_pending("shared_host_assignment").pending_resume_stage() == 3
    assert _session_with_pending("arena_conflict_resolution").pending_resume_stage() == 3


def test_post_plan_candidate_decisions_are_answered_with_resume_from_4():
    for capability in (
        "stage3_interactive",
        "stage3_optimize",
        "stage3_pareto",
        "host_team_missing_repair",
        "underfilled_roster_repair",
        "host_placement_repair",
        "search_neighborhood_repair",
    ):
        assert _session_with_pending(capability).pending_resume_stage() == 4, capability


def test_no_pending_decision_has_no_resume_owner():
    assert Stage3Session().pending_resume_stage() is None


def test_unknown_capability_falls_back_to_its_scope():
    assert resume_stage_for_capability("future_run_scoped", scope="run") == 3
    assert resume_stage_for_capability("future_candidate_scoped", scope="candidate") == 4


def test_session_status_exposes_resume_ownership(tmp_path):
    store = Stage3SessionStore(tmp_path)
    store.save(_session_with_pending("host_placement_repair"))

    view = status_for_session(store.load())
    assert view["pending_decision"]["capability"] == "host_placement_repair"
    assert view["pending_decision"]["resume_from"] == 4

    store.save(_session_with_pending("arena_conflict_resolution"))
    view = status_for_session(store.load())
    assert view["pending_decision"]["resume_from"] == 3


def _args(work_dir: str, *, resume_from: str, action: dict) -> argparse.Namespace:
    return argparse.Namespace(
        work_dir=work_dir,
        input="input.xlsx",
        resume_from=resume_from,
        non_strict=False,
        decision_action=json.dumps(action),
        decision_action_file=None,
        export_dir="export",
        iterations=1,
    )


def test_wrong_resume_for_a_candidate_repair_is_a_precise_lifecycle_error(tmp_path, capsys):
    """A repair context answered with --resume-from 3 must be rejected before
    any Stage 2 context is built, with the ownership in the message."""
    from tournament_scheduler.cli.pipeline_orchestrator.run_command_interactive import _cmd_run_interactive
    from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus

    state = PipelineState(str(tmp_path))
    state.write_stage(StageName.CONFIG, {"start_date": "2026-09-01", "end_date": "2027-04-30"}, status=StageStatus.DONE)
    state.write_stage(StageName.SCRAPING, {"sources": []}, status=StageStatus.DONE)
    state.write_stage(StageName.PLANNING, _plan(), status=StageStatus.DONE)
    Stage3SessionStore(str(tmp_path)).save(_session_with_pending("host_placement_repair"))

    args = _args(
        str(tmp_path),
        resume_from="3",
        action={"action_id": "apply_repair_option", "arguments": {"option_id": "opt-1"}},
    )
    exit_code = _cmd_run_interactive(args)
    out = capsys.readouterr().out

    assert exit_code == 1
    assert "--resume-from 4" in out
    assert "host_placement_repair" in out
    # The wrong resume must not be mistaken for a missing Stage 2 action.
    assert "decision_action_not_available" not in out


def test_wrong_resume_for_an_in_stage3_subdecision_is_rejected(tmp_path, capsys):
    from tournament_scheduler.cli.pipeline_orchestrator.run_command_interactive import _cmd_run_interactive
    from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus

    state = PipelineState(str(tmp_path))
    state.write_stage(StageName.CONFIG, {"start_date": "2026-09-01", "end_date": "2027-04-30"}, status=StageStatus.DONE)
    state.write_stage(StageName.SCRAPING, {"sources": []}, status=StageStatus.DONE)
    state.write_stage(StageName.PLANNING, _plan(), status=StageStatus.DONE)
    Stage3SessionStore(str(tmp_path)).save(_session_with_pending("shared_host_assignment"))

    args = _args(
        str(tmp_path),
        resume_from="4",
        action={"action_id": "assign_shared_host", "arguments": {"chosen_club": "Tønsberg"}},
    )
    exit_code = _cmd_run_interactive(args)
    out = capsys.readouterr().out

    assert exit_code == 1
    assert "--resume-from 3" in out
    assert "shared_host_assignment" in out
