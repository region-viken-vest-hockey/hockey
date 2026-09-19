"""Immutable audit chain + harness-assessment projection (issue #330).

Covers the narrowed remainder: one committed export directory must contain the
exact audit context, the fingerprint binding between context and verdict, and
the structured operator assessment rendered into ``season_plan.html``.
"""

from __future__ import annotations

import json

from tournament_scheduler.pipeline.audit_assessment import (
    ASSESSMENT_END_MARKER,
    ASSESSMENT_START_MARKER,
    apply_harness_assessment,
    render_harness_assessment_html,
)
from tournament_scheduler.pipeline.audit_export_artifact import (
    AUDIT_CONTEXT_FILENAME,
    SEMANTIC_AUDIT_FILENAME,
    context_fingerprint,
    load_materialized_audit_context,
)
from tournament_scheduler.pipeline.audit_result import validate_audit_result
from tournament_scheduler.pipeline.canonical_export_evidence import (
    build_canonical_export_evidence,
)
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256
from tournament_scheduler.pipeline.operator_action_audit import (
    execute_get_audit_context,
    execute_submit_audit_result,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.serialization.season_plan import season_plan_from_dict

_PLACEHOLDER = (
    '<html><body><div class="app">'
    f"{ASSESSMENT_START_MARKER}"
    '<div id="harnessAssessmentOverview" hidden data-export-fingerprint="$FP$"></div>'
    f"{ASSESSMENT_END_MARKER}"
    "</div></body></html>"
)


def _write_export(work_dir, *, fingerprint: str = "fp-1"):
    export_dir = work_dir / "export"
    export_dir.mkdir(exist_ok=True)
    (export_dir / "season_plan.html").write_text(
        _PLACEHOLDER.replace("$FP$", fingerprint), encoding="utf-8"
    )
    (export_dir / "manual_schedule.html").write_text("manual", encoding="utf-8")
    PipelineState(work_dir).write_stage(
        StageName.EXPORT,
        {
            "export_dir": str(export_dir),
            "output_files": {"html": str(export_dir / "season_plan.html")},
            "export_fingerprint": fingerprint,
            "verification_context": {"candidate_fingerprint": fingerprint},
            "verify_result": {"ok": True, "violations": []},
        },
        status=StageStatus.DONE,
    )
    return export_dir


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


def _audit_payload(*, status: str = "REVIEW_REQUIRED", fingerprint: str = "fp-1") -> dict:
    return {
        "schema_version": 1,
        "audit_id": "audit-1",
        "generated_at": "2026-09-19T08:00:00+00:00",
        "run_id": "run-1",
        "export_fingerprint": fingerprint,
        "source_fingerprints": {},
        "prompt_version": 2,
        "runbook_version": "v1",
        "backend": "interactive",
        "execution_mode": "interactive_harness",
        "status": status,
        "checklist_findings": _findings(),
        "potential_missing_rule": [],
        "could_not_independently_establish": [],
    }


def _assessment() -> dict:
    return {
        "operator_summary": "Planen er brukbar med ett manuelt unntak.",
        "key_tradeoffs": [
            {"title": "Reise", "summary": "Akseptert for å unngå konflikt", "severity": "minor"}
        ],
        "remaining_actions": [
            {"title": "Kongsberg U11", "summary": "Book istid", "category": "manual_placement"}
        ],
        "limitations": ["Klubbkalenderen for en kilde var utdatert"],
    }


def test_audit_context_is_materialized_immutably(tmp_path):
    export_dir = _write_export(tmp_path)
    result = execute_get_audit_context(work_dir=str(tmp_path))
    assert result.status == "ok"

    artifact = json.loads((export_dir / AUDIT_CONTEXT_FILENAME).read_text(encoding="utf-8"))
    assert artifact["artifact_type"] == "semantic_audit_context"
    assert artifact["export_fingerprint"] == "fp-1"
    assert artifact["selected_candidate_fingerprint"] == "fp-1"
    assert artifact["context_fingerprint"] == context_fingerprint(artifact["context"])
    assert artifact["context"]["export_fingerprint"] == "fp-1"
    assert artifact["context"]["checklist"][0]["item_id"] == 1


def test_submit_binds_context_and_projects_assessment_into_html(tmp_path):
    export_dir = _write_export(tmp_path)
    execute_get_audit_context(work_dir=str(tmp_path))
    materialized = json.loads((export_dir / AUDIT_CONTEXT_FILENAME).read_text(encoding="utf-8"))

    payload = _audit_payload()
    payload["operator_assessment"] = _assessment()
    result = execute_submit_audit_result(work_dir=str(tmp_path), result=payload)
    assert result.status == "ok"

    semantic = json.loads((export_dir / SEMANTIC_AUDIT_FILENAME).read_text(encoding="utf-8"))
    assert semantic["audit_context_fingerprint"] == materialized["context_fingerprint"]
    assert semantic["operator_assessment"] == _assessment()

    html = (export_dir / "season_plan.html").read_text(encoding="utf-8")
    assert "Vurdering fra planleggingsassistent" in html
    assert "Planen er brukbar med ett manuelt unntak." in html
    assert "Kongsberg U11" in html
    assert 'data-export-fingerprint="fp-1"' in html
    assert "manual_schedule.html" in html
    # Detailed checklist findings stay out of the default season-plan view.
    assert "q1" not in html


def test_context_binding_never_reuses_a_stale_export_context(tmp_path):
    export_dir = _write_export(tmp_path, fingerprint="fp-1")
    execute_get_audit_context(work_dir=str(tmp_path))
    first = json.loads((export_dir / AUDIT_CONTEXT_FILENAME).read_text(encoding="utf-8"))

    _write_export(tmp_path, fingerprint="fp-2")
    result = execute_submit_audit_result(
        work_dir=str(tmp_path), result=_audit_payload(fingerprint="fp-2")
    )
    assert result.status == "ok"

    semantic = json.loads((export_dir / SEMANTIC_AUDIT_FILENAME).read_text(encoding="utf-8"))
    assert semantic["audit_context_fingerprint"] != first["context_fingerprint"]
    refreshed = load_materialized_audit_context(tmp_path, export_fingerprint="fp-2")
    assert refreshed is not None
    assert refreshed["context_fingerprint"] == semantic["audit_context_fingerprint"]
    assert refreshed["export_fingerprint"] == "fp-2"


def test_operator_assessment_shape_is_validated():
    payload = _audit_payload()
    payload["operator_assessment"] = {"key_tradeoffs": [{"title": "mangler summary"}]}
    errors = validate_audit_result(payload)
    assert any("operator_summary" in error for error in errors)
    assert any("title and summary" in error for error in errors)

    payload["operator_assessment"] = _assessment()
    assert validate_audit_result(payload) == []


def test_render_harness_assessment_escapes_submitted_text():
    artifact = {
        "status": "PASS",
        "export_fingerprint": "fp",
        "audit_id": "a",
        "run_id": "r",
        "generated_at": "2026-09-19T08:00:00+00:00",
        "operator_assessment": {"operator_summary": "<script>alert(1)</script>"},
    }
    html = render_harness_assessment_html(artifact)
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html
    assert "REVISJON: PASS" in html


def test_apply_harness_assessment_replaces_only_the_marked_section(tmp_path):
    page = tmp_path / "season_plan.html"
    page.write_text(
        "<header>KEEP-HEADER</header>" + _PLACEHOLDER.replace("$FP$", "fp") + "<footer>KEEP-FOOTER</footer>",
        encoding="utf-8",
    )
    artifact = {
        "status": "PASS",
        "export_fingerprint": "fp",
        "operator_assessment": {"operator_summary": "Alt ok"},
    }
    assert apply_harness_assessment(artifact, html_path=page) is True
    text = page.read_text(encoding="utf-8")
    assert "KEEP-HEADER" in text and "KEEP-FOOTER" in text
    assert "Alt ok" in text
    assert text.count(ASSESSMENT_START_MARKER) == 1
    assert text.count(ASSESSMENT_END_MARKER) == 1


def test_canonical_export_evidence_bundle_is_self_describing():
    plan = {"tournaments": [], "publication_readiness": {"status": "PASS"}}
    fingerprint = stable_payload_sha256([])
    schedule = {
        "season": "2026-2027",
        "revision": "rev-1",
        "plan": plan,
        "verification_context": {"run_id": "run-1", "problem": None},
    }
    checkpoint = {
        "export_dir": "/tmp/export",
        "output_files": {"html": "/tmp/export/season_plan.html"},
        "export_fingerprint": fingerprint,
        "verify_result": {"ok": True, "violations": []},
        "public_export_context": {"scrape": {"source_count": 2, "blocked": ["down-source"]}},
    }
    bundle = build_canonical_export_evidence(schedule=schedule, export_checkpoint=checkpoint)
    assert bundle["evidence_source"] == "canonical_season_export"
    assert bundle["canonical_season"] == "2026-2027"
    assert bundle["canonical_revision"] == "rev-1"
    assert bundle["run_id"] == "run-1"
    assert bundle["fingerprint_consistent"] is True
    assert bundle["export"]["fingerprint"] == fingerprint
    assert bundle["final_operator_evidence"]["export_fingerprint"] == fingerprint
    assert bundle["source_summary"]["blocked_sources"] == ["down-source"]


def test_season_plan_html_stamps_fingerprint_and_report_excludes_assessment(tmp_path):
    from tournament_scheduler.html.html_exporter import HtmlExporter

    plan = season_plan_from_dict(
        {
            "schema_version": 1,
            "start_date": "2026-10-01",
            "end_date": "2027-03-01",
            "tournaments": [],
            "team_game_counts": {},
        }
    )
    out = tmp_path / "season_plan.html"
    HtmlExporter().export(
        plan,
        out,
        pipeline_meta={"export_fingerprint": "ABC123", "age_groups": []},
    )
    schedule_html = out.read_text(encoding="utf-8")
    assert ASSESSMENT_START_MARKER in schedule_html
    assert 'data-export-fingerprint="ABC123"' in schedule_html
    report_html = out.with_name("season_plan_report.html").read_text(encoding="utf-8")
    assert ASSESSMENT_START_MARKER not in report_html
