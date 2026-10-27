"""A canonical commit must not delete files it does not own (#853).

The atomic write replaces the whole season directory. Only the canonical layout
and the evidence archive were carried into the staged tree, so derived files
that live beside them -- notably ``change-log.md`` -- were silently removed by
an ordinary calendar refresh or other mutation.
"""

from __future__ import annotations

from pathlib import Path

from tournament_scheduler.infrastructure.canonical_season_store import (
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
)

SEASON = "2026-2027"
CHANGE_LOG = "change-log.md"


def _snapshot(*, marker: str) -> CanonicalSeasonSnapshot:
    schedule = {
        "schema_version": 1,
        "snapshot_marker": marker,
        "plan": {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": []},
    }
    decisions = {"schema_version": 1, "snapshot_marker": marker, "decisions": {"marker": marker}}
    return CanonicalSeasonSnapshot(season=SEASON, schedule=schedule, decisions=decisions)


def test_commit_keeps_derived_change_log_beside_canonical_state(tmp_path: Path) -> None:
    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    store.write(_snapshot(marker="first"))
    change_log = root / SEASON / CHANGE_LOG
    change_log.write_text("# Change log\n", encoding="utf-8")

    store.write(_snapshot(marker="second"))

    assert change_log.exists(), "a canonical commit deleted season/<season>/change-log.md"
    assert change_log.read_text(encoding="utf-8") == "# Change log\n"
    assert store.load(SEASON).schedule["snapshot_marker"] == "second"


def test_commit_still_replaces_canonical_files_not_just_preserves_them(tmp_path: Path) -> None:
    root = tmp_path / "season"
    store = CanonicalSeasonStore(root)
    store.write(_snapshot(marker="first"))
    (root / SEASON / CHANGE_LOG).write_text("# Change log\n", encoding="utf-8")

    store.write(_snapshot(marker="second"))

    # Carrying unowned files forward must never resurrect a stale canonical file.
    assert store.load(SEASON).decisions["snapshot_marker"] == "second"
