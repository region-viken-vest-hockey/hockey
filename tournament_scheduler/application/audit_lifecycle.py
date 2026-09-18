"""Persistence and transition ownership for the audit/convergence workflow.

This is the lifecycle layer for the phase defined in
:mod:`tournament_scheduler.pipeline.audit_workflow`. The typed phase lives on
the canonical :class:`~tournament_scheduler.application.stage3_session.Stage3Session`,
so it survives process boundaries exactly like the candidate revision/export it
describes rather than in a parallel side file.

The transition functions are the *only* way the phase changes:

* :func:`mark_audit_required` -- a (new) Stage 4 export exists;
* :func:`record_audit_verdict` -- a submitted verdict, where ``REVIEW_REQUIRED``
  enters convergence instead of terminating the run;
* :func:`record_convergence_result` -- bounded convergence committed a mutation
  (fresh export -> fresh audit) or reached an explicit terminal.

:func:`current_workflow` reconciles the persisted phase with the live export
fingerprint, so a stale audit/complete state can never satisfy a newer export
and a harness never has to infer the next transition from prose.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .stage3_session import Stage3Session
from .stage3_session_store import Stage3SessionStore
from ..pipeline.audit_workflow import (
    PHASE_AUDIT_REQUIRED,
    PHASE_COMPLETE,
    PHASE_CONVERGENCE_REQUIRED,
    TERMINAL_REASONS,
    TERMINAL_PASS,
    AuditWorkflow,
    completion_blockers as _phase_blockers,
    workflow_view,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _active_run_id(work_dir: Any) -> str:
    try:
        from ..pipeline.run_manifest import RunManifest

        return str(RunManifest(work_dir).read().get("run_id") or "")
    except Exception:
        return ""


def _load(work_dir: Any, run_id: str | None) -> Stage3Session:
    return Stage3SessionStore(work_dir).load(expected_run_id=run_id or None)


def _persist(work_dir: Any, run_id: str | None, workflow: AuditWorkflow) -> None:
    store = Stage3SessionStore(work_dir)
    session = store.load(expected_run_id=run_id or None)
    if run_id and not session.run_id:
        session.run_id = run_id
    session.audit_workflow = workflow.to_dict()
    store.save(session)


def _current_export(work_dir: Any) -> tuple[str, str]:
    from ..pipeline.audit_result import current_export_fingerprint
    from ..pipeline.state import PipelineState, StageName

    fingerprint = current_export_fingerprint(work_dir) or ""
    checkpoint = PipelineState(work_dir).read_stage(StageName.EXPORT) or {}
    return fingerprint, str(checkpoint.get("export_dir") or "")


def _session_identity(session: Stage3Session) -> tuple[int | None, str, str]:
    fingerprint = session.finalized_fingerprint or session.candidate_fingerprint
    revision = (
        session.finalized_revision
        if session.finalized_revision is not None
        else session.candidate_revision
    )
    return revision, str(fingerprint or ""), str(session.run_id or "")


def current_workflow(
    work_dir: Any, *, run_id: str | None = None, reconcile: bool = True
) -> AuditWorkflow | None:
    """Return the workflow phase reconciled with the live export.

    Returns ``None`` only when there is neither a persisted workflow nor a
    current Stage 4 export. A persisted phase whose export fingerprint no longer
    matches the current export is reported as a derived ``audit_required`` for
    the new fingerprint (never persisted here): the audit that described the
    older export must not satisfy the newer one.
    """
    session = _load(work_dir, run_id)
    stored = AuditWorkflow.from_dict(session.audit_workflow)
    revision, candidate_fingerprint, _ = _session_identity(session)
    export_fingerprint, export_dir = _current_export(work_dir)

    if not stored.phase:
        if not export_fingerprint:
            return None
        return AuditWorkflow(
            phase=PHASE_AUDIT_REQUIRED,
            export_fingerprint=export_fingerprint,
            export_dir=export_dir,
            candidate_revision=revision,
            candidate_fingerprint=candidate_fingerprint,
        )

    if reconcile and export_fingerprint and stored.export_fingerprint != export_fingerprint:
        return AuditWorkflow(
            phase=PHASE_AUDIT_REQUIRED,
            export_fingerprint=export_fingerprint,
            export_dir=export_dir,
            candidate_revision=revision,
            candidate_fingerprint=candidate_fingerprint,
            last_audit_status=stored.last_audit_status,
            transitions=list(stored.transitions),
        )
    return stored


def mark_audit_required(
    work_dir: Any, *, run_id: str | None = None, at: str | None = None
) -> AuditWorkflow | None:
    """Mark the current export as awaiting a fresh semantic audit.

    Idempotent for an unchanged export: re-marking the *same* export that is
    already complete (same content fingerprint and candidate) does not reset the
    completed workflow, so a redundant re-export cannot manufacture new work.
    """
    export_fingerprint, export_dir = _current_export(work_dir)
    if not export_fingerprint:
        return current_workflow(work_dir, run_id=run_id)
    session = _load(work_dir, run_id)
    stored = AuditWorkflow.from_dict(session.audit_workflow)
    revision, candidate_fingerprint, _ = _session_identity(session)
    if (
        stored.is_complete
        and stored.export_fingerprint == export_fingerprint
        and stored.candidate_fingerprint == candidate_fingerprint
    ):
        return stored
    if (
        stored.phase == PHASE_AUDIT_REQUIRED
        and stored.export_fingerprint == export_fingerprint
        and stored.candidate_fingerprint == candidate_fingerprint
    ):
        # Already awaiting the (same) audit: do not append a duplicate
        # transition or rewrite identical state.
        return stored
    workflow = stored
    workflow.record(
        PHASE_AUDIT_REQUIRED,
        at=at or _now(),
        export_fingerprint=export_fingerprint,
        export_dir=export_dir,
        candidate_revision=revision,
        candidate_fingerprint=candidate_fingerprint,
        last_audit_status="",
    )
    _persist(work_dir, run_id, workflow)
    return workflow


def record_audit_verdict(
    work_dir: Any, payload: Any, *, run_id: str | None = None, at: str | None = None
) -> AuditWorkflow | None:
    """Persist the workflow transition implied by one submitted audit verdict."""
    workflow = current_workflow(work_dir, run_id=run_id)
    if workflow is None:
        return None
    status = str((payload or {}).get("status") or "")
    if status == "REVIEW_REQUIRED":
        workflow.record(
            PHASE_CONVERGENCE_REQUIRED,
            at=at or _now(),
            last_audit_status=status,
        )
        _persist(work_dir, run_id, workflow)
        return workflow
    if status == "PASS":
        workflow.record(
            PHASE_COMPLETE,
            at=at or _now(),
            last_audit_status=status,
            terminal_reason=TERMINAL_PASS,
            terminal_detail="The semantic safety-net audit returned PASS for the current export.",
        )
        _persist(work_dir, run_id, workflow)
        return workflow
    # FAIL/INCOMPLETE: never complete, keep the run non-terminal.
    workflow.record(
        PHASE_AUDIT_REQUIRED,
        at=at or _now(),
        last_audit_status=status,
    )
    _persist(work_dir, run_id, workflow)
    return workflow


def record_convergence_result(
    work_dir: Any, result: Any, *, run_id: str | None = None, at: str | None = None
) -> AuditWorkflow | None:
    """Persist the workflow transition implied by one bounded-convergence report.

    A committed mutation produced a fresh export/revision, so a fresh audit is
    mandatory before completion. Only a terminal report that committed nothing
    in this batch may complete the workflow.
    """
    result = result or {}
    workflow = current_workflow(work_dir, run_id=run_id)
    if workflow is None:
        return None
    if not result.get("ok") or result.get("dry_run"):
        return workflow
    if result.get("audit_required") or result.get("committed_epochs"):
        return mark_audit_required(work_dir, run_id=run_id, at=at)
    reason = str(result.get("terminal_reason") or "")
    if reason in TERMINAL_REASONS:
        workflow.record(
            PHASE_COMPLETE,
            at=at or _now(),
            terminal_reason=reason,
            terminal_detail=str(result.get("terminal_detail") or ""),
        )
        _persist(work_dir, run_id, workflow)
    return workflow


def workflow_snapshot(work_dir: Any, *, run_id: str | None = None) -> dict[str, Any] | None:
    workflow = current_workflow(work_dir, run_id=run_id)
    if workflow is None:
        return None
    return workflow_view(workflow, work_dir)


def completion_blockers(work_dir: Any, *, run_id: str | None = None) -> list[str]:
    """Reasons the run may not be reported complete (empty when allowed)."""
    workflow = current_workflow(work_dir, run_id=run_id)
    if workflow is None:
        return []
    return _phase_blockers(workflow)


def request_completion(
    work_dir: Any, *, run_id: str | None = None
) -> dict[str, Any]:
    """Guarded request to declare the run complete.

    Refused while ``AUDIT_REQUIRED`` or ``CONVERGENCE_REQUIRED`` is pending, and
    refused for a workflow that was completed against an export that has since
    been superseded. Returns the canonical next transition on refusal so the
    caller can continue instead of stopping.
    """
    workflow = current_workflow(work_dir, run_id=run_id)
    blockers = _phase_blockers(workflow) if workflow is not None else [
        "No audit/convergence workflow has been started for the current export."
    ]
    view = workflow_view(workflow, work_dir) if workflow is not None else None
    if blockers:
        return {
            "ok": False,
            "reason": "workflow_pending",
            "blockers": blockers,
            "workflow": view,
        }
    return {"ok": True, "workflow": view}


__all__ = [
    "completion_blockers",
    "current_workflow",
    "mark_audit_required",
    "record_audit_verdict",
    "record_convergence_result",
    "request_completion",
    "workflow_snapshot",
]
