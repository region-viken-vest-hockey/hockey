"""Tests for tournament_scheduler.llm_judge.audit (issue #325).

Follows tests/test_llm_judge.py's pattern of patching urllib.request.urlopen
rather than making live model calls — the checklist here covers "mock/golden
responses exercise orchestration without live-model calls."
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from tournament_scheduler.llm_judge.audit import build_audit_prompt, run_headless_audit
from tournament_scheduler.pipeline.audit_context import AUDIT_CHECKLIST


def _context() -> dict:
    return {
        "audit_prompt_version": 1,
        "runbook_version": "v1",
        "checklist": [dict(item) for item in AUDIT_CHECKLIST],
        "run_id": "run-1",
        "export_fingerprint": "fp-1",
        "source_fingerprints": {},
        "export_dir": "/tmp/export",
        "output_files": {"html": "season_plan.html"},
        "deterministic_verify_result": {"ok": True, "violations": []},
        "publication_readiness": {"status": "PUBLISHABLE", "publishable": True, "reasons": []},
        "evidence_bundle": None,
        "calendar_evidence_summary": {},
    }


def _mock_llm_bridge_response(content: str) -> MagicMock:
    body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    cm = MagicMock()
    cm.__enter__ = lambda s: s
    cm.__exit__ = MagicMock(return_value=False)
    cm.read = MagicMock(return_value=body)
    return cm


def _golden_response_json(status: str) -> str:
    return json.dumps(
        {
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
        }
    )


def test_prompt_requests_all_nine_checklist_items():
    prompt = build_audit_prompt(_context())
    for item in AUDIT_CHECKLIST:
        assert item["question"] in prompt


def test_prompt_includes_fingerprints_and_evidence():
    prompt = build_audit_prompt(_context())
    assert "fp-1" in prompt
    assert "run-1" in prompt


class TestRunHeadlessAudit:
    def test_golden_pass_response_produces_pass_result(self):
        with patch("urllib.request.urlopen", return_value=_mock_llm_bridge_response(_golden_response_json("PASS"))):
            result = run_headless_audit(_context(), "llm_bridge")
        assert result["status"] == "PASS"
        assert result["execution_mode"] == "headless"
        assert result["export_fingerprint"] == "fp-1"
        assert result["run_id"] == "run-1"

    def test_golden_review_required_response(self):
        with patch(
            "urllib.request.urlopen", return_value=_mock_llm_bridge_response(_golden_response_json("REVIEW_REQUIRED"))
        ):
            result = run_headless_audit(_context(), "llm_bridge")
        assert result["status"] == "REVIEW_REQUIRED"

    def test_golden_fail_response(self):
        with patch("urllib.request.urlopen", return_value=_mock_llm_bridge_response(_golden_response_json("FAIL"))):
            result = run_headless_audit(_context(), "llm_bridge")
        assert result["status"] == "FAIL"

    def test_unparseable_response_becomes_incomplete_never_pass(self):
        with patch("urllib.request.urlopen", return_value=_mock_llm_bridge_response("not json at all")):
            result = run_headless_audit(_context(), "llm_bridge")
        assert result["status"] == "INCOMPLETE"
        assert result["status"] != "PASS"

    def test_schema_invalid_response_becomes_incomplete(self):
        bad = json.dumps({"status": "PASS", "checklist_findings": []})  # missing 9 items
        with patch("urllib.request.urlopen", return_value=_mock_llm_bridge_response(bad)):
            result = run_headless_audit(_context(), "llm_bridge")
        assert result["status"] == "INCOMPLETE"

    def test_backend_error_becomes_incomplete(self):
        with patch("urllib.request.urlopen", side_effect=RuntimeError("boom")):
            result = run_headless_audit(_context(), "llm_bridge")
        assert result["status"] == "INCOMPLETE"
        assert "boom" in result["could_not_independently_establish"][0]
