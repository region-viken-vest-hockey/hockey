"""Regression tests for immutable semantic-audit export provenance (#372)."""

from __future__ import annotations

import json

from tournament_scheduler.pipeline.audit_export_artifact import (
    PUBLICATION_APPROVAL_FILENAME,
    SEMANTIC_AUDIT_FILENAME,
    materialize_review_approval,
)
from tournament_scheduler.pipeline.audit_result import write_audit_result
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus


def _findings() -> list[dict]:
    return [
        {
            "item_id": item_id,
            "question": f"q{item_id}",
            "finding": "ok",
            "severity": "info",
            "confidence": "high",
            "evidence": [],
            "could_not_establish": False,
        }
        for item_id in range(1, 10)
    ]


def _audit(*, status: str = "PASS", fingerprint: str = "fp-1") -> dict:
    return {
        "schema_version": 1,
        "audit_id": "audit-1",
        "generated_at": "2026-09-17T12:00:00+00:00",
        "run_id": "run-1",
        "export_fingerprint": fingerprint,
        "source_fingerprints": {"stage2": "source-fp"},
        "prompt_version": 1,
        "runbook_version": "v1",
        "backend": "test",
        "execution_mode": "headless",
        "status": status,
        "checklist_findings": _findings(),
        "potential_missing_rule": [],
        "could_not_independently_establish": [],
        "private_reasoning": "must never be exported",
    }


def _write_export_checkpoint(work_dir, export_dir, *, fingerprint: str = "fp-1", candidate: str = "fp-1") -> None:
    PipelineState(work_dir).write_stage(
        StageName.EXPORT,
        {
            "export_dir": str(export_dir),
            "output_files": {"html": str(export_dir / "season_plan.html")},
            "export_fingerprint": fingerprint,
            "verification_context": {"candidate_fingerprint": candidate},
        },
        status=StageStatus.DONE,
    )


def test_write_audit_result_materializes_sanitized_export_artifact(tmp_path):
    work_dir = tmp_path / ".pipeline"
    export_dir = tmp_path / "export" / "2026-09-17T1200"
    export_dir.mkdir(parents=True)
    _write_export_checkpoint(work_dir, export_dir)

    assert write_audit_result(work_dir, _audit()) == []

    artifact = json.loads((export_dir / SEMANTIC_AUDIT_FILENAME).read_text(encoding="utf-8"))
    assert artifact["status"] == "PASS"
    assert artifact["publication_gate_status"] == "clear"
    assert artifact["selected_candidate_fingerprint"] == "fp-1"
    assert artifact["export_fingerprint"] == "fp-1"
    assert artifact["run_id"] == "run-1"
    assert "private_reasoning" not in artifact


def test_review_required_export_preserves_verdict_when_approval_is_written(tmp_path):
    work_dir = tmp_path / ".pipeline"
    export_dir = tmp_path / "export" / "2026-09-17T1200"
    export_dir.mkdir(parents=True)
    _write_export_checkpoint(work_dir, export_dir)
    audit = _audit(status="REVIEW_REQUIRED")

    assert write_audit_result(work_dir, audit) == []
    materialize_review_approval(
        work_dir,
        audit_payload=audit,
        question={
            "id": "question-1",
            "answer": "godkjenn revisjon",
            "answered_at": "2026-09-17T12:05:00+00:00",
        },
    )

    semantic = json.loads((export_dir / SEMANTIC_AUDIT_FILENAME).read_text(encoding="utf-8"))
    approval = json.loads((export_dir / PUBLICATION_APPROVAL_FILENAME).read_text(encoding="utf-8"))
    assert semantic["status"] == "REVIEW_REQUIRED"
    assert semantic["publication_gate_status"] == "operator_review_required"
    assert approval["audit_verdict"] == "REVIEW_REQUIRED"
    assert approval["approved"] is True
    assert approval["audit_id"] == "audit-1"
    assert approval["export_fingerprint"] == "fp-1"


def test_candidate_or_export_fingerprint_mismatch_rejects_materialization(tmp_path):
    work_dir = tmp_path / ".pipeline"
    export_dir = tmp_path / "export" / "2026-09-17T1200"
    export_dir.mkdir(parents=True)
    _write_export_checkpoint(work_dir, export_dir, fingerprint="fp-1", candidate="different")

    errors = write_audit_result(work_dir, _audit(fingerprint="fp-1"))
    assert errors
    assert "candidate fingerprint" in errors[0]
    assert not (export_dir / SEMANTIC_AUDIT_FILENAME).exists()
