from __future__ import annotations

import json
from pathlib import Path

import pytest

from tournament_scheduler.pipeline.export_cleanup import (
    ExportCleanupSafetyError,
    cleanup_superseded_exports,
    plan_superseded_export_cleanup,
)
from tournament_scheduler.pipeline.export_lifecycle import (
    PUBLISHED_STATUS,
    SUPERSEDED_STATUS,
    read_export_manifest,
    write_draft_manifest,
)


def _manifest(root: Path, name: str, fingerprint: str, *, status: str = "draft") -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "payload.txt").write_text(name, encoding="utf-8")
    write_draft_manifest(
        path,
        export_id=name,
        generated_at=f"{name[:10]}T12:00:00+00:00",
        export_fingerprint=fingerprint,
        source_run_id="run-1",
    )
    data = read_export_manifest(path)
    assert data is not None
    data["lifecycle_status"] = status
    (path / "export_manifest.json").write_text(json.dumps(data), encoding="utf-8")
    return path


def _supersede(source: Path, target: Path) -> None:
    source_manifest = read_export_manifest(source)
    target_manifest = read_export_manifest(target)
    assert source_manifest is not None
    assert target_manifest is not None
    source_manifest["lifecycle_status"] = SUPERSEDED_STATUS
    source_manifest["superseded_by"] = {
        "export_dir": str(target),
        "export_id": target_manifest["export_id"],
        "export_fingerprint": target_manifest["export_fingerprint"],
    }
    (source / "export_manifest.json").write_text(
        json.dumps(source_manifest), encoding="utf-8"
    )


def test_cleanup_removes_only_proven_superseded_export(tmp_path: Path) -> None:
    root = tmp_path / "export"
    old = _manifest(root, "2026-09-18T1200", "old")
    replacement = _manifest(root, "2026-09-18T1210", "new")
    published = _manifest(root, "2026-09-18T1220", "pub", status=PUBLISHED_STATUS)
    legacy = root / "2026-09-18T1230"
    legacy.mkdir()
    (legacy / "payload.txt").write_text("legacy", encoding="utf-8")
    _supersede(old, replacement)

    plan = plan_superseded_export_cleanup(root)
    assert [item["export_id"] for item in plan["eligible"]] == ["2026-09-18T1200"]
    assert old.exists()
    assert replacement.exists()
    assert published.exists()
    assert legacy.exists()

    result = cleanup_superseded_exports(root, apply=True)
    assert result["removed"] == ["2026-09-18T1200"]
    assert not old.exists()
    assert replacement.exists()
    assert published.exists()
    assert legacy.exists()


def test_cleanup_refuses_superseded_export_without_valid_replacement(tmp_path: Path) -> None:
    root = tmp_path / "export"
    old = _manifest(root, "2026-09-18T1200", "old", status=SUPERSEDED_STATUS)

    plan = plan_superseded_export_cleanup(root)
    assert plan["eligible"] == []
    assert plan["blocked"][0]["reason"] == "superseded_by_missing"

    with pytest.raises(ExportCleanupSafetyError):
        cleanup_superseded_exports(root, apply=True)
    assert old.exists()


def test_cleanup_refuses_replacement_fingerprint_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "export"
    old = _manifest(root, "2026-09-18T1200", "old")
    replacement = _manifest(root, "2026-09-18T1210", "new")
    _supersede(old, replacement)

    data = read_export_manifest(old)
    assert data is not None
    data["superseded_by"]["export_fingerprint"] = "wrong"
    (old / "export_manifest.json").write_text(json.dumps(data), encoding="utf-8")

    plan = plan_superseded_export_cleanup(root)
    assert plan["eligible"] == []
    assert plan["blocked"][0]["reason"] == "replacement_fingerprint_mismatch"
    with pytest.raises(ExportCleanupSafetyError):
        cleanup_superseded_exports(root, apply=True)
    assert old.exists()
    assert replacement.exists()


def test_cleanup_never_deletes_published_export(tmp_path: Path) -> None:
    root = tmp_path / "export"
    published = _manifest(root, "2026-09-18T1200", "pub", status=PUBLISHED_STATUS)
    data = read_export_manifest(published)
    assert data is not None
    data["published_at"] = "2026-09-18T12:30:00+00:00"
    data["pages_commit"] = "abc"
    (published / "export_manifest.json").write_text(json.dumps(data), encoding="utf-8")

    result = cleanup_superseded_exports(root, apply=True)
    assert result["removed"] == []
    assert published.exists()
