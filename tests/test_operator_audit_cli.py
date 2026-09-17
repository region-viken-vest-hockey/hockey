"""Tests for the 'rvv-miniputt operator audit-context/audit-submit/audit-run'
CLI commands (issue #325)."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

from tournament_scheduler.cli.args import build_parser
from tournament_scheduler.cli.rvv_cli import _cmd_operator
from tournament_scheduler.pipeline.audit_result import read_audit_result
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus

_parser = build_parser()


def _args(*argv: str):
    return _parser.parse_args(["operator", *argv])


def _write_export(work_dir, *, fingerprint: str = "fp-1") -> None:
    PipelineState(work_dir).write_stage(
        StageName.EXPORT,
        {"export_dir": str(work_dir / "export"), "output_files": {"html": "x"}, "export_fingerprint": fingerprint},
        status=StageStatus.DONE,
    )


def _golden_result(*, status: str, export_fingerprint: str = "fp-1") -> dict:
    return {
        "schema_version": 1,
        "audit_id": "a1",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "run_id": "",
        "export_fingerprint": export_fingerprint,
        "source_fingerprints": {},
        "prompt_version": 1,
        "runbook_version": "v1",
        "backend": "test",
        "execution_mode": "interactive_harness",
        "status": status,
        "checklist_findings": [
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
        ],
        "potential_missing_rule": [],
        "could_not_independently_establish": [],
        "raw_response_ref": None,
    }


class TestAuditContextCommand:
    def test_prints_context_json_for_current_export(self, tmp_path, capsys):
        _write_export(tmp_path)
        rc = _cmd_operator(_args("audit-context", "--work-dir", str(tmp_path)))
        assert rc == 0
        printed = json.loads(capsys.readouterr().out)
        assert printed["export_fingerprint"] == "fp-1"
        assert len(printed["checklist"]) == 9

    def test_fails_without_an_export(self, tmp_path, capsys):
        rc = _cmd_operator(_args("audit-context", "--work-dir", str(tmp_path)))
        assert rc == 1


class TestAuditEvidenceCommand:
    def _write_plan_with_shortfall(self, work_dir) -> None:
        PipelineState(work_dir).write_stage(
            StageName.PLANNING,
            {
                "plan": {
                    "tournaments": [],
                    "unresolved_participation_shortfalls": [
                        {"club": "Jar", "label": "Jar 1", "age_group": "U10", "actual": "3", "target": "5"}
                    ],
                }
            },
            status=StageStatus.DONE,
        )
        _write_export(work_dir)

    def test_prints_detailed_evidence_for_a_category(self, tmp_path, capsys):
        self._write_plan_with_shortfall(tmp_path)
        rc = _cmd_operator(
            _args("audit-evidence", "--work-dir", str(tmp_path), "--category", "participation_shortfalls")
        )
        assert rc == 0
        printed = json.loads(capsys.readouterr().out)
        assert printed["matched_record_count"] == 1
        assert printed["export_fingerprint"] == "fp-1"
        assert printed["records"][0]["detail"]["label"] == "Jar 1"

    def test_fails_without_an_export(self, tmp_path, capsys):
        rc = _cmd_operator(_args("audit-evidence", "--work-dir", str(tmp_path), "--item", "1"))
        assert rc == 1


class TestAuditSubmitCommand:
    def test_submits_a_valid_result_from_file(self, tmp_path):
        _write_export(tmp_path)
        result_file = tmp_path / "result.json"
        result_file.write_text(json.dumps(_golden_result(status="PASS")), encoding="utf-8")

        rc = _cmd_operator(
            _args("audit-submit", "--work-dir", str(tmp_path), "--result-file", str(result_file))
        )

        assert rc == 0
        stored = read_audit_result(tmp_path)
        assert stored["status"] == "PASS"

    def test_derives_a_missing_audit_id_through_the_operator_action(self, tmp_path):
        _write_export(tmp_path)
        payload = _golden_result(status="REVIEW_REQUIRED")
        del payload["audit_id"]
        result_file = tmp_path / "result.json"
        result_file.write_text(json.dumps(payload), encoding="utf-8")

        rc = _cmd_operator(
            _args("audit-submit", "--work-dir", str(tmp_path), "--result-file", str(result_file))
        )

        assert rc == 0
        stored = read_audit_result(tmp_path)
        assert stored["audit_id"]
        assert stored["audit_id"] != "None"

    def test_rejects_submission_for_a_different_export_fingerprint(self, tmp_path):
        _write_export(tmp_path, fingerprint="fp-current")
        result_file = tmp_path / "result.json"
        result_file.write_text(json.dumps(_golden_result(status="PASS", export_fingerprint="fp-stale")), encoding="utf-8")

        rc = _cmd_operator(
            _args("audit-submit", "--work-dir", str(tmp_path), "--result-file", str(result_file))
        )

        assert rc == 1
        assert read_audit_result(tmp_path) is None


class TestAuditRunCommand:
    def test_refuses_when_a_harness_session_is_active_without_force(self, tmp_path):
        _write_export(tmp_path)
        with patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "abc"}):
            rc = _cmd_operator(_args("audit-run", "--work-dir", str(tmp_path), "--backend", "llm_bridge"))
        assert rc == 1
        assert read_audit_result(tmp_path) is None

    def test_runs_headless_and_persists_result_when_no_harness_active(self, tmp_path):
        _write_export(tmp_path)
        clean_env = {k: "" for k in ("RVV_HARNESS", "CLAUDE_CODE_SESSION_ID", "PI_SESSION_ID")}
        golden = _golden_result(status="PASS")

        with patch.dict(os.environ, clean_env), patch(
            "tournament_scheduler.llm_judge.audit.run_headless_audit", return_value=golden
        ):
            rc = _cmd_operator(_args("audit-run", "--work-dir", str(tmp_path), "--backend", "llm_bridge"))

        assert rc == 0
        stored = read_audit_result(tmp_path)
        assert stored["status"] == "PASS"

    def test_incomplete_result_is_persisted_but_reported_as_failure(self, tmp_path):
        _write_export(tmp_path)
        clean_env = {k: "" for k in ("RVV_HARNESS", "CLAUDE_CODE_SESSION_ID", "PI_SESSION_ID")}
        golden = _golden_result(status="INCOMPLETE")

        with patch.dict(os.environ, clean_env), patch(
            "tournament_scheduler.llm_judge.audit.run_headless_audit", return_value=golden
        ):
            rc = _cmd_operator(_args("audit-run", "--work-dir", str(tmp_path), "--backend", "llm_bridge"))

        assert rc == 1
        stored = read_audit_result(tmp_path)
        assert stored["status"] == "INCOMPLETE"
