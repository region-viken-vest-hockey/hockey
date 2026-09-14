"""Tests for tournament_scheduler.pipeline.audit_context (issue #325)."""

from __future__ import annotations

import json

from tournament_scheduler.pipeline.audit_context import AUDIT_CHECKLIST, build_audit_context
from tournament_scheduler.pipeline.run_manifest import RunManifest
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus


def _write_export(work_dir, *, fingerprint: str, verify_ok: bool = True) -> None:
    export_dir = work_dir / "export"
    export_dir.mkdir(exist_ok=True)
    (export_dir / "evidence_bundle.json").write_text(
        json.dumps({"source_summary": {"sources_scanned": 3, "blocked_sources": []}}), encoding="utf-8"
    )
    PipelineState(work_dir).write_stage(
        StageName.EXPORT,
        {
            "export_dir": str(export_dir),
            "output_files": {"html": str(export_dir / "season_plan.html")},
            "verify_result": {"ok": verify_ok, "violations": []},
            "export_fingerprint": fingerprint,
        },
        status=StageStatus.DONE,
    )


def test_checklist_has_nine_items_in_order():
    assert [item["item_id"] for item in AUDIT_CHECKLIST] == list(range(1, 10))
    assert AUDIT_CHECKLIST[8]["question"].startswith("Ser harnesset")


def test_context_reflects_the_latest_export_not_a_stale_one(tmp_path):
    _write_export(tmp_path, fingerprint="fp-1")
    first = build_audit_context(work_dir=tmp_path)
    assert first["export_fingerprint"] == "fp-1"

    _write_export(tmp_path, fingerprint="fp-2")
    second = build_audit_context(work_dir=tmp_path)
    assert second["export_fingerprint"] == "fp-2"


def test_context_includes_run_and_source_fingerprints(tmp_path):
    RunManifest(tmp_path).start_run("test objective")
    _write_export(tmp_path, fingerprint="fp-1")
    context = build_audit_context(work_dir=tmp_path)
    assert "run_id" in context
    assert "source_fingerprints" in context
    assert set(context["source_fingerprints"]) == {"input_fingerprint", "effective_config_fingerprint"}


def test_context_includes_evidence_inventory(tmp_path):
    _write_export(tmp_path, fingerprint="fp-1")
    context = build_audit_context(work_dir=tmp_path)
    assert context["output_files"]
    assert context["deterministic_verify_result"] == {"ok": True, "violations": []}
    assert context["publication_readiness"] is not None
    assert context["evidence_bundle"] == {"source_summary": {"sources_scanned": 3, "blocked_sources": []}}
    assert context["calendar_evidence_summary"] == {"sources_scanned": 3, "blocked_sources": []}


def test_context_records_prompt_and_runbook_version(tmp_path):
    _write_export(tmp_path, fingerprint="fp-1")
    context = build_audit_context(work_dir=tmp_path)
    assert context["audit_prompt_version"] == 1
    assert context["runbook_version"]

    # Stable across repeated calls when SKILL.md hasn't changed.
    again = build_audit_context(work_dir=tmp_path)
    assert again["runbook_version"] == context["runbook_version"]


def test_context_without_any_export_has_no_fingerprint(tmp_path):
    context = build_audit_context(work_dir=tmp_path)
    assert context["export_fingerprint"] is None
