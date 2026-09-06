"""Tests for ``rvv-miniputt run --interactive`` (issue #260 Phase 5).

Covers the DecisionContext emission / DecisionAction validation loop added
around the existing ``_run_stageN`` helpers, not the helpers themselves
(already covered elsewhere) — this module mocks them out so tests run fast
and don't touch real pipeline stages.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tournament_scheduler.cli.pipeline_orchestrator import (
    _cmd_run_interactive,
    _decision_summary_for_checkpoint,
    _read_stage3_interactive_state,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus


def _team(club: str, label: str, age_group: str) -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(t_id: str, date_str: str, arena: str, age_group: str, teams: list[dict]) -> dict:
    game_pairs = [(a["label"], b["label"]) for i, a in enumerate(teams) for b in teams[i + 1 :]]
    return {
        "id": t_id,
        "date": date_str,
        "arena": arena,
        "age_group": age_group,
        "host_club": teams[0]["club"] if teams else None,
        "teams": teams,
        "games": [
            {"home": home, "away": away, "parallel_slot": 0, "round_number": 1} for home, away in game_pairs
        ],
    }


def _candidate(seed: int) -> dict:
    """A minimal, verifier-passing candidate (mirrors test_stage3_ab's fixture)."""
    teams = {f"T{i}": _team(f"Club{i}", f"T{i}", "U10") for i in range(1, 9)}
    group_a = [teams["T1"], teams["T2"], teams["T3"], teams["T4"]]
    group_b = [teams["T5"], teams["T6"], teams["T7"], teams["T8"]]
    month = 1 + (seed % 4)
    return {
        "schema_version": 1,
        "tournaments": [
            _tournament(f"t1-{seed}", f"2026-{month:02d}-05", "Arena1", "U10", group_a),
            _tournament(f"t2-{seed}", f"2026-{month:02d}-12", "Arena1", "U10", group_b),
        ],
    }


def _plan_checkpoint(seed: int) -> dict[str, Any]:
    return {"plan": _candidate(seed), "warnings": []}


def _args(**overrides: Any) -> SimpleNamespace:
    base = dict(
        work_dir=None,
        input="input.xlsx",
        export_dir="export",
        resume_from="1",
        non_strict=False,
        force_refresh=False,
        allow_missing_sources=False,
        manual_bookup_login=False,
        manual_bookup_login_timeout=None,
        timestamped_export=True,
        iterations=1,
        interactive=True,
        decision_action=None,
        decision_action_file=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture()
def state(tmp_path) -> PipelineState:
    return PipelineState(str(tmp_path))


class TestDecisionSummaryForCheckpoint:
    def test_stage1_uses_effective_config_not_raw_checkpoint(self):
        # Stage 1's own checkpoint never stores sources/dates (they come
        # from input.xlsx via load_effective_config) — a caller that reads
        # the raw checkpoint instead would silently see sources=0.
        raw_checkpoint = {"teams": [{"club": "A", "label": "A1", "age_group": "U10"}]}
        effective_config = {
            "sources": [{"name": "x"}],
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "age_groups": ["U10"],
            "clubs": ["A"],
        }
        summary = _decision_summary_for_checkpoint(1, raw_checkpoint, effective_config=effective_config)
        assert summary["sources"] == 1
        assert summary["start_date"] == "2026-09-01"

    def test_stage2_counts_blocked_and_scanned(self):
        checkpoint = {"sources": [{"name": "a"}, {"name": "b"}], "blocked": ["b"]}
        summary = _decision_summary_for_checkpoint(2, checkpoint)
        assert summary["sources_scanned"] == 2
        assert summary["blocked"] == ["b"]
        assert summary["source_details"] == checkpoint["sources"]

    def test_stage4_lists_output_file_kinds(self):
        checkpoint = {"output_files": {"excel": "a.xlsx", "ical": "a.ics"}, "errors": []}
        summary = _decision_summary_for_checkpoint(4, checkpoint)
        assert set(summary["files_written"]) == {"excel", "ical"}


class TestCmdRunInteractive:
    def test_emits_valid_decision_context_json(self, state, tmp_path, capsys):
        args = _args(work_dir=str(tmp_path), resume_from="1")
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=({"start_date": "2026-09-01", "end_date": "2027-04-30"}, False),
        ), patch(
            "tournament_scheduler.pipeline.stage1_config.load_effective_config",
            return_value={"sources": [{"name": "x"}], "start_date": "2026-09-01", "end_date": "2027-04-30"},
        ):
            exit_code = _cmd_run_interactive(args)
            out = capsys.readouterr().out

        assert exit_code == 2
        payload = json.loads(out)
        assert payload["capability"] == "config"
        assert payload["available_actions"] == ["proceed", "abort", "retry_stage", "request_operator"]
        assert payload["facts"]["sources"] == 1

    def test_abort_decision_stops_before_target_stage_runs(self, state, tmp_path):
        state.write_stage(StageName.CONFIG, {}, status=StageStatus.DONE)
        args = _args(
            work_dir=str(tmp_path),
            resume_from="2",
            decision_action=json.dumps({"action_id": "abort", "rationale": "test"}),
        )
        with patch(
            "tournament_scheduler.pipeline.stage1_config.load_effective_config",
            return_value={"sources": [], "start_date": "2026-09-01", "end_date": "2027-04-30"},
        ), patch("tournament_scheduler.cli.pipeline_orchestrator._run_stage2") as run_stage2:
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 1
        run_stage2.assert_not_called()

    def test_unknown_action_rejected_before_any_stage_runs(self, state, tmp_path):
        state.write_stage(StageName.CONFIG, {}, status=StageStatus.DONE)
        args = _args(
            work_dir=str(tmp_path),
            resume_from="2",
            decision_action=json.dumps({"action_id": "not_a_real_action"}),
        )
        with patch(
            "tournament_scheduler.pipeline.stage1_config.load_effective_config",
            return_value={"sources": [], "start_date": "2026-09-01", "end_date": "2027-04-30"},
        ), patch("tournament_scheduler.cli.pipeline_orchestrator._run_stage2") as run_stage2:
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 1
        run_stage2.assert_not_called()

    def test_decision_action_without_resume_from_2_or_more_is_rejected(self, state, tmp_path):
        args = _args(
            work_dir=str(tmp_path),
            resume_from="1",
            decision_action=json.dumps({"action_id": "proceed"}),
        )
        exit_code = _cmd_run_interactive(args)
        assert exit_code == 1

    def test_retry_stage_reruns_previous_stage_instead_of_target(self, state, tmp_path):
        state.write_stage(StageName.CONFIG, {}, status=StageStatus.DONE)
        args = _args(
            work_dir=str(tmp_path),
            resume_from="2",
            decision_action=json.dumps({"action_id": "retry_stage", "arguments": {"stage": "1"}}),
        )
        with patch(
            "tournament_scheduler.pipeline.stage1_config.load_effective_config",
            return_value={"sources": [], "start_date": "2026-09-01", "end_date": "2027-04-30"},
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=({"start_date": "2026-09-01", "end_date": "2027-04-30"}, False),
        ) as run_stage1, patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2"
        ) as run_stage2:
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 2
        run_stage1.assert_called_once()
        run_stage2.assert_not_called()

    def test_proceed_decision_records_audit_trail_in_manifest(self, state, tmp_path):
        state.write_stage(StageName.CONFIG, {}, status=StageStatus.DONE)
        args = _args(
            work_dir=str(tmp_path),
            resume_from="2",
            decision_action=json.dumps({"action_id": "proceed", "rationale": "looks fine"}),
        )
        with patch(
            "tournament_scheduler.pipeline.stage1_config.load_effective_config",
            return_value={"sources": [], "start_date": "2026-09-01", "end_date": "2027-04-30"},
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ):
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 2
        manifest_path = tmp_path / "run_manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        decisions = manifest.get("decision_log", [])
        assert len(decisions) == 1
        assert decisions[0]["action"]["action_id"] == "proceed"
        assert decisions[0]["result"]["accepted"] is True


class TestStage2InteractiveZeroEventsDefersToDecisionContext:
    """--interactive must not let _check_stage2_checkpoint pre-empt the
    zero-events sufficiency call with its own hard abort — the harness gets
    to decide via the Stage 2 DecisionContext instead (issue #260 P1)."""

    def test_zero_events_strict_interactive_proceeds_to_checkpoint(self, state, tmp_path):
        from tournament_scheduler.cli.pipeline_orchestrator import _run_stage2

        args = _args(work_dir=str(tmp_path), interactive=True, non_strict=False)
        cfg = {"start_date": "2026-09-01", "end_date": "2027-04-30"}
        zero_events_checkpoint = {
            "sources": [{"name": "a", "event_count": 0, "blocked": False}],
            "blocked": [],
        }
        with patch(
            "tournament_scheduler.pipeline.stage2_scraping.run",
            return_value=zero_events_checkpoint,
        ):
            scraping, abort, stage_failed = _run_stage2(
                args, cfg, state, None, None, True, lambda msg: None, resume_from=2
            )

        assert abort is False
        assert stage_failed is False
        assert scraping == zero_events_checkpoint

    def test_zero_events_strict_non_interactive_unattended_aborts(self, state, tmp_path):
        from tournament_scheduler.cli.pipeline_orchestrator import _run_stage2

        args = _args(work_dir=str(tmp_path), interactive=False, non_strict=False)
        cfg = {"start_date": "2026-09-01", "end_date": "2027-04-30"}
        zero_events_checkpoint = {
            "sources": [{"name": "a", "event_count": 0, "blocked": False}],
            "blocked": [],
        }
        with patch(
            "tournament_scheduler.pipeline.stage2_scraping.run",
            return_value=zero_events_checkpoint,
        ):
            scraping, abort, stage_failed = _run_stage2(
                args, cfg, state, None, None, True, lambda msg: None, resume_from=2
            )

        assert abort is True


class TestStage3InteractiveDecisionLoop:
    """Stage 3's nested optimize_plan/apply_candidate/keep_baseline loop
    (issue #260 P0) — unlike every other stage, which only ever offers the
    coarse proceed/abort-only context."""

    def test_first_attempt_emits_baseline_context(self, state, tmp_path, capsys):
        args = _args(work_dir=str(tmp_path), resume_from="3")
        plan = _plan_checkpoint(seed=1)
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=({"start_date": "2026-09-01", "end_date": "2027-04-30"}, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
            return_value=(plan, False, False),
        ):
            exit_code = _cmd_run_interactive(args)
            out = capsys.readouterr().out

        assert exit_code == 2
        payload = json.loads(out)
        assert payload["capability"] == "stage3_interactive"
        assert set(payload["available_actions"]) == {
            "optimize_plan",
            "keep_baseline",
            "request_operator",
            "abort",
        }
        assert "apply_candidate" not in payload["available_actions"]
        # issue #262 P0: the first-attempt context must already offer the
        # v2 optimizer's schema, not the legacy search_budget one -- the
        # dispatch on a later optimize_plan decision runs the v2 optimizer
        # regardless of which attempt this is.
        assert "weights" in payload["action_parameters"]["optimize_plan"]

        interactive_state = _read_stage3_interactive_state(state)
        assert interactive_state["attempts_used"] == 1
        assert interactive_state["best_plan"] == plan

    def test_optimize_plan_runs_v2_optimizer_not_legacy_stage3(self, state, tmp_path):
        """issue #262 P0: optimize_plan must invoke the generic Stage 3 v2
        optimizer, not rerun the legacy SeasonPlanner via _run_stage3."""
        from tournament_scheduler.cli.pipeline_orchestrator import _write_stage3_interactive_state
        from tournament_scheduler.stage3_ab import build_ab_report
        from tournament_scheduler.stage3_decision import build_stage3_decision_context

        plan1 = _plan_checkpoint(seed=1)
        report = build_ab_report(plan1["plan"], plan1["plan"])
        baseline_context = build_stage3_decision_context(
            report, run_id="", baseline_ref=None, candidate_ref=None,
            optimize_plan_schema="v2_optimizer",
        )
        _write_stage3_interactive_state(
            state,
            {
                "run_id": "legacy",
                "attempts_used": 1,
                "best_attempt": 1,
                "best_plan": plan1,
                "last_context": baseline_context.to_dict(),
            },
        )
        state.write_stage(StageName.PLANNING, plan1, status=StageStatus.DONE)

        plan2_candidate = _candidate(seed=2)
        args = _args(
            work_dir=str(tmp_path),
            resume_from="4",
            decision_action=json.dumps(
                {"action_id": "optimize_plan", "rationale": "try again", "arguments": {"iterations": 500}}
            ),
        )
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=({"start_date": "2026-09-01", "end_date": "2027-04-30"}, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
        ) as run_stage3, patch(
            "tournament_scheduler.stage3_optimizer.optimize_candidate",
            return_value=plan2_candidate,
        ) as optimize_candidate:
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 2
        # optimize_plan must not fall back to the legacy SeasonPlanner rerun.
        run_stage3.assert_not_called()
        optimize_candidate.assert_called_once()
        assert optimize_candidate.call_args.kwargs["iterations"] == 500
        assert optimize_candidate.call_args.args[0] == plan1["plan"]

        interactive_state = _read_stage3_interactive_state(state)
        assert interactive_state["attempts_used"] == 2
        assert interactive_state["best_plan"] == plan1
        assert interactive_state["pending_candidate"]["plan"] == plan2_candidate
        available = interactive_state["last_context"]["available_actions"]
        assert "apply_candidate" in available
        assert "keep_baseline" in available
        # The offered optimize_plan schema must match what actually executes.
        assert "optimize_plan" in interactive_state["last_context"]["action_parameters"]
        assert "weights" in interactive_state["last_context"]["action_parameters"]["optimize_plan"]

    def test_apply_candidate_advances_and_clears_state(self, state, tmp_path):
        from tournament_scheduler.cli.pipeline_orchestrator import _write_stage3_interactive_state
        from tournament_scheduler.stage3_ab import build_ab_report
        from tournament_scheduler.stage3_decision import build_stage3_decision_context

        plan1 = _plan_checkpoint(seed=1)
        plan2 = _plan_checkpoint(seed=2)
        report = build_ab_report(plan1["plan"], plan2["plan"])
        ab_context = build_stage3_decision_context(
            report,
            run_id="",
            baseline_ref="stage3_interactive:attempt_1",
            candidate_ref="stage3_interactive:attempt_2",
        )
        _write_stage3_interactive_state(
            state,
            {
                "run_id": "legacy",
                "attempts_used": 2,
                "best_attempt": 1,
                "best_plan": plan1,
                "pending_candidate": plan2,
                "pending_attempt": 2,
                "last_context": ab_context.to_dict(),
            },
        )
        # On-disk checkpoint currently holds the just-rerun candidate.
        state.write_stage(StageName.PLANNING, plan2, status=StageStatus.DONE)

        args = _args(
            work_dir=str(tmp_path),
            resume_from="4",
            decision_action=json.dumps(
                {"action_id": "apply_candidate", "arguments": {"candidate_ref": "stage3_interactive:attempt_2"}}
            ),
        )
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=({"start_date": "2026-09-01", "end_date": "2027-04-30"}, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
            return_value=(plan2, False, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage4_export",
            return_value=(False, False, False),
        ):
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 2
        assert _read_stage3_interactive_state(state) == {}
        assert state.read_stage(StageName.PLANNING) == plan2

    def test_apply_candidate_writes_run_evidence_bundle(self, state, tmp_path):
        """issue #264 P0: every production export carries an auditable
        decision/search/verification provenance bundle."""
        import json as _json

        from tournament_scheduler.cli.pipeline_orchestrator import (
            _emit_stage3_interactive_decision,
            _write_stage3_interactive_state,
        )
        from tournament_scheduler.stage3_ab import build_ab_report
        from tournament_scheduler.stage3_decision import build_stage3_decision_context

        plan1 = _plan_checkpoint(seed=1)
        plan2 = _plan_checkpoint(seed=2)
        report = build_ab_report(plan1["plan"], plan2["plan"])
        ab_context = build_stage3_decision_context(
            report,
            run_id="",
            baseline_ref="stage3_interactive:attempt_1",
            candidate_ref="stage3_interactive:attempt_2",
        )
        _write_stage3_interactive_state(
            state,
            {
                "run_id": "legacy",
                "attempts_used": 2,
                "best_attempt": 1,
                "best_plan": plan1,
                "pending_candidate": plan2,
                "pending_attempt": 2,
                "last_context": ab_context.to_dict(),
            },
        )
        # Seed the durable per-attempt log as if _emit_stage3_interactive_decision
        # had already recorded both attempts during this run.
        from tournament_scheduler.pipeline.evidence_bundle import (
            append_stage3_attempt_log_entry,
            build_stage3_attempt_entry,
        )

        append_stage3_attempt_log_entry(
            state.work_dir, build_stage3_attempt_entry(attempt=1, candidate=plan1["plan"], problem=None)
        )
        append_stage3_attempt_log_entry(
            state.work_dir, build_stage3_attempt_entry(attempt=2, candidate=plan2["plan"], problem=None)
        )
        state.write_stage(StageName.PLANNING, plan2, status=StageStatus.DONE)

        args = _args(
            work_dir=str(tmp_path),
            resume_from="4",
            decision_action=json.dumps(
                {"action_id": "apply_candidate", "arguments": {"candidate_ref": "stage3_interactive:attempt_2"}}
            ),
        )
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=({"start_date": "2026-09-01", "end_date": "2027-04-30"}, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
            return_value=(plan2, False, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage4_export",
            return_value=(False, False, False),
        ):
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 2
        bundle_path = tmp_path / "evidence_bundle.json"
        assert bundle_path.exists()
        bundle = _json.loads(bundle_path.read_text())
        assert len(bundle["stage3_attempt_log"]) == 2
        assert bundle["final_candidate"] is not None
        assert isinstance(bundle["final_verify_result"], dict)
        assert isinstance(bundle["decision_log"], list)

    def test_keep_baseline_restores_best_plan_and_clears_state(self, state, tmp_path):
        from tournament_scheduler.cli.pipeline_orchestrator import _write_stage3_interactive_state
        from tournament_scheduler.stage3_ab import build_ab_report
        from tournament_scheduler.stage3_decision import build_stage3_decision_context

        plan1 = _plan_checkpoint(seed=1)
        plan2 = _plan_checkpoint(seed=2)
        report = build_ab_report(plan1["plan"], plan2["plan"])
        ab_context = build_stage3_decision_context(
            report,
            run_id="",
            baseline_ref="stage3_interactive:attempt_1",
            candidate_ref="stage3_interactive:attempt_2",
        )
        _write_stage3_interactive_state(
            state,
            {
                "run_id": "legacy",
                "attempts_used": 2,
                "best_attempt": 1,
                "best_plan": plan1,
                "pending_candidate": plan2,
                "pending_attempt": 2,
                "last_context": ab_context.to_dict(),
            },
        )
        # On-disk checkpoint currently holds the just-rejected rerun candidate.
        state.write_stage(StageName.PLANNING, plan2, status=StageStatus.DONE)

        args = _args(
            work_dir=str(tmp_path),
            resume_from="4",
            decision_action=json.dumps({"action_id": "keep_baseline", "rationale": "not better"}),
        )
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=({"start_date": "2026-09-01", "end_date": "2027-04-30"}, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
            return_value=(plan2, False, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage4_export",
            return_value=(False, False, False),
        ):
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 2
        assert _read_stage3_interactive_state(state) == {}
        # The rejected candidate is not what gets exported — the best plan
        # is restored on disk before advancing.
        assert state.read_stage(StageName.PLANNING) == plan1

    def test_optimize_plan_not_offered_past_attempt_cap(self, state, tmp_path):
        from datetime import datetime

        from tournament_scheduler.cli.pipeline_orchestrator import (
            _MAX_INTERACTIVE_STAGE3_ATTEMPTS,
            _emit_stage3_interactive_decision,
            _write_stage3_interactive_state,
        )

        plan1 = _plan_checkpoint(seed=1)
        _write_stage3_interactive_state(
            state,
            {
                "run_id": "legacy",
                "attempts_used": _MAX_INTERACTIVE_STAGE3_ATTEMPTS,
                "best_attempt": 1,
                "best_plan": plan1,
            },
        )

        plan2 = _plan_checkpoint(seed=2)
        exit_code = _emit_stage3_interactive_decision(
            state,
            str(tmp_path),
            {},
            {},
            datetime(2026, 9, 1),
            datetime(2027, 4, 30),
            plan2,
            lambda msg: None,
        )

        assert exit_code == 2
        interactive_state = _read_stage3_interactive_state(state)
        assert "optimize_plan" not in interactive_state["last_context"]["available_actions"]
        assert "apply_candidate" in interactive_state["last_context"]["available_actions"]

    def test_fresh_run_start_ignores_stale_state_from_a_superseded_run(self, state, tmp_path):
        """issue #264 P0: a Stage 3 controller run must not inherit attempt
        counters or a "best plan so far" from a prior/superseded run sharing
        the same work directory -- reproduces the exact symptom reported in
        the issue (a recorded attempt count past _MAX_INTERACTIVE_STAGE3_ATTEMPTS,
        left over from an earlier/aborted run in the same work_dir)."""
        from tournament_scheduler.cli.pipeline_orchestrator import (
            _MAX_INTERACTIVE_STAGE3_ATTEMPTS,
            _write_stage3_interactive_state,
        )
        from tournament_scheduler.pipeline.run_manifest import RunManifest

        # Simulate leftover state from a previous, different run in this
        # same work directory: a run_id that won't match the fresh run's,
        # and an attempt count already past the cap.
        RunManifest(str(tmp_path)).start_run("old run", run_id="stale-old-run")
        stale_best_plan = _plan_checkpoint(seed=99)
        _write_stage3_interactive_state(
            state,
            {
                "run_id": "stale-old-run",
                "attempts_used": _MAX_INTERACTIVE_STAGE3_ATTEMPTS + 2,
                "best_attempt": 1,
                "best_plan": stale_best_plan,
            },
        )

        # Stage 1 of a genuinely new run: resume_from=1, no decision to
        # answer yet -- this is the fresh-start signal.
        args1 = _args(work_dir=str(tmp_path), resume_from="1")
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=({"start_date": "2026-09-01", "end_date": "2027-04-30"}, False),
        ), patch(
            "tournament_scheduler.pipeline.stage1_config.load_effective_config",
            return_value={"sources": [], "start_date": "2026-09-01", "end_date": "2027-04-30"},
        ):
            exit_code = _cmd_run_interactive(args1)
        assert exit_code == 2

        new_run_id = RunManifest(str(tmp_path)).read()["run_id"]
        assert new_run_id != "stale-old-run"
        # The stale side-file must already be gone, not merely shadowed.
        assert not (tmp_path / "stage3_interactive_state.json").exists()

        # Answer Stage 2's context (prev_stage_num=2) to advance into Stage 3
        # of this same (new) run -- a fresh manifest doesn't wipe checkpoints
        # already on disk from earlier in this same run.
        state.write_stage(StageName.SCRAPING, {"sources": [], "blocked": []}, status=StageStatus.DONE)
        plan_this_run = _plan_checkpoint(seed=1)
        args2 = _args(
            work_dir=str(tmp_path),
            resume_from="3",
            decision_action=json.dumps({"action_id": "proceed"}),
        )
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=({"start_date": "2026-09-01", "end_date": "2027-04-30"}, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
            return_value=(plan_this_run, False, False),
        ):
            exit_code = _cmd_run_interactive(args2)
        assert exit_code == 2

        interactive_state = _read_stage3_interactive_state(state)
        assert interactive_state["run_id"] == new_run_id
        assert interactive_state["attempts_used"] == 1
        assert interactive_state["best_plan"] == plan_this_run
        assert "optimize_plan" in interactive_state["last_context"]["available_actions"]
