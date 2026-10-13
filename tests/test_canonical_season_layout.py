"""Partitioned canonical season layout and its repository-owned migration (issue #649).

The partitioned layout must present one logical aggregate: the same effective
facts, one canonical revision, integrity-checked partitions, and atomic
mutations that keep the season's layout. Legacy monolithic seasons must stay
readable and migrate without changing facts or revision.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tournament_scheduler.infrastructure.canonical_season_migration as migration_module
from tournament_scheduler.infrastructure.canonical_season_layout import (
    DECISIONS_PARTITIONS,
    LAYOUT_MONOLITHIC,
    LAYOUT_PARTITIONED,
    MANIFEST_FILE_NAME,
    SeasonStateError,
    json_bytes,
)
from tournament_scheduler.infrastructure.canonical_season_migration import migrate_season_to_partitioned
from tournament_scheduler.infrastructure.canonical_season_store import (
    CanonicalRevisionConflictError,
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
)
from tournament_scheduler.season.state import canonical_state_revision

SEASON = "2026-2027"
REVISION = "rev-0001"


def _snapshot(*, history_note: str = "first") -> CanonicalSeasonSnapshot:
    schedule = {
        "schema_version": 1,
        "season": SEASON,
        "fingerprint": "fp-0001",
        "revision": REVISION,
        "plan": {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": []},
    }
    decisions = {
        "schema_version": 1,
        "season": SEASON,
        "actor": "tester",
        "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
        "canonical_state_revision": REVISION,
        "schedule_fingerprint": "fp-0001",
        "decisions": {"rvv-0001": {"status": "approved"}},
        "history": [{"event": history_note}],
        "banned_dates": [{"date": "2026-12-24"}],
        "change_protections": [{"id": "p1"}],
        "tournament_booking_evidence": [{"id": "b1"}],
        "season_lifecycle": {"phase": "planning"},
    }
    return CanonicalSeasonSnapshot(
        season=SEASON,
        schedule=schedule,
        decisions=decisions,
        export_context={"schema_version": 1, "season": SEASON},
    )


def _write_monolithic(root: Path) -> CanonicalSeasonSnapshot:
    snapshot = _snapshot()
    CanonicalSeasonStore(root).write(snapshot, require_absent=True, layout=LAYOUT_MONOLITHIC)
    return snapshot


def _manifest(root: Path) -> dict:
    return json.loads((root / SEASON / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))


def _rewrite_manifest_entry(root: Path, name: str, data: bytes) -> None:
    """Replace one partition and refresh its manifest hash, so only the aggregate check can fail."""

    import hashlib

    manifest = _manifest(root)
    entry = next(item for item in manifest["partitions"] if item["name"] == name)
    (root / SEASON / entry["path"]).write_bytes(data)
    entry["sha256"] = hashlib.sha256(data).hexdigest()
    (root / SEASON / MANIFEST_FILE_NAME).write_bytes(json_bytes(manifest))


def test_migration_moves_to_partitions_without_changing_facts_or_revision(tmp_path: Path) -> None:
    root = tmp_path / "season"
    before = _write_monolithic(root)
    store = CanonicalSeasonStore(root)
    assert store.layout(SEASON) == LAYOUT_MONOLITHIC

    report = migrate_season_to_partitioned(SEASON, root=root)

    assert report["migrated"] is True
    assert report["previous_layout"] == LAYOUT_MONOLITHIC
    assert store.layout(SEASON) == LAYOUT_PARTITIONED
    assert not (root / SEASON / "decisions.json").exists()
    assert (root / SEASON / "decisions" / "history.json").is_file()
    after = store.load(SEASON)
    assert after.schedule == before.schedule
    assert after.decisions == before.decisions
    assert after.export_context == before.export_context
    assert canonical_state_revision(after.schedule, after.decisions) == REVISION
    assert report["canonical_state_revision"] == REVISION


def test_repeating_migration_is_a_no_op(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_monolithic(root)
    migrate_season_to_partitioned(SEASON, root=root)
    manifest_before = (root / SEASON / MANIFEST_FILE_NAME).read_bytes()

    report = migrate_season_to_partitioned(SEASON, root=root)

    assert report == {"season": SEASON, "layout": LAYOUT_PARTITIONED, "migrated": False}
    assert (root / SEASON / MANIFEST_FILE_NAME).read_bytes() == manifest_before


def test_mutation_keeps_a_partitioned_season_partitioned(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_monolithic(root)
    migrate_season_to_partitioned(SEASON, root=root)
    store = CanonicalSeasonStore(root)
    changed = _snapshot(history_note="second")

    store.write(changed, expected_revision=REVISION)

    assert store.layout(SEASON) == LAYOUT_PARTITIONED
    assert store.load(SEASON).decisions["history"] == [{"event": "second"}]


def test_partition_files_hold_only_the_keys_they_own(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_monolithic(root)
    migrate_season_to_partitioned(SEASON, root=root)

    history = json.loads((root / SEASON / "decisions" / "history.json").read_text(encoding="utf-8"))
    operations = json.loads((root / SEASON / "decisions" / "operations.json").read_text(encoding="utf-8"))

    assert set(history) == {"history"}
    assert "history" not in operations
    assert "decisions" in operations


def test_tampered_partition_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_monolithic(root)
    migrate_season_to_partitioned(SEASON, root=root)
    path = root / SEASON / "decisions" / "history.json"
    path.write_bytes(path.read_bytes().replace(b"first", b"forged"))

    with pytest.raises(SeasonStateError, match="integrity"):
        CanonicalSeasonStore(root).load(SEASON)


def test_partitions_from_different_revisions_are_refused(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_monolithic(root)
    migrate_season_to_partitioned(SEASON, root=root)
    other = json.loads((root / SEASON / "decisions" / "operations.json").read_text(encoding="utf-8"))
    other["canonical_state_revision"] = "rev-stale"
    _rewrite_manifest_entry(root, "operations", json_bytes(other))

    with pytest.raises(SeasonStateError, match="one revision"):
        CanonicalSeasonStore(root).load(SEASON)


def test_unassigned_decisions_key_is_refused_on_partitioned_write(tmp_path: Path) -> None:
    root = tmp_path / "season"
    snapshot = _snapshot()
    unknown = snapshot.with_decisions({**snapshot.decisions, "unplanned_future_key": {"x": 1}})

    with pytest.raises(SeasonStateError, match="unassigned keys"):
        CanonicalSeasonStore(root).write(unknown, require_absent=True, layout=LAYOUT_PARTITIONED)

    assert not (root / SEASON).exists()


def test_partitioned_write_honours_the_revision_guard(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_monolithic(root)
    migrate_season_to_partitioned(SEASON, root=root)

    with pytest.raises(CanonicalRevisionConflictError):
        CanonicalSeasonStore(root).write(_snapshot(history_note="late"), expected_revision="rev-stale")

    assert CanonicalSeasonStore(root).load(SEASON).decisions["history"] == [{"event": "first"}]


def test_partitions_own_disjoint_decisions_keys() -> None:
    seen: set[str] = set()
    for partition in DECISIONS_PARTITIONS:
        assert not (seen & partition.keys), partition.name
        seen |= partition.keys


def test_failed_migration_proof_restores_the_monolithic_layout(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "season"
    before = _write_monolithic(root)
    real_revision = migration_module.canonical_state_revision
    calls = {"count": 0}

    def drifting_revision(schedule, decisions):
        # The first call computes the pre-migration revision; the post-migration proof sees a different one.
        calls["count"] += 1
        return real_revision(schedule, decisions) if calls["count"] == 1 else "rev-drifted"

    monkeypatch.setattr(migration_module, "canonical_state_revision", drifting_revision)

    with pytest.raises(SeasonStateError, match="restored"):
        migrate_season_to_partitioned(SEASON, root=root)

    store = CanonicalSeasonStore(root)
    assert store.layout(SEASON) == LAYOUT_MONOLITHIC
    restored = store.load(SEASON)
    assert restored.decisions == before.decisions
    assert restored.schedule == before.schedule


PRODUCTION_ROOT = Path(__file__).resolve().parents[1] / "tournament_scheduler"
# Persistence and migration modules own the physical layout. The compaction backup
# names its own ``decisions.json`` copy; that is a backup artifact, not canonical state.
PERSISTENCE_BOUNDARY = {
    "infrastructure/canonical_season_store.py",
    "infrastructure/canonical_season_layout.py",
    "infrastructure/canonical_season_migration.py",
    "infrastructure/canonical_season_evidence.py",
    "infrastructure/canonical_compaction_backup.py",
}
PARTITION_LITERALS = (
    '"decisions.json"',
    "'decisions.json'",
    '"season.json"',
    "'season.json'",
    '"decisions/',
    "'decisions/",
)


def test_partition_filenames_stay_behind_the_persistence_boundary() -> None:
    """Production code above the store must not hard-code the physical canonical layout."""

    offenders: list[str] = []
    for path in sorted(PRODUCTION_ROOT.rglob("*.py")):
        relative = path.relative_to(PRODUCTION_ROOT).as_posix()
        if relative in PERSISTENCE_BOUNDARY:
            continue
        text = path.read_text(encoding="utf-8")
        offenders.extend(f"{relative}: {literal}" for literal in PARTITION_LITERALS if literal in text)

    assert offenders == []
