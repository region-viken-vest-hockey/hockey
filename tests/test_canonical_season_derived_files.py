"""Derived season artifacts survive canonical commits, marked when stale (#853).

The atomic write replaces the whole season directory. Derived files such as
``change-log.md`` are carried forward so an ordinary commit no longer deletes
them. A carried projection that describes a different canonical revision is
marked stale, so it can never be read as current. Canonical files are still
replaced and nothing else is carried.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tournament_scheduler.infrastructure.canonical_season_store as store_module
from tournament_scheduler.infrastructure.canonical_season_store import (
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
)
from tournament_scheduler.season.state import canonical_state_revision

SEASON = "2026-2027"
CHANGE_LOG = "change-log.md"
BODY = "## Overview\n\nNo recorded change requests.\n"


def _snapshot(*, marker: str) -> CanonicalSeasonSnapshot:
    schedule = {
        "schema_version": 1,
        "snapshot_marker": marker,
        "plan": {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": []},
    }
    decisions = {"schema_version": 1, "snapshot_marker": marker, "decisions": {"marker": marker}}
    # The canonical service stamps an explicit revision on every write; mirror that.
    schedule["canonical_state_revision"] = f"rev-{marker}"
    return CanonicalSeasonSnapshot(season=SEASON, schedule=schedule, decisions=decisions)


def _change_log_for(snapshot: CanonicalSeasonSnapshot) -> str:
    revision = canonical_state_revision(snapshot.schedule, snapshot.decisions)
    return f"# Change-request ledger — {SEASON}\n\nCanonical state revision: `{revision}`\n\n{BODY}"


def test_commit_keeps_change_log_and_marks_it_stale_when_revision_moves(tmp_path: Path) -> None:
    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    first = _snapshot(marker="first")
    store.write(first)
    change_log = root / SEASON / CHANGE_LOG
    change_log.write_text(_change_log_for(first), encoding="utf-8")

    store.write(_snapshot(marker="second"))

    text = change_log.read_text(encoding="utf-8")
    assert BODY in text, "the change-log body was lost"
    assert text.count("> Stale projection:") == 1
    assert f"`{canonical_state_revision(first.schedule, first.decisions)}`" in text
    assert store.load(SEASON).schedule["snapshot_marker"] == "second"


def test_commit_leaves_change_log_unchanged_when_revision_is_identical(tmp_path: Path) -> None:
    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    snapshot = _snapshot(marker="same")
    store.write(snapshot)
    change_log = root / SEASON / CHANGE_LOG
    original = _change_log_for(snapshot)
    change_log.write_text(original, encoding="utf-8")

    store.write(snapshot)

    assert change_log.read_text(encoding="utf-8") == original


def test_stale_banner_is_not_duplicated_across_commits(tmp_path: Path) -> None:
    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    first = _snapshot(marker="first")
    store.write(first)
    change_log = root / SEASON / CHANGE_LOG
    change_log.write_text(_change_log_for(first), encoding="utf-8")

    store.write(_snapshot(marker="second"))
    third = _snapshot(marker="third")
    store.write(third)

    text = change_log.read_text(encoding="utf-8")
    assert text.count("> Stale projection:") == 1
    assert f"generated for canonical revision `{canonical_state_revision(first.schedule, first.decisions)}`" in text
    assert f"the season is now at `{canonical_state_revision(third.schedule, third.decisions)}`" in text


def test_commit_does_not_carry_unlisted_season_files(tmp_path: Path) -> None:
    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    store.write(_snapshot(marker="first"))
    obsolete = root / SEASON / "notes.txt"
    obsolete.write_text("not a canonical or derived file\n", encoding="utf-8")

    store.write(_snapshot(marker="second"))

    assert not obsolete.exists()


def test_failed_carry_forward_leaves_previous_state_intact(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    first = _snapshot(marker="first")
    store.write(first)
    change_log = root / SEASON / CHANGE_LOG
    change_log.write_text(_change_log_for(first), encoding="utf-8")

    def _fail(*args, **kwargs):
        raise OSError("simulated carry-forward failure")

    monkeypatch.setattr(store_module, "carry_forward_derived_files", _fail)

    with pytest.raises(OSError):
        store.write(_snapshot(marker="second"))

    assert store.load(SEASON).schedule["snapshot_marker"] == "first"
    assert change_log.read_text(encoding="utf-8") == _change_log_for(first)


def test_restored_revision_drops_the_stale_banner(tmp_path: Path) -> None:
    """A -> B -> A: the projection is current again, so no banner may remain."""

    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    first = _snapshot(marker="first")
    store.write(first)
    change_log = root / SEASON / CHANGE_LOG
    original = _change_log_for(first)
    change_log.write_text(original, encoding="utf-8")

    store.write(_snapshot(marker="second"))
    assert "> Stale projection:" in change_log.read_text(encoding="utf-8")

    store.write(_snapshot(marker="first"))

    assert change_log.read_text(encoding="utf-8") == original
