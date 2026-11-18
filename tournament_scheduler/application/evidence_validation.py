"""Validation of one source's returned evidence (identity, scope, shape, provenance).

Pure checks used by :mod:`.input_composition`; every problem is a reason string or
a typed :class:`PortError`, never an untyped exception.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from .ports.common import PortError, PortErrorCode, SourceRef
from .ports.ice_time import AllocationWindow, ObservedCalendarEvent, SourceCoverage, SourceScope


def failure_of(exc: Exception) -> tuple[PortErrorCode, str]:
    if isinstance(exc, PortError):
        return exc.code, exc.message
    return PortErrorCode.UNAVAILABLE, f"unexpected {type(exc).__name__}: {exc}"


def overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def typed_results(
    allocations: object, events: object, coverage: object
) -> tuple[tuple[AllocationWindow, ...], tuple[ObservedCalendarEvent, ...], tuple[SourceCoverage, ...]]:
    """Require three real collections of the right DTOs; anything else is SCHEMA_INCOMPATIBLE."""
    out = []
    for label, value, kind in (
        ("allocations", allocations, AllocationWindow),
        ("observed_events", events, ObservedCalendarEvent),
        ("coverage", coverage, SourceCoverage),
    ):
        if not isinstance(value, (tuple, list)):
            raise PortError(PortErrorCode.SCHEMA_INCOMPATIBLE, f"{label} must be a tuple or list, got {type(value).__name__}")
        for item in value:
            if not isinstance(item, kind):
                raise PortError(PortErrorCode.SCHEMA_INCOMPATIBLE, f"{label} contains {type(item).__name__}, expected {kind.__name__}")
        out.append(tuple(value))
    return out[0], out[1], out[2]


def foreign_reason(
    adapter_id: str,
    scope: SourceScope,
    allocations: Sequence[AllocationWindow],
    events: Sequence[ObservedCalendarEvent],
    coverage: Sequence[SourceCoverage],
) -> str | None:
    """Reason the returned evidence is not this adapter's, or not about *scope*, else None."""
    refs: list[SourceRef] = [x.source for x in (*allocations, *events, *coverage)]
    ids = {r.source_id for r in refs}
    if len(ids) > 1:
        return f"evidence mixes source identities {sorted(ids)}"
    # Identity contract: a source id is the adapter id or ``<adapter id>:<detail>``.
    for found in ids:
        if found != adapter_id and not found.startswith(adapter_id + ":"):
            return f"evidence from source {found!r} was returned by adapter {adapter_id!r}"
    for c in coverage:
        if c.scope != scope:
            return f"coverage is for scope {c.scope.venue_id!r}, requested {scope.venue_id!r} {scope.from_date:%Y-%m-%d}..{scope.to_date:%Y-%m-%d}"
    for x in (*allocations, *events):
        if x.venue_id != scope.venue_id:
            return f"evidence is for venue {x.venue_id!r}, requested {scope.venue_id!r}"
        if x.starts_at < scope.from_date or x.ends_at > scope.to_date:
            return "evidence is not contained in the requested period (no silent clipping)"
    # One observation: provenance must agree across every item of one call.
    shapes = {(r.kind, r.revision, r.fingerprint, r.captured_at, r.timezone) for r in refs}
    if len(shapes) > 1:
        return "evidence from one observation carries conflicting provenance (kind/revision/fingerprint/capture)"
    return None
