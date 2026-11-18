"""Compose normalized season input with ice-time source evidence (#958).

Consumes the typed :class:`IceTimeSourcePort` contract (adapters are owned by
#968) plus the operator-supplied facts carried by :class:`NormalizedSeasonInput`
and yields one evidence shape whatever the source mode (RVV live sources,
imported snapshots, or manual-only with no URL and no scraper).

Rules enforced here, never by a caller:

* a source error is a :class:`SourceFailure`, never an empty result;
* absence is :attr:`Absence.CONFIRMED_ABSENT` only for complete, fresh coverage
  of the scope; otherwise it is :attr:`Absence.NOT_CONFIRMED`;
* a venue with no offered window is ``UNKNOWN``, never free capacity;
* only operator-supplied :class:`BookingAssertion` s exist; observed events never
  become assertions;
* nothing is persisted or mutated: composition is a pure function of its inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Sequence

from .ports.common import PortError, PortErrorCode, SourceKind
from .ports.ice_time import (
    AllocationWindow,
    BookingAssertion,
    Completeness,
    ConflictKind,
    IceTimeSourcePort,
    ObservedCalendarEvent,
    SourceConflict,
    SourceCoverage,
    SourceScope,
    WindowKind,
)
from .ports.season_input import NormalizedSeasonInput


class FactOrigin(str, Enum):
    OPERATOR_SUPPLIED = "operator_supplied"
    IMPORTED_FILE = "imported_file"
    SOURCE_BACKED = "source_backed"


_ORIGIN = {
    SourceKind.MANUAL: FactOrigin.OPERATOR_SUPPLIED,
    SourceKind.FILE: FactOrigin.IMPORTED_FILE,
    SourceKind.LIVE: FactOrigin.SOURCE_BACKED,
}


def origin_of(kind: SourceKind) -> FactOrigin:
    return _ORIGIN[kind]


class Absence(str, Enum):
    CONFIRMED_ABSENT = "confirmed_absent"
    PRESENT = "present"
    NOT_CONFIRMED = "not_confirmed"
    SOURCE_ERROR = "source_error"


class VenueCapacity(str, Enum):
    OFFERED = "offered"
    BLOCKED_ONLY = "blocked_only"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceFailure:
    source_id: str
    scope: SourceScope
    code: PortErrorCode
    message: str


@dataclass(frozen=True)
class ScopeFinding:
    source_id: str
    scope: SourceScope
    absence: Absence
    reason: str


@dataclass(frozen=True)
class PlanningEvidence:
    season: NormalizedSeasonInput
    allocations: tuple[AllocationWindow, ...]
    observed_events: tuple[ObservedCalendarEvent, ...]
    coverage: tuple[SourceCoverage, ...]
    assertions: tuple[BookingAssertion, ...]
    findings: tuple[ScopeFinding, ...]
    failures: tuple[SourceFailure, ...]
    conflicts: tuple[SourceConflict, ...]
    venue_capacity: tuple[tuple[str, VenueCapacity], ...]

    @property
    def complete(self) -> bool:
        """True only when no source failed and every scope absence is decided."""
        return not self.failures and all(f.absence is not Absence.NOT_CONFIRMED for f in self.findings)


def _overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def _finding(
    source_id: str, scope: SourceScope, coverage: Sequence[SourceCoverage], events: Sequence[ObservedCalendarEvent], now: datetime
) -> ScopeFinding:
    mine = [c for c in coverage if c.scope == scope]
    if not mine:
        return ScopeFinding(source_id, scope, Absence.NOT_CONFIRMED, "source reported no coverage for this scope")
    if any(c.completeness is not Completeness.COMPLETE for c in mine):
        return ScopeFinding(source_id, scope, Absence.NOT_CONFIRMED, "coverage is partial or unknown")
    if not all(c.is_fresh(now) for c in mine):
        return ScopeFinding(source_id, scope, Absence.NOT_CONFIRMED, "coverage is stale or has no freshness bound")
    seen = [e for e in events if e.venue_id == scope.venue_id and _overlap(e.starts_at, e.ends_at, scope.from_date, scope.to_date)]
    if seen:
        return ScopeFinding(source_id, scope, Absence.PRESENT, f"{len(seen)} observed event(s)")
    return ScopeFinding(source_id, scope, Absence.CONFIRMED_ABSENT, "complete, fresh coverage with no events")


def _conflicts(
    allocations: Sequence[AllocationWindow], assertions: Sequence[BookingAssertion], coverage: Sequence[SourceCoverage], now: datetime
) -> tuple[SourceConflict, ...]:
    out: list[SourceConflict] = []
    for i, a in enumerate(assertions):
        for b in assertions[i + 1 :]:
            if a.venue_id == b.venue_id and a.tournament_id != b.tournament_id and _overlap(a.starts_at, a.ends_at, b.starts_at, b.ends_at):
                out.append(SourceConflict(ConflictKind.OVERLAPPING_ASSERTIONS, (a.tournament_id, b.tournament_id), f"both claim {a.venue_id}"))
        for w in allocations:
            if w.kind is WindowKind.BLOCKED and w.venue_id == a.venue_id and _overlap(a.starts_at, a.ends_at, w.starts_at, w.ends_at):
                out.append(SourceConflict(ConflictKind.ASSERTION_VS_BLOCKED_WINDOW, (a.tournament_id, w.venue_id), "assertion overlaps a blocked window"))
    for c in coverage:
        if c.completeness is Completeness.COMPLETE and not c.is_fresh(now):
            out.append(SourceConflict(ConflictKind.STALE_COVERAGE, (c.source.source_id, c.scope.venue_id), "complete coverage is no longer fresh"))
    return tuple(out)


def _capacity(venues: Sequence[str], allocations: Sequence[AllocationWindow]) -> tuple[tuple[str, VenueCapacity], ...]:
    result = []
    for venue in sorted(set(venues)):
        kinds = {w.kind for w in allocations if w.venue_id == venue}
        if WindowKind.AVAILABLE in kinds:
            result.append((venue, VenueCapacity.OFFERED))
        else:
            result.append((venue, VenueCapacity.BLOCKED_ONLY if kinds else VenueCapacity.UNKNOWN))
    return tuple(result)


def compose_planning_evidence(
    season: NormalizedSeasonInput,
    sources: Sequence[IceTimeSourcePort],
    scopes: Sequence[SourceScope],
    *,
    now: datetime,
) -> PlanningEvidence:
    """Gather evidence for *scopes* from *sources* and the operator's local facts.

    ``sources`` may be empty (manual-only). A source whose capabilities the
    application cannot use, or that raises :class:`PortError`, is recorded as a
    failure for every scope; remaining sources are still composed.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    allocations = list(season.operator_allocations)
    events: list[ObservedCalendarEvent] = []
    coverage: list[SourceCoverage] = []
    findings: list[ScopeFinding] = []
    failures: list[SourceFailure] = []
    for source in sources:
        caps = source.capabilities()
        for scope in scopes:
            try:
                caps.require()
                got_alloc = source.allocations(scope)
                got_events = source.observed_events(scope)
                got_cov = source.coverage(scope)
            except PortError as exc:
                failures.append(SourceFailure(caps.adapter_id, scope, exc.code, exc.message))
                findings.append(ScopeFinding(caps.adapter_id, scope, Absence.SOURCE_ERROR, exc.message))
                continue
            allocations += got_alloc
            events += got_events
            coverage += got_cov
            findings.append(_finding(caps.adapter_id, scope, got_cov, got_events, now))
    assertions = tuple(season.operator_assertions)
    venues = [s.venue_id for s in scopes] + [w.venue_id for w in season.operator_allocations]
    return PlanningEvidence(
        season=season,
        allocations=tuple(allocations),
        observed_events=tuple(events),
        coverage=tuple(coverage),
        assertions=assertions,
        findings=tuple(findings),
        failures=tuple(failures),
        conflicts=_conflicts(allocations, assertions, coverage, now),
        venue_capacity=_capacity(venues, allocations),
    )
