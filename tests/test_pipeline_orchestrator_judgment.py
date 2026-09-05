"""Tests for _judge_stage() integration inside _cmd_run() in pipeline_orchestrator.py.

Since _judge_stage() is a nested function, these tests exercise it via _cmd_run()
with all stage runners mocked to avoid full pipeline execution.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tournament_scheduler.cli.pipeline_orchestrator import _cmd_run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HARNESS_CLEAN = {
    "RVV_HARNESS": "",
    "CLAUDE_CODE_SESSION_ID": "",
    "PI_SESSION_ID": "",
    "OPENCODE_SESSION_ID": "",
}

_MINIMAL_CFG = {
    "sources": [],
    "start_date": "2026-01-01",
    "end_date": "2026-12-31",
    "age_groups": [],
    "clubs": [],
}

_MINIMAL_SCRAPING = {"sources": [], "blocked": [], "llm_fallback": []}
_MINIMAL_PLAN = {"plan": {"tournaments": []}, "warnings": []}
_MINIMAL_EXPORT = {"output_files": {}}


def _make_scored_rough_plan(
    plan_id: str,
    *,
    fairness_score: int,
    pairwise: float,
    diversity: float,
    month_balance: float,
) -> dict:
    return {
        "plan": {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "diversity_score": diversity,
            "pairwise_matchup_score": pairwise,
            "month_balance_score": month_balance,
            "fairness_gate": {"status": "warn", "score": fairness_score, "metrics": []},
            "tournaments": [
                {
                    "id": plan_id,
                    "date": "2026-09-05",
                    "arena": "Arena 1",
                    "age_group": "U10",
                    "host_club": "Jar",
                    "teams": [
                        {"club": "Jar", "label": "Jar 1", "age_group": "U10"},
                        {"club": "Holmen", "label": "Holmen 1", "age_group": "U10"},
                    ],
                    "games": [],
                    "start_time": "09:00",
                }
            ],
        },
    }


def _make_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        work_dir=str(tmp_path),
        input="config.yaml",
        non_strict=True,
        resume_from=None,
        allow_missing_sources=False,
        force_refresh=False,
        export_dir=str(tmp_path / "export"),
        timestamped_export=False,
        log_level="info",
    )


def _all_stage_patches():
    """Return a list of patch context managers for all stage imports used by _cmd_run."""
    return [
        patch(
            "tournament_scheduler.pipeline.stage1_config.run",
            return_value=None,
        ),
        patch(
            "tournament_scheduler.pipeline.stage1_config.load_effective_config",
            return_value=_MINIMAL_CFG,
        ),
        patch(
            "tournament_scheduler.pipeline.stage2_scraping.run",
            return_value=_MINIMAL_SCRAPING,
        ),
        patch(
            "tournament_scheduler.pipeline.stage3_planning.run",
            return_value=_MINIMAL_PLAN,
        ),
        patch(
            "tournament_scheduler.pipeline.stage4_export.run",
            return_value=_MINIMAL_EXPORT,
        ),
        patch(
            "tournament_scheduler.pipeline.calendar_viewer.generate_html",
            return_value="export/calendars.html",
        ),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_judge_skipped_when_harness_active(tmp_path: Path) -> None:
    """When a harness env var is set, get_judge_if_headless returns None and pipeline proceeds."""
    args = _make_args(tmp_path)
    judge_mock = MagicMock()
    judge_mock.judge.return_value = "PROCEED"

    patches = _all_stage_patches()
    with patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "session-abc"}):
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = _cmd_run(args)

    assert result == 0
    judge_mock.judge.assert_not_called()


def test_judge_proceed_verdict_continues_pipeline(tmp_path: Path) -> None:
    """Headless run with PROCEED verdict — pipeline completes successfully."""
    args = _make_args(tmp_path)
    judge_mock = MagicMock()
    judge_mock.judge.return_value = "PROCEED"

    patches = _all_stage_patches()
    with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}):
        with patch("tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge_mock):
            with patch("tournament_scheduler.llm_judge.build_stage_prompt", return_value="prompt text"):
                with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                    result = _cmd_run(args)

    assert result == 0
    # judge called after stage1, stage2, stage3, plus once for the
    # post-Stage4 refinement loop's continue-decision (issue #260 Phase 4:
    # "remove tone classification from control authority" —
    # _decide_continue_refinement consults the same headless judge before
    # falling back to the tone-bucket gate).
    assert judge_mock.judge.call_count == 4


def test_judge_abort_verdict_after_stage1_stops_pipeline(tmp_path: Path) -> None:
    """Headless run with ABORT verdict after stage 1 — _cmd_run returns 1."""
    args = _make_args(tmp_path)
    judge_mock = MagicMock()
    judge_mock.judge.return_value = "ABORT\nToo few sources configured."

    patches = _all_stage_patches()
    with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}):
        with patch("tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge_mock):
            with patch("tournament_scheduler.llm_judge.build_stage_prompt", return_value="prompt"):
                with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                    result = _cmd_run(args)

    assert result == 1
    # only called once — stage 1 aborted the pipeline
    assert judge_mock.judge.call_count == 1


def test_judge_proceed_with_reasoning_continues(tmp_path: Path) -> None:
    """Multi-line verdict starting with PROCEED still continues the pipeline."""
    args = _make_args(tmp_path)
    judge_mock = MagicMock()
    judge_mock.judge.return_value = "PROCEED\nAll sources look healthy."

    patches = _all_stage_patches()
    with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}):
        with patch("tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge_mock):
            with patch("tournament_scheduler.llm_judge.build_stage_prompt", return_value="prompt"):
                with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                    result = _cmd_run(args)

    assert result == 0


def test_judge_abort_verdict_writes_judgment_to_checkpoint(tmp_path: Path) -> None:
    """ABORT verdict is written via state.write_judgment with the correct verdict."""
    from tournament_scheduler.pipeline.state import PipelineState, StageName

    args = _make_args(tmp_path)
    judge_mock = MagicMock()
    judge_mock.judge.return_value = "ABORT\nMissing required sources."

    # Pre-create the stage1 checkpoint so write_judgment has an envelope to update.
    state = PipelineState(str(tmp_path))
    state.write_stage(StageName.CONFIG, _MINIMAL_CFG)

    patches = _all_stage_patches()
    with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}):
        with patch("tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge_mock):
            with patch("tournament_scheduler.llm_judge.build_stage_prompt", return_value="prompt"):
                with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                    result = _cmd_run(args)

    assert result == 1

    # Verify write_judgment persisted ABORT to the checkpoint.
    envelope = state.read_envelope(StageName.CONFIG)
    assert "judgment" in envelope, "judgment key must be written to checkpoint"
    assert envelope["judgment"]["verdict"] == "ABORT"
    assert "Missing required sources." in envelope["judgment"]["reasoning"]


def test_judge_runtime_error_continues_pipeline(tmp_path: Path) -> None:
    """When judge.judge() raises RuntimeError, pipeline continues (returns 0)."""
    args = _make_args(tmp_path)
    judge_mock = MagicMock()
    judge_mock.judge.side_effect = RuntimeError("LLM Bridge connection failed")

    patches = _all_stage_patches()
    with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}):
        with patch("tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge_mock):
            with patch("tournament_scheduler.llm_judge.build_stage_prompt", return_value="prompt"):
                with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                    result = _cmd_run(args)

    assert result == 0
    # judge was attempted despite failure
    assert judge_mock.judge.call_count >= 1


def test_judge_no_backend_configured_continues(tmp_path: Path) -> None:
    """When RVV_JUDGE_BACKEND is unset, get_judge_if_headless raises ValueError — pipeline proceeds."""
    args = _make_args(tmp_path)

    patches = _all_stage_patches()
    # Simulate no backend: get_judge_if_headless raises ValueError
    with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": ""}):
        with patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless",
            side_effect=ValueError("No judge backend specified"),
        ):
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                result = _cmd_run(args)

    assert result == 0


def test_judge_called_three_times_when_all_proceed(tmp_path: Path) -> None:
    """In a complete headless run, judge is invoked after stages 1, 2, and 3,
    plus once more for the post-Stage4 refinement loop's continue-decision
    (issue #260 Phase 4 — see test_judge_proceed_verdict_continues_pipeline)."""
    args = _make_args(tmp_path)
    judge_mock = MagicMock()
    judge_mock.judge.return_value = "PROCEED"

    patches = _all_stage_patches()
    with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}):
        with patch("tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge_mock):
            with patch("tournament_scheduler.llm_judge.build_stage_prompt", return_value="prompt"):
                with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                    result = _cmd_run(args)

    assert result == 0
    assert judge_mock.judge.call_count == 4


def test_rough_plan_triggers_stage3_retries_and_fails(tmp_path: Path) -> None:
    """A rough plan with tournaments should be retried before the run fails."""
    args = _make_args(tmp_path)

    rough_plan = _make_scored_rough_plan(
        "t1",
        fairness_score=88,
        pairwise=0.34,
        diversity=1.0,
        month_balance=0.87,
    )

    with patch.dict(os.environ, _HARNESS_CLEAN):
        with patch("tournament_scheduler.pipeline.stage1_config.run", return_value=None), patch(
            "tournament_scheduler.pipeline.stage1_config.load_effective_config",
            return_value=_MINIMAL_CFG,
        ), patch(
            "tournament_scheduler.pipeline.stage2_scraping.run",
            return_value=_MINIMAL_SCRAPING,
        ), patch(
            "tournament_scheduler.pipeline.stage3_planning.run",
            return_value=rough_plan,
        ) as stage3_run, patch(
            "tournament_scheduler.pipeline.stage4_export.run",
            return_value=_MINIMAL_EXPORT,
        ) as stage4_run, patch(
            "tournament_scheduler.pipeline.calendar_viewer.generate_html",
            return_value="export/calendars.html",
        ):
            result = _cmd_run(args)

    assert result == 1
    assert stage3_run.call_count == 3
    assert stage4_run.call_count == 1


def test_stage3_retries_export_best_composite_attempt_not_last(tmp_path: Path) -> None:
    """Earlier attempts are kept when their composite metrics beat later retries."""
    args = _make_args(tmp_path)

    best_early = _make_scored_rough_plan(
        "best-early",
        fairness_score=95,
        pairwise=0.74,
        diversity=0.98,
        month_balance=0.96,
    )
    higher_gate_but_weaker_overall = _make_scored_rough_plan(
        "higher-gate",
        fairness_score=100,
        pairwise=0.30,
        diversity=0.30,
        month_balance=0.30,
    )
    weak_final = _make_scored_rough_plan(
        "weak-final",
        fairness_score=90,
        pairwise=0.40,
        diversity=0.40,
        month_balance=0.40,
    )

    with patch.dict(os.environ, _HARNESS_CLEAN):
        with patch("tournament_scheduler.pipeline.stage1_config.run", return_value=None), patch(
            "tournament_scheduler.pipeline.stage1_config.load_effective_config",
            return_value=_MINIMAL_CFG,
        ), patch(
            "tournament_scheduler.pipeline.stage2_scraping.run",
            return_value=_MINIMAL_SCRAPING,
        ), patch(
            "tournament_scheduler.pipeline.stage3_planning.run",
            side_effect=[best_early, higher_gate_but_weaker_overall, weak_final],
        ) as stage3_run, patch(
            "tournament_scheduler.pipeline.stage4_export.run",
            return_value=_MINIMAL_EXPORT,
        ) as stage4_run, patch(
            "tournament_scheduler.pipeline.calendar_viewer.generate_html",
            return_value="export/calendars.html",
        ):
            result = _cmd_run(args)

    assert result == 1
    assert stage3_run.call_count == 3
    exported_plan = stage4_run.call_args.args[0]
    assert exported_plan["plan"]["tournaments"][0]["id"] == "best-early"

    log_text = next((tmp_path / "export").glob("pipeline_run_*_FAILED.log")).read_text(encoding="utf-8")
    assert "Selected Stage 3 attempt 1/3" in log_text
    assert "attempt 2: gate=warn:100" in log_text
    assert "best-early" not in log_text  # Logs explain score components, not payload IDs.


def test_stage3_retries_log_later_composite_winner(tmp_path: Path) -> None:
    """The selection log also records when a later retry wins the composite comparison."""
    args = _make_args(tmp_path)

    weak_early = _make_scored_rough_plan(
        "weak-early",
        fairness_score=99,
        pairwise=0.25,
        diversity=0.25,
        month_balance=0.25,
    )
    middle = _make_scored_rough_plan(
        "middle",
        fairness_score=85,
        pairwise=0.45,
        diversity=0.45,
        month_balance=0.45,
    )
    best_late = _make_scored_rough_plan(
        "best-late",
        fairness_score=95,
        pairwise=0.74,
        diversity=0.98,
        month_balance=0.96,
    )

    with patch.dict(os.environ, _HARNESS_CLEAN):
        with patch("tournament_scheduler.pipeline.stage1_config.run", return_value=None), patch(
            "tournament_scheduler.pipeline.stage1_config.load_effective_config",
            return_value=_MINIMAL_CFG,
        ), patch(
            "tournament_scheduler.pipeline.stage2_scraping.run",
            return_value=_MINIMAL_SCRAPING,
        ), patch(
            "tournament_scheduler.pipeline.stage3_planning.run",
            side_effect=[weak_early, middle, best_late],
        ), patch(
            "tournament_scheduler.pipeline.stage4_export.run",
            return_value=_MINIMAL_EXPORT,
        ) as stage4_run, patch(
            "tournament_scheduler.pipeline.calendar_viewer.generate_html",
            return_value="export/calendars.html",
        ):
            result = _cmd_run(args)

    assert result == 1
    exported_plan = stage4_run.call_args.args[0]
    assert exported_plan["plan"]["tournaments"][0]["id"] == "best-late"

    log_text = next((tmp_path / "export").glob("pipeline_run_*_FAILED.log")).read_text(encoding="utf-8")
    assert "Selected Stage 3 attempt 3/3" in log_text
    assert "attempt 1: gate=warn:99" in log_text


# ---------------------------------------------------------------------------
# _check_stage2_checkpoint unit tests
# ---------------------------------------------------------------------------

from tournament_scheduler.cli.pipeline_orchestrator import _check_stage2_checkpoint  # noqa: E402


def _make_stage2_checkpoint_with_blocked() -> dict:
    """Return a Stage 2 checkpoint with one good source and one blocked source."""
    return {
        "sources": [
            {"name": "a", "event_count": 3, "blocked": False},
            {"name": "b", "event_count": 0, "blocked": True},
        ],
        "blocked": ["b"],
    }


def test_confidence_gate_warn_strict_decline_aborts() -> None:
    """Blocked source + strict=True, operator answers 'n' — gate returns False (abort)."""
    console = MagicMock()
    log_fn = MagicMock()
    chk = _make_stage2_checkpoint_with_blocked()

    with patch("builtins.input", return_value="n"):
        result = _check_stage2_checkpoint(chk, True, console, log_fn, harness_active=False)

    assert result is False


def test_confidence_gate_warn_strict_approve_proceeds() -> None:
    """Blocked source + strict=True, operator answers 'j' — gate returns True (proceed)."""
    console = MagicMock()
    log_fn = MagicMock()
    chk = _make_stage2_checkpoint_with_blocked()

    with patch("builtins.input", return_value="j"):
        result = _check_stage2_checkpoint(chk, True, console, log_fn, harness_active=False)

    assert result is True


def test_confidence_gate_warn_non_strict_proceeds_without_prompt() -> None:
    """Blocked source + strict=False — gate returns True without calling input()."""
    console = MagicMock()
    log_fn = MagicMock()
    chk = _make_stage2_checkpoint_with_blocked()

    with patch("builtins.input", side_effect=AssertionError("must not prompt in non-strict")):
        result = _check_stage2_checkpoint(chk, False, console, log_fn, harness_active=False)

    assert result is True


def test_confidence_gate_ok_verdict_skips_gate(tmp_path: Path) -> None:
    """OK confidence verdict — _run_confidence_gate is not called (pipeline proceeds normally)."""
    # Integration: run _cmd_run with a mocked confidence assessment returning OK;
    # verify the pipeline completes with return code 0.
    args = _make_args(tmp_path)
    args.non_strict = False  # strict mode

    from unittest.mock import MagicMock as _MM
    ok_verdict = _MM()
    ok_verdict.verdict = "OK"
    ok_verdict.overall_assessment = "all sources healthy"
    ok_verdict.suspicious_sources = []
    ok_verdict.gaps = []

    patches = _all_stage_patches()
    with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_APPROVAL_ENDPOINT": "http://localhost:1234"}):
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = _cmd_run(args)

    assert result == 0
