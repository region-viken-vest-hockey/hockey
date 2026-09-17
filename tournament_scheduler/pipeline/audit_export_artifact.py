"""Immutable export projections of semantic-audit workflow state.

The mutable audit/result authority remains in the pipeline work directory.
This module copies only sanitized, fingerprint-bound provenance into the exact
Stage 4 export so a committed export can be reviewed without `.pipeline`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .state import PipelineState, StageName

SEMANTIC_AUDIT_FILENAME = "semantic_audit.json"
PUBLICATION_APPROVAL_FILENAME = "publication_approval.json"

_EXPORT_AUDIT_FIELDS = (
    "schema_version",
    "audit_id",
    "generated_at",
    "run_id",
    "export_fingerprint",
    "source_fingerprints",
    "prompt_version",
    "runbook_version",
    "backend",
    "execution_mode",
    "status",
    "audit_metrics",
    "checklist_findings",
    "potential_missing_rule",
    "could_not_independently_establish",
)


def _publication_gate_status(status: str | None) -> str:
    if status == "PASS":
        return "clear"
    if status == "REVIEW_REQUIRED":
        return "operator_review_required"
    return "blocked"


def _resolve_export_context(
    work_dir: str | Path,
    *,
    export_fingerprint: str,
) -> tuple[Path, str] | None:
    """Return ``(export_dir, candidate_fingerprint)`` for the current export.

    ``None`` means there is no materialized export directory yet. A present
    export with mismatched provenance is an error rather than a best-effort
    copy: an audit artifact must never be attached to the wrong schedule.
    """
    checkpoint = PipelineState(work_dir).read_stage(StageName.EXPORT)
    if not isinstance(checkpoint, dict) or not checkpoint.get("export_dir"):
        return None

    current_fp = str(checkpoint.get("export_fingerprint") or "")
    if not current_fp or current_fp != export_fingerprint:
        raise ValueError(
            "semantic audit export fingerprint does not match the current Stage 4 export"
        )

    verification_context = checkpoint.get("verification_context")
    candidate_fp = (
        str(verification_context.get("candidate_fingerprint") or "")
        if isinstance(verification_context, dict)
        else ""
    )
    if candidate_fp and candidate_fp != current_fp:
        raise ValueError(
            "Stage 4 candidate fingerprint does not match the current export fingerprint"
        )
    candidate_fp = candidate_fp or current_fp

    export_dir = Path(str(checkpoint["export_dir"]))
    if not export_dir.is_absolute():
        export_dir = Path(work_dir).parent / export_dir
    return export_dir, candidate_fp


def materialize_audit_result(
    work_dir: str | Path,
    payload: dict[str, Any],
) -> Path | None:
    """Write a sanitized audit result into the exact Stage 4 export directory."""
    export_fp = str(payload.get("export_fingerprint") or "")
    context = _resolve_export_context(work_dir, export_fingerprint=export_fp)
    if context is None:
        return None
    export_dir, candidate_fp = context
    export_dir.mkdir(parents=True, exist_ok=True)

    artifact = {key: payload[key] for key in _EXPORT_AUDIT_FIELDS if key in payload}
    artifact.update(
        {
            "artifact_type": "semantic_audit",
            "selected_candidate_fingerprint": candidate_fp,
            "publication_gate_status": _publication_gate_status(payload.get("status")),
        }
    )
    path = export_dir / SEMANTIC_AUDIT_FILENAME
    path.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return path


def materialize_review_approval(
    work_dir: str | Path,
    *,
    audit_payload: dict[str, Any],
    question: dict[str, Any],
) -> Path:
    """Persist explicit approval without overwriting the original audit verdict."""
    if audit_payload.get("status") != "REVIEW_REQUIRED":
        raise ValueError("publication approval artifact requires a REVIEW_REQUIRED audit")
    export_fp = str(audit_payload.get("export_fingerprint") or "")
    context = _resolve_export_context(work_dir, export_fingerprint=export_fp)
    if context is None:
        raise ValueError("cannot persist publication approval without a Stage 4 export directory")
    export_dir, candidate_fp = context
    export_dir.mkdir(parents=True, exist_ok=True)

    artifact = {
        "schema_version": 1,
        "artifact_type": "publication_approval",
        "audit_id": audit_payload.get("audit_id"),
        "audit_verdict": "REVIEW_REQUIRED",
        "run_id": audit_payload.get("run_id"),
        "export_fingerprint": export_fp,
        "selected_candidate_fingerprint": candidate_fp,
        "approved": True,
        "question_id": question.get("id"),
        "answer": question.get("answer"),
        "answered_at": question.get("answered_at"),
    }
    path = export_dir / PUBLICATION_APPROVAL_FILENAME
    path.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return path
