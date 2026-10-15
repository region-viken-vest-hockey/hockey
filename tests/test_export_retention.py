"""Draft-export retention must not prune exports the repository still relies on.

Regression: ``export/2026-10-06T1358`` was an ordinary draft manifest, but canonical
``mark_export_fresh`` history referenced it. Draft retention deleted it (and the
tracked files went with it) once newer drafts existed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from tournament_scheduler.pipeline import stage4_export_timing as timing
from tournament_scheduler.pipeline.stage4_export_errors import Stage4Error
from tournament_scheduler.publication.export_lifecycle import (
    canonical_export_references,
    prune_draft_exports,
    write_draft_manifest,
)

SEASON = "2026-2027"
UNREFERENCED_OLD = "2026-10-01T1341"
HISTORY_REFERENCED = "2026-10-06T1358"
EXPORT_STATE_REFERENCED = "2026-10-15T1026"
NEWER_DRAFTS = ["2026-10-13T1107", "2026-10-15T1012", "2026-10-15T1108"]


def _draft(export_root: Path, name: str) -> Path:
    directory = export_root / name
    directory.mkdir(parents=True)
    write_draft_manifest(
        directory,
        export_id=name,
        generated_at=f"{name[:10]}T{name[11:13]}:{name[13:]}:00+00:00",
        export_fingerprint=f"fp-{name}",
        source_run_id="run-1",
        canonical_season=SEASON,
        canonical_revision="655a8b98",
    )
    (directory / "season_plan.xlsx").write_bytes(b"artifact")
    return directory


def _export_root(tmp_path: Path) -> Path:
    root = tmp_path / "export"
    for name in [UNREFERENCED_OLD, HISTORY_REFERENCED, *NEWER_DRAFTS, EXPORT_STATE_REFERENCED]:
        _draft(root, name)
    return root


def _canonical_decisions() -> dict[str, Any]:
    """Shape of canonical decisions that point at real exports (as in season/2026-2027)."""
    return {
        "export_state": {"status": "fresh", "export_dir": f"export/{EXPORT_STATE_REFERENCED}"},
        "history": [
            {"event": "approve", "tournament_id": "rvv-0001", "details": {}},
            {
                "event": "mark_export_fresh",
                "tournament_id": "",
                "details": {"export_dir": f"export/{HISTORY_REFERENCED}", "canonical_state_revision": "x"},
            },
        ],
    }


def _sorted_dirs(root: Path) -> list[Path]:
    return sorted(root.iterdir())


def test_history_referenced_draft_is_retained_while_unreferenced_old_draft_is_pruned(tmp_path: Path) -> None:
    root = _export_root(tmp_path)
    retained = canonical_export_references(_canonical_decisions())

    removed = prune_draft_exports(_sorted_dirs(root), keep=3, retained_names=retained)

    assert removed == [UNREFERENCED_OLD]
    assert (root / HISTORY_REFERENCED / "season_plan.xlsx").exists()
    assert (root / EXPORT_STATE_REFERENCED / "season_plan.xlsx").exists()


def test_without_canonical_references_the_2026_10_06_shape_is_pruned(tmp_path: Path) -> None:
    """Documents the original failure: with nothing retained, the history draft is deleted."""
    root = _export_root(tmp_path)

    removed = prune_draft_exports(_sorted_dirs(root), keep=3)

    assert HISTORY_REFERENCED in removed


def test_canonical_references_read_export_state_and_history_names_only() -> None:
    assert canonical_export_references(_canonical_decisions()) == {
        HISTORY_REFERENCED,
        EXPORT_STATE_REFERENCED,
    }
    assert canonical_export_references({}) == set()
    assert canonical_export_references({"history": [{"event": "approve", "details": {}}]}) == set()


def test_pipeline_pruner_consults_canonical_state_before_deleting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _export_root(tmp_path)
    season_root = tmp_path / "season"
    seen: dict[str, Any] = {}

    def fake_load_decisions(season: str, *, root: object) -> dict[str, Any]:
        seen["season"], seen["root"] = season, root
        return _canonical_decisions()

    monkeypatch.setattr(timing, "load_decisions", fake_load_decisions)

    removed = timing._prune_old_exports(
        root, canonical_season=SEASON, season_root=season_root
    )

    assert removed == [UNREFERENCED_OLD]
    assert seen == {"season": SEASON, "root": season_root}
    assert (root / HISTORY_REFERENCED).is_dir()


def test_pruner_fails_closed_when_canonical_root_cannot_be_resolved(tmp_path: Path) -> None:
    root = _export_root(tmp_path)

    with pytest.raises(Stage4Error, match="stoppet"):
        timing._prune_old_exports(root, canonical_season=SEASON, season_root=None)

    assert (root / UNREFERENCED_OLD).is_dir()
    assert (root / HISTORY_REFERENCED).is_dir()


def test_export_helper_deletes_nothing_when_retention_cannot_be_proven(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = _export_root(tmp_path)

    with caplog.at_level("WARNING"):
        removed = timing._prune_draft_exports_for_export(root, SEASON, None)

    assert removed == []
    assert all((root / name).is_dir() for name in [UNREFERENCED_OLD, HISTORY_REFERENCED])
    assert "ingen eksporter slettet" in caplog.text


def test_git_committed_export_is_retained_as_repository_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    root = repo / "export"
    _draft(root, UNREFERENCED_OLD)
    _draft(root, HISTORY_REFERENCED)
    for name in NEWER_DRAFTS:
        _draft(root, name)
    subprocess.run(["git", "-C", str(repo), "add", f"export/{HISTORY_REFERENCED}"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=retention-test",
            "-c",
            "user.email=retention-test@example.invalid",
            "commit",
            "-q",
            "-m",
            "committed export",
        ],
        check=True,
    )

    removed = timing._prune_old_exports(root, keep=3)

    assert removed == [UNREFERENCED_OLD]
    assert (root / HISTORY_REFERENCED / "season_plan.xlsx").exists()


def test_without_canonical_season_or_git_checkout_plain_draft_retention_applies(tmp_path: Path) -> None:
    root = _export_root(tmp_path)

    removed = timing._prune_old_exports(root, keep=3)

    assert removed == [UNREFERENCED_OLD, HISTORY_REFERENCED, NEWER_DRAFTS[0]]
