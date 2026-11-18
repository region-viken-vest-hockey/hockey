"""Ice-time source contracts: four distinct fact kinds, never conflated.

(a) :class:`AllocationWindow` -- venue availability/allocation windows.
(b) :class:`ObservedCalendarEvent` + :class:`SourceCoverage` -- what a source was
    seen to contain, and how complete/fresh that observation is.
(c) :class:`BookingAssertion` -- an exact booked-tournament claim with explicit
    trust. Only this type can carry booking authority.
(d) Effective planning/verification facts are *derived* by the canonical
    projection owner from (a)-(c); they are intentionally not a DTO here.

Invariants: free ice (a) and an empty or partial observation (b) are never
booking authority. Uncertainty and conflict are explicit values. A manual-only
mode needs no URL or network (``PortCapabilities.requires_network`` is False).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from .common import PortCapabilities, SourceRef


def _aware_interval(starts_at: datetime, ends_at: datetime) -> None:
    if starts_at.tzinfo is None or ends_at.tzinfo is None:
        raise ValueError("interval bounds must be timezone-aware")
    if not starts_at < ends_at:
        raise ValueError("interval must have positive duration")


@dataclass(frozen=True)
class SourceScope:
    """The venue and period a source claims to describe."""

    venue_id: str
    from_date: datetime
    to_date: datetime

    def __post_init__(self) -> None:
        _aware_interval(self.from_date, self.to_date)


class WindowKind(str, Enum):
    AVAILABLE = "available"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class AllocationWindow:
    """Venue time that may be offered to planning. Carries no booking authority."""

    source: SourceRef
    venue_id: str
    starts_at: datetime
    ends_at: datetime
    kind: WindowKind

    def __post_init__(self) -> None:
        _aware_interval(self.starts_at, self.ends_at)


@dataclass(frozen=True)
class ObservedCalendarEvent:
    """One event seen in a source. Occupancy evidence, never automatic confirmation."""

    source: SourceRef
    event_id: str
    venue_id: str
    starts_at: datetime
    ends_at: datetime
    title: str = ""

    def __post_init__(self) -> None:
        _aware_interval(self.starts_at, self.ends_at)


class Completeness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceCoverage:
    """How much of a scope one observation covers and until when it is fresh.

    ``COMPLETE`` with zero events means "nothing seen", which is *not* proof that
    nothing is booked: absence is evidence only for the scope and freshness
    stated here, and never creates a :class:`BookingAssertion`.
    """

    source: SourceRef
    scope: SourceScope
    completeness: Completeness
    observed_at: datetime
    fresh_until: datetime | None = None

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or (
            self.fresh_until is not None and self.fresh_until.tzinfo is None
        ):
            raise ValueError("coverage timestamps must be timezone-aware")

    def is_fresh(self, now: datetime) -> bool:
        return self.fresh_until is not None and now <= self.fresh_until


class AssertionTrust(str, Enum):
    CONFIRMED = "confirmed"
    PROVISIONAL = "provisional"
    CONTRADICTED = "contradicted"


@dataclass(frozen=True)
class BookingAssertion:
    """An exact claim that one tournament holds one interval at one venue.

    ``CONFIRMED`` requires an actor-, document- or payload-fingerprinted source;
    ``source`` (and so the manual/file/live kind) is always recorded.
    """

    source: SourceRef
    tournament_id: str
    venue_id: str
    starts_at: datetime
    ends_at: datetime
    trust: AssertionTrust
    event_id: str | None = None

    def __post_init__(self) -> None:
        _aware_interval(self.starts_at, self.ends_at)
        if not self.tournament_id:
            raise ValueError("an assertion must name its tournament")
        if self.trust is AssertionTrust.CONFIRMED and not (
            self.source.actor or self.source.fingerprint
        ):
            raise ValueError("a confirmed assertion needs an accountable or fingerprinted source")


class ConflictKind(str, Enum):
    OVERLAPPING_ASSERTIONS = "overlapping_assertions"
    ASSERTION_VS_BLOCKED_WINDOW = "assertion_vs_blocked_window"
    STALE_COVERAGE = "stale_coverage"
    CONTRADICTORY_SOURCES = "contradictory_sources"


@dataclass(frozen=True)
class SourceConflict:
    """An explicit, unresolved disagreement or uncertainty. Never silently dropped."""

    kind: ConflictKind
    subject_ids: tuple[str, ...]
    detail: str


@runtime_checkable
class IceTimeSourcePort(Protocol):
    """One normalized-input source (manual entry, file import or live scraper)."""

    def capabilities(self) -> PortCapabilities: ...

    def allocations(self, scope: SourceScope) -> tuple[AllocationWindow, ...]: ...

    def observed_events(self, scope: SourceScope) -> tuple[ObservedCalendarEvent, ...]: ...

    def coverage(self, scope: SourceScope) -> tuple[SourceCoverage, ...]: ...
