"""Tests for tournament_scheduler.pipeline.audit_result (issue #325)."""

from __future__ import annotations

from tournament_scheduler.pipeline.audit_result import (
    audit_is_fresh,
    build_audit_id,
    current_export_fingerprint,
    is_blocking_status,
    read_audit_result,
    validate_audit_result,
    with_resolved_audit_id,
    write_audit_result,
)
from tournament_scheduler.pipeline.run_manifest import RunManifest
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus


def _checklist_findings() -> list[dict]:
    return [
        {
            "item_id": i,
            "question": f"q{i}",
            "finding": "ok",
            "severity": "info",
            "confidence": "high",
            "evidence": [],
            "could_not_establish": False,
        }
        for i in range(1, 10)
    ]


def _golden(*, status: str, export_fingerprint: str = "fp-1", run_id: str = "run-1") -> dict:
    return {
        "schema_version": 1,
        "audit_id": build_audit_id(export_fingerprint=export_fingerprint, run_id=run_id, generated_at="t"),
        "generated_at": "2026-01-01T00:00:00+00:00",
        "run_id": run_id,
        "export_fingerprint": export_fingerprint,
        "source_fingerprints": {},
        "prompt_version": 1,
        "runbook_version": "v1",
        "backend": "test",
        "execution_mode": "headless",
        "status": status,
        "checklist_findings": _checklist_findings(),
        "potential_missing_rule": [],
        "could_not_independently_establish": [],
        "raw_response_ref": None,
    }


def _write_export_checkpoint(work_dir, *, fingerprint: str) -> None:
    PipelineState(work_dir).write_stage(
        StageName.EXPORT,
        {"output_files": {"html": "x"}, "export_fingerprint": fingerprint},
        status=StageStatus.DONE,
    )


class TestValidateAuditResult:
    def test_golden_pass_is_valid(self):
        assert validate_audit_result(_golden(status="PASS")) == []

    def test_golden_review_required_is_valid(self):
        assert validate_audit_result(_golden(status="REVIEW_REQUIRED")) == []

    def test_golden_fail_is_valid(self):
        assert validate_audit_result(_golden(status="FAIL")) == []

    def test_rejects_missing_status(self):
        payload = _golden(status="PASS")
        del payload["status"]
        errors = validate_audit_result(payload)
        assert any("status" in e for e in errors)

    def test_rejects_invalid_status_enum(self):
        errors = validate_audit_result(_golden(status="MAYBE"))
        assert any("invalid status" in e for e in errors)

    def test_rejects_wrong_checklist_item_count(self):
        payload = _golden(status="PASS")
        payload["checklist_findings"] = payload["checklist_findings"][:5]
        errors = validate_audit_result(payload)
        assert any("item_id" in e for e in errors)

    def test_potential_missing_rule_round_trips(self, tmp_path):
        payload = _golden(status="REVIEW_REQUIRED")
        payload["potential_missing_rule"] = [
            {"description": "host club never participates", "severity": "major", "confidence": "high", "evidence": []}
        ]
        errors = write_audit_result(tmp_path, payload)
        assert errors == []
        stored = read_audit_result(tmp_path)
        assert stored["potential_missing_rule"] == payload["potential_missing_rule"]

    def test_rejects_missing_audit_id(self):
        payload = _golden(status="PASS")
        del payload["audit_id"]
        errors = validate_audit_result(payload)
        assert any("audit_id" in e for e in errors)

    def test_rejects_empty_audit_id(self):
        payload = _golden(status="PASS")
        payload["audit_id"] = ""
        errors = validate_audit_result(payload)
        assert any("audit_id" in e for e in errors)


class TestWithResolvedAuditId:
    def test_fills_in_a_missing_id_from_export_run_and_timestamp(self):
        payload = _golden(status="REVIEW_REQUIRED")
        del payload["audit_id"]
        resolved = with_resolved_audit_id(payload)
        assert resolved["audit_id"] == build_audit_id(
            export_fingerprint=payload["export_fingerprint"],
            run_id=payload["run_id"],
            generated_at=payload["generated_at"],
        )
        # The original payload is left untouched (no surprising in-place mutation).
        assert "audit_id" not in payload

    def test_preserves_an_explicit_id(self):
        payload = _golden(status="REVIEW_REQUIRED")
        assert with_resolved_audit_id(payload) is payload

    def test_two_exports_resolve_to_different_ids(self):
        first = _golden(status="REVIEW_REQUIRED", export_fingerprint="fp-1")
        second = _golden(status="REVIEW_REQUIRED", export_fingerprint="fp-2")
        del first["audit_id"]
        del second["audit_id"]
        assert with_resolved_audit_id(first)["audit_id"] != with_resolved_audit_id(second)["audit_id"]

    def test_write_audit_result_persists_a_computed_id(self, tmp_path):
        payload = _golden(status="REVIEW_REQUIRED")
        del payload["audit_id"]
        assert write_audit_result(tmp_path, payload) == []
        stored = read_audit_result(tmp_path)
        assert stored["audit_id"] == build_audit_id(
            export_fingerprint="fp-1", run_id="run-1", generated_at=payload["generated_at"]
        )


class TestIsBlockingStatus:
    def test_pass_is_not_blocking(self):
        assert is_blocking_status("PASS") is False

    def test_review_required_is_not_blocking_on_its_own(self):
        assert is_blocking_status("REVIEW_REQUIRED") is False

    def test_fail_is_blocking(self):
        assert is_blocking_status("FAIL") is True

    def test_incomplete_is_blocking(self):
        assert is_blocking_status("INCOMPLETE") is True

    def test_missing_status_is_blocking(self):
        assert is_blocking_status(None) is True


class TestAuditIsFresh:
    def test_no_stored_result_is_not_fresh(self, tmp_path):
        _write_export_checkpoint(tmp_path, fingerprint="fp-1")
        fresh, stored = audit_is_fresh(tmp_path)
        assert fresh is False
        assert stored is None

    def test_matching_fingerprint_is_fresh(self, tmp_path):
        run_id = RunManifest(tmp_path).start_run("objective")["run_id"]
        _write_export_checkpoint(tmp_path, fingerprint="fp-1")
        write_audit_result(tmp_path, _golden(status="PASS", export_fingerprint="fp-1", run_id=run_id))
        fresh, stored = audit_is_fresh(tmp_path)
        assert fresh is True
        assert stored["status"] == "PASS"

    def test_mismatched_run_id_is_not_fresh_even_with_matching_fingerprint(self, tmp_path):
        RunManifest(tmp_path).start_run("objective")
        _write_export_checkpoint(tmp_path, fingerprint="fp-1")
        write_audit_result(tmp_path, _golden(status="PASS", export_fingerprint="fp-1", run_id="some-other-run"))
        fresh, _stored = audit_is_fresh(tmp_path)
        assert fresh is False

    def test_stale_audit_from_a_different_export_fingerprint_is_rejected(self, tmp_path):
        run_id = RunManifest(tmp_path).start_run("objective")["run_id"]
        _write_export_checkpoint(tmp_path, fingerprint="fp-1")
        write_audit_result(tmp_path, _golden(status="PASS", export_fingerprint="fp-1", run_id=run_id))

        # Export regenerated -> new fingerprint. The stored audit no longer covers it.
        _write_export_checkpoint(tmp_path, fingerprint="fp-2")
        fresh, stored = audit_is_fresh(tmp_path)
        assert fresh is False
        assert stored["export_fingerprint"] == "fp-1"

    def test_current_export_fingerprint_reads_live_checkpoint(self, tmp_path):
        assert current_export_fingerprint(tmp_path) is None
        _write_export_checkpoint(tmp_path, fingerprint="fp-1")
        assert current_export_fingerprint(tmp_path) == "fp-1"
