"""Semantic-audit → repository repair direction bridge (issue #384).

The audit verdict belongs to the active harness; these tests prove the
repository classifies a REVIEW_REQUIRED verdict into covered directions and
explicit operator questions instead of either ignoring it or searching blindly.
"""

from __future__ import annotations

from tournament_scheduler.application.audit_convergence import (
    audit_convergence_decision,
    direction_for_audit_item,
    material_audit_findings,
)


def _payload(*findings: dict) -> dict:
    return {"status": "REVIEW_REQUIRED", "checklist_findings": list(findings)}


def _finding(item_id: int, severity: str = "major", finding: str = "problem") -> dict:
    return {
        "item_id": item_id,
        "question": f"question {item_id}",
        "finding": finding,
        "severity": severity,
        "confidence": "high",
    }


def test_checklist_items_map_to_repository_directions() -> None:
    assert direction_for_audit_item(1) == "participants"
    assert direction_for_audit_item(2) == "hosting"
    assert direction_for_audit_item(6) == "hard_constraints"
    # Export-format and open-ended items have no automatic scheduling repair.
    assert direction_for_audit_item(8) == ""
    assert direction_for_audit_item(9) == ""


def test_only_material_severities_are_material() -> None:
    findings = material_audit_findings(
        _payload(_finding(1, "minor"), _finding(2, "major"), _finding(3, "critical"))
    )
    assert [f.item_id for f in findings] == [2, 3]


def test_open_ended_audit_finding_becomes_an_operator_question() -> None:
    decision = audit_convergence_decision(
        _payload(_finding(9, "major", "missing rule about X")),
        repository_directions=["participants", "hosting"],
    )
    assert decision["operator_required"] is True
    assert [q["item_id"] for q in decision["operator_questions"]] == [9]
    assert "missing rule about X" in decision["operator_questions"][0]["finding"]


def test_mapped_and_actionable_finding_is_covered() -> None:
    decision = audit_convergence_decision(
        _payload(_finding(1, "major", "participation shortfall")),
        repository_directions=["participants"],
    )
    assert decision["operator_required"] is False
    assert decision["covered_directions"] == ["participants"]
    assert decision["operator_questions"] == []


def test_mapped_but_not_actionable_finding_is_an_operator_question() -> None:
    # The repository does not currently surface a hosting finding, so the audit
    # claim cannot be acted on automatically; ask rather than search blindly.
    decision = audit_convergence_decision(
        _payload(_finding(2, "major", "hosting deficit")),
        repository_directions=["participants"],
    )
    assert decision["operator_required"] is True
    assert [q["item_id"] for q in decision["operator_questions"]] == [2]


def test_non_review_required_does_not_claim_convergence() -> None:
    decision = audit_convergence_decision({"status": "PASS"}, repository_directions=["participants"])
    assert decision["review_required"] is False
    assert decision["operator_required"] is False
    assert decision["convergence_available"] is False
