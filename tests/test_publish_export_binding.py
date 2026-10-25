"""Operator publish refuses an export whose bytes are not the reviewed ones (issue #682)."""

from __future__ import annotations

import argparse

import pytest

from tournament_scheduler.cli.pipeline_orchestrator import operator_publish
from tournament_scheduler.pipeline.operator_action import _execute_publish_pages
from tournament_scheduler.publication.export_lifecycle import (
    EXPORT_LIFECYCLE_FILENAME,
    write_draft_manifest,
)

REVISION = "rev-reviewed"


def _reviewed_export(tmp_path):
    """Write export artifacts, then the lifecycle manifest, as stage 4 does."""

    export_dir = tmp_path / "export" / "2026-10-13T1107"
    export_dir.mkdir(parents=True)
    (export_dir / "season.html").write_text("<html>reviewed</html>", encoding="utf-8")
    (export_dir / "season.xlsx").write_bytes(b"reviewed-workbook")
    write_draft_manifest(
        export_dir,
        export_id="2026-10-13T1107",
        generated_at="2026-10-13T11:07:00+00:00",
        export_fingerprint="tournament-list-hash",
        source_run_id="run-1",
        canonical_season="2026-2027",
        canonical_revision=REVISION,
    )
    return export_dir


def _reviewed_content_sha(export_dir) -> str:
    from tournament_scheduler.publication.export_lifecycle import export_content_sha256

    return export_content_sha256(export_dir)


def test_content_hash_is_stable_and_covers_every_artifact(tmp_path):
    export_dir = _reviewed_export(tmp_path)
    before = _reviewed_content_sha(export_dir)

    assert _reviewed_content_sha(export_dir) == before

    (export_dir / "season.html").write_text("<html>changed</html>", encoding="utf-8")

    assert _reviewed_content_sha(export_dir) != before


def test_content_hash_ignores_the_lifecycle_manifest(tmp_path):
    export_dir = _reviewed_export(tmp_path)
    before = _reviewed_content_sha(export_dir)

    (export_dir / EXPORT_LIFECYCLE_FILENAME).write_text("{}", encoding="utf-8")

    assert _reviewed_content_sha(export_dir) == before


def test_binding_accepts_the_reviewed_export(tmp_path):
    from tournament_scheduler.pipeline.export_binding import assert_export_binding

    export_dir = _reviewed_export(tmp_path)

    manifest = assert_export_binding(
        export_dir,
        expected_export_content_sha256=_reviewed_content_sha(export_dir),
        expected_canonical_revision=REVISION,
    )

    assert manifest["canonical_revision"] == REVISION


def test_binding_refuses_an_artifact_changed_after_review(tmp_path):
    """The manifest still matches; only the bytes changed. This is the case a manifest check misses."""

    from tournament_scheduler.pipeline.export_binding import assert_export_binding

    export_dir = _reviewed_export(tmp_path)
    reviewed = _reviewed_content_sha(export_dir)
    (export_dir / "season.html").write_text("<html>edited after review</html>", encoding="utf-8")

    with pytest.raises(ValueError, match="Export content mismatch"):
        assert_export_binding(
            export_dir,
            expected_export_content_sha256=reviewed,
            expected_canonical_revision=REVISION,
        )


def test_binding_refuses_a_changed_canonical_revision(tmp_path):
    from tournament_scheduler.pipeline.export_binding import assert_export_binding

    export_dir = _reviewed_export(tmp_path)

    with pytest.raises(ValueError, match="Canonical revision mismatch"):
        assert_export_binding(
            export_dir,
            expected_export_content_sha256=_reviewed_content_sha(export_dir),
            expected_canonical_revision="rev-moved-on",
        )


def test_binding_refuses_an_export_without_a_lifecycle_manifest(tmp_path):
    from tournament_scheduler.pipeline.export_binding import assert_export_binding

    export_dir = tmp_path / "export" / "empty"
    export_dir.mkdir(parents=True)

    with pytest.raises(ValueError, match="lifecycle manifest missing"):
        assert_export_binding(
            export_dir,
            expected_export_content_sha256="anything",
            expected_canonical_revision=REVISION,
        )


def test_publish_action_refuses_a_modified_export_before_publishing(tmp_path):
    """The publish action itself enforces the binding, so preview and apply share the check."""

    export_dir = _reviewed_export(tmp_path)
    reviewed = _reviewed_content_sha(export_dir)
    (export_dir / "season.xlsx").write_bytes(b"edited-after-review")

    result = _execute_publish_pages(
        work_dir=str(tmp_path / ".pipeline"),
        export_dir=str(export_dir),
        expected_export_content_sha256=reviewed,
        expected_canonical_revision=REVISION,
    )

    assert result.status == "failed"
    assert "Export content mismatch" in result.summary


def _publish_args(**overrides) -> argparse.Namespace:
    values = {
        "work_dir": ".pipeline",
        "export_dir": None,
        "expected_revision": None,
        "expected_content_sha256": None,
        "json": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.fixture
def action_must_not_run(monkeypatch):
    def _fail(args):
        raise AssertionError("publish action must not run for a refused request shape")

    monkeypatch.setattr(operator_publish, "_execute_operator_publish", _fail)


def test_publish_refuses_a_stale_export_and_does_not_publish(tmp_path):
    export_dir = _reviewed_export(tmp_path)
    reviewed = _reviewed_content_sha(export_dir)
    (export_dir / "season.html").write_text("<html>edited</html>", encoding="utf-8")
    args = _publish_args(
        work_dir=str(tmp_path / ".pipeline"),
        export_dir=str(export_dir),
        expected_revision=REVISION,
        expected_content_sha256=reviewed,
    )

    assert operator_publish._cmd_operator_publish(args) == 1


def test_publish_requires_both_expectations_together(tmp_path, action_must_not_run):
    export_dir = _reviewed_export(tmp_path)
    args = _publish_args(export_dir=str(export_dir), expected_content_sha256="x")

    assert operator_publish._cmd_operator_publish(args) == 1


def test_publish_expectations_require_an_explicit_export_dir(action_must_not_run):
    args = _publish_args(expected_revision=REVISION, expected_content_sha256="x")

    assert operator_publish._cmd_operator_publish(args) == 1
