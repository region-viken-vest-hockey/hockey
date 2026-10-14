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
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

from ..models_calendar import CalendarEvent, SourceResource

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
    for group in _group_by_booking(raw_events, resource_names):
        item = group["item"]

        time_range = str(item.get("timeRange") or "")
        time_match = _TIME_RANGE_RE.match(time_range)
        if not time_match:
            continue

        sh, sm, eh, em = (int(value) for value in time_match.groups())
        duration_minutes = (eh * 60 + em) - (sh * 60 + sm)
        if duration_minutes < 0:
            duration_minutes += 24 * 60
        duration = duration_minutes / 60

        title = str(item.get("title") or "Booking").strip() or "Booking"
        message = str(item.get("message") or "").strip()
        event_name = f"{title} - {message}" if message else title
        if time_range not in event_name:
            event_name = f"{event_name} {time_range}"

        # One booking can occupy several resources. The structured resources
        # are the evidence; the LOCATION label is derived from them for display.
        resources = tuple(group["resources"])
        event_dt = day.replace(hour=sh, minute=sm)
        events.append(
            CalendarEvent(
                date=day.strftime("%d.%m.%Y"),
                name=event_name,
                datetime=event_dt,
                duration_hours=duration,
                location=" + ".join(resource.label for resource in resources),
                duration_minutes=duration_minutes,
                resources=resources,
                order_id=group["order_id"],
            )
        )

    return events


def _group_by_booking(raw_events: list, resource_names: dict[int, str]) -> list[dict]:
    """Group raw rows that are one booking occupying several resources.

    The live payload writes a booking that spans both rinks as one row per
    resource. Those rows share ``orderId``, time range, title and message, and
    differ only in ``resourceId``. Each group keeps its order identity and every
    resource it occupies, in source order.
    """

    groups: dict[tuple, dict] = {}
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        order_id = item.get("orderId")
        if order_id is None:
            key: tuple = ("id", item.get("id"))
        else:
            key = (order_id, item.get("timeRange"), item.get("title"), item.get("message"))
        group = groups.setdefault(
            key,
            {"item": item, "order_id": "" if order_id is None else str(order_id), "resources": []},
        )
        resource_id = item.get("resourceId")
        try:
            label = resource_names.get(int(resource_id), "")
        except (TypeError, ValueError):
            label = ""
        resource = SourceResource(id=str(resource_id), label=label)
        if label and resource not in group["resources"]:
            group["resources"].append(resource)
    return list(groups.values())


def _unescape_next_payload_array(raw_array_contents: str) -> str:
    """Turn escaped React flight array contents into a JSON array string."""
    return "[" + raw_array_contents.replace('\\"', '"') + "]"
