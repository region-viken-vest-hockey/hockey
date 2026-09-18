"""Interactive audit -> convergence -> re-audit lifecycle (issue #385).

The bounded convergence engine and the audit bridge already existed (#384),
but the interactive harness lifecycle could still terminate after a
``REVIEW_REQUIRED`` verdict because completion was a prompt convention rather
than repository state. These tests drive the real persisted workflow state
machine end to end: two full audit/convergence cycles, a refusal to complete
while a phase is pending, stale-audit rejection, and process-boundary resume.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

from tournament_scheduler.application.audit_lifecycle import (
    completion_blockers,
    current_workflow,
    mark_audit_required,
    record_audit_verdict,
    record_convergence_result,
    request_completion,
    workflow_snapshot,
)
from tournament_scheduler.application.convergence_refinement import (
    run_bounded_convergence,
)
from tournament_scheduler.application.stage3_session_store import (
    Stage3SessionStore,
    finalize_stage3_plan,
    fingerprint_plan,
)
from tournament_scheduler.pipeline.audit_result import audit_is_fresh, write_audit_result
from tournament_scheduler.pipeline.audit_workflow import (
    PAUSE_BUDGET_EXHAUSTED,
    PHASE_AUDIT_REQUIRED,
    PHASE_COMPLETE,
    PHASE_CONVERGENCE_REQUIRED,
    TERMINAL_BOUNDED_SEARCH_EXHAUSTED,
    TERMINAL_OPERATOR_REQUIRED,
    TERMINAL_PASS,
    AuditWorkflow,
    completion_blockers as workflow_completion_blockers,
    is_pause_reason,
    next_command,
    terminal_is_distinct,
)
from tournament_scheduler.pipeline.run_manifest import RunManifest
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus

RUN_ID = "run-385"


def _body(stage: int) -> Dict[str, Any]:
    return {"tournaments": [], "stage": stage}


def _write_export(work_dir: Path, fingerprint: str) -> None:
    export_dir = work_dir / "export" / fingerprint
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "evidence_bundle.json").write_text("{}", encoding="utf-8")
    PipelineState(str(work_dir)).write_stage(
        StageName.EXPORT,
        {
            "export_dir": str(export_dir),
            "output_files": {"html": str(export_dir / "season_plan.html")},
            "export_fingerprint": fingerprint,
        },
        status=StageStatus.DONE,
    )


def _seed_finalized(tmp_path: Path, *, export_fingerprint: str = "fp-1") -> None:
    state = PipelineState(str(tmp_path))
    RunManifest(str(tmp_path)).start_run("objective", run_id=RUN_ID)
    checkpoint = {"plan": _body(0), "source": "reviewed"}
    state.write_stage(StageName.PLANNING, checkpoint, status=StageStatus.DONE)
    finalize_stage3_plan(
        str(tmp_path), checkpoint, action_id="apply_candidate", rationale="reviewed", run_id=RUN_ID
    )
    _write_export(tmp_path, export_fingerprint)
    mark_audit_required(tmp_path, run_id=RUN_ID)


def _commit(work_dir: Path, body: Dict[str, Any], export_fingerprint: str) -> str:
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
    _write_export(work_dir, export_fingerprint)
    return fingerprint


def _review_payload(item_id: int, *, finding: str = "material audit finding") -> Dict[str, Any]:
    return {
        "status": "REVIEW_REQUIRED",
        "checklist_findings": [
            {
                "item_id": item_id,
                "question": f"q{item_id}",
                "finding": finding,
                "severity": "major",
                "confidence": "high",
            }
        ],
    }


def _full_review_payload(item_id: int) -> Dict[str, Any]:
    """A schema-valid 9-item audit verdict with one material finding."""
    return {
        "status": "REVIEW_REQUIRED",
        "checklist_findings": [
            {
                "item_id": index,
                "question": f"q{index}",
                "finding": "material finding" if index == item_id else "ok",
                "severity": "major" if index == item_id else "info",
                "confidence": "high",
            }
            for index in range(1, 10)
        ],
    }


def _cycle_one_providers(commits: List[str]):
    def finding_provider(candidate: Dict[str, Any], problem: Dict[str, Any]) -> List[Dict[str, Any]]:
        if int(candidate.get("stage", 99)) == 0:
            return [
                {
                    "finding_id": "h",
                    "category": "hosting",
                    "search_coverage": {"status": "search_incomplete"},
                }
            ]
        return []

    def option_provider(
        plan: Dict[str, Any],
        problem: Dict[str, Any],
        finding_id: str,
        *,
        allow_search: bool,
        dimensions: tuple,
    ) -> Dict[str, Any]:
        return {
            "finding": {
                "finding_id": "h",
                "category": "hosting",
                "search_coverage": {"status": "option_available"},
            },
            "options": [
                {
                    "option_id": "oh",
                    "finding_id": "h",
                    "family": "hosting_balance",
                    "objectives": {"hard_violations": 0.0, "x": 1.0},
                    "non_dominated": True,
                }
            ],
        }

    def body_provider(
        plan: Dict[str, Any],
        problem: Dict[str, Any],
        option_id: str,
        *,
        finding_id: str,
        dimensions: tuple,
    ) -> Dict[str, Any]:
        return {"ok": True, "candidate": _body(1)}

    def apply_provider(work_dir: Path, **kwargs: Any) -> Dict[str, Any]:
        fingerprint = _commit(work_dir, _body(1), "fp-2")
        commits.append(fingerprint)
        return {
            "ok": True,
            "candidate_fingerprint_after": fingerprint,
            "export_fingerprint": "fp-2",
        }

    return finding_provider, option_provider, body_provider, apply_provider


# ---------------------------------------------------------------------------
# Pure state machine
# ---------------------------------------------------------------------------


def test_workflow_record_clears_terminal_when_leaving_complete() -> None:
    workflow = AuditWorkflow(phase=PHASE_COMPLETE, terminal_reason=TERMINAL_PASS)
    workflow.record(PHASE_CONVERGENCE_REQUIRED, at="T")
    assert workflow.phase == PHASE_CONVERGENCE_REQUIRED
    assert workflow.terminal_reason == ""
    assert workflow.is_pending is True
    assert workflow.is_complete is False


def test_next_command_is_derived_from_the_phase() -> None:
    assert "operator audit-context" in (next_command(PHASE_AUDIT_REQUIRED, ".pipeline") or "")
    assert "stage3 converge" in (next_command(PHASE_CONVERGENCE_REQUIRED, ".pipeline") or "")
    assert next_command(PHASE_COMPLETE, ".pipeline") is None


def test_completion_blockers_refuse_pending_phases() -> None:
    assert workflow_completion_blockers(AuditWorkflow(phase=PHASE_AUDIT_REQUIRED))
    assert workflow_completion_blockers(AuditWorkflow(phase=PHASE_CONVERGENCE_REQUIRED))
    assert workflow_completion_blockers(
        AuditWorkflow(phase=PHASE_COMPLETE, terminal_reason=TERMINAL_PASS)
    ) == []


def test_budget_pause_is_not_a_terminal_and_keeps_the_workflow_pending(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)
    record_audit_verdict(tmp_path, _review_payload(2), run_id=RUN_ID)
    assert current_workflow(tmp_path, run_id=RUN_ID).phase == PHASE_CONVERGENCE_REQUIRED

    # A budget pause is resumable bounded-search evidence, not a terminal: it
    # must not complete the workflow (which would end the outer loop).
    assert is_pause_reason(PAUSE_BUDGET_EXHAUSTED)
    assert terminal_is_distinct(PAUSE_BUDGET_EXHAUSTED) is False
    record_convergence_result(
        tmp_path,
        {
            "ok": True,
            "terminal_reason": "",
            "pause_reason": PAUSE_BUDGET_EXHAUSTED,
            "resumable": True,
            "committed_epochs": 0,
        },
        run_id=RUN_ID,
    )
    workflow = current_workflow(tmp_path, run_id=RUN_ID)
    assert workflow.phase == PHASE_CONVERGENCE_REQUIRED
    assert request_completion(tmp_path, run_id=RUN_ID)["ok"] is False
    assert completion_blockers(tmp_path, run_id=RUN_ID)


# ---------------------------------------------------------------------------
# End-to-end interactive lifecycle: two audit/convergence cycles
# ---------------------------------------------------------------------------


def test_two_cycle_lifecycle_requires_fresh_audit_until_terminal(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)

    # Stage 4 completion creates the explicit audit-pending state.
    workflow = current_workflow(tmp_path, run_id=RUN_ID)
    assert workflow is not None and workflow.phase == PHASE_AUDIT_REQUIRED
    assert workflow.export_fingerprint == "fp-1"
    snapshot = workflow_snapshot(tmp_path, run_id=RUN_ID)
    assert snapshot["pending"] is True
    assert "operator audit-context" in snapshot["next_command"]

    # First verdict: REVIEW_REQUIRED enters the convergence-pending state.
    payload_one = _review_payload(1)
    record_audit_verdict(tmp_path, payload_one, run_id=RUN_ID)
    assert current_workflow(tmp_path, run_id=RUN_ID).phase == PHASE_CONVERGENCE_REQUIRED

    # Bounded convergence commits one mutation and re-exports E2.
    commits: List[str] = []
    finding_provider, option_provider, body_provider, apply_provider = _cycle_one_providers(commits)
    first = run_bounded_convergence(
        tmp_path,
        problem={},
        max_epochs=4,
        max_no_improvement_epochs=1,
        export=False,
        audit_payload=payload_one,
        finding_provider=finding_provider,
        option_provider=option_provider,
        body_provider=body_provider,
        apply_provider=apply_provider,
        run_id=RUN_ID,
    )
    assert first["committed_epochs"] == 1
    assert len(commits) == 1  # a dominated/redundant option never commits twice
    # The report itself exposes the canonical next transition.
    assert first["workflow"]["phase"] == PHASE_AUDIT_REQUIRED
    assert first["workflow"]["export_fingerprint"] == "fp-2"

    # A mutation creates a fresh audit-pending state for E2, and the run may
    # not be reported complete yet.
    workflow = current_workflow(tmp_path, run_id=RUN_ID)
    assert workflow.phase == PHASE_AUDIT_REQUIRED
    assert workflow.export_fingerprint == "fp-2"
    refusal = request_completion(tmp_path, run_id=RUN_ID)
    assert refusal["ok"] is False
    assert refusal["workflow"]["phase"] == PHASE_AUDIT_REQUIRED
    assert "operator audit-context" in refusal["workflow"]["next_command"]

    # A stale audit for E1 can never satisfy E2.
    write_audit_result(
        tmp_path,
        {
            "schema_version": 1,
            "audit_id": "stale-e1",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "run_id": RUN_ID,
            "export_fingerprint": "fp-1",
            "status": "PASS",
            "checklist_findings": [],
            "potential_missing_rule": [],
        },
    )
    fresh, _stored = audit_is_fresh(tmp_path)
    assert fresh is False

    # Fresh audit context bound to E2 carries the reconciled workflow.
    context = build_audit_context(tmp_path)
    assert context["export_fingerprint"] == "fp-2"
    assert context["workflow"]["phase"] == PHASE_AUDIT_REQUIRED

    # Second verdict: REVIEW_REQUIRED again enters convergence.
    payload_two = _review_payload(2)
    record_audit_verdict(tmp_path, payload_two, run_id=RUN_ID)
    assert current_workflow(tmp_path, run_id=RUN_ID).phase == PHASE_CONVERGENCE_REQUIRED

    def finding_provider_two(candidate: Dict[str, Any], problem: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {
                "finding_id": "h2",
                "category": "hosting",
                "search_coverage": {
                    "status": "bounded_search_exhausted",
                    "proven_infeasible": False,
                },
            }
        ]

    def option_provider_two(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {
            "finding": {
                "finding_id": "h2",
                "category": "hosting",
                "search_coverage": {
                    "status": "bounded_search_exhausted",
                    "search_requested": True,
                    "proven_infeasible": False,
                },
            },
            "options": [],
        }

    second = run_bounded_convergence(
        tmp_path,
        problem={},
        max_epochs=4,
        max_no_improvement_epochs=1,
        export=False,
        audit_payload=payload_two,
        finding_provider=finding_provider_two,
        option_provider=option_provider_two,
        body_provider=lambda *a, **k: {"ok": False},
        apply_provider=lambda *a, **k: {"ok": False, "reason": "should_not_run"},
        run_id=RUN_ID,
    )
    assert second["committed_epochs"] == 0
    assert second["terminal_reason"] == TERMINAL_BOUNDED_SEARCH_EXHAUSTED

    # Only now does the workflow reach an explicit terminal.
    workflow = current_workflow(tmp_path, run_id=RUN_ID)
    assert workflow.phase == PHASE_COMPLETE
    assert workflow.terminal_reason == TERMINAL_BOUNDED_SEARCH_EXHAUSTED
    assert completion_blockers(tmp_path, run_id=RUN_ID) == []
    assert request_completion(tmp_path, run_id=RUN_ID)["ok"] is True


def build_audit_context(work_dir: Path) -> Dict[str, Any]:
    """Thin wrapper so the test can inject the persisted workflow projection."""
    from tournament_scheduler.pipeline.audit_context import build_audit_context as _build

    return _build(work_dir=work_dir, workflow=workflow_snapshot(work_dir, run_id=RUN_ID))


def test_pass_verdict_completes_the_workflow(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)
    record_audit_verdict(tmp_path, {"status": "PASS"}, run_id=RUN_ID)
    workflow = current_workflow(tmp_path, run_id=RUN_ID)
    assert workflow.phase == PHASE_COMPLETE
    assert workflow.terminal_reason == TERMINAL_PASS
    assert request_completion(tmp_path, run_id=RUN_ID)["ok"] is True


def test_operator_required_is_a_distinct_terminal(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)
    payload = _review_payload(9, finding="a rule we have not modelled")
    record_audit_verdict(tmp_path, payload, run_id=RUN_ID)
    result = run_bounded_convergence(
        tmp_path,
        problem={},
        audit_payload=payload,
        finding_provider=lambda candidate, problem: [],
        option_provider=lambda *a, **k: {"finding": {}, "options": []},
        body_provider=lambda *a, **k: {"ok": False},
        apply_provider=lambda *a, **k: {"ok": False, "reason": "should_not_run"},
        run_id=RUN_ID,
    )
    assert result["terminal_reason"] == TERMINAL_OPERATOR_REQUIRED
    workflow = current_workflow(tmp_path, run_id=RUN_ID)
    assert workflow.phase == PHASE_COMPLETE
    assert workflow.terminal_reason == TERMINAL_OPERATOR_REQUIRED


# ---------------------------------------------------------------------------
# Persistence across process boundaries and stale completion
# ---------------------------------------------------------------------------


def test_pending_state_survives_a_new_store_instance(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)
    payload = _review_payload(2)
    record_audit_verdict(tmp_path, payload, run_id=RUN_ID)

    # A brand-new store (simulating a new process) reads the persisted phase.
    session = Stage3SessionStore(str(tmp_path)).load(expected_run_id=RUN_ID)
    assert session.audit_workflow is not None
    assert session.audit_workflow["phase"] == PHASE_CONVERGENCE_REQUIRED
    assert session.audit_workflow["export_fingerprint"] == "fp-1"

    resumed = current_workflow(tmp_path, run_id=RUN_ID)
    assert resumed.phase == PHASE_CONVERGENCE_REQUIRED


def test_completed_workflow_is_stale_after_a_new_export(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)
    record_audit_verdict(tmp_path, {"status": "PASS"}, run_id=RUN_ID)
    assert current_workflow(tmp_path, run_id=RUN_ID).phase == PHASE_COMPLETE

    _write_export(tmp_path, "fp-2")
    workflow = current_workflow(tmp_path, run_id=RUN_ID)
    assert workflow.phase == PHASE_AUDIT_REQUIRED
    assert workflow.export_fingerprint == "fp-2"
    refusal = request_completion(tmp_path, run_id=RUN_ID)
    assert refusal["ok"] is False


def test_mark_audit_required_is_idempotent_for_the_same_export(tmp_path: Path) -> None:
    _seed_finalized(tmp_path)
    record_audit_verdict(tmp_path, {"status": "PASS"}, run_id=RUN_ID)
    # Re-marking the same export/candidate must not manufacture new work.
    workflow = mark_audit_required(tmp_path, run_id=RUN_ID)
    assert workflow.phase == PHASE_COMPLETE


# ---------------------------------------------------------------------------
# Repository output exposes the canonical next transition
# ---------------------------------------------------------------------------


def test_audit_context_and_session_cli_expose_the_next_transition(tmp_path: Path, capsys: Any) -> None:
    from tournament_scheduler.cli.args import build_parser
    from tournament_scheduler.cli.pipeline_orchestrator.operator_audit import (
        _cmd_operator_audit_context,
    )
    from tournament_scheduler.cli.pipeline_orchestrator.stage3_session_command import (
        _cmd_stage3_session,
    )

    parser = build_parser()
    _seed_finalized(tmp_path)

    rc = _cmd_operator_audit_context(
        parser.parse_args(["operator", "audit-context", "--work-dir", str(tmp_path)])
    )
    assert rc == 0
    context = __import__("json").loads(capsys.readouterr().out)
    assert context["workflow"]["phase"] == PHASE_AUDIT_REQUIRED
    assert context["workflow"]["pending"] is True
    assert "operator audit-context" in context["workflow"]["next_command"]

    rc = _cmd_stage3_session(
        parser.parse_args(["stage3", "session", "--work-dir", str(tmp_path), "--json"])
    )
    assert rc == 0
    session_view = __import__("json").loads(capsys.readouterr().out)
    assert session_view["audit_workflow"]["phase"] == PHASE_AUDIT_REQUIRED
    assert "operator audit-context" in session_view["audit_workflow"]["next_command"]


def test_headless_audit_run_uses_the_same_workflow_phases(tmp_path: Path) -> None:
    from tournament_scheduler.cli.args import build_parser
    from tournament_scheduler.cli.pipeline_orchestrator.operator_audit import (
        _cmd_operator_audit_run,
    )

    _seed_finalized(tmp_path)
    clean_env = {k: "" for k in ("RVV_HARNESS", "CLAUDE_CODE_SESSION_ID", "PI_SESSION_ID")}
    review = _full_review_payload(2)
    review.update(
        {
            "schema_version": 1,
            "audit_id": "headless-review",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "run_id": RUN_ID,
            "export_fingerprint": "fp-1",
            "potential_missing_rule": [],
        }
    )
    args = build_parser().parse_args(
        ["operator", "audit-run", "--work-dir", str(tmp_path), "--backend", "llm_bridge"]
    )
    with patch.dict(os.environ, clean_env), patch(
        "tournament_scheduler.llm_judge.audit.run_headless_audit", return_value=review
    ):
        rc = _cmd_operator_audit_run(args)

    # The default (auto-refine) path needs a real planning problem; with only a
    # synthetic session it cannot run, so the workflow stays in the explicit
    # convergence-pending phase instead of silently completing.
    assert rc == 1
    assert current_workflow(tmp_path, run_id=RUN_ID).phase == PHASE_CONVERGENCE_REQUIRED
