"""Bridge semantic-audit checklist findings to repository repair directions.

The semantic safety-net audit is owned by the active harness, but its verdict
is a structured checklist (``pipeline.audit_result``). A ``REVIEW_REQUIRED``
verdict is not automatically a human escalation: this module classifies each
material audit finding and decides whether the repository already exposes an
actionable repair/search direction for it.

The mapping is deliberately a thin label bridge, not a second scheduler:

* an audit finding whose checklist item maps to a direction the repository
  currently has an actionable finding for is *covered* -- the ordinary
  convergence loop already addresses it;
* an audit finding that is open-ended or has no repository capability becomes
  an explicit operator question (policy/waiver/input), never a blind search;
* the bridge never fabricates a repository finding id and never invents a
  repair -- it only tells the controller which already-declared directions are
  covered and which question must be asked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

# Checklist item -> repository direction label. Derived from the canonical
# evidence-category -> checklist-item mapping in ``pipeline.audit_evidence``,
# collapsed to the Stage 3/refinement direction vocabulary. Items 8 (export
# format consistency) and 9 (open-ended, "anything else material") have no
# automatic scheduling repair: they are operator/report concerns.
AUDIT_ITEM_DIRECTIONS: Mapping[int, str] = {
    1: "participants",
    2: "hosting",
    3: "roster_shape",
    4: "placement",
    5: "hosting",
    6: "hard_constraints",
    7: "hard_constraints",
}
OPERATOR_ONLY_ITEM_IDS: frozenset[int] = frozenset({8, 9})

# Directions the repository repair/search providers can actually act on.
CAPABILITY_DIRECTIONS: frozenset[str] = frozenset(
    {
        "participants",
        "hosting",
        "placement",
        "unplaced_placement",
        "movable_capacity",
        "hard_constraints",
        "roster_shape",
    }
)

# A material audit finding is one that should block automatic PASS. Minor/info
# findings stay informational.
MATERIAL_SEVERITIES: frozenset[str] = frozenset({"major", "critical"})


@dataclass
class AuditFindingDirection:
    """One material semantic-audit finding and its repository mapping."""

    item_id: int | None
    direction: str
    severity: str
    confidence: str
    finding: str
    question: str

    @property
    def mappable(self) -> bool:
        return self.direction in CAPABILITY_DIRECTIONS

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "direction": self.direction,
            "severity": self.severity,
            "confidence": self.confidence,
            "finding": self.finding,
            "question": self.question,
            "mappable": self.mappable,
        }


def direction_for_audit_item(item_id: Any) -> str:
    """Repository direction label for one checklist item (or ``""``)."""
    try:
        resolved = int(item_id)
    except (TypeError, ValueError):
        return ""
    return AUDIT_ITEM_DIRECTIONS.get(resolved, "")


def material_audit_findings(payload: Mapping[str, Any] | None) -> list[AuditFindingDirection]:
    """Every material checklist finding in a persisted audit result."""
    if not payload:
        return []
    out: list[AuditFindingDirection] = []
    for entry in payload.get("checklist_findings") or []:
        if not isinstance(entry, Mapping):
            continue
        severity = str(entry.get("severity") or "").lower()
        if severity not in MATERIAL_SEVERITIES:
            continue
        out.append(
            AuditFindingDirection(
                item_id=entry.get("item_id") if isinstance(entry.get("item_id"), int) else None,
                direction=direction_for_audit_item(entry.get("item_id")),
                severity=severity,
                confidence=str(entry.get("confidence") or ""),
                finding=str(entry.get("finding") or ""),
                question=str(entry.get("question") or ""),
            )
        )
    return out


def audit_convergence_decision(
    payload: Mapping[str, Any] | None,
    *,
    repository_directions: Iterable[str] = (),
) -> dict[str, Any]:
    """Classify an audit verdict against the repository's current directions.

    ``operator_questions`` are the material audit findings the repository
    cannot act on automatically (open-ended items, or a mapped direction the
    repository does not currently surface as an actionable finding). They are
    the exact questions the controller must ask instead of continuing blind.
    """
    status = str((payload or {}).get("status") or "")
    repository = {str(item) for item in repository_directions}
    material = material_audit_findings(payload)
    covered: list[dict[str, Any]] = []
    operator_questions: list[dict[str, Any]] = []
    for finding in material:
        if finding.direction and finding.direction in repository and finding.mappable:
            covered.append(finding.to_dict())
        else:
            operator_questions.append(finding.to_dict())
    return {
        "status": status,
        "review_required": status == "REVIEW_REQUIRED",
        "material_count": len(material),
        "covered": covered,
        "covered_directions": sorted({item["direction"] for item in covered if item["direction"]}),
        "operator_questions": operator_questions,
        "operator_required": bool(operator_questions),
        "convergence_available": status == "REVIEW_REQUIRED" and bool(covered or repository),
    }


def audit_payload_for_current_export(work_dir: Any) -> dict[str, Any] | None:
    """Return the persisted audit result only when it is fresh for the export.

    A stale audit verdict (different export/run) must never steer refinement:
    the candidate it described no longer exists.
    """
    from ..pipeline.audit_result import audit_is_fresh

    fresh, payload = audit_is_fresh(work_dir)
    return payload if fresh else None


__all__ = [
    "AUDIT_ITEM_DIRECTIONS",
    "CAPABILITY_DIRECTIONS",
    "OPERATOR_ONLY_ITEM_IDS",
    "AuditFindingDirection",
    "audit_convergence_decision",
    "audit_payload_for_current_export",
    "direction_for_audit_item",
    "material_audit_findings",
]
