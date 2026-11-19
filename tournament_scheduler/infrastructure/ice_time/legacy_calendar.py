"""Thin adapter: legacy ``CalendarEvent`` producer output -> live port.

Wraps any callable that returns legacy ``CalendarEvent`` lists; today only the
fixed-allocation source (``run_fixed_allocation_source``) is exercised. Binding
the per-club Outlook/BookUp/iCal scrapers is out of scope here (tracked in #978). It adds no policy: each
busy event becomes one observed event; trust/freshness come from the port.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from tournament_scheduler.application.ports.ice_time import Completeness, SourceScope

from .live import LivePayload


def legacy_events_fetcher(
    run: Callable[[datetime, datetime], Sequence],
    *, venue_id: str, timezone: str, completeness: Completeness = Completeness.PARTIAL,
) -> Callable[[SourceScope], LivePayload]:
    """Return a fetcher for ``LiveCalendarSource``.

    ``run(start, end)`` yields legacy events (``datetime``, ``end_datetime``,
    ``name``, optional ``order_id``). Scraped calendars default to PARTIAL
    (what a scraper saw is not proof of vacancy); a deterministic fixed
    allocation may declare ``COMPLETE``. Naive legacy datetimes are read in
    ``timezone``; events entirely outside the requested scope (range-edge
    artefacts of legacy generators) are dropped.
    """
    tz = ZoneInfo(timezone)

    def aware(moment: datetime) -> datetime:
        return moment.replace(tzinfo=tz) if moment.tzinfo is None else moment

    def fetch(scope: SourceScope) -> LivePayload:
        raw = run(scope.from_date.astimezone(tz).replace(tzinfo=None),
                  scope.to_date.astimezone(tz).replace(tzinfo=None))
        raw = [e for e in raw if aware(e.end_datetime) > scope.from_date and aware(e.datetime) < scope.to_date]
        rows = tuple(
            (getattr(e, "order_id", "") or f"{aware(e.datetime).isoformat()}|{e.name}",
             venue_id, aware(e.datetime), aware(e.end_datetime), e.name)
            for e in raw
        )
        digest = hashlib.sha256("\n".join(repr(r) for r in sorted(rows, key=repr)).encode()).hexdigest()
        return LivePayload(digest, rows, completeness)

    return fetch
