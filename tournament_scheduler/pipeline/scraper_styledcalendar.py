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

Because that single request processes the whole requested window, a
successful fetch/decode/expansion is explicit *coverage provenance*: the
result is annotated with :func:`~tournament_scheduler.pipeline.source_integrity.with_coverage`
so the source-integrity gate can trust absence of an event as evidence about
the requested window. The annotation is attached to this concrete strategy
only -- a generic ``outlook`` browser scraper never receives it. Any failure
to fetch, decode, validate or expand the payload fails closed (``failed`` or
``partial``) instead of masquerading as a complete, empty calendar.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime, time
from typing import Any

import icalendar
import recurring_ical_events
import requests

from ..models import CalendarEvent
from ..utils.lzstring import decompress_from_utf16
from .fingerprints import stable_payload_sha256
from .source_integrity import (
    INTEGRITY_COMPLETE,
    INTEGRITY_FAILED,
    INTEGRITY_PARTIAL,
    with_coverage,
)

_EVENTS_API_URL = "https://embed.styledcalendar.com/api/get-styled-calendar-events-data/"
_STYLED_CALENDAR_ID = "rYk5U1FtYNByMIMz2AoR"

# Provenance marker: this coverage record can only come from the
# strategy-specific StyledCalendar API scraper below, never from a generic
# browser/navigation source.
_STYLEDCALENDAR_STRATEGY = "styledcalendar"


class StyledCalendarPayloadError(RuntimeError):
    """The StyledCalendar API response cannot be trusted as a complete event set."""


def _run_styledcalendar_scraper(
    name: str,
    start_date: datetime,
    end_date: datetime,
) -> tuple[list[CalendarEvent], str]:
    """Fetch and expand StyledCalendar events (Bærum ishall/Jutul).

    Calls the widget's JSON events API directly, decompresses the
    ``lz-string``-encoded event payload, expands any recurring events
    (``RRULE``/``EXDATE``) against the inclusive ``[start_date, end_date]``
    window, and returns one :class:`CalendarEvent` per occurrence with its
    real start time and duration.

    The returned list carries a coverage record (see
    :func:`_styledcalendar_coverage_record`). The window is the canonical
    season window Stage 2 asked for: the end date is treated as inclusive to
    the end of that day, and ``recurring_ical_events`` includes occurrences
    that overlap the window even when they start the previous day.
    """
    requested_start = start_date.date()
    requested_end = end_date.date()
    # Seconds-resolution end-of-day bound: Stage 2 passes the last season day
    # at midnight, so a plain ``between(start, end)`` would silently drop
    # every event later on the final day.
    window_start = datetime.combine(requested_start, time.min)
    window_end = datetime.combine(requested_end, time.max)

    try:
        response = requests.get(
            _EVENTS_API_URL,
            params={"styledCalendarId": _STYLED_CALENDAR_ID},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        raw_events, payload_problems = _extract_raw_events(payload)
        calendar, build_problems = _build_icalendar(raw_events)
        problems = [*payload_problems, *build_problems]
        # Materialize inside the guarded block: ``between`` is lazy, so an
        # iteration-time recurrence failure must still fail the scrape closed
        # instead of escaping past the ``except`` below.
        occurrences = list(
            recurring_ical_events.of(calendar).between(window_start, window_end)
        )
    except Exception as exc:  # noqa: BLE001 -- fail closed, report as coverage
        return with_coverage(
            [],
            **_styledcalendar_coverage_record(
                requested_start=requested_start,
                requested_end=requested_end,
                events=[],
                exceptions=[_failure_message(exc)],
                status=INTEGRITY_FAILED,
            ),
        ), ""

    events: list[CalendarEvent] = []
    for occurrence in occurrences:
        title = str(occurrence.get("summary", "")).strip()
        dtstart = occurrence.get("dtstart")
        dtend = occurrence.get("dtend")
        if not title:
            problems.append("En utvidet forekomst manglet tittel og ble forkastet.")
            continue
        if dtstart is None:
            problems.append(f"Hendelsen '{title}' manglet starttidspunkt og ble forkastet.")
            continue
        start_dt = _as_naive_datetime(dtstart.dt)
        if start_dt is None:
            problems.append(
                f"Hendelsen '{title}' hadde et utolkbart starttidspunkt og ble forkastet."
            )
            continue
        if dtend is None:
            problems.append(f"Hendelsen '{title}' manglet sluttidspunkt og ble forkastet.")
            continue
        end_dt = _as_naive_datetime(dtend.dt)
        if end_dt is None:
            problems.append(
                f"Hendelsen '{title}' hadde et utolkbart sluttidspunkt og ble forkastet."
            )
            continue
        duration_hours = (end_dt - start_dt).total_seconds() / 3600.0
        if duration_hours <= 0:
            # A nonpositive occupancy interval is unusable evidence and must
            # never be silently read as a real zero-length booking.
            problems.append(
                f"Hendelsen '{title}' hadde ikke-positiv varighet og ble forkastet."
            )
            continue

        events.append(CalendarEvent(
            date=start_dt.strftime("%d.%m.%Y"),
            name=title,
            datetime=start_dt,
            duration_hours=duration_hours,
        ))

    status = INTEGRITY_PARTIAL if problems else INTEGRITY_COMPLETE
    return with_coverage(
        events,
        **_styledcalendar_coverage_record(
            requested_start=requested_start,
            requested_end=requested_end,
            events=events,
            exceptions=problems,
            status=status,
        ),
    ), ""


def _styledcalendar_coverage_record(
    *,
    requested_start: date,
    requested_end: date,
    events: list[CalendarEvent],
    exceptions: list[str],
    status: str,
) -> dict[str, Any]:
    """Return explicit coverage provenance for one StyledCalendar API scrape.

    The API returns the widget's *complete* underlying event set in a single
    request, so a successful fetch/decode/expansion processes the whole
    requested window regardless of how many (or how few) events happen to fall
    inside it. ``observed_start``/``observed_end`` therefore describe the
    processed window -- they are never derived from where events happen to be --
    while ``event_observed_start``/``event_observed_end`` describe where events
    actually occurred. Conflating the two would let a sparse calendar look like
    an uncovered one, or vice versa.
    """
    completed = status == INTEGRITY_COMPLETE
    event_dates = [event.datetime.date() for event in events if event.datetime is not None]
    return {
        "strategy": _STYLEDCALENDAR_STRATEGY,
        "status": status,
        "navigation_complete": completed,
        "requested_start": requested_start.isoformat(),
        "requested_end": requested_end.isoformat(),
        # A complete single-request fetch covers the requested window; a
        # failed/partial fetch has no trustworthy observed bounds.
        "observed_start": requested_start.isoformat() if completed else None,
        "observed_end": requested_end.isoformat() if completed else None,
        "event_observed_start": min(event_dates).isoformat() if event_dates else None,
        "event_observed_end": max(event_dates).isoformat() if event_dates else None,
        "event_count": len(events),
        "event_fingerprint": _event_fingerprint(events),
        "exceptions": list(exceptions),
    }


def _event_fingerprint(events: list[CalendarEvent]) -> str:
    """Deterministic fingerprint of the expanded occurrences this scrape produced."""
    return stable_payload_sha256([
        {
            "date": event.date,
            "name": event.name,
            "datetime": event.datetime.isoformat() if event.datetime else None,
            "duration_hours": event.duration_hours,
            "location": event.location,
        }
        for event in events
    ])


def _failure_message(exc: Exception) -> str:
    """Human-readable, non-secret description of a failed StyledCalendar fetch."""
    if isinstance(exc, StyledCalendarPayloadError):
        return str(exc)
    return f"StyledCalendar API-skraping feilet: {type(exc).__name__}: {exc}"


def _extract_raw_events(payload: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Decompress and merge every ``compressedEvents`` blob in the API payload.

    Returns the merged raw events plus any per-blob problems. A structurally
    empty payload raises :class:`StyledCalendarPayloadError` (hard failure);
    a missing/undecodable/unparseable individual blob is reported as a problem
    so the scrape fails closed as ``partial`` rather than silently dropping
    that sub-calendar's events.
    """
    entries = payload.get("compressedEventsAndIds") if isinstance(payload, Mapping) else None
    if not isinstance(entries, list) or not entries:
        raise StyledCalendarPayloadError(
            "StyledCalendar-svaret inneholdt ingen komprimerte hendelsesdata."
        )

    raw_events: list[dict[str, Any]] = []
    problems: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            problems.append(f"Hendelsesblokk #{index} er ikke et objekt.")
            continue
        blob = entry.get("compressedEvents")
        if not blob or not isinstance(blob, str):
            problems.append(f"Hendelsesblokk #{index} mangler komprimerte data.")
            continue
        decoded = decompress_from_utf16(blob)
        if not decoded:
            problems.append(f"Kunne ikke dekomprimere hendelsesblokk #{index}.")
            continue
        try:
            parsed = json.loads(decoded)
        except json.JSONDecodeError:
            problems.append(f"Hendelsesblokk #{index} inneholdt ikke gyldig JSON.")
            continue
        if not isinstance(parsed, list):
            problems.append(f"Hendelsesblokk #{index} inneholdt ikke en liste med hendelser.")
            continue
        for item in parsed:
            if isinstance(item, Mapping):
                raw_events.append(dict(item))
            else:
                problems.append(f"Hendelsesblokk #{index} inneholdt en ugyldig hendelse.")
    return raw_events, problems


def _build_icalendar(raw_events: list[dict[str, Any]]) -> tuple[icalendar.Calendar, list[str]]:
    """Build an in-memory :class:`icalendar.Calendar` from the API's raw event dicts.

    Returns the calendar plus any malformed-event/rule problems. Invalid
    recurrence or exception data is never silently skipped: it is reported so
    the scrape fails closed (a dropped recurrence would under-count the
    calendar and could hide a real booking).
    """
    calendar = icalendar.Calendar()
    problems: list[str] = []
    for index, raw in enumerate(raw_events):
        start = _parse_iso(raw.get("start"))
        end = _parse_iso(raw.get("end"))
        if start is None or end is None:
            problems.append(f"Hendelse #{index} mangler et tolkebart start/slutt-tidspunkt.")
            continue

        vevent = icalendar.Event()
        vevent.add("summary", raw.get("title", ""))
        vevent.add("uid", raw.get("id", ""))
        vevent.add("dtstart", start)
        vevent.add("dtend", end)

        for rule in _as_rule_list(raw.get("recurrence"), problems, index, "recurrence"):
            if not isinstance(rule, str) or not rule.upper().startswith("RRULE:"):
                problems.append(f"Hendelse #{index} har en ugyldig gjentakelsesregel: {rule!r}.")
                continue
            parsed_rule = _parse_rrule(rule[len("RRULE:"):])
            if parsed_rule is None:
                problems.append(
                    f"Hendelse #{index} har en gjentakelsesregel uten gyldig FREQ: {rule!r}."
                )
                continue
            vevent.add("rrule", parsed_rule)

        for exdate in _as_rule_list(raw.get("exdate"), problems, index, "exdate"):
            parsed_exdate = _parse_iso(exdate)
            if parsed_exdate is None:
                problems.append(f"Hendelse #{index} har et ugyldig EXDATE-tidspunkt: {exdate!r}.")
                continue
            vevent.add("exdate", parsed_exdate)

        calendar.add_component(vevent)
    return calendar, problems


def _as_rule_list(value: Any, problems: list[str], index: int, field: str) -> list[Any]:
    """Normalize an API recurrence/exdate field to a list, reporting junk values."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return list(value)
    problems.append(f"Hendelse #{index} har et ugyldig {field}-felt: {type(value).__name__}.")
    return []


def _parse_rrule(value: str) -> icalendar.vRecur | None:
    """Parse one RRULE body, returning ``None`` when it carries no valid FREQ."""
    try:
        rule = icalendar.vRecur.from_ical(value)
    except (ValueError, KeyError):
        return None
    if not any(str(key).upper() == "FREQ" for key in rule.keys()):
        return None
    return rule


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
