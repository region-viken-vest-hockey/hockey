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
import threading
from pathlib import Path

import pytest

import tournament_scheduler.infrastructure.canonical_season_store as store_module
from tournament_scheduler.infrastructure.canonical_season_store import (
    CanonicalCommitDurabilityError,
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
)

SEASON = "2026-2027"


def _snapshot(season: str = SEASON, *, marker: str = "") -> CanonicalSeasonSnapshot:
    schedule = {
        "schema_version": 1,
        "plan": {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "tournaments": [],
        },
    }
    decisions = {"schema_version": 1, "decisions": {"marker": marker}}
    return CanonicalSeasonSnapshot(season=season, schedule=schedule, decisions=decisions)


def _marked_snapshot(season: str = SEASON, *, marker: str) -> CanonicalSeasonSnapshot:
    """A snapshot whose three canonical files all carry the same version marker."""

    schedule = {
        "schema_version": 1,
        "snapshot_marker": marker,
        "plan": {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "tournaments": [],
        },
    }
    decisions = {
        "schema_version": 1,
        "snapshot_marker": marker,
        "decisions": {"marker": marker},
    }
    export_context = {"snapshot_marker": marker}
    return CanonicalSeasonSnapshot(
        season=season,
        schedule=schedule,
        decisions=decisions,
        export_context=export_context,
    )


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


def test_load_recovers_from_interrupted_swap(tmp_path: Path) -> None:
    """A normal read recovers the last durable state after an interrupted swap."""

    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    store.write(_snapshot())

    season_dir = root / SEASON
    backup = root / f".{SEASON}.backup"
    # Simulate a crash after the active directory was moved to backup but before
    # the staged directory was installed: only the backup remains.
    os.replace(season_dir, backup)
    assert not season_dir.exists()
    assert backup.exists()

    # A read (not a write) must restore the last committed state instead of
    # failing with a missing season.
    snapshot = store.load(SEASON)
    assert snapshot.schedule["schema_version"] == 1
    assert season_dir.exists()
    assert not backup.exists()


def test_write_surfaces_directory_fsync_failure(tmp_path: Path, monkeypatch) -> None:
    """A directory fsync failure is raised, never silently ignored."""

    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    store.write(_snapshot())
    original_decisions = store.load(SEASON).decisions

    def _boom_dir_fsync(path):
        raise OSError("simulated directory fsync failure")

    monkeypatch.setattr(
        "tournament_scheduler.infrastructure.canonical_season_store._fsync_directory",
        _boom_dir_fsync,
    )

    with pytest.raises(OSError):
        store.write(_snapshot())

    # The swap never happened, so the prior canonical state is intact.
    assert store.load(SEASON).decisions == original_decisions


def test_reader_recovery_waits_for_active_writer(tmp_path: Path, monkeypatch) -> None:
    """A reader entering recovery mid-swap waits instead of clobbering the writer.

    The writer renames the active directory to the backup, then installs the
    staged tree. A reader that lands in that window must block on the shared
    season-directory lock and observe the newly installed state, rather than
    restoring the backup from underneath the writer and racing the second rename.
    """

    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    store.write(_snapshot(marker="old"))

    season_dir = root / SEASON
    backup = root / f".{SEASON}.backup"

    real_replace = os.replace
    at_install = threading.Event()
    allow_install = threading.Event()

    def _paused_install_replace(src, dst):
        if Path(src).name.endswith(".tmp"):
            at_install.set()
            assert allow_install.wait(timeout=5)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _paused_install_replace)

    writer_result: dict[str, object] = {}

    def _writer() -> None:
        try:
            store.write(_snapshot(marker="new"))
            writer_result["ok"] = True
        except BaseException as exc:  # pragma: no cover - diagnostic only
            writer_result["exc"] = exc

    writer_thread = threading.Thread(target=_writer)
    writer_thread.start()
    assert at_install.wait(timeout=5)

    # The writer sits between its two renames: active absent, backup present.
    assert not season_dir.exists()
    assert backup.exists()

    reader_result: dict[str, object] = {}

    def _reader() -> None:
        reader_result["marker"] = store.load(SEASON).decisions["decisions"]["marker"]

    reader_thread = threading.Thread(target=_reader)
    reader_thread.start()
    reader_thread.join(timeout=0.5)
    # The reader must wait for the writer, not restore the backup underneath it.
    assert reader_thread.is_alive()

    allow_install.set()
    writer_thread.join(timeout=5)
    reader_thread.join(timeout=5)

    assert "exc" not in writer_result
    assert writer_result.get("ok") is True
    assert not writer_thread.is_alive()
    assert not reader_thread.is_alive()
    # The reader observed the committed new state, after the writer finished.
    assert reader_result["marker"] == "new"
    # The writer's own swap consumed the backup; the reader did not clobber it.
    assert not backup.exists()


def test_post_install_fsync_failure_is_committed_and_recoverable(
    tmp_path: Path, monkeypatch
) -> None:
    """A failure after the install rename is committed, not an ambiguous rollback."""

    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    store.write(_snapshot(marker="old"))

    backup = root / f".{SEASON}.backup"
    real_replace = os.replace
    real_fsync_directory = store_module._fsync_directory
    installed = {"done": False}

    def _tracking_replace(src, dst):
        result = real_replace(src, dst)
        if Path(src).name.endswith(".tmp"):
            installed["done"] = True
        return result

    def _failing_post_install_fsync(path):
        if installed["done"]:
            raise OSError("simulated post-install directory fsync failure")
        return real_fsync_directory(path)

    monkeypatch.setattr(os, "replace", _tracking_replace)
    monkeypatch.setattr(store_module, "_fsync_directory", _failing_post_install_fsync)

    with pytest.raises(CanonicalCommitDurabilityError) as excinfo:
        store.write(_snapshot(marker="new"))
    # The error reports a committed write, not a rolled-back one.
    assert excinfo.value.committed is True

    # The new state is active; the previous state is preserved at the backup so
    # an interrupted durability finalization is still recoverable.
    assert store.load(SEASON).decisions["decisions"]["marker"] == "new"
    assert backup.exists()

    # A subsequent write completes cleanly, consumes the stale backup and keeps
    # the committed new state.
    monkeypatch.undo()
    store.write(_snapshot(marker="newer"))
    assert store.load(SEASON).decisions["decisions"]["marker"] == "newer"
    assert not backup.exists()


def test_snapshot_load_holds_one_lock_across_all_three_files(
    tmp_path: Path, monkeypatch
) -> None:
    """A write cannot land between the schedule and decisions reads.

    ``CanonicalSeasonStore.load`` reads all three canonical files under one lock
    acquisition, so a concurrent writer commits entirely before or entirely
    after the load; the loaded snapshot is never a torn old/new mix.
    """

    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    store.write(_marked_snapshot(marker="old"))

    real_load_schedule_unlocked = store_module._load_schedule_unlocked
    schedule_read = threading.Event()
    allow_continue = threading.Event()

    def _paused_load_schedule_unlocked(season, *, root):
        result = real_load_schedule_unlocked(season, root=root)
        schedule_read.set()
        assert allow_continue.wait(timeout=5)
        return result

    monkeypatch.setattr(
        store_module, "_load_schedule_unlocked", _paused_load_schedule_unlocked
    )

    loaded: dict[str, object] = {}

    def _loader() -> None:
        loaded["snapshot"] = store.load(SEASON)

    loader_thread = threading.Thread(target=_loader)
    loader_thread.start()
    assert schedule_read.wait(timeout=5)

    writer_done = threading.Event()

    def _writer() -> None:
        store.write(_marked_snapshot(marker="new"))
        writer_done.set()

    writer_thread = threading.Thread(target=_writer)
    writer_thread.start()
    # The writer cannot commit while the loader holds the lock across the load.
    assert not writer_done.wait(timeout=0.5)
    assert writer_thread.is_alive()

    allow_continue.set()
    loader_thread.join(timeout=5)
    writer_thread.join(timeout=5)
    assert not loader_thread.is_alive()
    assert not writer_thread.is_alive()
    assert writer_done.is_set()

    snapshot = loaded["snapshot"]
    markers = {
        snapshot.schedule["snapshot_marker"],  # type: ignore[union-attr]
        snapshot.decisions["snapshot_marker"],  # type: ignore[union-attr]
        snapshot.export_context["snapshot_marker"],  # type: ignore[union-attr]
    }
    # Read entirely before the blocked writer committed, so all three files are
    # old -- never an old schedule with new decisions or vice versa.
    assert markers == {"old"}


def test_backup_cleanup_failure_is_non_fatal_and_cleaned_later(
    tmp_path: Path, monkeypatch
) -> None:
    """A failed previous-state cleanup does not fail an already-committed write."""

    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    store.write(_marked_snapshot(marker="old"))

    backup = root / f".{SEASON}.backup"
    real_rmtree = shutil.rmtree
    failed = {"once": False}

    def _failing_rmtree(path, *args, **kwargs):
        # Fail only the post-install removal of the retained previous state.
        if Path(path) == backup and not failed["once"]:
            failed["once"] = True
            raise OSError("simulated backup cleanup failure")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", _failing_rmtree)

    # The write still commits: the new state is active even though the previous
    # state could not be removed, and no durability error is raised.
    store.write(_marked_snapshot(marker="new"))
    assert failed["once"] is True
    assert store.load(SEASON).decisions["snapshot_marker"] == "new"
    assert backup.exists()

    # The retained backup is harmless and is removed by the next write.
    monkeypatch.undo()
    store.write(_marked_snapshot(marker="newer"))
    assert store.load(SEASON).decisions["snapshot_marker"] == "newer"
    assert not backup.exists()
