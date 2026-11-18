"""Shared in-memory snapshot source: every adapter normalizes into this."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tournament_scheduler.application.ice_time_sources.diff import windows_fingerprint
from tournament_scheduler.application.ice_time_sources.validation import validate_snapshot
from tournament_scheduler.application.ports.common import PortCapabilities, SourceRef
from tournament_scheduler.application.ports.ice_time import (
    AllocationWindow,
    Completeness,
    ObservedCalendarEvent,
    SourceCoverage,
    SourceScope,
)


@dataclass(frozen=True)
class IceTimeSnapshot:
    """Validated, revision-bound facts from one source. Construction validates all-or-nothing."""

    source: SourceRef
    scope: SourceScope
    completeness: Completeness
    windows: tuple[AllocationWindow, ...] = ()
    events: tuple[ObservedCalendarEvent, ...] = ()
    fresh_until: datetime | None = None

    def __post_init__(self) -> None:
        validate_snapshot(self.scope, self.windows, self.events)

    @property
    def revision(self) -> str:
        return windows_fingerprint(self.windows, self.completeness)


class SnapshotSource:
    """``IceTimeSourcePort`` over one snapshot; scope must match the declared one."""

    def __init__(self, snapshot: IceTimeSnapshot, features: frozenset[str],
                 adapter_id: str, requires_network: bool = False) -> None:
        self._snap = snapshot
        self._caps = PortCapabilities(adapter_id=adapter_id, features=features,
                                      requires_network=requires_network)

    @property
    def snapshot(self) -> IceTimeSnapshot:
        return self._snap

    def capabilities(self) -> PortCapabilities:
        return self._caps

    def _applies(self, scope: SourceScope) -> bool:
        s = self._snap.scope
        return s.venue_id == scope.venue_id and s.from_date <= scope.to_date and scope.from_date <= s.to_date

    def allocations(self, scope: SourceScope) -> tuple[AllocationWindow, ...]:
        return tuple(w for w in self._snap.windows
                     if self._applies(scope) and w.starts_at < scope.to_date and scope.from_date < w.ends_at)

    def observed_events(self, scope: SourceScope) -> tuple[ObservedCalendarEvent, ...]:
        return tuple(e for e in self._snap.events
                     if self._applies(scope) and e.starts_at < scope.to_date and scope.from_date < e.ends_at)

    def coverage(self, scope: SourceScope) -> tuple[SourceCoverage, ...]:
        if not self._applies(scope):
            return ()
        s = self._snap
        # A snapshot only covers its own scope; a wider query is partial at best.
        full = s.scope.from_date <= scope.from_date and scope.to_date <= s.scope.to_date
        completeness = s.completeness if full else (
            Completeness.PARTIAL if s.completeness is Completeness.COMPLETE else s.completeness)
        return (SourceCoverage(s.source, s.scope, completeness, s.source.captured_at, s.fresh_until),)
