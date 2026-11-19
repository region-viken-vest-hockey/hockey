"""Source-independent availability verdict, conflicts and source health."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from ..ports.common import SourceKind
from ..ports.ice_time import (
    AllocationWindow,
    BookingAssertion,
    Completeness,
    ConflictKind,
    IceTimeSourcePort,
    SourceConflict,
    SourceCoverage,
    SourceScope,
    WindowKind,
)


class Verdict(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class _Span:
    starts_at: datetime
    ends_at: datetime


def _overlaps(a, b) -> bool:
    return a.starts_at < b.ends_at and b.starts_at < a.ends_at


def availability_verdict(
    source: IceTimeSourcePort, scope: SourceScope, venue_id: str,
    starts_at: datetime, ends_at: datetime, now: datetime,
) -> Verdict:
    """Same normalized facts give the same verdict whatever the source kind.

    Blocking evidence always wins. Positive AVAILABLE (and a "nothing here"
    UNAVAILABLE) needs COMPLETE, fresh coverage of the probe; anything else
    (partial, unverified, stale or retained evidence) is UNKNOWN, never free ice.
    """
    probe = _Span(starts_at, ends_at)
    wins = [w for w in source.allocations(scope) if w.venue_id == venue_id]
    if any(w.kind is WindowKind.BLOCKED and _overlaps(w, probe) for w in wins):
        return Verdict.UNAVAILABLE
    if any(e.venue_id == venue_id and _overlaps(e, probe) for e in source.observed_events(scope)):
        return Verdict.UNAVAILABLE
    covs = source.coverage(scope)
    trusted = bool(covs) and all(
        c.completeness is Completeness.COMPLETE and c.is_fresh(now)
        and c.scope.venue_id == venue_id and c.scope.from_date <= starts_at and ends_at <= c.scope.to_date
        for c in covs
    )
    if not trusted:
        return Verdict.UNKNOWN
    if any(w.kind is WindowKind.AVAILABLE and w.starts_at <= starts_at and w.ends_at >= ends_at
           for w in wins):
        return Verdict.AVAILABLE
    return Verdict.UNAVAILABLE


def find_conflicts(
    windows: tuple[AllocationWindow, ...],
    assertions: tuple[BookingAssertion, ...] = (),
    coverages: tuple[SourceCoverage, ...] = (),
    now: datetime | None = None,
) -> tuple[SourceConflict, ...]:
    """Explicit REVIEW_REQUIRED items; independent sources are never auto-ranked."""
    out: list[SourceConflict] = []
    for i, a in enumerate(assertions):
        for b in assertions[i + 1:]:
            if a.venue_id == b.venue_id and _overlaps(a, b) and a.tournament_id != b.tournament_id:
                out.append(SourceConflict(ConflictKind.OVERLAPPING_ASSERTIONS,
                                          (a.tournament_id, b.tournament_id), f"both claim {a.venue_id}"))
    blocked = [w for w in windows if w.kind is WindowKind.BLOCKED]
    for a in assertions:
        for w in blocked:
            if a.venue_id == w.venue_id and _overlaps(a, w):
                out.append(SourceConflict(ConflictKind.ASSERTION_VS_BLOCKED_WINDOW,
                                          (a.tournament_id, w.venue_id), "assertion overlaps a blocked window"))
    for i, a in enumerate(windows):
        for b in windows[i + 1:]:
            if (a.venue_id == b.venue_id and a.source.source_id != b.source.source_id
                    and a.kind is not b.kind and _overlaps(a, b)):
                out.append(SourceConflict(ConflictKind.CONTRADICTORY_SOURCES,
                                          (a.source.source_id, b.source.source_id),
                                          f"{a.kind.value} vs {b.kind.value} at {a.venue_id}"))
    if now is not None:
        out += [SourceConflict(ConflictKind.STALE_COVERAGE, (c.source.source_id, c.scope.venue_id), "complete coverage is no longer fresh")
                for c in coverages if c.completeness is Completeness.COMPLETE and not c.is_fresh(now)]
    return tuple(out)


class HealthStatus(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    PARTIAL = "partial"
    UNVERIFIED = "unverified"
    NEEDS_REATTESTATION = "needs_reattestation"
    UNCONFIGURED = "unconfigured"


@dataclass(frozen=True)
class SourceHealth:
    status: HealthStatus
    kind: SourceKind | None
    as_of: datetime | None
    detail: str


def source_health(
    coverages: tuple[SourceCoverage, ...], now: datetime,
    reattest_after: timedelta = timedelta(days=30),
) -> SourceHealth:
    """Manual sources report snapshot date/attestation, never scraper freshness errors."""
    if not coverages:
        return SourceHealth(HealthStatus.UNCONFIGURED, None, None, "no source supplied")
    c = max(coverages, key=lambda c: c.observed_at)
    if c.completeness is Completeness.UNKNOWN:
        return SourceHealth(HealthStatus.UNVERIFIED, c.source.kind, c.observed_at, "completeness not attested")
    if c.completeness is Completeness.PARTIAL:
        return SourceHealth(HealthStatus.PARTIAL, c.source.kind, c.observed_at, "partial coverage")
    if c.source.kind is SourceKind.MANUAL:
        if now - c.observed_at > reattest_after:
            return SourceHealth(HealthStatus.NEEDS_REATTESTATION, c.source.kind, c.observed_at,
                                f"attested {c.observed_at.date()} by {c.source.actor}")
        return SourceHealth(HealthStatus.CURRENT, c.source.kind, c.observed_at, f"attested by {c.source.actor}")
    if not c.is_fresh(now):
        return SourceHealth(HealthStatus.STALE, c.source.kind, c.observed_at, "retained snapshot, not current")
    return SourceHealth(HealthStatus.CURRENT, c.source.kind, c.observed_at, "fresh")
