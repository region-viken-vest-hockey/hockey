"""BRP/Exigo daily booking scraper for Stage 2.

Skien Fritidspark exposes the ice-hall booking view through a small Next.js
page at ``skienfritidspark.brp.exigo.no/ishallen``. The page renders one day at
a time and embeds the bookings in the React flight payload as JSON-like text.
The generic date-param scraper sampled only the first day of each month; this
module walks the configured date range day-by-day and parses the embedded event
objects with their real dates, times, and rink resources.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

from ..models import CalendarEvent

_RESOURCE_AND_EVENT_RE = re.compile(
    r'\\"resources\\":\[(?P<resources>.*?)\],\\"events\\":\[(?P<events>.*?)\],\\"date\\"',
    re.DOTALL,
)
_TIME_RANGE_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")


def _run_brp_exigo_scraper(
    url: str,
    name: str,
    start_date: datetime,
    end_date: datetime,
) -> tuple[list[CalendarEvent], str]:
    """Scrape BRP/Exigo daily booking pages for the whole date range."""
    session = requests.Session()
    events: list[CalendarEvent] = []
    raw_html = ""

    current = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end = end_date.replace(hour=0, minute=0, second=0, microsecond=0)
    while current <= end:
        day_url = _url_for_date(url, current)
        try:
            response = session.get(day_url, timeout=15)
            response.raise_for_status()
        except requests.RequestException:
            current += timedelta(days=1)
            continue

        page_content = response.text
        raw_html += page_content
        events.extend(_parse_brp_exigo_day(page_content, current))
        current += timedelta(days=1)

    seen: set[tuple[str, str, str, str]] = set()
    unique: list[CalendarEvent] = []
    for event in events:
        key = (event.date, event.datetime.strftime("%H:%M"), event.name, event.location)
        if key not in seen:
            seen.add(key)
            unique.append(event)

    return unique, raw_html


def _url_for_date(url: str, day: datetime) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["date"] = [day.strftime("%Y-%m-%d")]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _parse_brp_exigo_day(page_content: str, day: datetime) -> list[CalendarEvent]:
    """Parse one BRP/Exigo day page into ``CalendarEvent`` objects."""
    match = _RESOURCE_AND_EVENT_RE.search(page_content)
    if not match:
        return []

    try:
        resources = json.loads(_unescape_next_payload_array(match.group("resources")))
        raw_events = json.loads(_unescape_next_payload_array(match.group("events")))
    except json.JSONDecodeError:
        return []

    resource_names: dict[int, str] = {}
    for resource in resources:
        if isinstance(resource, dict):
            try:
                resource_names[int(resource.get("id"))] = str(resource.get("name") or "")
            except (TypeError, ValueError):
                continue

    events: list[CalendarEvent] = []
    for item in raw_events:
        if not isinstance(item, dict):
            continue

        time_range = str(item.get("timeRange") or "")
        time_match = _TIME_RANGE_RE.match(time_range)
        if not time_match:
            continue

        sh, sm, eh, em = (int(value) for value in time_match.groups())
        start_decimal = sh + sm / 60.0
        end_decimal = eh + em / 60.0
        duration = end_decimal - start_decimal
        if duration < 0:
            duration += 24

        title = str(item.get("title") or "Booking").strip() or "Booking"
        message = str(item.get("message") or "").strip()
        event_name = f"{title} - {message}" if message else title
        if time_range not in event_name:
            event_name = f"{event_name} {time_range}"

        location = ""
        try:
            location = resource_names.get(int(item.get("resourceId")), "")
        except (TypeError, ValueError):
            location = ""

        event_dt = day.replace(hour=sh, minute=sm)
        events.append(
            CalendarEvent(
                date=day.strftime("%d.%m.%Y"),
                name=event_name,
                datetime=event_dt,
                duration_hours=duration,
                location=location,
            )
        )

    return events


def _unescape_next_payload_array(raw_array_contents: str) -> str:
    """Turn escaped React flight array contents into a JSON array string."""
    return "[" + raw_array_contents.replace('\\"', '"') + "]"
