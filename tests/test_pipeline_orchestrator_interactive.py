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
    """A minimal, verifier-passing candidate (mirrors test_stage3_ab's fixture).

    Months are chosen from the 2026-09-01..2027-04-30 config window used
    throughout this file's fixtures (see ``_args``/effective-config helpers)
    so the full hard verifier (including ``date_outside_window``) passes,
    not just the self-consistency-only checks (issue #264 real-run
    finding's Stage 4 hard-verification gate).
    """
    teams = {f"T{i}": _team(f"Club{i}", f"T{i}", "U10") for i in range(1, 9)}
    group_a = [teams["T1"], teams["T2"], teams["T3"], teams["T4"]]
    group_b = [teams["T5"], teams["T6"], teams["T7"], teams["T8"]]
    month = 9 + (seed % 4)
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

    def test_first_attempt_automatically_evaluates_cp_sat_shadow(self, state, tmp_path, capsys):
        """issue #288: "CP-SAT shadow evaluation must be reachable
        automatically from `/rvv-miniputt:run`; the operator should not need
        to know or invoke `plan ab --engine cp-sat`" -- the very first Stage
        3 baseline decision must already carry CP-SAT shadow evidence in its
        facts, with no optimize_plan(engine=...) request from the caller."""
        from tournament_scheduler.pipeline.evidence_bundle import read_stage3_attempt_log

        args = _args(work_dir=str(tmp_path), resume_from="3")
        plan = _plan_checkpoint(seed=1)
        shadow_candidate = dict(_candidate(seed=2))
        shadow_candidate["source"] = {"planner": "cp_sat", "status": "OPTIMAL", "runtime_seconds": 0.4}
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=({"start_date": "2026-09-01", "end_date": "2027-04-30"}, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
            return_value=(plan, False, False),
        ), patch(
            "tournament_scheduler.stage3_cpsat.optimize_candidate_cp_sat",
            return_value=shadow_candidate,
        ) as optimize_candidate_cp_sat:
            exit_code = _cmd_run_interactive(args)
            out = capsys.readouterr().out

        assert exit_code == 2
        optimize_candidate_cp_sat.assert_called_once()
        # issue #298 Phase 2/3: the automatic shadow evaluation must use the
        # per-half decomposed model, not the monolithic Oct-Apr model that
        # produced the original UNKNOWN evidence, so quality-mode search gets
        # the smaller per-half search space Phase 2 built.
        assert optimize_candidate_cp_sat.call_args.kwargs["decompose_by_half"] is True
        payload = json.loads(out)
        shadow_facts = payload["facts"]["cp_sat_shadow"]
        assert shadow_facts["attempted"] is True
        assert shadow_facts["available"] is True
        assert shadow_facts["candidate_source"]["planner"] == "cp_sat"

        attempt_log = read_stage3_attempt_log(state.work_dir)
        assert any(entry.get("engine") == "cp_sat_shadow" for entry in attempt_log)

    def test_first_attempt_continues_safely_when_cp_sat_unavailable(self, state, tmp_path, capsys):
        """issue #288: "Solver failure/timeout/unavailable dependency must
        degrade safely inside `/run` ... never break the production
        workflow unnecessarily" -- a missing OR-Tools dependency must not
        stop the normal Stage 3 decision from being emitted."""
        from tournament_scheduler.stage3_cpsat import CpSatUnavailable

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
        ), patch(
            "tournament_scheduler.stage3_cpsat.optimize_candidate_cp_sat",
            side_effect=CpSatUnavailable("OR-Tools not installed"),
        ):
            exit_code = _cmd_run_interactive(args)
            out = capsys.readouterr().out

        assert exit_code == 2
        payload = json.loads(out)
        assert payload["capability"] == "stage3_interactive"
        assert set(payload["available_actions"]) >= {"optimize_plan", "keep_baseline"}
        shadow_facts = payload["facts"]["cp_sat_shadow"]
        assert shadow_facts["attempted"] is True
        assert shadow_facts["available"] is False
        assert shadow_facts["error"]["type"] == "CpSatUnavailable"

    def test_first_attempt_continues_safely_when_cp_sat_times_out(self, state, tmp_path, capsys):
        """issue #288: distinct from the "OR-Tools not installed" case above
        -- the solver being *available* but exhausting its budget without a
        feasible candidate (``CpSatNoCandidate`` with an ``UNKNOWN``/timeout
        status) must degrade the same way: the normal Stage 3 decision still
        gets emitted, with the timeout recorded as shadow evidence rather
        than interrupting `/run`."""
        from tournament_scheduler.stage3_cpsat import CpSatNoCandidate

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
        ), patch(
            "tournament_scheduler.stage3_cpsat.optimize_candidate_cp_sat",
            side_effect=CpSatNoCandidate("UNKNOWN", 15.0),
        ):
            exit_code = _cmd_run_interactive(args)
            out = capsys.readouterr().out

        assert exit_code == 2
        payload = json.loads(out)
        assert payload["capability"] == "stage3_interactive"
        assert set(payload["available_actions"]) >= {"optimize_plan", "keep_baseline"}
        shadow_facts = payload["facts"]["cp_sat_shadow"]
        assert shadow_facts["attempted"] is True
        assert shadow_facts["available"] is True
        assert shadow_facts["error"]["type"] == "CpSatNoCandidate"
        assert shadow_facts["error"]["status"] == "UNKNOWN"
        assert shadow_facts["error"]["runtime_seconds"] == 15.0

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

    def test_optimize_plan_engine_cp_sat_routes_through_engine_boundary(self, state, tmp_path):
        """issue #276: optimize_plan(arguments={"engine": "cp_sat"}) must
        dispatch through stage3_engine.run_planner to the CP-SAT shadow
        optimizer, and the resulting pending candidate's source must record
        which engine actually produced it."""
        from tournament_scheduler.cli.pipeline_orchestrator import _write_stage3_interactive_state
        from tournament_scheduler.pipeline.evidence_bundle import read_stage3_attempt_log
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

        plan2_candidate = dict(_candidate(seed=2))
        plan2_candidate["source"] = {"planner": "cp_sat", "status": "OPTIMAL", "runtime_seconds": 1.2}
        args = _args(
            work_dir=str(tmp_path),
            resume_from="4",
            decision_action=json.dumps(
                {
                    "action_id": "optimize_plan",
                    "rationale": "try CP-SAT shadow engine",
                    "arguments": {"engine": "cp_sat", "solve_budget_seconds": 5},
                }
            ),
        )
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=(
                {"start_date": "2026-09-01", "end_date": "2027-04-30", "cp_sat_shadow_enabled": False},
                False,
            ),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
        ) as run_stage3, patch(
            "tournament_scheduler.stage3_cpsat.optimize_candidate_cp_sat",
            return_value=plan2_candidate,
        ) as optimize_candidate_cp_sat:
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 2
        run_stage3.assert_not_called()
        # issue #288: automatic CP-SAT shadow evaluation is disabled above
        # (cp_sat_shadow_enabled: False) so this call is unambiguously the
        # explicit optimize_plan(engine="cp_sat") dispatch under test.
        optimize_candidate_cp_sat.assert_called_once()
        assert optimize_candidate_cp_sat.call_args.kwargs["solve_budget_seconds"] == 5
        assert optimize_candidate_cp_sat.call_args.args[0] == plan1["plan"]

        interactive_state = _read_stage3_interactive_state(state)
        assert interactive_state["pending_candidate"]["plan"]["source"]["planner"] == "cp_sat"

        attempt_log = read_stage3_attempt_log(state.work_dir)
        assert any(
            isinstance(entry.get("candidate_source"), dict)
            and entry["candidate_source"].get("planner") == "cp_sat"
            for entry in attempt_log
        )

    def test_optimize_plan_engine_cp_sat_failure_preserves_baseline(self, state, tmp_path):
        """issue #276: a CP-SAT shadow-mode failure (no OR-Tools, or the
        solver timing out without a feasible candidate) must never abort the
        run or silently publish a solver candidate -- the existing best plan
        stays the Stage 3 checkpoint, and the failure is recorded as an
        attempt-log evidence entry."""
        from tournament_scheduler.cli.pipeline_orchestrator import _write_stage3_interactive_state
        from tournament_scheduler.pipeline.evidence_bundle import read_stage3_attempt_log
        from tournament_scheduler.pipeline.state import StageName as _StageName
        from tournament_scheduler.stage3_ab import build_ab_report
        from tournament_scheduler.stage3_cpsat import CpSatNoCandidate
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

        args = _args(
            work_dir=str(tmp_path),
            resume_from="4",
            decision_action=json.dumps(
                {
                    "action_id": "optimize_plan",
                    "rationale": "try CP-SAT shadow engine",
                    "arguments": {"engine": "cp_sat"},
                }
            ),
        )
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=(
                {"start_date": "2026-09-01", "end_date": "2027-04-30", "cp_sat_shadow_enabled": False},
                False,
            ),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
        ) as run_stage3, patch(
            "tournament_scheduler.stage3_cpsat.optimize_candidate_cp_sat",
            side_effect=CpSatNoCandidate("INFEASIBLE", 5.0),
        ):
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 2
        run_stage3.assert_not_called()

        # The on-disk Stage 3 checkpoint must still be the unchanged baseline.
        checkpoint = state.read_stage(_StageName.PLANNING)
        assert checkpoint == plan1

        attempt_log = read_stage3_attempt_log(state.work_dir)
        assert any(
            entry.get("engine") == "cp_sat" and entry.get("engine_error", {}).get("type") == "CpSatNoCandidate"
            for entry in attempt_log
        )

    def test_optimize_plan_pareto_mode_offers_a_candidate_choice(self, state, tmp_path):
        """issue #264 P1 / issue #265 P1: optimize_plan(mode="pareto") runs
        the shared multi-objective search and offers every non-dominated
        candidate by candidate_ref, instead of a single rerun."""
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

        candidate_a = _candidate(seed=2)
        candidate_b = _candidate(seed=3)
        fake_portfolio_result = {
            "schema_version": 1,
            "baseline_objective_vector": {"max_pair_repeat": 2.0},
            "baseline_score": {},
            "candidates": [
                {
                    "candidate": candidate_a,
                    "objective_vector": {"max_pair_repeat": 0.0},
                    "score": {"opponent_diversity": {}},
                    "weights_used": {"pair_repeat": 15.0},
                    "epoch": 0,
                    "verify_result": {"ok": True, "violations": []},
                    "dominates_baseline": True,
                },
                {
                    "candidate": candidate_b,
                    "objective_vector": {"max_pair_repeat": 1.0},
                    "score": {"opponent_diversity": {}},
                    "weights_used": {"gap_under_7": 25.0},
                    "epoch": 1,
                    "verify_result": {"ok": True, "violations": []},
                    "dominates_baseline": True,
                },
            ],
            "search_summary": {"epochs": 2, "epoch_summaries": [], "archive_size": 2},
        }

        args = _args(
            work_dir=str(tmp_path),
            resume_from="4",
            decision_action=json.dumps(
                {"action_id": "optimize_plan", "rationale": "explore tradeoffs", "arguments": {"mode": "pareto"}}
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
            "tournament_scheduler.stage3_optimizer.optimize_candidate_pareto",
            return_value=fake_portfolio_result,
        ) as optimize_candidate_pareto:
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 2
        run_stage3.assert_not_called()
        optimize_candidate_pareto.assert_called_once()
        assert optimize_candidate_pareto.call_args.args[0] == plan1["plan"]

        interactive_state = _read_stage3_interactive_state(state)
        assert interactive_state["attempts_used"] == 2
        assert interactive_state["best_plan"] == plan1
        pending = interactive_state["pending_candidates"]
        assert len(pending) == 2
        refs = [entry["candidate_ref"] for entry in pending]
        assert refs == ["pareto:2:0", "pareto:2:1"]

        last_context = interactive_state["last_context"]
        assert last_context["capability"] == "stage3_pareto"
        assert len(last_context["facts"]["candidates"]) == 2
        assert last_context["action_parameters"]["apply_candidate"]["candidate_ref"]["enum"] == refs

    def test_apply_candidate_from_pareto_portfolio_writes_chosen_candidate(self, state, tmp_path):
        """issue #264 P1: apply_candidate against a Pareto portfolio must
        write the *chosen* candidate_ref's candidate, not whatever happens
        to already be on disk (there is no single "just reran" candidate
        for a multi-candidate Pareto attempt)."""
        from tournament_scheduler.cli.pipeline_orchestrator import _write_stage3_interactive_state
        from tournament_scheduler.stage3_decision import STAGE3_DECISION_ACTIONS

        plan1 = _plan_checkpoint(seed=1)
        candidate_a = _candidate(seed=2)
        candidate_b = _candidate(seed=3)
        pending_candidates = [
            {
                "candidate": candidate_a,
                "objective_vector": {"max_pair_repeat": 0.0},
                "weights_used": {"pair_repeat": 15.0},
                "candidate_ref": "pareto:2:0",
            },
            {
                "candidate": candidate_b,
                "objective_vector": {"max_pair_repeat": 1.0},
                "weights_used": {"gap_under_7": 25.0},
                "candidate_ref": "pareto:2:1",
            },
        ]
        from tournament_scheduler.application.decisions import DecisionContext

        pareto_context = DecisionContext(
            run_id="legacy",
            capability="stage3_pareto",
            stage="planning",
            objective="pick one",
            available_actions=tuple(STAGE3_DECISION_ACTIONS),
            action_parameters={
                "apply_candidate": {
                    "candidate_ref": {"type": "string", "enum": ["pareto:2:0", "pareto:2:1"]}
                }
            },
        )
        _write_stage3_interactive_state(
            state,
            {
                "run_id": "legacy",
                "attempts_used": 2,
                "best_attempt": 1,
                "best_plan": plan1,
                "pending_candidates": pending_candidates,
                "pending_attempt": 2,
                "last_context": pareto_context.to_dict(),
            },
        )
        # On-disk checkpoint still holds the pre-search baseline -- the
        # chosen candidate must be written explicitly by apply_candidate.
        state.write_stage(StageName.PLANNING, plan1, status=StageStatus.DONE)

        args = _args(
            work_dir=str(tmp_path),
            resume_from="4",
            decision_action=json.dumps(
                {"action_id": "apply_candidate", "arguments": {"candidate_ref": "pareto:2:1"}}
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
            return_value=(plan1, False, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage4_export",
            return_value=(False, False, False),
        ):
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 2
        assert _read_stage3_interactive_state(state) == {}
        assert state.read_stage(StageName.PLANNING)["plan"] == candidate_b

    def test_apply_candidate_advances_and_clears_state(self, state, tmp_path):
        import copy

        from tournament_scheduler.cli.pipeline_orchestrator import _write_stage3_interactive_state
        from tournament_scheduler.stage3_ab import build_ab_report
        from tournament_scheduler.stage3_decision import build_stage3_decision_context

        plan1 = _plan_checkpoint(seed=1)
        plan2 = _plan_checkpoint(seed=2)
        expected_plan2 = copy.deepcopy(plan2)
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
        assert state.read_stage(StageName.PLANNING) == expected_plan2

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


def _joint_club_cfg(**overrides: Any) -> dict[str, Any]:
    """A config with one shared/joint-club registration (issue #274),
    resolved against real dates safely in the future so
    `compute_shared_registration_facts`'s probe pass never hits the
    past-date clamp regardless of when this test runs.
    """
    from datetime import date, timedelta

    start = date.today() + timedelta(days=60)
    end = start + timedelta(days=150)
    clubs = ["Kongsberg", "Skien", "Ringerike", "Tønsberg", "Frisk Asker", "Sandefjord Penguins", "Jar"]
    teams = [{"club": c, "label": f"{c} U10A", "age_group": "U10"} for c in clubs]
    teams.append({"club": "Kongsberg/Tønsberg", "label": "Kongsberg/Tønsberg U10", "age_group": "U10"})
    cfg = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "age_groups": ["U10"],
        "parallel_games": {"U10": 2},
        "teams": teams,
    }
    cfg.update(overrides)
    return cfg


class TestSharedHostInteractiveDecision:
    """issue #274: a shared/joint-club hosting decision must pause for the
    interactive harness the same way every other Stage 3 decision point
    does — not silently fall back to the deterministic heuristic just
    because a harness is active (issue #260's closed "harness-active vs
    headless must differ by transport, not decision rules" mandate).
    """

    def test_pending_decision_pauses_before_stage3_runs(self, state, tmp_path):
        cfg = _joint_club_cfg()
        args = _args(work_dir=str(tmp_path), resume_from="3")

        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=(cfg, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=None,
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
        ) as run_stage3:
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 2
        run_stage3.assert_not_called()

        from tournament_scheduler.cli.pipeline_orchestrator import _read_shared_host_state

        shared_state = _read_shared_host_state(state)
        pending = shared_state["pending"]
        assert pending == {"registration": "Kongsberg/Tønsberg", "age_group": "U10"}
        assert shared_state["last_context"]["capability"] == "shared_host_assignment"
        assert set(shared_state["last_context"]["available_actions"]) == {
            "assign_shared_host", "request_operator",
        }

    def test_answering_the_decision_resumes_and_threads_it_into_stage3(self, state, tmp_path):
        cfg = _joint_club_cfg()

        # First call: reach the pause and persist shared_host_decision_state.json.
        args1 = _args(work_dir=str(tmp_path), resume_from="3")
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=(cfg, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=None,
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
        ):
            assert _cmd_run_interactive(args1) == 2

        # Second call: harness answers assign_shared_host -> Tønsberg. No
        # other joint registration is pending, so this must fall straight
        # through into Stage 3 within the same invocation (no extra round
        # trip), carrying the decision in shared_host_decisions.
        plan = _plan_checkpoint(seed=1)
        args2 = _args(
            work_dir=str(tmp_path),
            resume_from="3",
            decision_action=json.dumps({
                "action_id": "assign_shared_host",
                "arguments": {"chosen_club": "Tønsberg"},
                "rationale": "Kongsberg already hosts materially more this season.",
            }),
        )
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=(cfg, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=None,
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
            return_value=(plan, False, False),
        ) as run_stage3:
            exit_code = _cmd_run_interactive(args2)

        assert exit_code == 2  # advances into Stage 3's own promote/reject context
        run_stage3.assert_called_once()
        threaded = run_stage3.call_args.kwargs["shared_host_decisions"]
        assert len(threaded) == 1
        assert threaded[0]["registration"] == "Kongsberg/Tønsberg"
        assert threaded[0]["age_group"] == "U10"
        assert threaded[0]["chosen_club"] == "Tønsberg"
        assert threaded[0]["decided_by"] == "harness"
        assert "Kongsberg already hosts materially more" in threaded[0]["rationale"]

        from tournament_scheduler.cli.pipeline_orchestrator import _read_shared_host_state

        # Resolved -- the pause-tracking state was cleared, not merely
        # left with pending=None (compute_shared_registration_facts must
        # not be asked about this registration again on a future resume).
        assert _read_shared_host_state(state) == {}

    def test_shared_host_resolution_falls_through_to_stage3_context_with_cp_sat_shadow(
        self, state, tmp_path,
    ):
        """issue #289: reproduces the exact production sequence -- Stage 2 ->
        shared-host pause -> accepted decision -> fresh Stage 3 -- and pins
        that the same invocation's printed `DecisionContext` is a genuine
        `stage3_interactive` context (not a reused/stale checkpoint) whose
        `facts` already contain `cp_sat_shadow`, without a second round
        trip. This is what the Claude adapter's `run.md` capability-branch
        table (`--resume-from 3` for `shared_host_assignment`, `4` only once
        `capability` is `stage3_interactive`/`stage3_pareto`) exists to get
        right; a harness that instead special-cases "Stage 3" text rather
        than the printed `capability` field is the bug this test guards
        against.
        """
        cfg = _joint_club_cfg()

        # First call: reach the shared-host pause.
        args1 = _args(work_dir=str(tmp_path), resume_from="3")
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=(cfg, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=None,
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
        ):
            assert _cmd_run_interactive(args1) == 2

        # Second call: answer assign_shared_host at the SAME --resume-from 3
        # (the shared-host exception) and let it fall through into a fresh
        # Stage 3 run within this one invocation. The automatic CP-SAT
        # shadow evaluation (issue #288) is stubbed so the test stays fast
        # and deterministic while still proving it lands in `facts`.
        plan = _plan_checkpoint(seed=1)
        shadow_evidence = {"engine": "cp_sat", "status": "ok", "fingerprint": "shadow-fp-1"}
        args2 = _args(
            work_dir=str(tmp_path),
            resume_from="3",
            decision_action=json.dumps({
                "action_id": "assign_shared_host",
                "arguments": {"chosen_club": "Tønsberg"},
                "rationale": "Kongsberg already hosts materially more this season.",
            }),
        )
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=(cfg, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=None,
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
            return_value=(plan, False, False),
        ) as run_stage3, patch(
            "tournament_scheduler.cli.pipeline_orchestrator._maybe_run_stage3_cp_sat_shadow",
            return_value=shadow_evidence,
        ) as cp_sat_shadow_mock:
            exit_code = _cmd_run_interactive(args2)

        assert exit_code == 2
        run_stage3.assert_called_once()
        cp_sat_shadow_mock.assert_called_once()

        interactive_state = _read_stage3_interactive_state(state)
        fresh_context = interactive_state["last_context"]
        # Not Stage 4, not a reused baseline -- a genuine fresh Stage 3
        # decision context produced within this same invocation.
        assert fresh_context["capability"] == "stage3_interactive"
        assert fresh_context["facts"]["cp_sat_shadow"] == shadow_evidence

        from tournament_scheduler.cli.pipeline_orchestrator import _read_shared_host_state

        assert _read_shared_host_state(state) == {}

    def test_rejected_decision_action_does_not_advance(self, state, tmp_path):
        cfg = _joint_club_cfg()
        args1 = _args(work_dir=str(tmp_path), resume_from="3")
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=(cfg, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=None,
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
        ):
            assert _cmd_run_interactive(args1) == 2

        # A non-constituent chosen_club must be rejected deterministically,
        # not silently accepted or misapplied.
        args2 = _args(
            work_dir=str(tmp_path),
            resume_from="3",
            decision_action=json.dumps({
                "action_id": "assign_shared_host",
                "arguments": {"chosen_club": "Jar"},
            }),
        )
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=(cfg, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=None,
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
        ) as run_stage3:
            exit_code = _cmd_run_interactive(args2)

        assert exit_code == 1
        run_stage3.assert_not_called()

        from tournament_scheduler.cli.pipeline_orchestrator import _read_shared_host_state

        # Still pending -- a rejected action must not consume the decision.
        assert _read_shared_host_state(state)["pending"] == {
            "registration": "Kongsberg/Tønsberg", "age_group": "U10",
        }

    def test_headless_judge_auto_resolves_without_pausing(self, state, tmp_path):
        """The non-interactive path (`interactive=False`, used by `_cmd_run`
        for the auto-confirmed `run`/`publish` command) auto-calls a
        configured headless judge instead of ever pausing."""
        from datetime import datetime

        from tournament_scheduler.cli.pipeline_orchestrator import _resolve_shared_host_decisions

        judge_mock = MagicMock()
        judge_mock.judge.return_value = "assign_shared_host\nTønsberg\nfairness rationale"
        cfg = _joint_club_cfg()
        start = datetime.fromisoformat(cfg["start_date"])
        end = datetime.fromisoformat(cfg["end_date"])

        with patch("tournament_scheduler.llm_judge.get_judge_if_headless", return_value=judge_mock):
            pause_code, decisions = _resolve_shared_host_decisions(
                state, cfg, {}, start, end, lambda msg: None, interactive=False,
            )

        assert pause_code is None
        assert judge_mock.judge.call_count == 1
        assert len(decisions) == 1
        assert decisions[0]["registration"] == "Kongsberg/Tønsberg"
        assert decisions[0]["chosen_club"] == "Tønsberg"
        assert decisions[0]["decided_by"] == "llm"

        from tournament_scheduler.cli.pipeline_orchestrator import _read_shared_host_state

        # Fully resolved -- no leftover pending/side-state for next run.
        assert _read_shared_host_state(state) == {}

    def test_no_judge_and_non_interactive_falls_back_without_pausing(self, state, tmp_path):
        """`_cmd_run` (never interactive) with no headless judge configured:
        matches every other decision point's no-judge legacy fallback --
        empty decisions, host_assignment.py's deterministic order applies,
        no pause (there is nothing to pause for in this entrypoint)."""
        from datetime import datetime

        from tournament_scheduler.cli.pipeline_orchestrator import _resolve_shared_host_decisions

        cfg = _joint_club_cfg()
        start = datetime.fromisoformat(cfg["start_date"])
        end = datetime.fromisoformat(cfg["end_date"])

        with patch("tournament_scheduler.llm_judge.get_judge_if_headless", return_value=None):
            pause_code, decisions = _resolve_shared_host_decisions(
                state, cfg, {}, start, end, lambda msg: None, interactive=False,
            )

        assert pause_code is None
        assert decisions == []


class TestStage4StaleCheckpointGuard:
    """issue #290: a Stage 3 checkpoint file existing on disk is not proof
    it belongs to the active run's own finished planning/decision loop --
    ``PipelineState.write_stage(..., status=DONE)`` already marks every
    downstream checkpoint ``stale`` (``_invalidate_downstream``) whenever
    an earlier stage actually (re)runs, but nothing consulted that flag
    before treating a skipped Stage 3 as good enough to export. These tests
    pin the independent safety invariant added on top of the #289 harness
    sequencing fix: Stage 4 must refuse to export from a stale checkpoint
    even if the adapter/harness makes the same resume mistake again.
    """

    def test_stale_checkpoint_blocks_stage3_skip(self, state, tmp_path):
        from datetime import datetime

        from tournament_scheduler.cli.pipeline_orchestrator import _run_stage3

        stale_plan = _plan_checkpoint(seed=99)
        state.write_stage(StageName.PLANNING, stale_plan, status=StageStatus.DONE)
        # Simulate this run's own Stage 1 actually (re)running fresh --
        # exactly what invalidates the leftover Stage 3 checkpoint above.
        state.write_stage(StageName.CONFIG, {"start_date": "2026-09-01", "end_date": "2027-04-30"}, status=StageStatus.DONE)
        assert state.is_stale(StageName.PLANNING)

        args = _args(work_dir=str(tmp_path), resume_from="4")
        plan, abort, run_failed = _run_stage3(
            args, {}, {}, state,
            datetime.strptime("2026-09-01", "%Y-%m-%d"), datetime.strptime("2027-04-30", "%Y-%m-%d"),
            strict=True, resume_from=4, log_fn=lambda msg: None,
        )

        assert abort is True
        assert plan is None
        assert run_failed is False

    def test_fresh_checkpoint_allows_stage3_skip(self, state, tmp_path):
        """A checkpoint written DONE by this same run's own Stage 3 pass
        (e.g. via the interactive decision loop's keep_baseline/
        apply_candidate finalization) is not stale and must still resume
        cleanly into Stage 4 -- the guard must not block legitimate
        multi-invocation resume within the same logical run."""
        from datetime import datetime

        from tournament_scheduler.cli.pipeline_orchestrator import _run_stage3

        fresh_plan = _plan_checkpoint(seed=1)
        state.write_stage(StageName.PLANNING, fresh_plan, status=StageStatus.DONE)
        assert not state.is_stale(StageName.PLANNING)

        args = _args(work_dir=str(tmp_path), resume_from="4")
        plan, abort, run_failed = _run_stage3(
            args, {}, {}, state,
            datetime.strptime("2026-09-01", "%Y-%m-%d"), datetime.strptime("2027-04-30", "%Y-%m-%d"),
            strict=True, resume_from=4, log_fn=lambda msg: None,
        )

        assert abort is False
        assert run_failed is False
        assert plan == fresh_plan

    def test_shared_host_resolved_but_wrongly_resumed_to_stage4_is_blocked(self, state, tmp_path):
        """Reproduces the #289 production bug shape at #290's independent
        safety boundary: even if a harness answers the shared-host decision
        correctly but then (incorrectly) advances straight to
        ``--resume-from 4`` instead of keeping ``--resume-from 3``, Stage 4
        must not export -- regardless of a valid-looking old Stage 3
        checkpoint sitting on disk from an earlier run.
        """
        cfg = _joint_club_cfg()

        # A previous run's finished (and otherwise perfectly valid) Stage 3
        # checkpoint is already on disk in this work directory.
        stale_plan = _plan_checkpoint(seed=99)
        state.write_stage(StageName.PLANNING, stale_plan, status=StageStatus.DONE)
        # This run's own Stage 1 has (per the harness) already run fresh --
        # invalidates the leftover checkpoint above, same as production.
        state.write_stage(StageName.CONFIG, cfg, status=StageStatus.DONE)
        assert state.is_stale(StageName.PLANNING)

        # First call: reach the shared-host pause (mirrors
        # TestSharedHostInteractiveDecision's pattern).
        args1 = _args(work_dir=str(tmp_path), resume_from="3")
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=(cfg, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=None,
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage3",
        ):
            assert _cmd_run_interactive(args1) == 2

        # Second call: harness answers assign_shared_host correctly but
        # makes the #289-shaped mistake of jumping to --resume-from 4
        # instead of staying at 3. _run_stage3 is intentionally left
        # unmocked here so the new stale-checkpoint guard actually runs.
        args2 = _args(
            work_dir=str(tmp_path),
            resume_from="4",
            decision_action=json.dumps({
                "action_id": "assign_shared_host",
                "arguments": {"chosen_club": "Tønsberg"},
                "rationale": "Kongsberg already hosts materially more this season.",
            }),
        )
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage1",
            return_value=(cfg, False),
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage2",
            return_value=({"sources": [], "blocked": []}, False, False),
        ), patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless", return_value=None,
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage4_export",
        ) as run_stage4_export:
            exit_code = _cmd_run_interactive(args2)

        assert exit_code == 1
        run_stage4_export.assert_not_called()
        # The stale checkpoint on disk is untouched/unexported.
        assert state.read_stage(StageName.PLANNING) == stale_plan

    def test_keep_baseline_finalization_clears_staleness_and_allows_export(self, state, tmp_path):
        """Item 2/3 of the regression matrix: once this run's Stage 3
        decision loop actually finalizes a selection (keep_baseline here,
        apply_candidate follows the identical write_stage(...,status=DONE)
        path), the checkpoint is no longer stale and the very next
        invocation's --resume-from 4 must be allowed to reach Stage 4."""
        from tournament_scheduler.cli.pipeline_orchestrator import _write_stage3_interactive_state
        from tournament_scheduler.stage3_ab import build_ab_report
        from tournament_scheduler.stage3_decision import build_stage3_decision_context

        plan1 = _plan_checkpoint(seed=1)
        plan2 = _plan_checkpoint(seed=2)
        report = build_ab_report(plan1["plan"], plan2["plan"])
        ab_context = build_stage3_decision_context(
            report, run_id="", baseline_ref="stage3_interactive:attempt_1",
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
        # On-disk checkpoint currently holds the just-rejected rerun
        # candidate, marked stale by an upstream write to simulate a prior
        # invalidation -- keep_baseline's restore must still clear it.
        state.write_stage(StageName.PLANNING, plan2, status=StageStatus.DONE)
        state.write_stage(StageName.CONFIG, {"start_date": "2026-09-01", "end_date": "2027-04-30"}, status=StageStatus.DONE)
        assert state.is_stale(StageName.PLANNING)

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
            "tournament_scheduler.cli.pipeline_orchestrator._run_stage4_export",
            return_value=(False, False, False),
        ) as run_stage4_export:
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 2
        run_stage4_export.assert_called_once()
        assert not state.is_stale(StageName.PLANNING)
        assert state.read_stage(StageName.PLANNING) == plan1
