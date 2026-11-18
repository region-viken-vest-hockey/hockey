"""Live calendar adapter: the fetcher is injected, so this module does no I/O itself.

Wraps existing per-club scrapers (or fixtures) behind the port. A failure
retains the last snapshot as stale/limited evidence per an explicit policy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from tournament_scheduler.application.ports.common import PortCapabilities, PortError, PortErrorCode, SourceKind, SourceRef
from tournament_scheduler.application.ports.ice_time import (
    AllocationWindow,
    Completeness,
    ObservedCalendarEvent,
    SourceCoverage,
    SourceScope,
)

from .snapshot import IceTimeSnapshot, SnapshotSource

FEATURES = frozenset({"observed_events", "live"})
_CAPS = PortCapabilities("live-calendar", features=FEATURES, requires_network=True)


@dataclass(frozen=True)
class LivePayload:
    """Normalized result of one fetch; ``fingerprint`` hashes the raw payload."""

    fingerprint: str
    events: tuple[tuple[str, str, datetime, datetime, str], ...]  # id, venue, start, end, title
    completeness: Completeness
    allocations: tuple[AllocationWindow, ...] = ()


@dataclass(frozen=True)
class StalePolicy:
    """How long a retained snapshot stays usable as (limited) evidence."""

    max_age: timedelta = timedelta(days=2)


class LiveCalendarSource:
    def __init__(self, source_id: str, timezone: str, fetch: Callable[[SourceScope], LivePayload],
                 clock: Callable[[], datetime], ttl: timedelta = timedelta(hours=6),
                 stale: StalePolicy = StalePolicy()) -> None:
        self._id, self._tz, self._fetch, self._clock = source_id, timezone, fetch, clock
        self._ttl, self._stale = ttl, stale
        self._last: SnapshotSource | None = None
        self._retained = False

    def capabilities(self) -> PortCapabilities:
        return _CAPS

    def refresh(self, scope: SourceScope) -> None:
        now = self._clock()
        try:
            p = self._fetch(scope)
        except PortError as exc:
            if exc.code is not PortErrorCode.UNAVAILABLE or self._last is None:
                raise
            self._retained = now - self._last.snapshot.source.captured_at <= self._stale.max_age
            if not self._retained:
                self._last = None
                raise
            return
        ref = SourceRef(self._id, SourceKind.LIVE, now, self._tz, fingerprint=p.fingerprint)
        events = tuple(ObservedCalendarEvent(ref, i, v, s, e, t) for i, v, s, e, t in p.events)
        wins = tuple(replace(w, source=ref) for w in p.allocations)
        snap = IceTimeSnapshot(ref, scope, p.completeness, wins, events, fresh_until=now + self._ttl)
        self._last, self._retained = SnapshotSource(snap, FEATURES, "live-calendar", True), False

    def _need(self) -> SnapshotSource:
        if self._last is None:
            raise PortError(PortErrorCode.UNAVAILABLE, "live source has no snapshot; call refresh()")
        return self._last

    def allocations(self, scope):
        return self._need().allocations(scope)

    def observed_events(self, scope):
        return self._need().observed_events(scope)

    def coverage(self, scope) -> tuple[SourceCoverage, ...]:
        cov = self._need().coverage(scope)
        if not self._retained:
            return cov
        # Retained after a failed refresh: limited evidence, explicitly not fresh.
        return tuple(replace(c, completeness=Completeness.PARTIAL if c.completeness is
                             Completeness.COMPLETE else c.completeness,
                             fresh_until=c.observed_at) for c in cov)
