"""Contracts for the read-only operational blocker projection."""

from __future__ import annotations

from tournament_scheduler.application.season_blockers import build_blocker_report
from tournament_scheduler.pipeline.export_parity.gate import CanonicalFreshness
from tournament_scheduler.pipeline.publication_scope import (
    STATUS_BLOCKED,
    STATUS_ELIGIBLE,
    STATUS_HELD,
    STATUS_NOT_CHECKABLE,
)

SEASON = "2026-2027"
REVISION = "revision-1"


def _finding(
    code: str,
    *,
    blocking: bool = True,
    severity: str = "strong_goal",
    tournament_id: str = "",
) -> dict:
    return {
        "finding_id": f"{code}:{tournament_id or 'season'}",
        "code": code,
        "rule_id": code,
        "category": "test",
        "severity": severity,
        "message": f"finding {code}",
        "tournament_id": tournament_id or None,
        "resolution": {
            "status": (
                "actionable_unresolved_violation"
                if blocking
                else "accepted_authoritative_fact"
            ),
            "blocking": blocking,
            "reason": "test",
            "evidence": {},
        },
    }


def _report(
    *,
    scope: dict | None = None,
    findings: list[dict] | None = None,
    verification_ok: bool = True,
    reconciliation_ok: bool = True,
    evidence_blocked: bool = False,
    audit_status: str = "PASS",
    freshness: CanonicalFreshness | None = None,
) -> dict:
    return build_blocker_report(
        season=SEASON,
        revision=REVISION,
        findings_report={
            "revision": REVISION,
            "verification_ok": verification_ok,
            "findings": findings or [],
        },
        publication_scope=scope or {"status": STATUS_ELIGIBLE, "reasons": [], "held": []},
        lifecycle={
            "canonical_state_revision": REVISION,
            "reconciliation": {
                "ok": reconciliation_ok,
                "unexplained_delta": {"changed": not reconciliation_ok},
            },
        },
        publication_evidence={
            "blocked": evidence_blocked,
            "delta_error": "bad published projection" if evidence_blocked else None,
        },
        diagnostic_audit={
            "audit": {
                "status": audit_status,
                "ok": audit_status == "PASS",
                "reasons": ["unresolved planning debt"] if audit_status != "PASS" else [],
                "blocking_finding_count": 1 if audit_status != "PASS" else 0,
            }
        },
        freshness=freshness
        or CanonicalFreshness(revision=REVISION, determined=True),
        source_revisions={"audit": REVISION, "audit_after": REVISION},
    )


def test_eligible_scope_is_clear_even_when_full_season_audit_fails() -> None:
    report = _report(
        findings=[_finding("unplaced_tournament_placement")],
        audit_status="FAIL",
    )

    assert report["status"] == STATUS_ELIGIBLE
    assert report["publishable"] is True
    assert report["exit_code"] == 0
    assert report["genuine_blockers"] == []
    assert report["diagnostic_audit"]["status"] == "FAIL"
    assert report["summary"]["historical_debt"]["count"] == 1


def test_tournament_scoped_hold_is_an_actionable_blocker() -> None:
    report = _report(
        scope={
            "status": STATUS_HELD,
            "held": [
                {
                    "code": "changed_interval_contradicted_booking_evidence",
                    "message": "host rejected the changed interval",
                    "tournament_id": "rvv-0042",
                }
            ],
            "reasons": [],
        }
    )

    assert report["status"] == STATUS_HELD
    assert report["publishable"] is False
    assert report["exit_code"] == 1
    assert report["genuine_blockers"][0]["tournament_ids"] == ["rvv-0042"]


def test_global_reconciliation_failure_blocks_even_when_scope_is_eligible() -> None:
    report = _report(reconciliation_ok=False)

    assert report["status"] == STATUS_BLOCKED
    assert report["exit_code"] == 1
    assert report["global_blockers"][0]["code"] == "canonical_reconciliation_failed"


def test_hard_verification_failure_is_a_global_blocker() -> None:
    report = _report(
        findings=[_finding("arena_interval_conflict", severity="hard", tournament_id="rvv-0007")],
        verification_ok=False,
    )

    assert report["status"] == STATUS_BLOCKED
    assert report["global_blockers"][0]["code"] == "arena_interval_conflict"
    assert report["global_blockers"][0]["tournament_ids"] == ["rvv-0007"]


def test_accepted_authoritative_finding_stays_visible_and_non_blocking() -> None:
    report = _report(
        findings=[
            _finding(
                "ice_time_governing_minimum",
                blocking=False,
                severity="follow_up",
                tournament_id="rvv-0001",
            )
        ]
    )

    assert report["status"] == STATUS_ELIGIBLE
    assert report["accepted_non_blocking"][0]["code"] == "ice_time_governing_minimum"
    assert report["summary"]["accepted_non_blocking"]["count"] == 1


def test_not_checkable_scope_uses_prerequisite_exit_code() -> None:
    report = _report(
        scope={
            "status": STATUS_NOT_CHECKABLE,
            "held": [],
            "reasons": [
                {
                    "code": "no_published_baseline",
                    "message": "no published baseline",
                }
            ],
        }
    )

    assert report["status"] == STATUS_NOT_CHECKABLE
    assert report["publishable"] is False
    assert report["exit_code"] == 2
    assert report["prerequisite_failures"][0]["code"] == "no_published_baseline"


def test_fresh_export_requirement_is_a_global_safety_blocker() -> None:
    report = _report(
        freshness=CanonicalFreshness(
            revision=REVISION,
            requires_fresh_export=True,
            determined=True,
        )
    )

    assert report["status"] == STATUS_BLOCKED
    assert report["global_blockers"][0]["code"] == "requires_fresh_export"
