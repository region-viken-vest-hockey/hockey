"""StyledCalendar scraper for Stage 2 (Bærum ishall / Jutul).

Provides :func:`_run_styledcalendar_scraper`.

StyledCalendar's public embed page (https://embed.styledcalendar.com/#<id>)
renders a FullCalendar widget whose DOM never carries per-event start/end
times as text -- month view shows only a title dot, and even the time-grid
week/day views position events purely by CSS pixel offset with no time
label. Browser/DOM scraping can therefore only ever recover the *date* of
an event, never its real time or duration.

The widget itself is backed by a plain JSON API
(``/api/get-styled-calendar-events-data/?styledCalendarId=<id>``) that
returns the *complete* underlying event set -- including recurrence rules
-- with real ISO-8601 start/end timestamps, independent of whatever month
happens to be in view. This module calls that API directly (no browser
required) and decompresses its payload, which is compressed with the
``lz-string`` ``compressToUTF16``/``decompressFromUTF16`` scheme (see
:mod:`tournament_scheduler.utils.lzstring`).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import icalendar
import recurring_ical_events
import requests

from ..models import CalendarEvent
from ..utils.lzstring import decompress_from_utf16

_EVENTS_API_URL = "https://embed.styledcalendar.com/api/get-styled-calendar-events-data/"
_STYLED_CALENDAR_ID = "rYk5U1FtYNByMIMz2AoR"


def _run_styledcalendar_scraper(
    name: str,
    start_date: datetime,
    end_date: datetime,
) -> tuple[list[CalendarEvent], str]:
    """Fetch and expand StyledCalendar events (Bærum ishall/Jutul).

    Calls the widget's JSON events API directly, decompresses the
    ``lz-string``-encoded event payload, expands any recurring events
    (``RRULE``/``EXDATE``) against ``[start_date, end_date]``, and returns
    one :class:`CalendarEvent` per occurrence with its real start time and
    duration.
    """
    events: list[CalendarEvent] = []

    try:
        response = requests.get(
            _EVENTS_API_URL,
            params={"styledCalendarId": _STYLED_CALENDAR_ID},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        raw_events = _extract_raw_events(payload)
        calendar = _build_icalendar(raw_events)
        occurrences = recurring_ical_events.of(calendar).between(start_date, end_date)
    except Exception:
        return [], ""

    for occurrence in occurrences:
        title = str(occurrence.get("summary", "")).strip()
        dtstart = occurrence.get("dtstart")
        dtend = occurrence.get("dtend")
        if not title or dtstart is None:
            continue
        start_dt = _as_naive_datetime(dtstart.dt)
        if start_dt is None:
            continue
        if dtend is not None and (end_dt := _as_naive_datetime(dtend.dt)) is not None:
            duration_hours = max((end_dt - start_dt).total_seconds() / 3600.0, 0.0)
        else:
            duration_hours = 0.0

        events.append(CalendarEvent(
            date=start_dt.strftime("%d.%m.%Y"),
            name=title,
            datetime=start_dt,
            duration_hours=duration_hours,
        ))

    return events, ""


def _extract_raw_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Decompress and merge every ``compressedEvents`` blob in the API payload."""
    raw_events: list[dict[str, Any]] = []
    for entry in payload.get("compressedEventsAndIds") or []:
        blob = entry.get("compressedEvents")
        if not blob:
            continue
        decoded = decompress_from_utf16(blob)
        if not decoded:
            continue
        parsed = json.loads(decoded)
        if isinstance(parsed, list):
            raw_events.extend(item for item in parsed if isinstance(item, dict))
    return raw_events


def _build_icalendar(raw_events: list[dict[str, Any]]) -> icalendar.Calendar:
    """Build an in-memory :class:`icalendar.Calendar` from the API's raw event dicts."""
    calendar = icalendar.Calendar()
    for raw in raw_events:
        start = _parse_iso(raw.get("start"))
        end = _parse_iso(raw.get("end"))
        if start is None or end is None:
            continue

        vevent = icalendar.Event()
        vevent.add("summary", raw.get("title", ""))
        vevent.add("uid", raw.get("id", ""))
        vevent.add("dtstart", start)
        vevent.add("dtend", end)

        for rule in raw.get("recurrence") or []:
            if not isinstance(rule, str):
                continue
            if rule.upper().startswith("RRULE:"):
                try:
                    vevent.add("rrule", icalendar.vRecur.from_ical(rule[len("RRULE:"):]))
                except (ValueError, KeyError):
                    continue

        for exdate in raw.get("exdate") or []:
            parsed_exdate = _parse_iso(exdate)
            if parsed_exdate is not None:
                vevent.add("exdate", parsed_exdate)

        calendar.add_component(vevent)
    return calendar


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _as_naive_datetime(value: Any) -> datetime | None:
    """Normalize an icalendar-resolved occurrence boundary to a naive local datetime.

    The API's timestamps already carry the event's own Europe/Oslo wall-clock
    offset (e.g. +02:00 in summer). Stripping tzinfo directly keeps that
    wall-clock value; converting via ``.astimezone()`` would instead shift it
    to the *process's* local timezone, which is wrong here and is exactly
    what made this non-deterministic between a machine set to Oslo time and
    a UTC CI runner.
    """
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.replace(tzinfo=None)
        return value
    # icalendar can resolve an all-day/date-only occurrence to a plain date.
    try:
        return datetime(value.year, value.month, value.day)
    except AttributeError:
        return None
