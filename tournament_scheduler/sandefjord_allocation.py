"""Sandefjord Penguins' fixed weekend ice allocation (issue #261).

Sandefjord Penguins do not book Bugårdshallen (Bugården ishall) ad hoc
through BookUp like the other RVV clubs with BookUp sources (Tønsberg
excepted, which genuinely still requires that). Instead the club holds a
known, fixed weekly ice-time allocation -- Saturday and Sunday 15:00-18:00 --
from the club's season ice-time plan ("ISTIDER PLAN 26-27", attached to
https://github.com/region-viken-vest-hockey/hockey/issues/261). Outside that
window the hall is booked by other user groups and is not available for RVV
Miniputt hosting.

This module encodes that allocation as deterministic evidence -- ordinary
"busy" `CalendarEvent`s covering everything *outside* the fixed window -- so
the existing generic slot-finding machinery (`utils.slot_finder`,
`scheduler.TournamentScheduler.find_arena_slot_for_date`) treats Sandefjord
exactly like a club with a real scraped calendar. Sandefjord simply never
needs one: the allocation is already known, and Stage 2 no longer has to
scrape or recover a Sandefjord BookUp session for planning to work.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import List

from .models import CalendarEvent

# Canonical club/arena names, matching `club_registry.CLUB_REGISTRY`.
SANDEFJORD_CLUB_NAME = "Sandefjord Penguins"
SANDEFJORD_ARENA = "Bugården ishall"

# Saturday=5, Sunday=6 per `date.weekday()`.
_ALLOCATED_WEEKDAYS = (5, 6)
_ALLOCATION_START_HOUR = 15
_ALLOCATION_END_HOUR = 18

_BUSY_EVENT_NAME = "Sandefjord Penguins fast istid (opptatt utenom tildelt helgevindu)"


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _busy_event(date_str: str, day: date, start_hour: int, duration_hours: float) -> CalendarEvent:
    return CalendarEvent(
        date=date_str,
        name=_BUSY_EVENT_NAME,
        datetime=datetime.combine(day, time(start_hour, 0)),
        duration_hours=duration_hours,
        location=SANDEFJORD_ARENA,
    )


def sandefjord_fixed_busy_events(
    start: date | datetime, end: date | datetime
) -> List[CalendarEvent]:
    """Return synthetic busy events blocking everything outside the fixed window.

    For every date in ``[start, end]``: weekdays are fully busy (no ice
    available at all), and Saturday/Sunday are busy except for
    15:00-18:00 -- the club's fixed allocation. Feeding this into
    ``events_by_club["Sandefjord Penguins"]`` makes Stage 3's generic slot
    search only ever place a Sandefjord-hosted tournament inside a valid
    allocation window, without any Sandefjord-specific logic in the planner
    or scheduler themselves.
    """
    start_date = _as_date(start)
    end_date = _as_date(end)

    events: List[CalendarEvent] = []
    current = start_date
    while current <= end_date:
        date_str = current.strftime("%d.%m.%Y")
        if current.weekday() in _ALLOCATED_WEEKDAYS:
            events.append(_busy_event(date_str, current, 0, _ALLOCATION_START_HOUR))
            events.append(
                _busy_event(
                    date_str,
                    current,
                    _ALLOCATION_END_HOUR,
                    24 - _ALLOCATION_END_HOUR,
                )
            )
        else:
            events.append(_busy_event(date_str, current, 0, 24))
        current += timedelta(days=1)
    return events
