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
    PAUSE_BUDGET_EXHAUSTED,
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
    """Simulate a repository-owned internal commit: new checkpoint + re-finalized session.

    Mirrors the real batched convergence commit boundary: the candidate revision
    advances without a Stage 4 export, so the session records that exactly one
    review handoff is still owed.
    """
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
    session.export_pending = True
    store.save(session)
    return fingerprint


def _materialize_stub(materializations: List[Dict[str, Any]], *, ok: bool = True):
    """A boundary export stub that records calls and clears the pending flag."""

    def materialize(work_dir: Path, **kwargs: Any) -> Dict[str, Any]:
        materializations.append({"work_dir": str(work_dir)})
        store = Stage3SessionStore(str(work_dir))
        session = store.load(expected_run_id=RUN_ID)
        if ok:
            session.export_pending = False
        store.save(session)
        if not ok:
            return {"ok": False, "reason": "export_failed", "export_required": True}
        return {
            "ok": True,
            "export_dir": str(Path(work_dir) / "export" / "batch"),
            "export_fingerprint": "fp-batch",
        }

    return materialize


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
    # Internal epochs committed verified revisions without a Stage 4 handoff,
    # so no audit can bind yet: the batch owes exactly one export first.
    assert result["audit_required"] is False
    assert result["export_required"] is True
    assert session.export_pending is True


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


# ---------------------------------------------------------------------------
# Truthful coverage: zero options from the bounded search is not a budget stop
# ---------------------------------------------------------------------------


def test_exhausted_finding_reports_bounded_search_exhausted_not_budget(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)

    def finding_provider(candidate: Dict[str, Any], problem: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {
                "finding_id": "h",
                "category": "hosting",
                "search_coverage": {"status": "search_incomplete", "search_requested": False},
            }
        ]

    def option_provider(
        plan: Dict[str, Any], problem: Dict[str, Any], finding_id: str, *, allow_search: bool, dimensions: tuple
    ) -> Dict[str, Any]:
        return {
            "finding": {
                "finding_id": finding_id,
                "category": "hosting",
                "search_coverage": {
                    "status": "bounded_search_exhausted",
                    "search_requested": True,
                    "proven_infeasible": False,
                },
            },
            "options": [],
            "rejected_candidates": [{"finding_id": finding_id, "reason": "no_candidate"}],
        }

    result = run_bounded_convergence(
        tmp_path,
        problem={},
        max_epochs=6,
        max_no_improvement_epochs=2,
        finding_provider=finding_provider,
        option_provider=option_provider,
        body_provider=lambda *a, **k: {"ok": False},
        apply_provider=lambda *a, **k: {"ok": False, "reason": "should_not_run"},
        run_id=RUN_ID,
    )

    assert result["terminal_reason"] == "bounded_search_exhausted"
    assert "not proof of infeasibility" in result["terminal_detail"]
    session = Stage3SessionStore(str(tmp_path)).load(expected_run_id=RUN_ID)
    assert session.convergence["search_coverage"]["h"]["status"] == "bounded_search_exhausted"
    # It stopped because the configured search is exhausted, not because an
    # epoch/attempt budget ran out.
    assert session.convergence["epoch"] == 1


# ---------------------------------------------------------------------------
# Semantic-audit bridge
# ---------------------------------------------------------------------------


def test_audit_open_ended_finding_stops_and_asks_operator(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)
    payload = {
        "status": "REVIEW_REQUIRED",
        "checklist_findings": [
            {
                "item_id": 9,
                "question": "anything else?",
                "finding": "a rule we have not modelled",
                "severity": "major",
                "confidence": "high",
            }
        ],
    }

    def finding_provider(candidate: Dict[str, Any], problem: Dict[str, Any]) -> List[Dict[str, Any]]:
        return []

    result = run_bounded_convergence(
        tmp_path,
        problem={},
        audit_payload=payload,
        finding_provider=finding_provider,
        option_provider=lambda *a, **k: {"finding": {}, "options": []},
        body_provider=lambda *a, **k: {"ok": False},
        apply_provider=lambda *a, **k: {"ok": False, "reason": "should_not_run"},
        run_id=RUN_ID,
    )

    assert result["terminal_reason"] == TERMINAL_OPERATOR_REQUIRED
    questions = result["audit_decision"]["operator_questions"]
    assert questions and questions[0]["item_id"] == 9
    assert "a rule we have not modelled" in result["terminal_detail"]


def test_audit_covered_finding_continues_refinement(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)
    commits: List[str] = []
    finding_provider, option_provider, body_provider, apply_provider = _providers(commits)
    # stage 1 surfaces finding "B" (direction participants).
    payload = {
        "status": "REVIEW_REQUIRED",
        "checklist_findings": [
            {
                "item_id": 1,
                "question": "cuper pr lag?",
                "finding": "one team is short",
                "severity": "major",
                "confidence": "high",
            }
        ],
    }

    result = run_bounded_convergence(
        tmp_path,
        problem={},
        max_epochs=4,
        max_no_improvement_epochs=1,
        export=False,
        audit_payload=payload,
        finding_provider=finding_provider,
        option_provider=option_provider,
        body_provider=body_provider,
        apply_provider=apply_provider,
        run_id=RUN_ID,
    )

    assert result["committed_epochs"] >= 1
    assert result["audit_decision"]["covered_directions"] == ["participants"]
    # The covered audit finding did not itself become an operator question, and
    # automatic refinement was not blocked.
    assert not result["audit_decision"]["operator_questions"]


# ---------------------------------------------------------------------------
# Frontier selectability
# ---------------------------------------------------------------------------


def test_any_retained_frontier_candidate_can_be_adopted(tmp_path: Path) -> None:
    from tournament_scheduler.application.convergence_refinement import select_frontier_candidate
    from tournament_scheduler.application.stage3_session_store import extract_candidate_body

    _seed_finalized(tmp_path)
    commits: List[str] = []
    finding_provider, option_provider, body_provider, apply_provider = _providers(commits)
    run_bounded_convergence(
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
    session = Stage3SessionStore(str(tmp_path)).load(expected_run_id=RUN_ID)
    # Two non-dominated frontier entries; only one is the current candidate.
    assert len(session.pareto_archive) == 2
    hosting = next(e for e in session.pareto_archive if e["direction"] == "hosting")
    assert hosting["objective_vector"]["x"] == 1.0

    result = select_frontier_candidate(
        tmp_path,
        candidate_ref=hosting["candidate_ref"],
        problem={},
        export=False,
        run_id=RUN_ID,
    )

    assert result["ok"] is True, result
    after = Stage3SessionStore(str(tmp_path)).load(expected_run_id=RUN_ID)
    assert after.finalized_fingerprint == result["candidate_fingerprint_after"]
    body = extract_candidate_body(PipelineState(str(tmp_path)).read_stage(StageName.PLANNING))
    assert body["stage"] == 1  # the retained A candidate, not the later B


def test_unknown_frontier_candidate_ref_is_rejected(tmp_path: Path) -> None:
    from tournament_scheduler.application.convergence_refinement import select_frontier_candidate

    _seed_finalized(tmp_path)
    result = select_frontier_candidate(
        tmp_path, candidate_ref="pareto:hosting:does-not-exist", problem={}, export=False, run_id=RUN_ID
    )
    assert result["ok"] is False
    assert result["reason"] == "unknown_frontier_candidate_ref"


# ---------------------------------------------------------------------------
# Multiple non-dominated options from one epoch are retained
# ---------------------------------------------------------------------------


def test_multiple_non_dominated_options_are_retained_and_selectable(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)

    def finding_provider(candidate: Dict[str, Any], problem: Dict[str, Any]) -> List[Dict[str, Any]]:
        if int(candidate.get("stage", 99)) == 0:
            return [{"finding_id": "A", "category": "hosting", "search_coverage": {"status": "search_incomplete"}}]
        return []

    options = [
        {
            "option_id": "oA1",
            "finding_id": "A",
            "family": "hosting_balance",
            "objectives": {"hard_violations": 0.0, "manual_placements": 0.0, "x": 1.0, "y": 5.0},
            "non_dominated": True,
        },
        {
            "option_id": "oA2",
            "finding_id": "A",
            "family": "hosting_balance",
            "objectives": {"hard_violations": 0.0, "manual_placements": 1.0, "x": 5.0, "y": 1.0},
            "non_dominated": True,
        },
    ]

    def option_provider(plan, problem, finding_id, *, allow_search, dimensions):
        return {
            "finding": {"finding_id": "A", "category": "hosting", "search_coverage": {"status": "option_available"}},
            "options": [dict(o) for o in options],
        }

    def body_provider(plan, problem, option_id, *, finding_id, dimensions):
        return {"ok": True, "candidate": {"tournaments": [], "stage": 1 if option_id == "oA1" else 2}}

    def apply_provider(work_dir, **kwargs):
        option_id = kwargs["option_id"]
        fingerprint = _commit(
            work_dir, {"tournaments": [], "stage": 1 if option_id == "oA1" else 2}
        )
        return {"ok": True, "candidate_fingerprint_after": fingerprint, "export_fingerprint": "e"}

    result = run_bounded_convergence(
        tmp_path,
        problem={},
        max_epochs=3,
        export=False,
        finding_provider=finding_provider,
        option_provider=option_provider,
        body_provider=body_provider,
        apply_provider=apply_provider,
        run_id=RUN_ID,
    )

    assert result["terminal_reason"] == "pass"
    refs = [entry["candidate_ref"] for entry in result["frontier"]]
    assert len(refs) == 2
    session = Stage3SessionStore(str(tmp_path)).load(expected_run_id=RUN_ID)
    retained = set(session.retained_candidate_refs())
    assert set(refs).issubset(retained)
    # Each retained entry carries the named consequence metrics, not just the
    # raw objective vector.
    manual_counts = sorted(e["metrics"]["manual_placement_count"] for e in result["frontier"])
    assert manual_counts == [0.0, 1.0]


def test_preferred_option_chooses_the_next_exploration_baseline(tmp_path: Path) -> None:
    from tournament_scheduler.application.stage3_session_store import extract_candidate_body

    _seed_finalized(tmp_path)

    def finding_provider(candidate: Dict[str, Any], problem: Dict[str, Any]) -> List[Dict[str, Any]]:
        if int(candidate.get("stage", 99)) == 0:
            return [{"finding_id": "A", "category": "hosting", "search_coverage": {"status": "search_incomplete"}}]
        return []

    def option_provider(plan, problem, finding_id, *, allow_search, dimensions):
        return {
            "finding": {"finding_id": "A", "category": "hosting", "search_coverage": {"status": "option_available"}},
            "options": [
                {"option_id": "oA1", "finding_id": "A", "family": "f", "objectives": {"x": 1.0, "y": 5.0}, "non_dominated": True},
                {"option_id": "oA2", "finding_id": "A", "family": "f", "objectives": {"x": 5.0, "y": 1.0}, "non_dominated": True},
            ],
        }

    def body_provider(plan, problem, option_id, *, finding_id, dimensions):
        return {"ok": True, "candidate": {"tournaments": [], "stage": 1 if option_id == "oA1" else 2}}

    def apply_provider(work_dir, **kwargs):
        body = {"tournaments": [], "stage": 1 if kwargs["option_id"] == "oA1" else 2}
        fingerprint = _commit(work_dir, body)
        return {"ok": True, "candidate_fingerprint_after": fingerprint}

    run_bounded_convergence(
        tmp_path,
        problem={},
        max_epochs=2,
        export=False,
        preferred_option_id="oA2",
        finding_provider=finding_provider,
        option_provider=option_provider,
        body_provider=body_provider,
        apply_provider=apply_provider,
        run_id=RUN_ID,
    )

    body = extract_candidate_body(PipelineState(str(tmp_path)).read_stage(StageName.PLANNING))
    assert body["stage"] == 2


# ---------------------------------------------------------------------------
# Resumable budget pauses and direction fairness (#386)
# ---------------------------------------------------------------------------


def test_budget_pause_resumes_across_processes_with_a_larger_budget(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)
    commits: List[str] = []

    def finding_provider(candidate: Dict[str, Any], problem: Dict[str, Any]) -> List[Dict[str, Any]]:
        stage = int(candidate.get("stage", 0))
        return [
            {
                "finding_id": f"H{stage}",
                "category": "hosting",
                "search_coverage": {"status": "search_incomplete"},
            }
        ]

    def option_provider(plan, problem, finding_id, *, allow_search, dimensions):
        stage = int(plan.get("stage", 0))
        return {
            "finding": {
                "finding_id": finding_id,
                "category": "hosting",
                "search_coverage": {"status": "option_available"},
            },
            "options": [
                {
                    "option_id": f"o{stage}",
                    "finding_id": finding_id,
                    "family": "hosting_balance",
                    "objectives": {
                        "hard_violations": 0.0,
                        "x": float(stage + 1),
                        "y": float(-(stage + 1)),
                    },
                    "non_dominated": True,
                }
            ],
        }

    def body_provider(plan, problem, option_id, *, finding_id, dimensions):
        return {"ok": True, "candidate": {"tournaments": [], "stage": int(plan.get("stage", 0)) + 1}}

    def apply_provider(work_dir, **kwargs):
        stage = int(kwargs["option_id"][1:]) + 1
        fingerprint = _commit(work_dir, {"tournaments": [], "stage": stage})
        commits.append(fingerprint)
        return {
            "ok": True,
            "candidate_fingerprint_after": fingerprint,
            "export_fingerprint": f"e{fingerprint}",
        }

    def run(max_epochs: int) -> Dict[str, Any]:
        return run_bounded_convergence(
            tmp_path,
            problem={},
            max_epochs=max_epochs,
            max_no_improvement_epochs=8,
            export=False,
            finding_provider=finding_provider,
            option_provider=option_provider,
            body_provider=body_provider,
            apply_provider=apply_provider,
            run_id=RUN_ID,
        )

    first = run(max_epochs=2)
    assert first["committed_epochs"] == 2
    assert first["terminal_reason"] == ""
    assert first["pause_reason"] == PAUSE_BUDGET_EXHAUSTED
    assert first["paused"] is True and first["resumable"] is True
    assert "not completed convergence" in first["pause_detail"].lower()
    assert len(first["frontier"]) == 2

    session = Stage3SessionStore(str(tmp_path)).load(expected_run_id=RUN_ID)
    assert session.convergence["epoch"] == 2
    assert session.convergence["terminal_reason"] == ""
    assert session.convergence["pause_reason"] == PAUSE_BUDGET_EXHAUSTED
    assert len(session.pareto_archive) == 2

    # A new process with a larger budget resumes from the persisted epoch
    # without a candidate mutation, forced finding or manual state edit.
    second = run(max_epochs=4)
    assert second["committed_epochs"] == 2  # epochs 3 and 4 only
    assert second["epochs"][0]["epoch"] == 3
    assert second["terminal_reason"] == ""
    assert second["pause_reason"] == PAUSE_BUDGET_EXHAUSTED
    # The bounded Pareto archive survived the pause and grew.
    assert len(second["frontier"]) == 4
    session = Stage3SessionStore(str(tmp_path)).load(expected_run_id=RUN_ID)
    assert session.convergence["epoch"] == 4
    assert len(session.pareto_archive) == 4


def test_hosting_cannot_monopolize_the_convergence_budget(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)
    commits: List[str] = []
    counter = {"n": 0}

    def finding_provider(candidate: Dict[str, Any], problem: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {
                "finding_id": "h",
                "category": "hosting",
                "search_coverage": {"status": "search_incomplete"},
            },
            {
                "finding_id": "p",
                "category": "participation",
                "search_coverage": {"status": "search_incomplete"},
            },
        ]

    def option_provider(plan, problem, finding_id, *, allow_search, dimensions):
        counter["n"] += 1
        n = counter["n"]
        return {
            "finding": {
                "finding_id": finding_id,
                "category": "hosting" if finding_id == "h" else "participation",
                "search_coverage": {"status": "option_available"},
            },
            "options": [
                {
                    "option_id": f"o{n}",
                    "finding_id": finding_id,
                    "family": "f",
                    "objectives": {"hard_violations": 0.0, "x": float(n), "y": float(-n)},
                    "non_dominated": True,
                }
            ],
        }

    def body_provider(plan, problem, option_id, *, finding_id, dimensions):
        return {"ok": True, "candidate": {"tournaments": [], "stage": int(option_id[1:])}}

    def apply_provider(work_dir, **kwargs):
        body = {"tournaments": [], "stage": int(kwargs["option_id"][1:])}
        fingerprint = _commit(work_dir, body)
        commits.append(fingerprint)
        return {
            "ok": True,
            "candidate_fingerprint_after": fingerprint,
            "export_fingerprint": f"e{fingerprint}",
        }

    result = run_bounded_convergence(
        tmp_path,
        problem={},
        max_epochs=4,
        max_no_improvement_epochs=8,
        export=False,
        finding_provider=finding_provider,
        option_provider=option_provider,
        body_provider=body_provider,
        apply_provider=apply_provider,
        run_id=RUN_ID,
    )

    directions = [epoch["direction"] for epoch in result["epochs"] if epoch["direction"]]
    # A successful hosting mutation must not reset fairness back to hosting.
    assert directions[0] == "hosting"
    assert directions[1] == "participants"
    assert directions.count("hosting") <= 2


# ---------------------------------------------------------------------------
# Batched export materialization boundary (#387)
# ---------------------------------------------------------------------------


def test_accepted_epochs_materialize_exactly_one_review_export(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)
    commits: List[str] = []
    materializations: List[Dict[str, Any]] = []
    finding_provider, option_provider, body_provider, apply_provider = _providers(commits)

    result = run_bounded_convergence(
        tmp_path,
        problem={},
        max_epochs=6,
        max_no_improvement_epochs=1,
        export=True,
        finding_provider=finding_provider,
        option_provider=option_provider,
        body_provider=body_provider,
        apply_provider=apply_provider,
        materialize_provider=_materialize_stub(materializations),
        run_id=RUN_ID,
    )

    assert len(commits) >= 2
    assert len(materializations) == 1
    assert result["export_materialized"] is True
    assert result["audit_required"] is True
    assert result["export_required"] is False
    assert result["export_fingerprint"] == "fp-batch"


def test_zero_accepted_epochs_produce_no_redundant_export(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)
    materializations: List[Dict[str, Any]] = []

    result = run_bounded_convergence(
        tmp_path,
        problem={},
        export=True,
        finding_provider=lambda candidate, problem: [],
        option_provider=lambda *a, **k: {"finding": {}, "options": []},
        body_provider=lambda *a, **k: {"ok": False},
        apply_provider=lambda *a, **k: {"ok": False, "reason": "should_not_run"},
        materialize_provider=_materialize_stub(materializations),
        run_id=RUN_ID,
    )

    assert result["committed_epochs"] == 0
    assert materializations == []
    assert result["export_materialized"] is False
    assert result["export_required"] is False
    assert result["audit_required"] is False


def test_failed_batch_export_keeps_verified_candidate_and_reports_export_required(
    tmp_path: Path,
) -> None:
    _seed_finalized(tmp_path)
    commits: List[str] = []
    materializations: List[Dict[str, Any]] = []
    finding_provider, option_provider, body_provider, apply_provider = _providers(commits)

    result = run_bounded_convergence(
        tmp_path,
        problem={},
        max_epochs=6,
        max_no_improvement_epochs=1,
        export=True,
        finding_provider=finding_provider,
        option_provider=option_provider,
        body_provider=body_provider,
        apply_provider=apply_provider,
        materialize_provider=_materialize_stub(materializations, ok=False),
        run_id=RUN_ID,
    )

    assert len(materializations) == 1
    assert result["ok"] is True
    assert result["export_materialized"] is False
    assert result["export_required"] is True
    assert result["audit_required"] is False
    # The independently verified candidate survives a failed export and is
    # still flagged as owing exactly one Stage 4 handoff.
    session = Stage3SessionStore(str(tmp_path)).load(expected_run_id=RUN_ID)
    assert session.is_finalized() is True
    assert session.finalized_fingerprint == commits[-1]
    assert session.export_pending is True


def test_resume_materializes_a_pending_handoff_once_without_duplicate_churn(
    tmp_path: Path,
) -> None:
    _seed_finalized(tmp_path)
    commits: List[str] = []
    materializations: List[Dict[str, Any]] = []
    finding_provider, option_provider, body_provider, apply_provider = _providers(commits)
    materialize = _materialize_stub(materializations)

    # First batch: internal revisions only, no export (``--no-export``).
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
        materialize_provider=materialize,
        run_id=RUN_ID,
    )
    assert first["export_required"] is True
    assert first["terminal_reason"] == TERMINAL_PARETO_STABLE
    assert materializations == []

    # A later invocation with no new commits still owes one handoff and
    # materializes it exactly once.
    second = run_bounded_convergence(
        tmp_path,
        problem={},
        max_epochs=6,
        max_no_improvement_epochs=1,
        export=True,
        finding_provider=finding_provider,
        option_provider=option_provider,
        body_provider=body_provider,
        apply_provider=apply_provider,
        materialize_provider=materialize,
        run_id=RUN_ID,
    )
    assert second["committed_epochs"] == 0
    assert second["export_materialized"] is True
    assert len(materializations) == 1

    # Once materialized, another idle invocation must not re-export.
    third = run_bounded_convergence(
        tmp_path,
        problem={},
        max_epochs=6,
        max_no_improvement_epochs=1,
        export=True,
        finding_provider=finding_provider,
        option_provider=option_provider,
        body_provider=body_provider,
        apply_provider=apply_provider,
        materialize_provider=materialize,
        run_id=RUN_ID,
    )
    assert third["committed_epochs"] == 0
    assert third["export_materialized"] is False
    assert len(materializations) == 1
