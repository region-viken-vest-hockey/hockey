"""Persistence, schema validation, and staleness checks for the harness-led
semantic safety-net audit result (issue #325 Phase 1).

The audit result is a standalone sibling artifact next to the Stage 4 export
(``audit_result.json``), schema-versioned the same way as
:mod:`.evidence_bundle`, and pinned to the exact export it was produced
against via Stage 4's own ``export_fingerprint`` (see
``stage4_export.py``'s ``export_fingerprint = stable_payload_sha256(...)``).
That fingerprint is recomputed fresh from the current Stage 4 checkpoint on
every read, never trusted from a cached value, so a stale or mismatched
audit is always detectable rather than silently reused (issue #325
requirement: "stale audit fingerprints are invalid").

The mutable workflow copy remains in the pipeline work directory. When Stage 4
has a materialized export directory, a sanitized immutable projection is also
written there by :mod:`.audit_export_artifact` so committed exports remain
self-describing without making the export copy a second resume authority.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fingerprints import stable_payload_sha256
from .state import PipelineState, StageName

AUDIT_RESULT_SCHEMA_VERSION = 1

_RESULT_FILENAME = "audit_result.json"

# A partial/failed audit run must never silently read as PASS (issue #325).
# INCOMPLETE is the explicit "audit ran but produced no trustworthy verdict"
# state; it is treated identically to FAIL for publish-gating purposes.
_VALID_STATUSES = {"PASS", "REVIEW_REQUIRED", "FAIL", "INCOMPLETE"}
_BLOCKING_STATUSES = {"FAIL", "INCOMPLETE"}
_VALID_SEVERITIES = {"info", "minor", "major", "critical"}
_VALID_CONFIDENCES = {"low", "medium", "high"}
_REQUIRED_CHECKLIST_ITEM_IDS = set(range(1, 10))  # the 9-item checklist


def audit_result_path(work_dir: "str | Path") -> Path:
    return Path(work_dir) / _RESULT_FILENAME


def current_export_fingerprint(work_dir: "str | Path") -> str | None:
    """Return the Stage 4 export's own content fingerprint, recomputed fresh
    from the live checkpoint on every call (never from a cached copy)."""
    export_checkpoint = PipelineState(work_dir).read_stage(StageName.EXPORT)
    fingerprint = export_checkpoint.get("export_fingerprint")
    return str(fingerprint) if fingerprint else None


def current_run_id(work_dir: "str | Path") -> str | None:
    from .run_manifest import RunManifest

    run_id = RunManifest(work_dir).read().get("run_id")
    return str(run_id) if run_id else None


def _checklist_item_ids(payload: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    for item in payload.get("checklist_findings") or []:
        if isinstance(item, dict) and isinstance(item.get("item_id"), int):
            ids.add(item["item_id"])
    return ids


def validate_audit_result(payload: dict[str, Any]) -> list[str]:
    """Structurally validate *payload* against the audit result schema.

    Returns a list of human-readable validation errors; an empty list means
    the payload is valid. Deliberately a hand-rolled whitelist check (no new
    JSON-schema dependency), matching ``llm_judge.prompts``' existing
    "validate against a known whitelist, never trust unparsed output" style.
    """
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["payload is not a JSON object"]

    for key in ("schema_version", "audit_id", "export_fingerprint", "generated_at", "status"):
        if key not in payload or payload.get(key) in (None, ""):
            errors.append(f"missing required field: {key}")
    # run_id may legitimately be empty (e.g. no run manifest was started for
    # this workspace yet) — it only needs to be present as a key so a
    # stored result always has *some* value to compare freshness against.
    if "run_id" not in payload:
        errors.append("missing required field: run_id")

    status = payload.get("status")
    if status is not None and status not in _VALID_STATUSES:
        errors.append(f"invalid status {status!r}; must be one of {sorted(_VALID_STATUSES)}")

    findings = payload.get("checklist_findings")
    if not isinstance(findings, list):
        errors.append("checklist_findings must be a list")
    else:
        found_ids = _checklist_item_ids(payload)
        missing_ids = _REQUIRED_CHECKLIST_ITEM_IDS - found_ids
        if missing_ids:
            errors.append(f"checklist_findings missing item_id(s): {sorted(missing_ids)}")
        for entry in findings:
            if not isinstance(entry, dict):
                errors.append("checklist_findings entries must be objects")
                continue
            for key in ("item_id", "question", "finding", "severity", "confidence"):
                if key not in entry:
                    errors.append(f"checklist_findings entry missing field: {key}")
            severity = entry.get("severity")
            if severity is not None and severity not in _VALID_SEVERITIES:
                errors.append(f"checklist_findings entry has invalid severity {severity!r}")
            confidence = entry.get("confidence")
            if confidence is not None and confidence not in _VALID_CONFIDENCES:
                errors.append(f"checklist_findings entry has invalid confidence {confidence!r}")

    for finding in payload.get("potential_missing_rule") or []:
        if not isinstance(finding, dict) or "description" not in finding:
            errors.append("potential_missing_rule entries must be objects with a 'description' field")

    context_fingerprint = payload.get("audit_context_fingerprint")
    if context_fingerprint is not None and not isinstance(context_fingerprint, str):
        errors.append("audit_context_fingerprint must be a string when present")

    if "operator_assessment" in payload and payload.get("operator_assessment") is not None:
        _validate_operator_assessment(payload.get("operator_assessment"), errors)

    return errors


def _validate_operator_assessment(assessment: Any, errors: list[str]) -> None:
    """Validate the optional structured harness assessment projection.

    The assessment is the operator-facing conclusion the harness submits with
    its verdict; it is explicitly not a second rule engine, so this only
    enforces a small, stable shape (there is exactly one source of truth for
    counts/validity: the deterministic evidence).
    """
    if not isinstance(assessment, dict):
        errors.append("operator_assessment must be an object")
        return
    summary = assessment.get("operator_summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("operator_assessment.operator_summary must be a non-empty string")
    for key in ("key_tradeoffs", "remaining_actions", "limitations"):
        value = assessment.get(key)
        if value is not None and not isinstance(value, list):
            errors.append(f"operator_assessment.{key} must be a list when present")
    for entry in assessment.get("key_tradeoffs") or []:
        if not isinstance(entry, dict) or not entry.get("title") or not entry.get("summary"):
            errors.append("operator_assessment.key_tradeoffs entries must have title and summary")
            continue
        severity = entry.get("severity")
        if severity is not None and severity not in _VALID_SEVERITIES:
            errors.append(f"operator_assessment.key_tradeoffs entry has invalid severity {severity!r}")
    for entry in assessment.get("remaining_actions") or []:
        if not isinstance(entry, dict) or not entry.get("title") or not entry.get("summary"):
            errors.append("operator_assessment.remaining_actions entries must have title and summary")
            continue
        category = entry.get("category")
        if category is not None and not isinstance(category, str):
            errors.append("operator_assessment.remaining_actions entry category must be a string")
    for entry in assessment.get("limitations") or []:
        if not isinstance(entry, str):
            errors.append("operator_assessment.limitations entries must be strings")


def read_audit_result(work_dir: "str | Path") -> dict[str, Any] | None:
    path = audit_result_path(work_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_audit_result(work_dir: "str | Path", payload: dict[str, Any]) -> list[str]:
    """Validate and persist *payload* as the current audit result.

    Returns validation errors (empty on success); refuses to write an
    invalid payload rather than persisting something a later reader would
    have to re-validate to trust. A caller that omitted ``audit_id`` gets a
    server-computed one (see :func:`with_resolved_audit_id`) so every stored
    result carries the identity that scopes any later review approval to
    this exact export.

    When the Stage 4 checkpoint names a materialized export directory, the
    sanitized immutable export projection is written first. A provenance or
    I/O failure there rejects the submission instead of leaving the committed
    export without the audit result that gates its publication.
    """
    payload = with_resolved_audit_id(payload)
    errors = validate_audit_result(payload)
    if errors:
        return errors

    try:
        from .audit_export_artifact import materialize_audit_result

        materialize_audit_result(work_dir, payload)
    except (OSError, ValueError) as exc:
        return [f"failed to materialize semantic audit in Stage 4 export: {exc}"]

    path = audit_result_path(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return []


def build_audit_id(*, export_fingerprint: str, run_id: str, generated_at: str) -> str:
    return stable_payload_sha256(
        {"export_fingerprint": export_fingerprint, "run_id": run_id, "generated_at": generated_at}
    )[:16]


def with_resolved_audit_id(payload: dict[str, Any]) -> dict[str, Any]:
    """Return *payload* with a server-computed ``audit_id`` when the caller
    omitted one.

    ``audit_id`` is what keeps a REVIEW_REQUIRED operator approval scoped to
    the export it was actually raised for. A harness submission is allowed
    to leave it out; the server must still derive a stable, content-derived
    id (from the export fingerprint, run and timestamp it already has to
    provide) so two unrelated exports' review questions never collapse into
    the same identity — and an old approval never silently covers new
    content. Returns *payload* unchanged when an id is already present or a
    required input is missing (the missing field is reported by validation).
    """
    if not isinstance(payload, dict) or payload.get("audit_id"):
        return payload
    export_fingerprint = payload.get("export_fingerprint")
    run_id = payload.get("run_id")
    generated_at = payload.get("generated_at")
    if not export_fingerprint or run_id is None or generated_at in (None, ""):
        return payload
    resolved = dict(payload)
    resolved["audit_id"] = build_audit_id(
        export_fingerprint=str(export_fingerprint),
        run_id=str(run_id),
        generated_at=str(generated_at),
    )
    return resolved


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def audit_is_fresh(work_dir: "str | Path") -> tuple[bool, dict[str, Any] | None]:
    """Return ``(is_fresh, stored_result)``.

    ``stored_result`` is the persisted audit result (or ``None`` if none
    exists). ``is_fresh`` is ``True`` only when a result exists and its
    recorded ``export_fingerprint``/``run_id`` match the *current* live
    values — a regenerated export or a new run always invalidates a prior
    audit, regardless of what status it recorded.
    """
    stored = read_audit_result(work_dir)
    if stored is None:
        return False, None
    current_fp = current_export_fingerprint(work_dir)
    current_run = current_run_id(work_dir)
    if not current_fp or stored.get("export_fingerprint") != current_fp:
        return False, stored
    if current_run and stored.get("run_id") and stored.get("run_id") != current_run:
        return False, stored
    return True, stored


def is_blocking_status(status: "str | None") -> bool:
    """True for a status that must never be treated as clear-to-publish."""
    return status is None or status in _BLOCKING_STATUSES
