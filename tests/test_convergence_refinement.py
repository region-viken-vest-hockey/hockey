"""Bounded convergence driver over an unpromoted reviewed candidate (#384).

These tests drive the real :func:`run_bounded_convergence` composition with
synthetic repository-owned findings/options/bodies so the outer-loop mechanics
(multi-epoch improvement, frontier retention, domination pruning, plateau
detection, re-audit boundary and human-escalation stop) are proven without
running the planner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from tournament_scheduler.application.convergence_refinement import (
    run_bounded_convergence,
)
from tournament_scheduler.application.pareto_convergence import (
    TERMINAL_OPERATOR_REQUIRED,
    TERMINAL_PARETO_STABLE,
)
from tournament_scheduler.application.stage3_session_store import (
    Stage3SessionStore,
    finalize_stage3_plan,
    fingerprint_plan,
)
from tournament_scheduler.pipeline.run_manifest import RunManifest
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus

RUN_ID = "run-1"


# ---------------------------------------------------------------------------
# Synthetic finalized unpromoted candidate
# ---------------------------------------------------------------------------


def _body(stage: int) -> Dict[str, Any]:
    return {"tournaments": [], "stage": stage}


def _seed_finalized(tmp_path: Path) -> None:
    state = PipelineState(str(tmp_path))
    RunManifest(str(tmp_path)).start_run("objective", run_id=RUN_ID)
    checkpoint = {"plan": _body(0), "source": "reviewed"}
    state.write_stage(StageName.PLANNING, checkpoint, status=StageStatus.DONE)
    finalize_stage3_plan(
        str(tmp_path), checkpoint, action_id="apply_candidate", rationale="reviewed", run_id=RUN_ID
    )


def _commit(work_dir: Path, body: Dict[str, Any]) -> str:
    """Simulate a repository-owned commit: new checkpoint + re-finalized session."""
    state = PipelineState(str(work_dir))
    checkpoint = {"plan": body, "source": "convergence_repair"}
    state.write_stage(StageName.PLANNING, checkpoint, status=StageStatus.DONE)
    store = Stage3SessionStore(str(work_dir))
    session = store.load(expected_run_id=RUN_ID)
    fingerprint = fingerprint_plan(checkpoint)
    session.advance_candidate(
        checkpoint,
        fingerprint=fingerprint,
        source="convergence_repair",
        transition="apply_repair",
        action_id="apply_repair",
        rationale="test commit",
        at="T",
    )
    session.finalize(
        transition="apply_repair", action_id="apply_repair", rationale="test commit", at="T"
    )
    store.save(session)
    return fingerprint


# ---------------------------------------------------------------------------
# Regression scenario: A -> B -> {C dominates, D tradeoff} -> plateau
# ---------------------------------------------------------------------------


_FINDINGS_BY_STAGE: Dict[int, List[Dict[str, Any]]] = {
    0: [
        {
            "finding_id": "A",
            "category": "hosting",
            "search_coverage": {"status": "search_incomplete"},
        }
    ],
    1: [{"finding_id": "B", "category": "participation"}],
    2: [{"finding_id": "C", "category": "hosting"}],
    3: [],
    4: [{"finding_id": "D", "category": "hosting"}],
    5: [],
}

_OPTIONS_BY_FINDING: Dict[str, Dict[str, Any]] = {
    "A": {
        "body": _body(1),
        "option": {
            "option_id": "oA",
            "finding_id": "A",
            "family": "hosting_balance",
            "objectives": {"hard_violations": 0.0, "x": 1.0, "y": 5.0},
            "non_dominated": True,
        },
    },
    "B": {
        "body": _body(2),
        "option": {
            "option_id": "oB",
            "finding_id": "B",
            "family": "participation_deviation",
            "objectives": {"hard_violations": 0.0, "x": 5.0, "y": 1.0},
            "non_dominated": True,
        },
    },
    "C": {
        "body": _body(3),
        "option": {
            "option_id": "oC",
            "finding_id": "C",
            "family": "hosting_balance",
            "objectives": {"hard_violations": 0.0, "x": 9.0, "y": 9.0},
            "non_dominated": True,
        },
    },
    "D": {
        "body": _body(5),
        "option": {
            "option_id": "oD",
            "finding_id": "D",
            "family": "hosting_balance",
            "objectives": {"hard_violations": 0.0, "x": 0.5, "y": 0.5},
            "non_dominated": True,
        },
    },
}


def _providers(commits: List[str]):
    def finding_provider(candidate: Dict[str, Any], problem: Dict[str, Any]) -> List[Dict[str, Any]]:
        return list(_FINDINGS_BY_STAGE.get(int(candidate.get("stage", 99)), []))

    def option_provider(
        plan: Dict[str, Any], problem: Dict[str, Any], finding_id: str, *, allow_search: bool, dimensions: tuple
    ) -> Dict[str, Any]:
        spec = _OPTIONS_BY_FINDING[finding_id]
        finding = next(
            (f for f in _FINDINGS_BY_STAGE[int(plan["stage"])] if f["finding_id"] == finding_id),
            {"finding_id": finding_id, "category": "hosting"},
        )
        return {"finding": finding, "options": [dict(spec["option"])], "pareto": {}}

    def body_provider(
        plan: Dict[str, Any],
        problem: Dict[str, Any],
        option_id: str,
        *,
        finding_id: str,
        dimensions: tuple,
    ) -> Dict[str, Any]:
        spec = _OPTIONS_BY_FINDING[finding_id]
        return {"ok": True, "candidate": spec["body"]}

    def apply_provider(work_dir: Path, **kwargs: Any) -> Dict[str, Any]:
        finding_id = str(kwargs.get("finding_id"))
        body = _OPTIONS_BY_FINDING[finding_id]["body"]
        fingerprint = _commit(work_dir, body)
        commits.append(fingerprint)
        return {
            "ok": True,
            "candidate_fingerprint_after": fingerprint,
            "export_fingerprint": f"export-{fingerprint}",
        }

    return finding_provider, option_provider, body_provider, apply_provider


def test_multi_epoch_convergence_retains_frontier_and_detects_plateau(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)
    commits: List[str] = []
    finding_provider, option_provider, body_provider, apply_provider = _providers(commits)

    result = run_bounded_convergence(
        tmp_path,
        problem={},
        max_epochs=6,
        max_no_improvement_epochs=1,
        frontier_limit=4,
        export=False,
        finding_provider=finding_provider,
        option_provider=option_provider,
        body_provider=body_provider,
        apply_provider=apply_provider,
        run_id=RUN_ID,
    )

    assert result["ok"] is True
    assert result["terminal_reason"] == TERMINAL_PARETO_STABLE
    assert result["globally_optimal"] is False
    assert len(commits) == 2  # B and the plateau epoch never committed a dominated C

    frontier = {entry["candidate_ref"]: entry for entry in result["frontier"]}
    assert len(frontier) == 2
    vectors = sorted(tuple(sorted(e["objective_vector"].items())) for e in frontier.values())
    assert vectors == sorted(
        [
            (("hard_violations", 0.0), ("x", 1.0), ("y", 5.0)),
            (("hard_violations", 0.0), ("x", 5.0), ("y", 1.0)),
        ]
    )

    # The archive/convergence state is persisted on the Stage 3 session, not a
    # parallel side file.
    session = Stage3SessionStore(str(tmp_path)).load(expected_run_id=RUN_ID)
    assert len(session.pareto_archive) == 2
    assert session.convergence is not None
    assert session.convergence["terminal_reason"] == TERMINAL_PARETO_STABLE
    assert "hosting" in session.convergence["explored_directions"]
    assert "participants" in session.convergence["explored_directions"]
    # A re-audit is required because accepted mutations superseded the export.
    assert result["audit_required"] is True


def test_convergence_resumes_after_a_manual_refine_changed_the_candidate(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)
    commits: List[str] = []
    finding_provider, option_provider, body_provider, apply_provider = _providers(commits)

    first = run_bounded_convergence(
        tmp_path,
        problem={},
        max_epochs=6,
        max_no_improvement_epochs=1,
        export=False,
        finding_provider=finding_provider,
        option_provider=option_provider,
        body_provider=body_provider,
        apply_provider=apply_provider,
        run_id=RUN_ID,
    )
    assert first["terminal_reason"] == TERMINAL_PARETO_STABLE

    # The harness applies a manual stage3 refine that produces a candidate the
    # frontier never saw. The stale terminal must not suppress exploration.
    _commit(tmp_path, _body(4))
    second = run_bounded_convergence(
        tmp_path,
        problem={},
        max_epochs=3,
        max_no_improvement_epochs=1,
        export=False,
        finding_provider=finding_provider,
        option_provider=option_provider,
        body_provider=body_provider,
        apply_provider=apply_provider,
        run_id=RUN_ID,
    )

    assert second["committed_epochs"] == 1
    assert len(second["frontier"]) == 1
    assert second["frontier"][0]["objective_vector"]["x"] == 0.5


def test_convergence_stops_for_operator_authority_without_searching_blindly(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)
    commits: List[str] = []

    def finding_provider(candidate: Dict[str, Any], problem: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {
                "finding_id": "p",
                "category": "participation",
                "requires_operator": True,
                "message": "waiver required",
            }
        ]

    def option_provider(*args: Any, **kwargs: Any) -> Dict[str, Any]:  # pragma: no cover - never called
        raise AssertionError("no search should run when only operator authority remains")

    result = run_bounded_convergence(
        tmp_path,
        problem={},
        finding_provider=finding_provider,
        option_provider=option_provider,
        apply_provider=lambda *a, **k: {"ok": False, "reason": "should_not_run"},
        body_provider=lambda *a, **k: {"ok": False},
        run_id=RUN_ID,
    )

    assert result["terminal_reason"] == TERMINAL_OPERATOR_REQUIRED
    assert commits == []
    session = Stage3SessionStore(str(tmp_path)).load(expected_run_id=RUN_ID)
    assert session.finalized_fingerprint is not None
    assert session.convergence["terminal_reason"] == TERMINAL_OPERATOR_REQUIRED


def test_dry_run_does_not_mutate_candidate_or_persist(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)
    before = Stage3SessionStore(str(tmp_path)).load(expected_run_id=RUN_ID).finalized_fingerprint
    commits: List[str] = []
    finding_provider, option_provider, body_provider, apply_provider = _providers(commits)

    result = run_bounded_convergence(
        tmp_path,
        problem={},
        dry_run=True,
        finding_provider=finding_provider,
        option_provider=option_provider,
        body_provider=body_provider,
        apply_provider=apply_provider,
        run_id=RUN_ID,
    )

    assert result["terminal_reason"] == "dry_run"
    assert commits == []
    session = Stage3SessionStore(str(tmp_path)).load(expected_run_id=RUN_ID)
    assert session.finalized_fingerprint == before
    assert session.pareto_archive == []
    assert session.convergence is None


# ---------------------------------------------------------------------------
# Real repository providers: an actionable finding enters refinement
# ---------------------------------------------------------------------------


def test_actionable_finding_enters_verified_refinement_without_human_prompt(tmp_path: Path) -> None:
    from tests.test_candidate_refinement import _refinement_fixture

    work_dir, plan, problem = _refinement_fixture(tmp_path)
    before = Stage3SessionStore(str(work_dir)).load(expected_run_id="run-1").finalized_fingerprint

    result = run_bounded_convergence(
        work_dir,
        problem=problem,
        export=False,
        max_epochs=4,
        run_id="run-1",
    )

    assert result["ok"] is True
    # The unplaced obligation is repaired automatically; the next epoch sees no
    # material finding and converges as PASS -- no operator question was needed.
    assert result["committed_epochs"] >= 1
    assert result["terminal_reason"] == "pass"
    assert result["terminal_detail"].lower().startswith("no remaining material finding")

    session = Stage3SessionStore(str(work_dir)).load(expected_run_id="run-1")
    assert session.finalized_fingerprint != before
    assert session.convergence is not None
    assert session.convergence["terminal_reason"] == "pass"


def test_stage3_converge_parser_exposes_bounded_budgets() -> None:
    from tournament_scheduler.cli.args import build_parser

    args = build_parser().parse_args(
        ["stage3", "converge", "--max-epochs", "3", "--max-no-improvement", "1"]
    )
    assert args.stage3_command == "converge"
    assert args.max_epochs == 3
    assert args.max_no_improvement == 1
    assert args.frontier_limit == 6


def test_stage3_converge_cli_reports_truthful_terminal(tmp_path: Path, capsys: Any) -> None:
    import argparse
    import json

    from tournament_scheduler.cli.pipeline_orchestrator.stage3_converge_command import (
        _cmd_stage3_converge,
    )

    _seed_finalized(tmp_path)
    state = PipelineState(str(tmp_path))
    state.write_stage(
        StageName.CONFIG,
        {
            "start_date": "2026-10-01",
            "end_date": "2026-10-31",
            "teams": [],
            "age_groups": ["U10"],
        },
        status=StageStatus.DONE,
    )
    args = argparse.Namespace(
        work_dir=str(tmp_path),
        input=None,
        finding=None,
        max_epochs=2,
        max_no_improvement=1,
        frontier_limit=4,
        no_search=False,
        no_export=True,
        dry_run=False,
        json=True,
    )
    rc = _cmd_stage3_converge(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["terminal_reason"] in {"pass", "operator_required", "pareto_stable"}
    assert payload["globally_optimal"] is False
