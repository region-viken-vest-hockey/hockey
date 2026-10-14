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

from tournament_scheduler.clubs.profiles import AvailabilityPolicy, get_club_profile
from tournament_scheduler.models import CalendarEvent

# Compatibility names for existing source and tests. The arena and allocation
# policy themselves are owned by the validated structured profile.
SANDEFJORD_CLUB_NAME = "Sandefjord Penguins"
_SANDEFJORD_PROFILE = get_club_profile(SANDEFJORD_CLUB_NAME)
if _SANDEFJORD_PROFILE is None or not _SANDEFJORD_PROFILE.availability_policies:
    raise RuntimeError("Sandefjord's validated club-controlled availability profile is required")
SANDEFJORD_ARENA = _SANDEFJORD_PROFILE.venue_for_arena().arena

_BUSY_EVENT_NAME = "Sandefjord Penguins fast istid (opptatt utenom tildelt helgevindu)"


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _busy_event(date_str: str, day: date, start_minutes: int, duration_minutes: int) -> CalendarEvent:
    return CalendarEvent(
        date=date_str,
        name=_BUSY_EVENT_NAME,
        datetime=datetime.combine(day, time(start_minutes // 60, start_minutes % 60)),
        duration_minutes=duration_minutes,
        location=SANDEFJORD_ARENA,
    )


def _minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":", 1))
    return hour * 60 + minute


def _fixed_busy_events_for_policy(
    start: date | datetime,
    end: date | datetime,
    policy: AvailabilityPolicy,
) -> List[CalendarEvent]:
    """Materialize a profile policy as conservative busy-complement evidence."""

    start_date = _as_date(start)
    end_date = _as_date(end)
    events: List[CalendarEvent] = []
    current = start_date
    while current <= end_date:
        date_str = current.strftime("%d.%m.%Y")
        windows = sorted(
            (window for window in policy.windows if current.weekday() in window.weekdays),
            key=lambda window: window.start,
        )
        cursor = 0
        for window in windows:
            window_start = _minutes(window.start)
            window_end = _minutes(window.end)
            if cursor < window_start:
                events.append(_busy_event(date_str, current, cursor, window_start - cursor))
            cursor = max(cursor, window_end)
        if cursor < 24 * 60:
            events.append(_busy_event(date_str, current, cursor, 24 * 60 - cursor))
        current += timedelta(days=1)
    return events


def sandefjord_fixed_busy_events(
    start: date | datetime, end: date | datetime
) -> List[CalendarEvent]:
    """Return busy evidence outside Sandefjord's club-controlled windows.

    The positive window is not scraper-proven ``free`` time. Its authority and
    ``available_for_internal_allocation`` semantics remain explicit in the club
    profile; this compatibility projection only supplies its fixed-busy
    complement to generic slot finding.
    """

    return _fixed_busy_events_for_policy(
        start,
        end,
        _SANDEFJORD_PROFILE.availability_policies[0],
    )
