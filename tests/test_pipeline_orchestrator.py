"""Tests for _compute_verdict_tone and _run_refinement_loop in pipeline_orchestrator.py."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from tournament_scheduler.cli.pipeline_orchestrator import (
    _MAX_REFINEMENT_ITERATIONS,
    _compute_verdict_tone,
    _decide_continue_refinement,
    _decide_plan_adoption,
    _decide_refinement_candidate,
    _refinement_metrics,
    _run_approval_gate,
    _run_mid_planning_critic_loop,
    _run_refinement_loop,
)
from tournament_scheduler.models import SeasonPlan

_HARNESS_CLEAN = {
    "RVV_HARNESS": "",
    "CLAUDE_CODE_SESSION_ID": "",
    "PI_SESSION_ID": "",
    "OPENCODE_SESSION_ID": "",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan_obj(
    *,
    gate_status: str = "pass",
    gate_score: int = 100,
    pairwise: float = 1.0,
    diversity: float = 1.0,
    month_balance: float = 1.0,
) -> SeasonPlan:
    plan = SeasonPlan(
        fairness_gate={"status": gate_status, "score": gate_score},
        pairwise_matchup_score=pairwise,
        diversity_score=diversity,
        month_balance_score=month_balance,
    )
    return plan


def _make_checkpoint(plan_obj: SeasonPlan) -> dict[str, Any]:
    return {"plan": plan_obj, "warnings": []}


def _make_state() -> MagicMock:
    state = MagicMock()
    state.read_stage.return_value = {"plan": MagicMock(spec=SeasonPlan)}
    return state


def _make_args() -> MagicMock:
    args = MagicMock()
    args.work_dir = "/tmp/test"
    args.export_dir = "export"
    args.timestamped_export = False
    args.iterations = 1
    args.mid_planning_critic_iterations = 0
    return args


# ---------------------------------------------------------------------------
# _compute_verdict_tone — unit tests
# ---------------------------------------------------------------------------


class TestComputeVerdictTone:
    def test_strong_when_all_scores_perfect(self) -> None:
        plan = _make_plan_obj(gate_status="pass", gate_score=100, pairwise=1.0, diversity=1.0, month_balance=1.0)
        assert _compute_verdict_tone(plan) == "strong"

    def test_rough_when_gate_fails(self) -> None:
        plan = _make_plan_obj(gate_status="fail", gate_score=40, pairwise=0.5, diversity=0.5, month_balance=0.5)
        assert _compute_verdict_tone(plan) == "rough"

    def test_rough_when_gate_score_below_70(self) -> None:
        plan = _make_plan_obj(gate_status="pass", gate_score=65, pairwise=0.8, diversity=0.95, month_balance=0.95)
        assert _compute_verdict_tone(plan) == "rough"

    def test_rough_when_pairwise_below_0_75(self) -> None:
        plan = _make_plan_obj(gate_status="pass", gate_score=80, pairwise=0.7, diversity=0.95, month_balance=0.95)
        assert _compute_verdict_tone(plan) == "rough"

    def test_mixed_when_gate_warns(self) -> None:
        plan = _make_plan_obj(gate_status="warn", gate_score=85, pairwise=0.92, diversity=0.95, month_balance=0.95)
        assert _compute_verdict_tone(plan) == "mixed"

    def test_mixed_when_pairwise_between_75_and_90(self) -> None:
        plan = _make_plan_obj(gate_status="pass", gate_score=90, pairwise=0.80, diversity=0.95, month_balance=0.95)
        assert _compute_verdict_tone(plan) == "mixed"

    def test_mixed_when_diversity_below_0_9(self) -> None:
        plan = _make_plan_obj(gate_status="pass", gate_score=90, pairwise=0.92, diversity=0.85, month_balance=0.95)
        assert _compute_verdict_tone(plan) == "mixed"

    def test_mixed_when_month_balance_below_0_9(self) -> None:
        plan = _make_plan_obj(gate_status="pass", gate_score=90, pairwise=0.92, diversity=0.95, month_balance=0.85)
        assert _compute_verdict_tone(plan) == "mixed"

    def test_accepts_checkpoint_dict(self) -> None:
        plan_obj = _make_plan_obj(gate_status="pass", gate_score=95, pairwise=0.95, diversity=0.95, month_balance=0.95)
        checkpoint = _make_checkpoint(plan_obj)
        assert _compute_verdict_tone(checkpoint) == "strong"

    def test_accepts_bare_dict_without_plan_key(self) -> None:
        # A bare dict with no "plan" key should not crash
        result = _compute_verdict_tone({"pairwise_matchup_score": 0.5})
        # dict has no SeasonPlan attributes — defaults to 0s, gate_status=pass → rough
        assert result in ("rough", "mixed", "strong")

    def test_accepts_plan_checkpoint_dict_with_serialised_plan(self) -> None:
        checkpoint = {
            "plan": {
                "start_date": "2026-09-01",
                "end_date": "2027-04-30",
                "diversity_score": 1.0,
                "pairwise_matchup_score": 0.34,
                "month_balance_score": 0.87,
                "fairness_gate": {"status": "warn", "score": 88, "metrics": []},
                "tournaments": [
                    {
                        "id": "t1",
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
            }
        }
        assert _compute_verdict_tone(checkpoint) == "rough"

    def test_gate_status_case_insensitive(self) -> None:
        plan = _make_plan_obj(gate_status="FAIL", gate_score=40, pairwise=0.5, diversity=0.5, month_balance=0.5)
        assert _compute_verdict_tone(plan) == "rough"


# ---------------------------------------------------------------------------
# _run_mid_planning_critic_loop — unit tests
# ---------------------------------------------------------------------------


class TestRunMidPlanningCriticLoop:
    def test_zero_cap_returns_without_rerun(self) -> None:
        plan = _make_gate_checkpoint("pass", gate_score=100)
        args = _make_args()
        args.mid_planning_critic_iterations = 0

        with patch("tournament_scheduler.cli.pipeline_orchestrator._run_stage3") as mock_stage3:
            updated, abort, failed = _run_mid_planning_critic_loop(
                args, {}, {}, MagicMock(), MagicMock(), MagicMock(), True, 1, lambda _: None, plan
            )

        assert updated is plan
        assert abort is False
        assert failed is False
        mock_stage3.assert_not_called()

    def test_no_issues_or_hints_stops_without_rerun(self) -> None:
        plan = _make_gate_checkpoint("pass", gate_score=100)
        args = _make_args()
        args.mid_planning_critic_iterations = 2
        log_calls: list[str] = []

        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._build_mid_planning_critic_hints",
            return_value={"issues": [], "penalty_hints": {}, "tone": "strong"},
        ), patch("tournament_scheduler.cli.pipeline_orchestrator._run_stage3") as mock_stage3:
            _run_mid_planning_critic_loop(
                args, {}, {}, MagicMock(), MagicMock(), MagicMock(), True, 1, log_calls.append, plan
            )

        mock_stage3.assert_not_called()
        assert any("no issues" in msg for msg in log_calls)

    def test_reruns_stage3_with_structured_penalty_hints(self) -> None:
        plan = _make_gate_checkpoint("warn", gate_score=80)
        improved = _make_gate_checkpoint("pass", gate_score=95)
        args = _make_args()
        args.mid_planning_critic_iterations = 2
        state = MagicMock()

        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._build_mid_planning_critic_hints",
            side_effect=[
                {"source": "mid_planning_critic", "issues": ["issue"], "penalty_hints": {"game_count_spread_score": 40.0}, "tone": "rough"},
                {"issues": [], "penalty_hints": {}, "tone": "strong"},
            ],
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
            return_value=(improved, False, False),
        ) as mock_stage3:
            updated, abort, failed = _run_mid_planning_critic_loop(
                args, {"cfg": True}, {"scrape": True}, state, "start", "end", True, 1, lambda _: None, plan
            )

        assert updated is improved
        assert abort is False
        assert failed is False
        assert mock_stage3.call_count == 1
        assert mock_stage3.call_args.args[-2] == {"game_count_spread_score": 40.0}
        assert mock_stage3.call_args.args[-1]["source"] == "mid_planning_critic"

    def test_respects_iteration_cap_when_hints_continue(self) -> None:
        plan = _make_gate_checkpoint("fail", gate_score=40)
        args = _make_args()
        args.mid_planning_critic_iterations = 2

        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._build_mid_planning_critic_hints",
            return_value={"issues": ["issue"], "penalty_hints": {"hosting_deviation_score": 20.0}, "tone": "rough"},
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
            return_value=(plan, False, False),
        ) as mock_stage3:
            _run_mid_planning_critic_loop(
                args, {}, {}, MagicMock(), MagicMock(), MagicMock(), True, 1, lambda _: None, plan
            )

        assert mock_stage3.call_count == 2


# ---------------------------------------------------------------------------
# _decide_plan_adoption — unit tests (issue #260 Phase 4)
# ---------------------------------------------------------------------------


def _decision_team(club: str, label: str, age_group: str) -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _decision_tournament(t_id: str, date_str: str, arena: str, age_group: str, teams: list[dict]) -> dict:
    game_pairs = [(a["label"], b["label"]) for i, a in enumerate(teams) for b in teams[i + 1 :]]
    return {
        "id": t_id,
        "date": date_str,
        "arena": arena,
        "age_group": age_group,
        "host_club": teams[0]["club"] if teams else None,
        "teams": teams,
        "games": [
            {"home": home, "away": away, "parallel_slot": 0, "round_number": 1}
            for home, away in game_pairs
        ],
    }


def _worse_candidate() -> dict:
    teams = {f"T{i}": _decision_team(f"Club{i}", f"T{i}", "U10") for i in range(1, 9)}
    group_a = [teams["T1"], teams["T2"], teams["T3"], teams["T4"]]
    group_b = [teams["T5"], teams["T6"], teams["T7"], teams["T8"]]
    return {
        "schema_version": 1,
        "tournaments": [
            _decision_tournament("t1", "2026-01-05", "Arena1", "U10", group_a),
            _decision_tournament("t2", "2026-02-04", "Arena1", "U10", group_a),
            _decision_tournament("t3", "2026-03-06", "Arena5", "U10", group_b),
            _decision_tournament("t4", "2026-04-05", "Arena5", "U10", group_b),
        ],
    }


def _better_candidate() -> dict:
    from tournament_scheduler.stage3_optimizer import optimize_candidate

    return optimize_candidate(_worse_candidate(), iterations=3000, seed=1)


class TestDecideMidPlanningAdoption:
    """Issue #260 Phase 4: promote/reject via DecisionContext when a headless
    judge is configured, falling back to the pre-existing deterministic
    composite-quality rank comparison otherwise."""

    def test_no_headless_judge_falls_back_to_deterministic_rank(self, tmp_path) -> None:
        # CLAUDE_CODE_SESSION_ID etc. are set in this test process (or at
        # least RVV_JUDGE_BACKEND is unset), so get_judge_if_headless()
        # returns None/raises — behavior must be identical to before this
        # change: adopt strictly-better rerun by _plan_attempt_quality rank
        # (the SeasonPlanner-specific fairness_gate/pairwise/diversity/
        # month_balance composite, not the stage3_ab/planning_contract
        # metrics used once a judge is actually consulted).
        best = _make_gate_checkpoint("warn", gate_score=50)
        rerun = _make_gate_checkpoint("pass", gate_score=95)

        adopted = _decide_plan_adoption(
            best, rerun, None, run_id="run-1", iteration=1, work_dir=str(tmp_path), log_fn=lambda _: None
        )

        assert adopted is True

    def test_headless_judge_apply_candidate_is_recorded_and_adopted(self, tmp_path) -> None:
        best = {"plan": _worse_candidate()}
        rerun = {"plan": _better_candidate()}
        judge = MagicMock()
        judge.judge.return_value = "apply_candidate\nDominates baseline on every metric."

        with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge
        ):
            adopted = _decide_plan_adoption(
                best, rerun, None, run_id="run-1", iteration=1, work_dir=str(tmp_path), log_fn=lambda _: None
            )

        assert adopted is True
        judge.judge.assert_called_once()

        from tournament_scheduler.pipeline.run_manifest import RunManifest

        manifest = RunManifest(str(tmp_path)).read()
        entry = manifest["decision_log"][-1]
        assert entry["action"]["action_id"] == "apply_candidate"
        assert entry["result"]["accepted"] is True
        assert entry["context"]["capability"] == "stage3_optimize"

    def test_headless_judge_keep_baseline_is_not_adopted(self, tmp_path) -> None:
        best = {"plan": _worse_candidate()}
        rerun = {"plan": _better_candidate()}
        judge = MagicMock()
        judge.judge.return_value = "keep_baseline"

        with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge
        ):
            adopted = _decide_plan_adoption(
                best, rerun, None, run_id="run-1", iteration=1, work_dir=str(tmp_path), log_fn=lambda _: None
            )

        assert adopted is False

    def test_headless_judge_call_failure_falls_back_to_deterministic_rank(self, tmp_path) -> None:
        best = _make_gate_checkpoint("warn", gate_score=50)
        rerun = _make_gate_checkpoint("pass", gate_score=95)
        judge = MagicMock()
        judge.judge.side_effect = RuntimeError("backend unreachable")

        with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge
        ):
            adopted = _decide_plan_adoption(
                best, rerun, None, run_id="run-1", iteration=1, work_dir=str(tmp_path), log_fn=lambda _: None
            )

        # Falls back to the deterministic rank comparison, which prefers the
        # strictly-better candidate — same outcome as the no-judge case.
        assert adopted is True

    def test_headless_judge_cannot_bypass_invalid_candidate(self, tmp_path) -> None:
        """An LLM saying apply_candidate cannot adopt a candidate that fails
        the verifier — the deterministic validator rejects it regardless."""
        teams = [_decision_team(f"Club{i}", f"T{i}", "U10") for i in range(1, 5)]
        invalid = {
            "schema_version": 1,
            "tournaments": [
                _decision_tournament("t1", "2026-01-05", "Arena1", "U10", teams),
                _decision_tournament("t2", "2026-01-05", "Arena1", "U10", teams),
            ],
        }
        best = {"plan": _worse_candidate()}
        rerun = {"plan": invalid}
        judge = MagicMock()
        judge.judge.return_value = "apply_candidate\nLooks fine to me."

        with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge
        ):
            adopted = _decide_plan_adoption(
                best, rerun, None, run_id="run-1", iteration=1, work_dir=str(tmp_path), log_fn=lambda _: None
            )

        # The verifier rejection must not be silently papered over by a
        # fallback that never checks hard constraints at all.
        assert adopted is False

        from tournament_scheduler.pipeline.run_manifest import RunManifest

        manifest = RunManifest(str(tmp_path)).read()
        entry = manifest["decision_log"][-1]
        assert entry["action"]["action_id"] == "apply_candidate"
        assert entry["result"]["accepted"] is False
        assert entry["result"]["rejection_reason"] == "hard_violation_blocks_action"

    def test_label_distinguishes_multi_seed_from_mid_planning_critic_in_decision_context(
        self, tmp_path
    ) -> None:
        """The Stage 3 multi-seed best-attempt loop in ``_cmd_run`` reuses this
        same decision, just with ``label="stage3_multi_seed"`` (issue #260
        Phase 4) — the recorded DecisionContext refs/objective must reflect
        that instead of hardcoding "mid_planning_critic"."""
        best = {"plan": _worse_candidate()}
        rerun = {"plan": _better_candidate()}
        judge = MagicMock()
        judge.judge.return_value = "apply_candidate\nBetter on every metric."

        with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge
        ):
            adopted = _decide_plan_adoption(
                best,
                rerun,
                None,
                run_id="run-1",
                iteration=2,
                work_dir=str(tmp_path),
                log_fn=lambda _: None,
                label="stage3_multi_seed",
            )

        assert adopted is True

        from tournament_scheduler.pipeline.run_manifest import RunManifest

        manifest = RunManifest(str(tmp_path)).read()
        entry = manifest["decision_log"][-1]
        assert entry["context"]["baseline_ref"] == "stage3_multi_seed:best_attempt"
        assert entry["context"]["candidate_ref"] == "stage3_multi_seed:iteration_2"


# ---------------------------------------------------------------------------
# _run_refinement_loop — unit tests
# ---------------------------------------------------------------------------


def _make_update_result(*, success: bool = True) -> MagicMock:
    result = MagicMock()
    result.success = success
    result.summary_nb = "Ingen manuelle justeringer var nødvendige."
    return result


class TestRunRefinementLoop:
    """Tests for the skill-driven plan refinement loop."""

    def _patch_refinement(
        self,
        *,
        initial_tone: str = "rough",
        tone_after_apply: str = "mixed",
        critic_issues: list[str] | None = None,
        moves: list[dict] | None = None,
    ):
        """Return a context that patches all external dependencies of _run_refinement_loop."""
        if critic_issues is None:
            critic_issues = ["some issue"]
        if moves is None:
            moves = [{"tournament_id": "t1", "new_date": "2026-03-01", "reason": "test", "can_auto_fix": True, "issue": "some issue"}]

        plan_obj = _make_plan_obj(gate_status="pass", gate_score=95, pairwise=0.95, diversity=0.95, month_balance=0.95)

        return (plan_obj, [
            patch(
                "tournament_scheduler.cli.pipeline_orchestrator._compute_verdict_tone",
                side_effect=[initial_tone, tone_after_apply],
            ),
            patch(
                "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.load_plan",
                return_value=plan_obj,
            ),
            patch(
                "tournament_scheduler.cli.plan_critic.generate_critic_summary",
                return_value=critic_issues,
            ),
            patch(
                "tournament_scheduler.cli.plan_critic.suggest_moves",
                return_value=moves,
            ),
            patch(
                "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.apply",
                return_value=_make_update_result(success=True),
            ),
            patch(
                "tournament_scheduler.pipeline.tournament_updater.TournamentUpdater.write_updated_checkpoint",
            ),
        ])

    def test_exits_early_when_tone_not_rough(self) -> None:
        """If initial tone is already 'mixed', loop should return immediately without applying anything."""
        plan_obj = _make_plan_obj(gate_status="pass", gate_score=95, pairwise=0.95, diversity=0.95, month_balance=0.95)
        checkpoint = _make_checkpoint(plan_obj)
        state = _make_state()
        args = _make_args()
        log_calls: list[str] = []

        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._compute_verdict_tone",
            return_value="mixed",
        ) as mock_tone:
            tone, updated = _run_refinement_loop(checkpoint, state, args, False, log_calls.append)

        assert tone == "mixed"
        assert updated is checkpoint
        # _compute_verdict_tone called once for the check, then loop exits
        assert mock_tone.call_count == 1

    def test_exits_after_tone_improves_on_iteration_2(self) -> None:
        """Loop exits after tone becomes 'mixed' on the second iteration."""
        plan_obj = _make_plan_obj(gate_status="fail", gate_score=40, pairwise=0.5, diversity=0.5, month_balance=0.5)
        checkpoint = _make_checkpoint(plan_obj)
        state = _make_state()
        state.read_stage.return_value = checkpoint
        args = _make_args()
        log_calls: list[str] = []

        apply_result = _make_update_result(success=True)

        # First call: rough, second call: mixed → should exit after iteration 1 apply
        tone_sequence = ["rough", "mixed"]
        tone_iter = iter(tone_sequence)

        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._compute_verdict_tone",
            side_effect=tone_iter,
        ):
            with patch(
                "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.load_plan",
                return_value=plan_obj,
            ):
                with patch(
                    "tournament_scheduler.cli.plan_critic.generate_critic_summary",
                    return_value=["Arena-day collision on 2026-03-01"],
                ):
                    with patch(
                        "tournament_scheduler.cli.plan_critic.suggest_moves",
                        return_value=[{
                            "tournament_id": "t1",
                            "new_date": "2026-03-08",
                            "reason": "shift",
                            "can_auto_fix": True,
                            "issue": "Arena-day collision on 2026-03-01",
                        }],
                    ):
                        move_date_result = MagicMock()
                        move_date_result.summary_nb = "moved"
                        with patch(
                            "tournament_scheduler.pipeline.tournament_updater.TournamentUpdater.move_date",
                            return_value=move_date_result,
                        ):
                            with patch(
                                "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.apply",
                                return_value=apply_result,
                            ):
                                with patch(
                                    "tournament_scheduler.pipeline.tournament_updater.TournamentUpdater.write_updated_checkpoint",
                                ):
                                    tone, updated = _run_refinement_loop(
                                        checkpoint, state, args, False, log_calls.append
                                    )

        # After first iteration apply, tone becomes mixed → early exit before second iteration starts
        assert tone == "mixed"

    def test_exits_after_max_iterations_when_tone_stays_rough(self) -> None:
        """Loop stops after _MAX_REFINEMENT_ITERATIONS if tone never improves."""
        plan_obj = _make_plan_obj(gate_status="fail", gate_score=40, pairwise=0.5, diversity=0.5, month_balance=0.5)
        checkpoint = _make_checkpoint(plan_obj)
        state = _make_state()
        state.read_stage.return_value = checkpoint
        args = _make_args()
        log_calls: list[str] = []

        apply_result = _make_update_result(success=True)
        apply_mock = MagicMock(return_value=apply_result)

        # Always return 'rough' so the loop runs to the cap
        tone_values = ["rough"] * (_MAX_REFINEMENT_ITERATIONS + 1)

        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._compute_verdict_tone",
            side_effect=tone_values,
        ):
            with patch(
                "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.load_plan",
                return_value=plan_obj,
            ):
                with patch(
                    "tournament_scheduler.cli.plan_critic.generate_critic_summary",
                    return_value=["some issue"],
                ):
                    with patch(
                        "tournament_scheduler.cli.plan_critic.suggest_moves",
                        return_value=[{
                            "tournament_id": "t1",
                            "new_date": "2026-03-08",
                            "reason": "shift",
                            "can_auto_fix": True,
                            "issue": "some issue",
                        }],
                    ):
                        move_date_result = MagicMock()
                        move_date_result.summary_nb = "moved"
                        with patch(
                            "tournament_scheduler.pipeline.tournament_updater.TournamentUpdater.move_date",
                            return_value=move_date_result,
                        ):
                            with patch(
                                "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.apply",
                                apply_mock,
                            ):
                                with patch(
                                    "tournament_scheduler.pipeline.tournament_updater.TournamentUpdater.write_updated_checkpoint",
                                ):
                                    tone, updated = _run_refinement_loop(
                                        checkpoint, state, args, False, log_calls.append
                                    )

        # apply() called exactly _MAX_REFINEMENT_ITERATIONS times
        assert apply_mock.call_count == _MAX_REFINEMENT_ITERATIONS
        # Final tone is still rough
        assert tone == "rough"

    def test_stops_when_no_auto_fixable_moves(self) -> None:
        """Loop exits early when suggest_moves returns no auto-fixable entries."""
        plan_obj = _make_plan_obj(gate_status="fail", gate_score=40, pairwise=0.5, diversity=0.5, month_balance=0.5)
        checkpoint = _make_checkpoint(plan_obj)
        state = _make_state()
        args = _make_args()
        log_calls: list[str] = []

        apply_mock = MagicMock()

        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._compute_verdict_tone",
            return_value="rough",
        ):
            with patch(
                "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.load_plan",
                return_value=plan_obj,
            ):
                with patch(
                    "tournament_scheduler.cli.plan_critic.generate_critic_summary",
                    return_value=["Fairness gate FAIL: some metric"],
                ):
                    with patch(
                        "tournament_scheduler.cli.plan_critic.suggest_moves",
                        return_value=[{
                            "tournament_id": "",
                            "new_date": None,
                            "reason": "needs human input",
                            "can_auto_fix": False,
                            "issue": "Fairness gate FAIL: some metric",
                        }],
                    ):
                        with patch(
                            "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.apply",
                            apply_mock,
                        ):
                            tone, _ = _run_refinement_loop(
                                checkpoint, state, args, False, log_calls.append
                            )

        # apply() should NOT be called — no auto-fixable moves
        apply_mock.assert_not_called()

    def test_stops_when_no_critic_issues(self) -> None:
        """Loop exits early when generate_critic_summary returns an empty list."""
        plan_obj = _make_plan_obj(gate_status="fail", gate_score=40, pairwise=0.5, diversity=0.5, month_balance=0.5)
        checkpoint = _make_checkpoint(plan_obj)
        state = _make_state()
        args = _make_args()
        log_calls: list[str] = []

        apply_mock = MagicMock()

        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._compute_verdict_tone",
            return_value="rough",
        ):
            with patch(
                "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.load_plan",
                return_value=plan_obj,
            ):
                with patch(
                    "tournament_scheduler.cli.plan_critic.generate_critic_summary",
                    return_value=[],
                ):
                    with patch(
                        "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.apply",
                        apply_mock,
                    ):
                        tone, _ = _run_refinement_loop(
                            checkpoint, state, args, False, log_calls.append
                        )

        apply_mock.assert_not_called()

    def test_banned_dates_populated_for_moves_without_tournament_id(self) -> None:
        """Moves without tournament_id should have their old_date added to banned_dates."""
        plan_obj = _make_plan_obj(gate_status="fail", gate_score=40, pairwise=0.5, diversity=0.5, month_balance=0.5)
        checkpoint = _make_checkpoint(plan_obj)
        state = _make_state()
        state.read_stage.return_value = checkpoint
        args = _make_args()
        log_calls: list[str] = []

        apply_result = _make_update_result(success=True)

        # Move with no tournament_id but with old_date and new_date — should go to banned_dates
        moves = [{
            "tournament_id": "",
            "new_date": "2026-04-05",
            "old_date": "2026-03-22",
            "reason": "conflict",
            "can_auto_fix": True,
            "issue": "Arena collision",
        }]

        captured_plan_obj: list[Any] = []

        def capture_apply(plan, **kwargs):  # type: ignore[no-untyped-def]
            captured_plan_obj.append(plan)
            return apply_result

        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._compute_verdict_tone",
            side_effect=["rough", "mixed"],
        ):
            with patch(
                "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.load_plan",
                return_value=plan_obj,
            ):
                with patch(
                    "tournament_scheduler.cli.plan_critic.generate_critic_summary",
                    return_value=["Arena collision"],
                ):
                    with patch(
                        "tournament_scheduler.cli.plan_critic.suggest_moves",
                        return_value=moves,
                    ):
                        with patch(
                            "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.apply",
                            side_effect=capture_apply,
                        ):
                            with patch(
                                "tournament_scheduler.pipeline.tournament_updater.TournamentUpdater.write_updated_checkpoint",
                            ):
                                _run_refinement_loop(checkpoint, state, args, False, log_calls.append)

        # apply() must have been called with plan_obj that has banned_dates populated
        assert len(captured_plan_obj) == 1
        adj = getattr(captured_plan_obj[0], "manual_adjustments", {}) or {}
        assert "2026-03-22" in adj.get("banned_dates", []), (
            f"Expected '2026-03-22' in banned_dates, got manual_adjustments={adj!r}"
        )

    def test_move_date_called_directly_for_moves_with_tournament_id(self) -> None:
        """Moves with tournament_id should call TournamentUpdater.move_date directly."""
        from datetime import date

        plan_obj = _make_plan_obj(gate_status="fail", gate_score=40, pairwise=0.5, diversity=0.5, month_balance=0.5)
        checkpoint = _make_checkpoint(plan_obj)
        state = _make_state()
        state.read_stage.return_value = checkpoint
        args = _make_args()
        log_calls: list[str] = []

        apply_result = _make_update_result(success=True)
        move_date_result = MagicMock()
        move_date_result.summary_nb = "moved"

        moves = [{
            "tournament_id": "t42",
            "new_date": "2026-05-10",
            "old_date": "2026-04-27",
            "reason": "conflict",
            "can_auto_fix": True,
            "issue": "Arena collision",
        }]

        move_date_mock = MagicMock(return_value=move_date_result)

        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._compute_verdict_tone",
            side_effect=["rough", "mixed"],
        ):
            with patch(
                "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.load_plan",
                return_value=plan_obj,
            ):
                with patch(
                    "tournament_scheduler.cli.plan_critic.generate_critic_summary",
                    return_value=["Arena collision"],
                ):
                    with patch(
                        "tournament_scheduler.cli.plan_critic.suggest_moves",
                        return_value=moves,
                    ):
                        with patch(
                            "tournament_scheduler.pipeline.tournament_updater.TournamentUpdater.move_date",
                            move_date_mock,
                        ):
                            with patch(
                                "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.apply",
                                return_value=apply_result,
                            ):
                                with patch(
                                    "tournament_scheduler.pipeline.tournament_updater.TournamentUpdater.write_updated_checkpoint",
                                ):
                                    _run_refinement_loop(checkpoint, state, args, False, log_calls.append)

        # move_date should have been called with the correct tournament_id and parsed date
        move_date_mock.assert_called_once()
        call_args = move_date_mock.call_args
        assert call_args[0][0] == "t42"
        assert call_args[0][1] == date(2026, 5, 10)

    def test_apply_skipped_when_no_changes(self) -> None:
        """apply() should be skipped when moves have no old_date and no tournament_id (no-op)."""
        plan_obj = _make_plan_obj(gate_status="fail", gate_score=40, pairwise=0.5, diversity=0.5, month_balance=0.5)
        checkpoint = _make_checkpoint(plan_obj)
        state = _make_state()
        args = _make_args()
        log_calls: list[str] = []

        apply_mock = MagicMock()

        # Move with no tournament_id and no old_date — nothing to ban, nothing to move
        moves = [{
            "tournament_id": "",
            "new_date": "2026-04-05",
            "old_date": None,
            "reason": "conflict",
            "can_auto_fix": True,
            "issue": "some issue",
        }]

        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._compute_verdict_tone",
            return_value="rough",
        ):
            with patch(
                "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.load_plan",
                return_value=plan_obj,
            ):
                with patch(
                    "tournament_scheduler.cli.plan_critic.generate_critic_summary",
                    return_value=["some issue"],
                ):
                    with patch(
                        "tournament_scheduler.cli.plan_critic.suggest_moves",
                        return_value=moves,
                    ):
                        with patch(
                            "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.apply",
                            apply_mock,
                        ):
                            _run_refinement_loop(checkpoint, state, args, False, log_calls.append)

        # apply() should NOT be called — no effective changes were made
        apply_mock.assert_not_called()
        assert any("no effective changes" in msg for msg in log_calls), (
            f"Expected 'no effective changes' in log; got: {log_calls}"
        )

    def test_headless_judge_keep_baseline_holds_without_persisting(self, tmp_path) -> None:
        """Issue #260 Phase 4: a headless judge may decline to persist an
        iteration's trial candidate. apply() still runs (it only computes the
        trial candidate in memory), but write_updated_checkpoint must not be
        called when the judge holds."""
        plan_obj = _make_plan_obj(gate_status="fail", gate_score=40, pairwise=0.5, diversity=0.5, month_balance=0.5)
        checkpoint = _make_checkpoint(plan_obj)
        state = _make_state()
        state.work_dir = str(tmp_path)
        args = _make_args()
        log_calls: list[str] = []

        apply_mock = MagicMock()
        write_checkpoint_mock = MagicMock()
        judge = MagicMock()
        # First call is the continue-decision (must say optimize_plan to reach
        # the trial candidate at all); second is the candidate decision itself.
        judge.judge.side_effect = [
            "optimize_plan\nKeep trying this iteration.",
            "keep_baseline\nNot worth the churn this iteration.",
        ]
        move_date_result = MagicMock()
        move_date_result.summary_nb = "moved"

        with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge
        ):
            with patch(
                "tournament_scheduler.cli.pipeline_orchestrator._compute_verdict_tone",
                return_value="rough",
            ):
                with patch(
                    "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.load_plan",
                    return_value=plan_obj,
                ):
                    with patch(
                        "tournament_scheduler.cli.plan_critic.generate_critic_findings",
                        return_value=[{"category": "collision", "message": "Arena-day collision on 2026-03-01"}],
                    ):
                        with patch(
                            "tournament_scheduler.cli.plan_critic.suggest_moves",
                            return_value=[{
                                "tournament_id": "t1",
                                "new_date": "2026-03-08",
                                "reason": "shift",
                                "can_auto_fix": True,
                                "issue": "Arena-day collision on 2026-03-01",
                            }],
                        ):
                            with patch(
                                "tournament_scheduler.pipeline.tournament_updater.TournamentUpdater.move_date",
                                return_value=move_date_result,
                            ):
                                with patch(
                                    "tournament_scheduler.pipeline.manual_adjustment_workflow.ManualAdjustmentWorkflow.apply",
                                    apply_mock,
                                ):
                                    with patch(
                                        "tournament_scheduler.pipeline.tournament_updater.TournamentUpdater.write_updated_checkpoint",
                                        write_checkpoint_mock,
                                    ):
                                        tone, updated = _run_refinement_loop(
                                            checkpoint, state, args, False, log_calls.append
                                        )

        assert judge.judge.call_count == 2
        apply_mock.assert_called_once()
        write_checkpoint_mock.assert_not_called()
        assert tone == "rough"
        assert updated is checkpoint
        assert any("not persisting this iteration's candidate" in msg for msg in log_calls)


# ---------------------------------------------------------------------------
# _decide_refinement_candidate — unit tests (issue #260 Phase 4:
# cli/plan_critic.py + the refinement-candidate verification boundary)
# ---------------------------------------------------------------------------


class TestDecideRefinementCandidate:
    """Unlike the other three Phase 4 decision gates in this file (which fall
    back to their pre-existing deterministic comparison on any judge
    failure), this one is safe-by-default: a configured-but-failing/
    declining judge holds (does not persist) rather than falling back to
    auto-apply. Only "no judge configured at all" returns "no_judge", and
    even then this function does not itself auto-apply — it leaves that to
    the caller's explicitly-named legacy compatibility path."""

    def test_no_headless_judge_returns_no_judge(self, tmp_path) -> None:
        outcome, reason = _decide_refinement_candidate(
            _worse_candidate(),
            _worse_candidate(),
            None,
            run_id="run-1",
            iteration=1,
            work_dir=str(tmp_path),
            log_fn=lambda _: None,
        )
        assert outcome == "no_judge"

    def test_headless_judge_apply_candidate_is_recorded_and_persists(self, tmp_path) -> None:
        judge = MagicMock()
        judge.judge.return_value = "apply_candidate\nDominates baseline on every metric."

        with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge
        ):
            outcome, reason = _decide_refinement_candidate(
                _worse_candidate(),
                _better_candidate(),
                None,
                run_id="run-1",
                iteration=1,
                work_dir=str(tmp_path),
                log_fn=lambda _: None,
            )

        assert outcome == "persist"
        assert reason == "apply_candidate"

        from tournament_scheduler.pipeline.run_manifest import RunManifest

        manifest = RunManifest(str(tmp_path)).read()
        entry = manifest["decision_log"][-1]
        assert entry["action"]["action_id"] == "apply_candidate"
        assert entry["result"]["accepted"] is True

    def test_headless_judge_keep_baseline_holds(self, tmp_path) -> None:
        judge = MagicMock()
        judge.judge.return_value = "keep_baseline"

        with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge
        ):
            outcome, reason = _decide_refinement_candidate(
                _worse_candidate(),
                _better_candidate(),
                None,
                run_id="run-1",
                iteration=1,
                work_dir=str(tmp_path),
                log_fn=lambda _: None,
            )

        assert outcome == "hold"
        assert reason == "keep_baseline"

    def test_headless_judge_call_failure_holds_and_does_not_auto_apply(self, tmp_path) -> None:
        """Correction from the earlier, narrower version of this gate: a
        configured judge that errors must not silently fall back to
        auto-apply — it holds instead."""
        judge = MagicMock()
        judge.judge.side_effect = RuntimeError("backend unreachable")

        with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge
        ):
            outcome, reason = _decide_refinement_candidate(
                _worse_candidate(),
                _better_candidate(),
                None,
                run_id="run-1",
                iteration=1,
                work_dir=str(tmp_path),
                log_fn=lambda _: None,
            )

        assert outcome == "hold"
        assert reason == "judge_call_failed"

    def test_headless_judge_cannot_bypass_invalid_candidate(self, tmp_path) -> None:
        """An LLM saying apply_candidate cannot persist a candidate that
        fails the verifier — the deterministic validator rejects it
        regardless, and this gate does not fall back to auto-apply either."""
        teams = [_decision_team(f"Club{i}", f"T{i}", "U10") for i in range(1, 5)]
        invalid = {
            "schema_version": 1,
            "tournaments": [
                _decision_tournament("t1", "2026-01-05", "Arena1", "U10", teams),
                _decision_tournament("t2", "2026-01-05", "Arena1", "U10", teams),
            ],
        }
        judge = MagicMock()
        judge.judge.return_value = "apply_candidate\nLooks fine to me."

        with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge
        ):
            outcome, reason = _decide_refinement_candidate(
                _worse_candidate(),
                invalid,
                None,
                run_id="run-1",
                iteration=1,
                work_dir=str(tmp_path),
                log_fn=lambda _: None,
            )

        assert outcome == "hold"
        assert reason == "hard_violation_blocks_action"


# ---------------------------------------------------------------------------
# _refinement_metrics / _decide_continue_refinement — unit tests
# (issue #260 Phase 4: "remove tone classification from control authority")
# ---------------------------------------------------------------------------


class TestRefinementMetrics:
    def test_extracts_underlying_measurements_not_the_tone_bucket(self) -> None:
        plan_obj = _make_plan_obj(gate_status="warn", gate_score=72, pairwise=0.81, diversity=0.77, month_balance=0.85)
        checkpoint = _make_checkpoint(plan_obj)

        metrics = _refinement_metrics(checkpoint)

        assert metrics["fairness_gate_status"] == "warn"
        assert metrics["fairness_gate_score"] == 72
        assert metrics["pairwise_matchup_score"] == 0.81
        assert metrics["diversity_score"] == 0.77
        assert metrics["month_balance_score"] == 0.85
        assert "tone" not in metrics


class TestDecideContinueRefinement:
    """A configured judge sees the underlying metrics, not the rough/mixed/
    strong bucket, and its decision replaces the bucket's hard gate — with
    the bucket-based gate preserved only as an explicitly-named legacy
    fallback when no judge is configured."""

    _METRICS = {
        "fairness_gate_status": "fail",
        "fairness_gate_score": 40.0,
        "pairwise_matchup_score": 0.5,
        "diversity_score": 0.5,
        "month_balance_score": 0.5,
        "game_count_spread": 6,
    }

    def test_no_headless_judge_returns_no_judge(self, tmp_path) -> None:
        outcome, reason = _decide_continue_refinement(
            self._METRICS, run_id="run-1", iteration=1, work_dir=str(tmp_path), log_fn=lambda _: None
        )
        assert outcome == "no_judge"

    def test_headless_judge_optimize_plan_continues(self, tmp_path) -> None:
        judge = MagicMock()
        judge.judge.return_value = "optimize_plan\nStill worth another pass."

        with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge
        ):
            outcome, reason = _decide_continue_refinement(
                self._METRICS, run_id="run-1", iteration=1, work_dir=str(tmp_path), log_fn=lambda _: None
            )

        assert outcome == "continue"
        assert reason == "optimize_plan"

        from tournament_scheduler.pipeline.run_manifest import RunManifest

        manifest = RunManifest(str(tmp_path)).read()
        entry = manifest["decision_log"][-1]
        assert entry["action"]["action_id"] == "optimize_plan"
        assert entry["result"]["accepted"] is True

    def test_headless_judge_keep_baseline_stops(self, tmp_path) -> None:
        judge = MagicMock()
        judge.judge.return_value = "keep_baseline\nGood enough for this run."

        with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge
        ):
            outcome, reason = _decide_continue_refinement(
                self._METRICS, run_id="run-1", iteration=1, work_dir=str(tmp_path), log_fn=lambda _: None
            )

        assert outcome == "stop"
        assert reason == "keep_baseline"

    def test_headless_judge_call_failure_stops_safely(self, tmp_path) -> None:
        judge = MagicMock()
        judge.judge.side_effect = RuntimeError("backend unreachable")

        with patch.dict(os.environ, {**_HARNESS_CLEAN, "RVV_JUDGE_BACKEND": "llm_bridge"}), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge
        ):
            outcome, reason = _decide_continue_refinement(
                self._METRICS, run_id="run-1", iteration=1, work_dir=str(tmp_path), log_fn=lambda _: None
            )

        assert outcome == "stop"
        assert reason == "judge_call_failed"


# ---------------------------------------------------------------------------
# _run_approval_gate — unit tests
# ---------------------------------------------------------------------------


def _make_gate_checkpoint(gate_status: str, gate_score: int = 80) -> dict[str, Any]:
    """Return a minimal plan_checkpoint dict with fairness_gate embedded."""
    plan_dict: dict[str, Any] = {
        "fairness_gate": {"status": gate_status, "score": gate_score},
        "tournaments": [],
    }
    return {"plan": plan_dict, "warnings": []}


def _make_console() -> MagicMock:
    return MagicMock()


class TestRunApprovalGate:
    """Tests for blocking/non-blocking behaviour of _run_approval_gate."""

    # Patch generate_critic_summary at its source module — it is imported lazily
    # inside _run_approval_gate, so patching the orchestrator module directly
    # would not work (the name is not present at module level).
    _CRITIC_PATH = "tournament_scheduler.cli.plan_critic.generate_critic_summary"

    def test_blocks_when_strict_and_fail(self) -> None:
        """strict=True + status='fail' must return False (pipeline blocked)."""
        checkpoint = _make_gate_checkpoint("fail", gate_score=30)
        log_calls: list[str] = []
        with patch(self._CRITIC_PATH, return_value=[]) as _mock_critic:
            result = _run_approval_gate(
                MagicMock(), checkpoint, MagicMock(), strict=True,
                console=_make_console(), log_fn=log_calls.append,
            )
        assert result is False
        assert any("FAILED" in msg for msg in log_calls), (
            f"Expected FAILED in log; got: {log_calls}"
        )

    def test_allows_when_non_strict_and_fail(self) -> None:
        """strict=False + status='fail' must return True (pipeline continues)."""
        checkpoint = _make_gate_checkpoint("fail", gate_score=30)
        log_calls: list[str] = []
        with patch(self._CRITIC_PATH, return_value=[]):
            result = _run_approval_gate(
                MagicMock(), checkpoint, MagicMock(), strict=False,
                console=_make_console(), log_fn=log_calls.append,
            )
        assert result is True

    def test_allows_when_strict_and_warn(self) -> None:
        """strict=True + status='warn' must return True (warn never blocks)."""
        checkpoint = _make_gate_checkpoint("warn", gate_score=65)
        with patch(self._CRITIC_PATH, return_value=["issue A"]):
            result = _run_approval_gate(
                MagicMock(), checkpoint, MagicMock(), strict=True,
                console=_make_console(), log_fn=lambda _: None,
            )
        assert result is True

    def test_allows_when_strict_and_pass(self) -> None:
        """strict=True + status='pass' must return True."""
        checkpoint = _make_gate_checkpoint("pass", gate_score=100)
        with patch(self._CRITIC_PATH, return_value=[]):
            result = _run_approval_gate(
                MagicMock(), checkpoint, MagicMock(), strict=True,
                console=_make_console(), log_fn=lambda _: None,
            )
        assert result is True

    def test_allows_when_non_strict_and_pass(self) -> None:
        """strict=False + status='pass' must return True."""
        checkpoint = _make_gate_checkpoint("pass", gate_score=100)
        with patch(self._CRITIC_PATH, return_value=[]):
            result = _run_approval_gate(
                MagicMock(), checkpoint, MagicMock(), strict=False,
                console=_make_console(), log_fn=lambda _: None,
            )
        assert result is True

    def test_warn_status_calls_critic(self) -> None:
        """Warn status must call generate_critic_summary and surface issues."""
        checkpoint = _make_gate_checkpoint("warn")
        console = _make_console()
        with patch(self._CRITIC_PATH, return_value=["problem X"]) as mock_critic:
            _run_approval_gate(
                MagicMock(), checkpoint, MagicMock(), strict=True,
                console=console, log_fn=lambda _: None,
            )
        mock_critic.assert_called_once()
        # Console should have printed the issue
        printed = " ".join(str(c) for c in console.print.call_args_list)
        assert "problem X" in printed

    def test_none_plan_does_not_raise(self) -> None:
        """If plan_checkpoint has no 'plan' key the gate should still return True."""
        checkpoint: dict[str, Any] = {}
        with patch(self._CRITIC_PATH, return_value=[]):
            result = _run_approval_gate(
                MagicMock(), checkpoint, MagicMock(), strict=True,
                console=_make_console(), log_fn=lambda _: None,
            )
        assert result is True
