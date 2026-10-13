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
from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

from ..models import CalendarEvent

_RESOURCE_AND_EVENT_RE = re.compile(
    r'\\"resources\\":\[(?P<resources>.*?)\],\\"events\\":\[(?P<events>.*?)\],\\"date\\"',
    re.DOTALL,
)
_TIME_RANGE_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")
_AGE_TOKEN_RE = re.compile(r"\b(J?U)\s?(\d{1,2})\b", re.IGNORECASE)
# The booking page's click handler is a Next.js server action. Its ID belongs to
# the deployed build, so a lookup that stops working is treated as "no message".
_MESSAGE_ACTION_ID = "406f9fb526df35ca452c6ec2a9180b8d822a9efc4a"
_MESSAGE_ACTION_HEADERS = {
    "Next-Action": _MESSAGE_ACTION_ID,
    "Content-Type": "text/plain;charset=UTF-8",
    "Accept": "text/x-component",
}


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
        events.extend(
            _parse_brp_exigo_day(
                page_content,
                current,
                message_lookup=_make_message_lookup(session, day_url),
            )
        )
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


def _make_message_lookup(session: requests.Session, day_url: str) -> Callable[[dict], str]:
    """Return a lookup that reads one booking's click message from the day page.

    The message names the booking's age group and participating clubs. Only the
    age group is used downstream; the team list changes after booking. A failed
    or unrecognised response yields ``""``, which leaves the event unchanged.
    """

    def lookup(item: dict) -> str:
        body = [[{
            "orderId": item.get("orderId"),
            "startISO": item.get("start"),
            "endISO": item.get("end"),
            "eventId": item.get("id"),
        }]]
        try:
            response = session.post(
                day_url, data=json.dumps(body), headers=_MESSAGE_ACTION_HEADERS, timeout=15
            )
            response.raise_for_status()
        except requests.RequestException:
            return ""
        # The response carries no charset, so decode the bytes explicitly.
        text = response.content.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line.startswith("1:"):
                try:
                    data = json.loads(line[2:])
                except json.JSONDecodeError:
                    return ""
                return str(data[0][0]) if data and data[0] else ""
        return ""

    return lookup


def _booking_age_label(message: str) -> str | None:
    """Return the age group named in a booking message, e.g. ``"JU10"``.

    Only the text before the first comma is read, so a team name cannot supply
    the age group.
    """

    match = _AGE_TOKEN_RE.search(message.split(",")[0])
    if match is None:
        return None
    return f"{match.group(1).upper()}{match.group(2)}"


def _parse_brp_exigo_day(
    page_content: str,
    day: datetime,
    message_lookup: Callable[[dict], str] | None = None,
) -> list[CalendarEvent]:
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
        labels = group["labels"]

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
        age = None
        if message_lookup is not None and not message and not _AGE_TOKEN_RE.search(title):
            age = _booking_age_label(message_lookup(item))
        if age:
            event_name = f"{title} - {age}"
        else:
            event_name = f"{title} - {message}" if message else title
        if time_range not in event_name:
            event_name = f"{event_name} {time_range}"

        # One booking can occupy several resources. The LOCATION lists all of
        # them so the footprint is preserved; a single resource keeps its label.
        location = " + ".join(labels)

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


def _group_by_booking(raw_events: list, resource_names: dict[int, str]) -> list[dict]:
    """Group raw rows that are one booking occupying several resources.

    The live payload writes a booking that spans both rinks as one row per
    resource. Those rows share ``orderId``, time range, title and message, and
    their ``id`` embeds the resource. Each group keeps every resource label it
    occupies, in source order.
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
        try:
            label = resource_names.get(int(item.get("resourceId")), "")
        except (TypeError, ValueError):
            label = ""
        group = groups.setdefault(key, {"item": item, "labels": []})
        if label and label not in group["labels"]:
            group["labels"].append(label)
    return list(groups.values())


def _unescape_next_payload_array(raw_array_contents: str) -> str:
    """Turn escaped React flight array contents into a JSON array string."""
    return "[" + raw_array_contents.replace('\\"', '"') + "]"
