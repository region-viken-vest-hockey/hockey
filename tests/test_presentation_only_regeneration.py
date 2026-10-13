"""Presentation-only regeneration: same canonical revision, new renderer, faithful projection."""

from __future__ import annotations

from pathlib import Path

from tests.test_export_parity import OTHER_REVISION, REVISION, _export_pair
from tournament_scheduler.application.canonical_season.presentation_regeneration import (
    run_presentation_only_regeneration,
)
from tournament_scheduler.application.canonical_season.store_facade import SeasonStateError
from tournament_scheduler.pipeline.stage4_export import run
from tournament_scheduler.pipeline.state import PipelineState
from tournament_scheduler.pipeline.export_parity import STATUS_FAIL, STATUS_PASS
from tournament_scheduler.publication.export_lifecycle import write_draft_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _canonical_export_pair(tmp_path: Path) -> tuple[Path, Path, Path]:
    # A real canonical export always writes a manifest that binds the revision.
    export_dir, xlsx, html = _export_pair(tmp_path)
    write_draft_manifest(
        export_dir,
        export_id="presentation-test",
        generated_at="2026-10-13T00:00:00+00:00",
        export_fingerprint="fp-test",
        source_run_id=None,
        canonical_season="2026-2027",
        canonical_revision=REVISION,
    )
    return export_dir, xlsx, html


def _export_result(export_dir: Path, *, revision: str = REVISION, **overrides) -> dict:
    result = {
        "export_dir": str(export_dir),
        "canonical_revision": revision,
        "export_fingerprint": "fp-test",
        "errors": [],
        "stale_export": False,
    }
    result.update(overrides)
    return result


def _revisions(*values: str):
    queue = list(values)

    def read() -> str:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return read


def _reason_codes(report: dict) -> set[str]:
    return {reason["code"] for reason in report["reasons"]}


def test_unchanged_revision_and_faithful_projection_passes(tmp_path):
    export_dir, _, _ = _canonical_export_pair(tmp_path)

    report = run_presentation_only_regeneration(
        season="2026-2027",
        read_canonical_revision=_revisions(REVISION, REVISION),
        export=lambda: _export_result(export_dir),
    )

    assert report["status"] == STATUS_PASS
    assert report["canonical_revision_before"] == REVISION
    assert report["canonical_revision_after"] == REVISION
    assert report["canonical_revision_unchanged"] is True
    assert report["canonical_schedule_or_decisions_changed"] is False
    assert report["artifact_parity"]["status"] == STATUS_PASS
    assert report["export_fingerprint"] == "fp-test"
    assert report["semantic_audit"]["status"] == "REQUIRED_NOT_RUN"
    assert report["publication"] == {"performed": False, "authorized": False}
    assert report["reasons"] == []


def test_canonical_revision_moving_during_regeneration_is_not_reported_clean(tmp_path):
    export_dir, _, _ = _canonical_export_pair(tmp_path)

    report = run_presentation_only_regeneration(
        season="2026-2027",
        read_canonical_revision=_revisions(REVISION, OTHER_REVISION),
        export=lambda: _export_result(export_dir),
    )

    assert report["status"] == STATUS_FAIL
    assert report["canonical_revision_unchanged"] is False
    assert report["canonical_schedule_or_decisions_changed"] is True
    assert "canonical_revision_changed" in _reason_codes(report)


def test_export_covering_a_different_revision_fails(tmp_path):
    export_dir, _, _ = _canonical_export_pair(tmp_path)

    report = run_presentation_only_regeneration(
        season="2026-2027",
        read_canonical_revision=_revisions(REVISION, REVISION),
        export=lambda: _export_result(export_dir, revision=OTHER_REVISION),
    )

    assert report["status"] == STATUS_FAIL
    assert "canonical_revision_changed" in _reason_codes(report)


def test_stale_export_is_never_a_clean_regeneration(tmp_path):
    export_dir, _, _ = _canonical_export_pair(tmp_path)

    report = run_presentation_only_regeneration(
        season="2026-2027",
        read_canonical_revision=_revisions(REVISION, REVISION),
        export=lambda: _export_result(export_dir, stale_export=True),
    )

    assert report["status"] == STATUS_FAIL
    assert "stale_export" in _reason_codes(report)


def test_renderer_changing_a_canonical_fact_fails_with_unchanged_state(tmp_path):
    export_dir, _, html = _canonical_export_pair(tmp_path)
    text = html.read_text(encoding="utf-8")
    html.write_text(text.replace('"bs": "confirmed_not_booked"', '"bs": "confirmed_booked"', 1), encoding="utf-8")

    report = run_presentation_only_regeneration(
        season="2026-2027",
        read_canonical_revision=_revisions(REVISION, REVISION),
        export=lambda: _export_result(export_dir),
    )

    assert report["canonical_revision_unchanged"] is True
    assert report["status"] == STATUS_FAIL
    assert report["artifact_parity"]["status"] == STATUS_FAIL


def test_export_failure_is_reported_without_claiming_success(tmp_path):
    def failing_export():
        raise SeasonStateError("Canonical season export requires a promoted verification-context problem")

    report = run_presentation_only_regeneration(
        season="2026-2027",
        read_canonical_revision=_revisions(REVISION, REVISION),
        export=failing_export,
    )

    assert report["status"] == STATUS_FAIL
    assert "export_failed" in _reason_codes(report)
    assert report["artifact_parity"]["status"] == "NOT_RUN"


def test_real_stage4_strict_failure_is_reported_as_export_failed(tmp_path):
    # A planless checkpoint makes the real Stage 4 exporter raise Stage4Error in
    # strict mode, the same path a renderer or artifact failure takes.
    def real_failing_export():
        return run({"plan": {}}, PipelineState(str(tmp_path / "pipeline")), export_dir=str(tmp_path / "export"), strict=True)

    report = run_presentation_only_regeneration(
        season="2026-2027",
        read_canonical_revision=_revisions(REVISION, REVISION),
        export=real_failing_export,
    )

    assert report["status"] == STATUS_FAIL
    assert report["canonical_revision_unchanged"] is True
    export_failures = [reason for reason in report["reasons"] if reason["code"] == "export_failed"]
    assert export_failures and "Stage4Error" in export_failures[0]["message"]
    assert report["publication"] == {"performed": False, "authorized": False}


def test_harness_procedures_require_presentation_only_proof_and_never_imply_publication():
    delivery = (REPO_ROOT / ".agents/commands/rvv-miniputt/season-delivery.md").read_text(encoding="utf-8")
    republish = (REPO_ROOT / ".agents/commands/rvv-miniputt/republish.md").read_text(encoding="utf-8")

    for text in (delivery, republish):
        assert "season export --season <season> --presentation-only" in text
        assert "canonical revision" in text
    assert "Export alone never publishes" in republish
