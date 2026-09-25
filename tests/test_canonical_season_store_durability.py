"""Crash-durability and evidence carry-forward regression tests for the store.

Issue #464 follow-up: retained evidence must not be bulk-copied on every
canonical mutation (it is immutable, so it is hardlinked with a copy fallback),
and the atomic directory swap must be crash-durable -- staged contents and
directory entries are fsynced, and an interruption between the two renames is
recovered on the next write so canonical state and required evidence stay
recoverable.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tournament_scheduler.infrastructure.canonical_season_store import (
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
)

SEASON = "2026-2027"


def _snapshot(season: str = SEASON) -> CanonicalSeasonSnapshot:
    schedule = {
        "schema_version": 1,
        "plan": {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "tournaments": [],
        },
    }
    decisions = {"schema_version": 1, "decisions": {}}
    return CanonicalSeasonSnapshot(season=season, schedule=schedule, decisions=decisions)


def _add_evidence(root: Path, season: str, name: str, content: bytes) -> Path:
    evidence_dir = root / season / "evidence" / "moves"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / name
    path.write_bytes(content)
    return path


def test_mutation_carries_evidence_forward_by_hardlink(tmp_path: Path, monkeypatch) -> None:
    """Retained immutable evidence is hardlinked, not re-copied, on every mutation."""

    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    store.write(_snapshot())
    evidence = _add_evidence(root, SEASON, "abc.json", b"evidence-bytes")
    inode_before = evidence.stat().st_ino

    def _no_copy(*args, **kwargs):
        raise AssertionError("evidence must be carried forward by hardlink, not copied")

    monkeypatch.setattr(shutil, "copy2", _no_copy)
    monkeypatch.setattr(shutil, "copytree", _no_copy)

    store.write(_snapshot())

    evidence_after = root / SEASON / "evidence" / "moves" / "abc.json"
    assert evidence_after.read_bytes() == b"evidence-bytes"
    # Same inode: the file was hardlinked, so no bulk content copy occurred.
    assert evidence_after.stat().st_ino == inode_before


def test_mutation_copies_evidence_when_hardlinks_unavailable(tmp_path: Path, monkeypatch) -> None:
    """The copy fallback keeps evidence when hardlinks cannot be created."""

    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    store.write(_snapshot())
    _add_evidence(root, SEASON, "abc.json", b"evidence-bytes")

    def _no_link(src, dst):
        raise OSError("hardlinks unavailable")

    monkeypatch.setattr(os, "link", _no_link)

    store.write(_snapshot())

    evidence_after = root / SEASON / "evidence" / "moves" / "abc.json"
    assert evidence_after.read_bytes() == b"evidence-bytes"


def test_write_recovers_from_interrupted_swap(tmp_path: Path) -> None:
    """A crash between the two renames is recovered by the next write."""

    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    store.write(_snapshot())

    season_dir = root / SEASON
    backup = root / f".{SEASON}.backup"
    # Simulate a crash after the active directory was moved to backup but before
    # the staged directory was installed.
    os.replace(season_dir, backup)
    assert not season_dir.exists()
    assert backup.exists()

    store.write(_snapshot())

    assert season_dir.exists()
    assert store.load(SEASON).schedule["schema_version"] == 1
    # The swap completed cleanly: the backup was consumed, not left behind.
    assert not backup.exists()


def test_write_preserves_state_when_fsync_fails(tmp_path: Path, monkeypatch) -> None:
    """A failed staged-content sync leaves the prior state intact and loadable."""

    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    store.write(_snapshot())
    original_decisions = store.load(SEASON).decisions

    def _boom_fsync(fd):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", _boom_fsync)

    with pytest.raises(OSError):
        store.write(_snapshot())

    # The swap never happened, so the prior canonical state is intact.
    assert store.load(SEASON).decisions == original_decisions


def test_write_rolls_back_on_swap_failure(tmp_path: Path, monkeypatch) -> None:
    """A failure installing the staged directory restores the prior state."""

    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    store.write(_snapshot())
    original_decisions = store.load(SEASON).decisions

    real_replace = os.replace

    def _fail_installing_staging(src, dst):
        if Path(src).name.endswith(".tmp"):
            raise OSError("simulated swap failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _fail_installing_staging)

    with pytest.raises(OSError):
        store.write(_snapshot())

    # The prior canonical state is restored and remains loadable.
    assert store.load(SEASON).decisions == original_decisions
