"""Live calendar adapter: the fetcher is injected, so this module does no I/O itself.

Wraps existing per-club scrapers (or fixtures) behind the port. Snapshots are
cached per scope; a failed refresh retains the last snapshot for that scope as
stale/limited evidence per an explicit policy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from tournament_scheduler.application.ice_time_sources.validation import IceTimeValidationError
from tournament_scheduler.application.ports.common import (
    PortCapabilities,
    PortError,
    PortErrorCode,
    SourceKind,
    SourceRef,
)
from tournament_scheduler.application.ports.ice_time import (
    AllocationWindow,
    Completeness,
    ObservedCalendarEvent,
    SourceCoverage,
    SourceScope,
)

from .snapshot import IceTimeSnapshot, SnapshotSource

FEATURES = frozenset({"observed_events", "live"})



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


def _key(scope: SourceScope) -> tuple[str, datetime, datetime]:
    return scope.venue_id, scope.from_date, scope.to_date


class LiveCalendarSource:
    def __init__(self, source_id: str, timezone: str, fetch: Callable[[SourceScope], LivePayload],
                 clock: Callable[[], datetime], ttl: timedelta = timedelta(hours=6),
                 stale: StalePolicy = StalePolicy()) -> None:
        self._id, self._tz, self._fetch, self._clock = source_id, timezone, fetch, clock
        self._ttl, self._stale = ttl, stale
        self._cache: dict[tuple, SnapshotSource] = {}
        self._retained: set[tuple] = set()

    def capabilities(self) -> PortCapabilities:
        return PortCapabilities(self._id, features=FEATURES, requires_network=True)

    def refresh(self, scope: SourceScope) -> None:
        now, key = self._clock(), _key(scope)
        try:
            payload = self._fetch(scope)
        except PortError as exc:
            last = self._cache.get(key)
            if exc.code is not PortErrorCode.UNAVAILABLE or last is None:
                raise
            if now - last.snapshot.source.captured_at > self._stale.max_age:
                del self._cache[key]
                self._retained.discard(key)
                raise
            self._retained.add(key)
            return
        # Build and validate everything before touching the cache: atomic on bad payloads.
        try:
            ref = SourceRef(self._id, SourceKind.LIVE, now, self._tz, fingerprint=payload.fingerprint)
            events = tuple(ObservedCalendarEvent(ref, i, v, s, e, t) for i, v, s, e, t in payload.events)
            wins = tuple(replace(w, source=ref) for w in payload.allocations)
            snap = IceTimeSnapshot(ref, scope, payload.completeness, wins, events,
                                   fresh_until=now + self._ttl if payload.completeness is Completeness.COMPLETE
                                   else None)
        except (ValueError, TypeError) as exc:
            if isinstance(exc, IceTimeValidationError):
                raise
            raise IceTimeValidationError([f"malformed live payload: {exc}"]) from exc
        self._cache[key] = SnapshotSource(snap, FEATURES, self._id, True)
        self._retained.discard(key)

    def _need(self, scope: SourceScope) -> SnapshotSource:
        inner = self._cache.get(_key(scope))
        if inner is None:
            raise PortError(PortErrorCode.UNAVAILABLE,
                            f"live source has no snapshot for {scope.venue_id}; call refresh(scope)")
        return inner

    def allocations(self, scope):
        return self._need(scope).allocations(scope)

    def observed_events(self, scope):
        return self._need(scope).observed_events(scope)

    def coverage(self, scope) -> tuple[SourceCoverage, ...]:
        cov = self._need(scope).coverage(scope)
        if _key(scope) not in self._retained:
            return cov
        # Retained after a failed refresh: limited evidence, explicitly not fresh.
        return tuple(replace(c, completeness=Completeness.PARTIAL if c.completeness is
                             Completeness.COMPLETE else c.completeness,
                             fresh_until=c.observed_at) for c in cov)
