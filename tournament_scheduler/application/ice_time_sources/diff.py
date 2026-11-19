"""Revision fingerprint and diff of ice-time snapshots (windows and observed events)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from ..ports.ice_time import AllocationWindow, Completeness, ObservedCalendarEvent, WindowKind


def windows_fingerprint(windows, completeness: Completeness, events=(), scope=None, authority=None) -> str:
    """Stable content revision over every fact a consumer can see.

    Order-independent and capture-time independent. Includes normalized events
    (identity, venue, interval, title), the declared scope and any authority.
    """
    rows = sorted(
        f"W|{w.venue_id}|{w.kind.value}|{w.starts_at.isoformat()}|{w.ends_at.isoformat()}" for w in windows
    ) + sorted(
        f"E|{e.event_id}|{e.venue_id}|{e.starts_at.isoformat()}|{e.ends_at.isoformat()}|{e.title}"
        for e in events
    )
    head = [completeness.value]
    if scope is not None:
        head.append(f"S|{scope.venue_id}|{scope.from_date.isoformat()}|{scope.to_date.isoformat()}")
    if authority is not None:
        head.append(f"A|{authority.fingerprint()}")
    return hashlib.sha256("\n".join([*head, *rows]).encode()).hexdigest()


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
class EventChange:
    kind: ChangeKind  # ADDED, REMOVED, or REDUCED (same id, different time/venue/title)
    event: ObservedCalendarEvent


@dataclass(frozen=True)
class SnapshotDiff:
    changes: tuple[WindowChange, ...]
    completeness_changed: bool = False
    event_changes: tuple[EventChange, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.changes and not self.completeness_changed and not self.event_changes

    @property
    def invalidates_previews(self) -> bool:
        """Lost/reduced/blocked ice, changed completeness or any event change stales previews.

        Pure window additions only widen options, but still re-fingerprint the
        source; callers decide using ``is_empty``.
        """
        return (
            self.completeness_changed
            or bool(self.event_changes)
            or any(c.kind is not ChangeKind.ADDED for c in self.changes)
        )


def _diff_events(old, new) -> tuple[EventChange, ...]:
    key = lambda e: (e.event_id, e.venue_id)  # noqa: E731
    o, n = {key(e): e for e in old}, {key(e): e for e in new}
    out = [EventChange(ChangeKind.REMOVED, e) for k, e in sorted(o.items()) if k not in n]
    out += [EventChange(ChangeKind.ADDED, e) for k, e in sorted(n.items()) if k not in o]
    out += [EventChange(ChangeKind.REDUCED, e) for k, e in sorted(n.items())
            if k in o and (o[k].starts_at, o[k].ends_at, o[k].title) != (e.starts_at, e.ends_at, e.title)]
    return tuple(out)


def diff_snapshots(old, new, old_completeness: Completeness, new_completeness: Completeness,
                   old_events=(), new_events=()) -> SnapshotDiff:
    """Diff two snapshots. Never mutates anything (no booking is moved)."""
    old_avail = [w for w in old if w.kind is WindowKind.AVAILABLE]
    new_avail = [w for w in new if w.kind is WindowKind.AVAILABLE]
    new_blocked = [w for w in new if w.kind is WindowKind.BLOCKED]
    old_keys = {(w.venue_id, w.kind, w.starts_at, w.ends_at) for w in old}
    new_keys = {(w.venue_id, w.kind, w.starts_at, w.ends_at) for w in new}
    changes: list[WindowChange] = []
    for w in sorted(old, key=lambda w: (w.venue_id, w.starts_at)):
        if (w.venue_id, w.kind, w.starts_at, w.ends_at) in new_keys or w.kind is WindowKind.BLOCKED:
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
    return SnapshotDiff(tuple(changes), old_completeness is not new_completeness,
                        _diff_events(old_events, new_events))
