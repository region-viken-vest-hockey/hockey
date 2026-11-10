"""Pydantic v2 structural schema for the semantic audit result (issue #912).

This is the single structural contract for a model/agent-submitted audit
result: field presence, types, and the severity/confidence/status
vocabularies. It is DTO-first (Python models, no parallel hand-written JSON
Schema) and used for *validation only*: the stored artifact is still the
caller's original dict so unknown/forward fields round-trip unchanged
(``extra="allow"``). Scalar types are intentionally strict: payloads that the
legacy presence-only checks happened to accept with non-string text or IDs are
invalid rather than being silently coerced.

Deliberately out of scope here, and still owned by
:mod:`.audit_result` and its consumers: required checklist coverage
(item ids 1-9), export fingerprint / run freshness, audit-id derivation,
and PASS/INCOMPLETE publication policy. A schema library never decides
publication authority.

Versioning: :data:`~.audit_result.AUDIT_RESULT_SCHEMA_VERSION` is the only
schema version. ``schema_version`` is validated as a positive integer here;
rejecting versions this code does not understand is a domain decision made
in :func:`.audit_result.validate_audit_result`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, StrictInt, StringConstraints, ValidationError

Severity = Literal["info", "minor", "major", "critical"]
Confidence = Literal["low", "medium", "high"]
AuditStatus = Literal["PASS", "REVIEW_REQUIRED", "FAIL", "INCOMPLETE"]

VALID_STATUSES = frozenset(get_args(AuditStatus))
VALID_SEVERITIES = frozenset(get_args(Severity))
VALID_CONFIDENCES = frozenset(get_args(Confidence))

_NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
_REQUIRED_NONEMPTY = ("schema_version", "audit_id", "export_fingerprint", "generated_at", "status")


class _Open(BaseModel):
    """Forward-compatible base: unknown fields are preserved, not rejected."""

    model_config = ConfigDict(extra="allow")


class ChecklistFinding(_Open):
    item_id: StrictInt
    question: str
    finding: str
    severity: Severity
    confidence: Confidence


class PotentialMissingRule(_Open):
    description: str


class KeyTradeoff(_Open):
    title: _NonEmptyStr
    summary: _NonEmptyStr
    severity: Severity | None = None


class RemainingAction(_Open):
    title: _NonEmptyStr
    summary: _NonEmptyStr
    category: str | None = None


class OperatorAssessment(_Open):
    operator_summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    key_tradeoffs: list[KeyTradeoff] | None = None
    remaining_actions: list[RemainingAction] | None = None
    limitations: list[str] | None = None


class AuditResultModel(_Open):
    schema_version: StrictInt
    audit_id: _NonEmptyStr
    export_fingerprint: _NonEmptyStr
    generated_at: _NonEmptyStr
    # May legitimately be empty (no run manifest yet) but the key must exist
    # so a stored result always has *some* value to compare freshness against.
    run_id: str | None
    status: AuditStatus
    checklist_findings: list[ChecklistFinding]
    potential_missing_rule: list[PotentialMissingRule] | None = None
    audit_context_fingerprint: str | None = None
    operator_assessment: OperatorAssessment | None = None


def structural_errors(payload: Any) -> list[str]:
    """Return actionable structural errors for *payload* (empty when valid)."""
    if not isinstance(payload, dict):
        return ["payload is not a JSON object"]
    try:
        AuditResultModel.model_validate(payload)
    except ValidationError as exc:
        return list(dict.fromkeys(_format(err) for err in exc.errors()))
    return []


def _format(err: Any) -> str:
    loc = tuple(err["loc"])
    kind = err["type"]
    value = err.get("input")
    path = ".".join(str(part) for part in loc)
    head = loc[0] if loc else ""

    if len(loc) == 1:
        simple = _format_top_level(head, kind, value)
        if simple:
            return simple
    if head == "checklist_findings" and len(loc) >= 2:
        return _format_checklist_entry(loc, kind, value)
    if head == "potential_missing_rule" and len(loc) >= 2:
        return "potential_missing_rule entries must be objects with a 'description' field"
    if head == "operator_assessment" and len(loc) >= 2:
        return _format_assessment(loc, kind, value)
    return f"{path}: {err['msg']}"


def _format_top_level(head: Any, kind: str, value: Any) -> str | None:
    if head == "checklist_findings":
        return "checklist_findings must be a list"
    if kind == "missing" or (head in _REQUIRED_NONEMPTY and value in (None, "")):
        return f"missing required field: {head}"
    if head == "status" and kind == "literal_error":
        return f"invalid status {value!r}; must be one of {sorted(VALID_STATUSES)}"
    if head == "audit_context_fingerprint":
        return "audit_context_fingerprint must be a string when present"
    if head == "operator_assessment":
        return "operator_assessment must be an object"
    return None


def _format_checklist_entry(loc: tuple, kind: str, value: Any) -> str:
    if len(loc) == 2:
        return "checklist_findings entries must be objects"
    field = loc[2]
    if kind == "missing":
        return f"checklist_findings entry missing field: {field}"
    if kind == "literal_error" and field in ("severity", "confidence"):
        return f"checklist_findings entry has invalid {field} {value!r}"
    return f"checklist_findings entry field {field} has an invalid value ({kind})"


def _format_assessment(loc: tuple, kind: str, value: Any) -> str:
    field = loc[1]
    if field == "operator_summary":
        return "operator_assessment.operator_summary must be a non-empty string"
    if len(loc) == 2:
        return f"operator_assessment.{field} must be a list when present"
    if field == "limitations":
        return "operator_assessment.limitations entries must be strings"
    if len(loc) == 3 or loc[3] in ("title", "summary"):
        return f"operator_assessment.{field} entries must have title and summary"
    if kind == "literal_error":
        return f"operator_assessment.{field} entry has invalid severity {value!r}"
    return f"operator_assessment.{field} entry category must be a string"
