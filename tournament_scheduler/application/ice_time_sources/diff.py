"""Revision fingerprint and diff of ice-time snapshots."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from ..ports.ice_time import AllocationWindow, Completeness, WindowKind


def windows_fingerprint(windows, completeness: Completeness) -> str:
    """Stable content revision: order-independent, provenance-independent."""
    rows = sorted(
        f"{w.venue_id}|{w.kind.value}|{w.starts_at.isoformat()}|{w.ends_at.isoformat()}"
        for w in windows
    )
    body = "\n".join([completeness.value, *rows])
    return hashlib.sha256(body.encode()).hexdigest()


class ChangeKind(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    REDUCED = "reduced"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class WindowChange:
    kind: ChangeKind
    window: AllocationWindow


@dataclass(frozen=True)
class SnapshotDiff:
    changes: tuple[WindowChange, ...]
    completeness_changed: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.changes and not self.completeness_changed

    @property
    def invalidates_previews(self) -> bool:
        """Lost/reduced/blocked ice or changed completeness stales reviewed previews.

        Pure additions only widen options, but still re-fingerprint the source;
        callers decide using ``is_empty``.
        """
        return self.completeness_changed or any(
            c.kind is not ChangeKind.ADDED for c in self.changes
        )


def diff_snapshots(old, new, old_completeness: Completeness, new_completeness: Completeness) -> SnapshotDiff:
    """Diff two window collections. Never mutates anything (no booking is moved)."""
    old_avail = [w for w in old if w.kind is WindowKind.AVAILABLE]
    new_avail = [w for w in new if w.kind is WindowKind.AVAILABLE]
    new_blocked = [w for w in new if w.kind is WindowKind.BLOCKED]
    old_keys = {(w.venue_id, w.kind, w.starts_at, w.ends_at) for w in old}
    new_keys = {(w.venue_id, w.kind, w.starts_at, w.ends_at) for w in new}
    changes: list[WindowChange] = []
    for w in sorted(old, key=lambda w: (w.venue_id, w.starts_at)):
        if (w.venue_id, w.kind, w.starts_at, w.ends_at) in new_keys:
            continue
        if w.kind is WindowKind.BLOCKED:
            continue
        inside = [n for n in new_avail if n.venue_id == w.venue_id
                  and n.starts_at < w.ends_at and w.starts_at < n.ends_at]
        changes.append(WindowChange(ChangeKind.REDUCED if inside else ChangeKind.REMOVED, w))
    for w in sorted(new_blocked, key=lambda w: (w.venue_id, w.starts_at)):
        if (w.venue_id, w.kind, w.starts_at, w.ends_at) not in old_keys:
            changes.append(WindowChange(ChangeKind.BLOCKED, w))
    for w in sorted(new_avail, key=lambda w: (w.venue_id, w.starts_at)):
        if (w.venue_id, w.kind, w.starts_at, w.ends_at) not in old_keys:
            if not any(o.venue_id == w.venue_id and o.starts_at < w.ends_at and w.starts_at < o.ends_at
                       for o in old_avail):
                changes.append(WindowChange(ChangeKind.ADDED, w))
    return SnapshotDiff(tuple(changes), old_completeness is not new_completeness)
