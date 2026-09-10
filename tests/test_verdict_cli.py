"""Unit tests for the ``rvv-miniputt verdict`` CLI command."""
from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from tournament_scheduler.models import SeasonPlan
from tournament_scheduler.pipeline.stage3_helpers import _plan_to_dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan(**kwargs) -> SeasonPlan:
    defaults: dict = {
        "tournaments": [],
        "team_game_counts": {},
        "game_count_spread": 0,
        "month_balance_score": 1.0,
        "pairwise_matchup_score": 1.0,
        "diversity_score": 1.0,
        "fairness_gate": {"status": "pass", "score": 100},
        "arena_day_collisions": [],
    }
    defaults.update(kwargs)
    return SeasonPlan(**defaults)


def _verdict_args(**kwargs) -> argparse.Namespace:
    defaults = dict(work_dir=".pipeline")
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _make_checkpoint(plan: SeasonPlan) -> dict:
    return {"plan": _plan_to_dict(plan)}


def _patch_state(checkpoint):
    """Return a context manager that patches PipelineState.read_stage with a fixed checkpoint."""
    return patch(
        "tournament_scheduler.pipeline.state.PipelineState.read_stage",
        return_value=checkpoint,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCmdVerdictExitCodes:
    def test_returns_0_with_valid_checkpoint(self):
        from tournament_scheduler.cli.rvv_cli import _cmd_verdict

        plan = _make_plan()
        checkpoint = _make_checkpoint(plan)

        with _patch_state(checkpoint):
            rc = _cmd_verdict(_verdict_args())

        assert rc == 0

    def test_returns_1_when_no_checkpoint(self):
        from tournament_scheduler.cli.rvv_cli import _cmd_verdict

        with patch(
            "tournament_scheduler.pipeline.state.PipelineState.read_stage",
            return_value=None,
        ), pytest.raises(SystemExit) as exc_info:
            _cmd_verdict(_verdict_args())

        assert exc_info.value.code == 1

    def test_returns_1_when_checkpoint_missing_plan_key(self):
        from tournament_scheduler.cli.rvv_cli import _cmd_verdict

        with patch(
            "tournament_scheduler.pipeline.state.PipelineState.read_stage",
            return_value={"not_plan": "oops"},
        ), pytest.raises(SystemExit) as exc_info:
            _cmd_verdict(_verdict_args())

        assert exc_info.value.code == 1


class TestCmdVerdictOutput:
    def _run_and_capture(self, plan: SeasonPlan) -> list[str]:
        """Run _cmd_verdict and capture lines emitted via the Rich console."""
        from tournament_scheduler.cli.rvv_cli import _cmd_verdict

        captured: list[str] = []

        def _fake_print(msg: str = "", **_kwargs):
            captured.append(str(msg))

        checkpoint = _make_checkpoint(plan)
        with _patch_state(checkpoint), patch(
            "tournament_scheduler.cli.rvv_cli._console"
        ) as mock_console:
            mock_console.print.side_effect = _fake_print
            rc = _cmd_verdict(_verdict_args())

        assert rc == 0, "expected exit code 0"
        return captured

    def test_output_contains_tone_line(self):
        plan = _make_plan(pairwise_matchup_score=1.0, diversity_score=1.0, month_balance_score=1.0)
        lines = self._run_and_capture(plan)
        tone_lines = [l for l in lines if l.startswith("tone=")]
        assert tone_lines, f"No 'tone=' line found in output: {lines}"

    def test_tone_is_one_of_valid_values(self):
        """The tone must always be one of the three valid values."""
        plan = _make_plan(
            pairwise_matchup_score=1.0,
            diversity_score=1.0,
            month_balance_score=1.0,
            fairness_gate={"status": "pass", "score": 100},
        )
        lines = self._run_and_capture(plan)
        tone_line = next((l for l in lines if l.startswith("tone=")), None)
        assert tone_line is not None, "No tone= line in output"
        tone_value = tone_line.split("=", 1)[1]
        assert tone_value in ("strong", "mixed", "rough"), (
            f"tone must be strong/mixed/rough, got: {tone_value}"
        )

    def test_rough_tone_for_low_pairwise(self):
        plan = _make_plan(
            pairwise_matchup_score=0.5,
            diversity_score=1.0,
            month_balance_score=1.0,
            fairness_gate={"status": "pass", "score": 100},
        )
        lines = self._run_and_capture(plan)
        tone_line = next((l for l in lines if l.startswith("tone=")), None)
        assert tone_line == "tone=rough", f"Expected tone=rough, got: {tone_line}"

    def test_output_contains_tone_label(self):
        plan = _make_plan()
        lines = self._run_and_capture(plan)
        label_lines = [l for l in lines if l.startswith("tone_label=")]
        assert label_lines, f"No 'tone_label=' line found in output: {lines}"

    def test_output_contains_pairwise_score(self):
        plan = _make_plan(pairwise_matchup_score=0.88)
        lines = self._run_and_capture(plan)
        score_lines = [l for l in lines if l.startswith("pairwise_matchup_score=")]
        assert score_lines, f"No 'pairwise_matchup_score=' line found in output: {lines}"
        assert "0.8800" in score_lines[0]

    def test_output_contains_verdict_text(self):
        plan = _make_plan()
        lines = self._run_and_capture(plan)
        verdict_lines = [l for l in lines if l.startswith("verdict=")]
        assert verdict_lines, f"No 'verdict=' line found in output: {lines}"

    def test_output_contains_action_text(self):
        plan = _make_plan()
        lines = self._run_and_capture(plan)
        action_lines = [l for l in lines if l.startswith("action_text=")]
        assert action_lines, f"No 'action_text=' line found in output: {lines}"
