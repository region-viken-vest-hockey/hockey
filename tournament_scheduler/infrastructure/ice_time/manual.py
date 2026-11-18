"""Manual structured allocation snapshot, persisted in the local workspace."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from tournament_scheduler.application.ice_time_sources.diff import SnapshotDiff, diff_snapshots
from tournament_scheduler.application.ports.common import PortError, PortErrorCode, SourceKind, SourceRef
from tournament_scheduler.application.ports.ice_time import (
    AllocationWindow,
    Completeness,
    SourceScope,
    WindowKind,
)

from .snapshot import IceTimeSnapshot, SnapshotSource

FEATURES = frozenset({"allocations", "manual", "offline"})
SCHEMA_VERSION = 1


def build_manual_snapshot(
    *, source_id: str, actor: str, timezone: str, scope: SourceScope,
    windows: list[tuple[datetime, datetime, WindowKind]],
    attested_complete: bool, captured_at: datetime, document: str | None = None,
    revision: str | None = None,
) -> IceTimeSnapshot:
    """Build a snapshot. Completeness is COMPLETE only when explicitly attested.

    An empty list with ``attested_complete=True`` is a typed 'no availability'
    declaration; with ``False`` it is UNKNOWN, never free ice.
    """
    ref = SourceRef(source_id, SourceKind.MANUAL, captured_at, timezone, actor=actor,
                    document=document, revision=revision)
    wins = tuple(AllocationWindow(ref, scope.venue_id, s, e, k) for s, e, k in windows)
    completeness = Completeness.COMPLETE if attested_complete else (
        Completeness.PARTIAL if wins else Completeness.UNKNOWN)
    return IceTimeSnapshot(ref, scope, completeness, wins)


def manual_source(snapshot: IceTimeSnapshot) -> SnapshotSource:
    if snapshot.source.kind is not SourceKind.MANUAL:
        raise ValueError("manual_source needs a manual snapshot")
    return SnapshotSource(snapshot, FEATURES, "manual-allocation")


def _dump(s: IceTimeSnapshot) -> dict:
    r = s.source
    return {
        "schema": SCHEMA_VERSION, "revision": s.revision, "source_id": r.source_id,
        "actor": r.actor, "timezone": r.timezone, "document": r.document,
        "captured_at": r.captured_at.isoformat(), "completeness": s.completeness.value,
        "venue_id": s.scope.venue_id, "from": s.scope.from_date.isoformat(),
        "to": s.scope.to_date.isoformat(),
        "windows": [[w.starts_at.isoformat(), w.ends_at.isoformat(), w.kind.value] for w in s.windows],
    }


def _load(d: dict) -> IceTimeSnapshot:
    if d.get("schema") != SCHEMA_VERSION:
        raise PortError(PortErrorCode.SCHEMA_INCOMPATIBLE, f"manual snapshot schema {d.get('schema')}")
    scope = SourceScope(d["venue_id"], datetime.fromisoformat(d["from"]), datetime.fromisoformat(d["to"]))
    ref = SourceRef(d["source_id"], SourceKind.MANUAL, datetime.fromisoformat(d["captured_at"]),
                    d["timezone"], actor=d["actor"], document=d["document"])
    wins = tuple(AllocationWindow(ref, scope.venue_id, datetime.fromisoformat(a),
                                  datetime.fromisoformat(b), WindowKind(k)) for a, b, k in d["windows"])
    return IceTimeSnapshot(ref, scope, Completeness(d["completeness"]), wins)


class ManualSnapshotStore:
    """One JSON file per source id under a local directory; revision-bound replace."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _path(self, source_id: str) -> Path:
        if not source_id or Path(source_id).name != source_id or source_id.startswith("."):
            raise PortError(PortErrorCode.NOT_FOUND, f"invalid source id {source_id!r}")
        return self._root / f"{source_id}.json"

    def load(self, source_id: str) -> IceTimeSnapshot:
        path = self._path(source_id)
        if not path.exists():
            raise PortError(PortErrorCode.NOT_FOUND, f"no manual snapshot {source_id}")
        return _load(json.loads(path.read_text()))

    def replace(self, snapshot: IceTimeSnapshot, expected_revision: str | None) -> SnapshotDiff:
        """Atomically replace; the diff tells callers which previews to invalidate.

        Booked/approved events are never touched here: this only swaps evidence.
        """
        path = self._path(snapshot.source.source_id)
        old = self.load(snapshot.source.source_id) if path.exists() else None
        if (old.revision if old else None) != expected_revision:
            raise PortError(PortErrorCode.REVISION_CONFLICT, "manual snapshot changed since it was read")
        diff = diff_snapshots(old.windows if old else (), snapshot.windows,
                              old.completeness if old else Completeness.UNKNOWN, snapshot.completeness)
        self._root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._root, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(_dump(snapshot), fh, sort_keys=True)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return diff
