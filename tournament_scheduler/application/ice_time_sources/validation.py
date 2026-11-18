"""All-or-nothing validation of a normalized ice-time snapshot."""

from __future__ import annotations

from collections.abc import Iterable

from ..ports.common import PortError, PortErrorCode
from ..ports.ice_time import AllocationWindow, ObservedCalendarEvent, SourceScope


class IceTimeValidationError(PortError):
    """Bad snapshot. Carries every issue so nothing is partially accepted."""

    def __init__(self, issues: Iterable[str]) -> None:
        self.issues = tuple(issues)
        super().__init__(PortErrorCode.SCHEMA_INCOMPATIBLE, "; ".join(self.issues))


def _minute_aligned(moment) -> bool:
    return moment.second == 0 and moment.microsecond == 0


def validate_snapshot(
    scope: SourceScope,
    windows: Iterable[AllocationWindow],
    events: Iterable[ObservedCalendarEvent] = (),
) -> None:
    """Raise :class:`IceTimeValidationError` listing every defect, or return None.

    Checks: wrong venue/rink, outside scope, sub-minute precision, exact
    duplicates, and same-kind overlaps per venue (``AVAILABLE`` windows may
    legitimately be overlapped by ``BLOCKED`` ones; that is a revocation).
    """
    issues: list[str] = []
    ordered = sorted(windows, key=lambda w: (w.venue_id, w.kind.value, w.starts_at, w.ends_at))
    for w in ordered:
        label = f"{w.venue_id} {w.starts_at.isoformat()}..{w.ends_at.isoformat()}"
        if w.venue_id != scope.venue_id:
            issues.append(f"wrong rink: {label} is outside scope venue {scope.venue_id}")
        if w.starts_at < scope.from_date or w.ends_at > scope.to_date:
            issues.append(f"outside declared period: {label}")
        if not (_minute_aligned(w.starts_at) and _minute_aligned(w.ends_at)):
            issues.append(f"sub-minute precision: {label}")
    for a, b in zip(ordered, ordered[1:]):
        if (a.venue_id, a.kind) != (b.venue_id, b.kind):
            continue
        if (a.starts_at, a.ends_at) == (b.starts_at, b.ends_at):
            issues.append(f"duplicate window: {a.venue_id} {a.starts_at.isoformat()}")
        elif b.starts_at < a.ends_at:
            issues.append(f"overlapping {a.kind.value} windows at {a.venue_id} {b.starts_at.isoformat()}")
    seen: set[str] = set()
    for e in events:
        if e.venue_id != scope.venue_id:
            issues.append(f"wrong rink: event {e.event_id} at {e.venue_id}")
        if e.event_id in seen:
            issues.append(f"duplicate event id: {e.event_id}")
        seen.add(e.event_id)
    if issues:
        raise IceTimeValidationError(issues)
