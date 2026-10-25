"""Operator publish refuses an export that is not the reviewed one (issue #682)."""

from __future__ import annotations

import argparse

import pytest

from tournament_scheduler.cli.pipeline_orchestrator import operator_publish
from tournament_scheduler.publication.export_lifecycle import write_draft_manifest

FINGERPRINT = "fp-reviewed"
REVISION = "rev-reviewed"


def _reviewed_export(tmp_path):
    export_dir = tmp_path / "export" / "2026-10-13T1107"
    export_dir.mkdir(parents=True)
    write_draft_manifest(
        export_dir,
        export_id="2026-10-13T1107",
        generated_at="2026-10-13T11:07:00+00:00",
        export_fingerprint=FINGERPRINT,
        source_run_id="run-1",
        canonical_season="2026-2027",
        canonical_revision=REVISION,
    )
    return export_dir


def test_binding_accepts_the_reviewed_export(tmp_path):
    from tournament_scheduler.publication.export_lifecycle import assert_export_binding

    export_dir = _reviewed_export(tmp_path)

    manifest = assert_export_binding(
        export_dir,
        expected_export_fingerprint=FINGERPRINT,
        expected_canonical_revision=REVISION,
    )

    assert manifest["export_fingerprint"] == FINGERPRINT


def test_binding_refuses_a_changed_export_fingerprint(tmp_path):
    from tournament_scheduler.publication.export_lifecycle import assert_export_binding

    export_dir = _reviewed_export(tmp_path)

    with pytest.raises(ValueError, match="Export fingerprint mismatch"):
        assert_export_binding(
            export_dir,
            expected_export_fingerprint="fp-other",
            expected_canonical_revision=REVISION,
        )


def test_binding_refuses_a_changed_canonical_revision(tmp_path):
    from tournament_scheduler.publication.export_lifecycle import assert_export_binding

    export_dir = _reviewed_export(tmp_path)

    with pytest.raises(ValueError, match="Canonical revision mismatch"):
        assert_export_binding(
            export_dir,
            expected_export_fingerprint=FINGERPRINT,
            expected_canonical_revision="rev-moved-on",
        )


def test_binding_refuses_an_export_without_a_lifecycle_manifest(tmp_path):
    from tournament_scheduler.publication.export_lifecycle import assert_export_binding

    export_dir = tmp_path / "export" / "empty"
    export_dir.mkdir(parents=True)

    with pytest.raises(ValueError, match="lifecycle manifest missing"):
        assert_export_binding(
            export_dir,
            expected_export_fingerprint=FINGERPRINT,
            expected_canonical_revision=REVISION,
        )


def _publish_args(**overrides) -> argparse.Namespace:
    values = {
        "work_dir": ".pipeline",
        "export_dir": None,
        "expected_revision": None,
        "expected_fingerprint": None,
        "json": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.fixture
def action_must_not_run(monkeypatch):
    def _fail(args):
        raise AssertionError("publish action must not run for a refused binding")

    monkeypatch.setattr(operator_publish, "_execute_operator_publish", _fail)


def test_publish_refuses_a_stale_export_before_the_action_runs(tmp_path, action_must_not_run):
    export_dir = _reviewed_export(tmp_path)
    args = _publish_args(
        export_dir=str(export_dir),
        expected_revision="rev-moved-on",
        expected_fingerprint=FINGERPRINT,
    )

    assert operator_publish._cmd_operator_publish(args) == 1


def test_publish_requires_both_expectations_together(tmp_path, action_must_not_run):
    export_dir = _reviewed_export(tmp_path)
    args = _publish_args(export_dir=str(export_dir), expected_fingerprint=FINGERPRINT)

    assert operator_publish._cmd_operator_publish(args) == 1


def test_publish_expectations_require_an_explicit_export_dir(action_must_not_run):
    args = _publish_args(expected_revision=REVISION, expected_fingerprint=FINGERPRINT)

    assert operator_publish._cmd_operator_publish(args) == 1
