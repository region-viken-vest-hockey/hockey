"""Immutable export projections of semantic-audit workflow state.

The mutable audit/result authority remains in the pipeline work directory.
This module copies only sanitized, fingerprint-bound provenance into the exact
Stage 4 export so a committed export can be reviewed without `.pipeline`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fingerprints import stable_payload_sha256
from .state import PipelineState, StageName

SEMANTIC_AUDIT_FILENAME = "semantic_audit.json"
AUDIT_CONTEXT_FILENAME = "audit_context.json"
PUBLICATION_APPROVAL_FILENAME = "publication_approval.json"

_EXPORT_AUDIT_FIELDS = (
    "schema_version",
    "audit_id",
    "generated_at",
    "run_id",
    "export_fingerprint",
    "audit_context_fingerprint",
    "source_fingerprints",
    "prompt_version",
    "runbook_version",
    "backend",
    "execution_mode",
    "status",
    "operator_assessment",
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


def _checkpoint_export_dir(checkpoint: dict[str, Any]) -> str | None:
    explicit = checkpoint.get("export_dir")
    if explicit:
        return str(explicit)
    output_files = checkpoint.get("output_files")
    if not isinstance(output_files, dict):
        return None
    for value in output_files.values():
        if value:
            return str(Path(str(value)).parent)
    return None


def _resolve_export_context(
    work_dir: str | Path,
    *,
    export_fingerprint: str,
) -> tuple[Path, str, dict[str, Any]] | None:
    """Return ``(export_dir, candidate_fingerprint, checkpoint)`` for the current export.

    ``None`` means there is no materialized export directory yet. A present
    export with mismatched provenance is an error rather than a best-effort
    copy: an audit artifact must never be attached to the wrong schedule.
    """
    checkpoint = PipelineState(work_dir).read_stage(StageName.EXPORT)
    if not isinstance(checkpoint, dict):
        return None
    raw_export_dir = _checkpoint_export_dir(checkpoint)
    if not raw_export_dir:
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

    export_dir = Path(raw_export_dir)
    if not export_dir.is_absolute():
        export_dir = Path(work_dir).parent / export_dir
    return export_dir, candidate_fp, checkpoint


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def context_fingerprint(context: dict[str, Any]) -> str:
    """Deterministic fingerprint of a canonical audit context."""
    return stable_payload_sha256(context)


def materialize_audit_context(
    work_dir: str | Path,
    context: dict[str, Any],
) -> Path | None:
    """Persist the exact sanitized audit context into the matching export dir.

    This is the immutable analysis record of *what evidence the auditor
    actually saw*. The mutable/rebuildable copy (if any) is never reused as
    authority; this file is read back verbatim when binding the audit result
    to its context fingerprint.
    """
    export_fp = str(context.get("export_fingerprint") or "")
    resolved = _resolve_export_context(work_dir, export_fingerprint=export_fp)
    if resolved is None:
        return None
    export_dir, candidate_fp, _checkpoint = resolved
    export_dir.mkdir(parents=True, exist_ok=True)

    artifact = {
        "schema_version": 1,
        "artifact_type": "semantic_audit_context",
        "generated_at": _now_iso(),
        "run_id": context.get("run_id"),
        "export_fingerprint": export_fp,
        "selected_candidate_fingerprint": candidate_fp,
        "context_fingerprint": context_fingerprint(context),
        "context": context,
    }
    path = export_dir / AUDIT_CONTEXT_FILENAME
    path.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return path


def load_materialized_audit_context(
    work_dir: str | Path,
    *,
    export_fingerprint: str,
) -> dict[str, Any] | None:
    """Return the committed audit context for *export_fingerprint*, if any.

    Returns ``None`` when no context artifact exists. Raises ``ValueError``
    when a Stage 4 export exists but its fingerprint is different — a stale
    context must never be silently returned for another export.
    """
    resolved = _resolve_export_context(work_dir, export_fingerprint=export_fingerprint)
    if resolved is None:
        return None
    export_dir, _candidate_fp, _checkpoint = resolved
    path = export_dir / AUDIT_CONTEXT_FILENAME
    if not path.exists():
        return None
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(artifact, dict):
        return None
    if str(artifact.get("export_fingerprint") or "") != export_fingerprint:
        return None
    return artifact


def materialize_audit_result(
    work_dir: str | Path,
    payload: dict[str, Any],
) -> Path | None:
    """Write a sanitized audit result into the exact Stage 4 export directory."""
    export_fp = str(payload.get("export_fingerprint") or "")
    context = _resolve_export_context(work_dir, export_fingerprint=export_fp)
    if context is None:
        return None
    export_dir, candidate_fp, _checkpoint = context
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
    export_dir, candidate_fp, _checkpoint = context
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
