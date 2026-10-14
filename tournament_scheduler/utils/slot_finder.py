"""Reusable per-arena time-slot finding.

Generalizes the slot-finding logic that originally lived in
``TimeSlotChecker._find_available_slots`` so it can be reused outside the
conflict-checker pipeline (e.g. by the season planner when looking for
hour-level free slots on a candidate arena/date that fit a tournament's
total computed duration).

The core entry point is :func:`find_available_slots`, parameterized by
*required_minutes* (rather than a fixed ``min_duration_hours``) so callers
can pass the required hall occupancy for a tournament directly.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import List, Optional, Tuple

from tournament_scheduler.models_calendar import minute_duration_from_hours
from tournament_scheduler.utils.date_parser import DateParser


def parse_time(time_str: str) -> time:
    """Parse a ``HH:MM`` string into a :class:`datetime.time`."""
    hour, minute = map(int, time_str.split(':'))
    return time(hour, minute)


def format_time(t: time) -> str:
    """Format a :class:`datetime.time` as ``HH:MM``."""
    return f"{t.hour:02d}:{t.minute:02d}"


def minutes_to_time(minutes: int) -> str:
    """Convert minutes-since-midnight to a ``HH:MM`` string."""
    hour = minutes // 60
    minute = minutes % 60
    return f"{hour:02d}:{minute:02d}"


def matchday_duration_minutes(round_length: int, round_count: int, setup_buffer_minutes: int = 5) -> int:
    """Return the total occupied hall time for a round-robin matchday.

    `round_count` is the number of round-robin rounds that must fit in the
    hall. The result includes one setup/changeover buffer after each round.
    """
    if round_length <= 0 or round_count <= 0:
        return 0
    return round_count * (round_length + max(0, setup_buffer_minutes))


def _event_busy_range_on_date(event, check_date: date) -> Optional[Tuple[int, int]]:
    """Return an event's busy range on *check_date* in minutes since midnight.

    Events that cross midnight are projected onto both affected dates, so a
    booking from 23:00 to 02:00 blocks 23:00-24:00 on its start date and
    00:00-02:00 on the following date.
    """
    duration_minutes = getattr(event, "duration_minutes", None)
    if duration_minutes is None:
        duration_minutes = minute_duration_from_hours(getattr(event, "duration_hours", 0))
    if duration_minutes <= 0 or not hasattr(event.datetime, "hour"):
        return None
    parsed = DateParser.parse(event.date)
    if not parsed:
        return None
    event_start = event.datetime
    if not isinstance(event_start, datetime):
        event_start = datetime.combine(parsed.date(), time(event.datetime.hour, event.datetime.minute))
    event_end = event_start + timedelta(minutes=duration_minutes)
    day_start = datetime.combine(check_date, time.min)
    if event_start.tzinfo is not None and event_start.utcoffset() is not None:
        # Calendar feeds can produce timezone-aware datetimes, while the
        # planner's candidate dates are plain local dates. Compare within the
        # event's own timezone so Python does not mix aware and naive values.
        day_start = day_start.replace(tzinfo=event_start.tzinfo)
    day_end = day_start + timedelta(days=1)
    if event_end <= day_start or event_start >= day_end:
        return None
    clipped_start = max(event_start, day_start)
    clipped_end = min(event_end, day_end)
    start_minutes = int((clipped_start - day_start).total_seconds() // 60)
    end_minutes = int((clipped_end - day_start).total_seconds() // 60)
    if end_minutes <= start_minutes:
        return None
    return start_minutes, end_minutes


def find_available_slots(
    events: List,
    check_date: date,
    required_minutes: int,
    earliest_start: str = "10:00",
    latest_start: str = "15:30",
) -> List[Tuple[str, str]]:
    """Find available time slots on *check_date* that fit *required_minutes*.

    Args:
        events: Calendar events to check against (objects with ``date``,
            ``datetime`` and ``duration_hours`` attributes, e.g.
            :class:`tournament_scheduler.models.CalendarEvent`).
        check_date: Date to check.
        required_minutes: Minimum contiguous free duration required, in
            minutes.
        earliest_start: Earliest acceptable start time (``HH:MM``).
        latest_start: Latest acceptable start time (``HH:MM``).

    Returns:
        List of ``(start_time, end_time)`` tuples in ``HH:MM`` format,
        each representing a slot of exactly *required_minutes* starting at
        the earliest possible time within a free gap.
    """
    earliest = parse_time(earliest_start)
    latest = parse_time(latest_start)

    # Build list of busy time ranges (in minutes since midnight), including
    # the portion of overnight events that lands on check_date.
    busy_ranges = []
    for event in events:
        busy_range = _event_busy_range_on_date(event, check_date)
        if busy_range is not None:
            busy_ranges.append(busy_range)

    busy_ranges.sort()

    available_slots: List[Tuple[str, str]] = []
    earliest_minutes = earliest.hour * 60 + earliest.minute
    latest_start_minutes = latest.hour * 60 + latest.minute
    min_duration_minutes = required_minutes

    if busy_ranges:
        # Check if we can fit a slot before the first event.
        first_busy_start = busy_ranges[0][0]
        if first_busy_start >= earliest_minutes + min_duration_minutes:
            slot_start = max(earliest_minutes, 0)
            slot_end = first_busy_start
            if slot_start <= latest_start_minutes and slot_end - slot_start >= min_duration_minutes:
                available_slots.append((
                    minutes_to_time(slot_start),
                    minutes_to_time(slot_start + min_duration_minutes)
                ))

        # Check gaps between consecutive events.
        for i in range(len(busy_ranges) - 1):
            gap_start = busy_ranges[i][1]
            gap_end = busy_ranges[i + 1][0]

            earliest_possible_start = max(gap_start, earliest_minutes)
            latest_possible_start = min(gap_end - min_duration_minutes, latest_start_minutes)

            if earliest_possible_start <= latest_possible_start:
                available_slots.append((
                    minutes_to_time(earliest_possible_start),
                    minutes_to_time(earliest_possible_start + min_duration_minutes)
                ))

        # Check after the last event.
        last_busy_end = busy_ranges[-1][1]
        earliest_possible_start = max(last_busy_end, earliest_minutes)

        if earliest_possible_start <= latest_start_minutes:
            available_slots.append((
                minutes_to_time(earliest_possible_start),
                minutes_to_time(earliest_possible_start + min_duration_minutes)
            ))
    else:
        # No events - entire window is available.
        available_slots.append((
            format_time(earliest),
            minutes_to_time(earliest_minutes + min_duration_minutes)
        ))

    return available_slots
