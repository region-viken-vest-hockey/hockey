"""BRP/Exigo daily booking scraper for Stage 2.

Skien Fritidspark exposes the ice-hall booking view through a small Next.js
page at ``skienfritidspark.brp.exigo.no/ishallen``. The page renders one day at
a time and embeds the bookings in the React flight payload as JSON-like text.
The generic date-param scraper sampled only the first day of each month; this
module walks the configured date range day-by-day and parses the embedded event
objects with their real dates, times, and rink resources.

A clean walk returns strategy-specific coverage provenance. Empty dates count as
inspected dates; they do not shrink the covered window. Any request, response-date,
or payload failure is retained as partial/failed evidence so Stage 2 cannot treat
an incomplete walk as proof that a missing booking does not exist.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

from ..models_calendar import CalendarEvent, SourceResource
from .scraper_brp_exigo_coverage import coverage_for_walk, failure_message
from .source_integrity import with_coverage

_RESOURCE_AND_EVENT_RE = re.compile(
    r'\\"resources\\":\[(?P<resources>.*?)\],\\"events\\":\[(?P<events>.*?)\],'
    r'\\"date\\":\\"(?:\$D)?(?P<date>\d{4}-\d{2}-\d{2})(?:T[^\\"]*)?\\"',
    re.DOTALL,
)
_TIME_RANGE_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")


class BrpExigoPayloadError(RuntimeError):
    """One daily response cannot be trusted as complete booking evidence."""


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
    inspected_dates: list[date] = []
    exceptions: list[str] = []

    current = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end = end_date.replace(hour=0, minute=0, second=0, microsecond=0)
    while current <= end:
        day_url = _url_for_date(url, current)
        try:
            response = session.get(day_url, timeout=15)
            response.raise_for_status()
            page_content = response.text
            day_events = _parse_brp_exigo_day_strict(page_content, current)
        except Exception as exc:  # noqa: BLE001 -- retain partial coverage and fail closed
            exceptions.append(failure_message(current.date(), exc))
            current += timedelta(days=1)
            continue

        raw_html += page_content
        inspected_dates.append(current.date())
        events.extend(day_events)
        current += timedelta(days=1)

    seen: set[tuple[str, str, str, str]] = set()
    unique: list[CalendarEvent] = []
    for event in events:
        key = (event.date, event.datetime.strftime("%H:%M"), event.name, event.location)
        if key not in seen:
            seen.add(key)
            unique.append(event)

    coverage = coverage_for_walk(
        requested_start=start_date.date(),
        requested_end=end_date.date(),
        inspected_dates=inspected_dates,
        events=unique,
        exceptions=exceptions,
    )
    return with_coverage(unique, **coverage), raw_html


def _url_for_date(url: str, day: datetime) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["date"] = [day.strftime("%Y-%m-%d")]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _parse_brp_exigo_day(page_content: str, day: datetime) -> list[CalendarEvent]:
    """Parse one BRP/Exigo day, retaining the legacy empty-on-error API."""
    try:
        return _parse_brp_exigo_day_strict(
            page_content,
            day,
            validate_response_date=False,
        )
    except BrpExigoPayloadError:
        return []


def _parse_brp_exigo_day_strict(
    page_content: str,
    day: datetime,
    *,
    validate_response_date: bool = True,
) -> list[CalendarEvent]:
    """Parse one day or raise when its response is not trustworthy evidence."""
    match = _RESOURCE_AND_EVENT_RE.search(page_content)
    if not match:
        raise BrpExigoPayloadError("BRP/Exigo-svaret mangler ressurs-/hendelsesdata.")

    response_date = match.group("date")
    expected_date = day.date().isoformat()
    if validate_response_date and response_date != expected_date:
        raise BrpExigoPayloadError(
            f"BRP/Exigo svarte med en annen dato ({response_date}) enn forespurt ({expected_date})."
        )

    try:
        resources = json.loads(_unescape_next_payload_array(match.group("resources")))
        raw_events = json.loads(_unescape_next_payload_array(match.group("events")))
    except json.JSONDecodeError as exc:
        raise BrpExigoPayloadError("BRP/Exigo-svaret inneholder ugyldig JSON.") from exc
    if not isinstance(resources, list) or not isinstance(raw_events, list):
        raise BrpExigoPayloadError("BRP/Exigo-svaret har ugyldig ressurs-/hendelsesformat.")

    resource_names: dict[int, str] = {}
    for resource in resources:
        if not isinstance(resource, dict):
            raise BrpExigoPayloadError(
                f"BRP/Exigo-svaret for {expected_date} inneholder en ugyldig ressurs."
            )
        try:
            resource_id = int(resource.get("id"))
        except (TypeError, ValueError) as exc:
            raise BrpExigoPayloadError(
                f"BRP/Exigo-svaret for {expected_date} har en ressurs uten gyldig id."
            ) from exc
        resource_name = str(resource.get("name") or "").strip()
        if not resource_name:
            raise BrpExigoPayloadError(
                f"BRP/Exigo-svaret for {expected_date} har en ressurs uten navn."
            )
        resource_names[resource_id] = resource_name

    if any(not isinstance(item, dict) for item in raw_events):
        raise BrpExigoPayloadError(
            f"BRP/Exigo-svaret for {expected_date} inneholder en ugyldig hendelse."
        )
    for item in raw_events:
        try:
            event_resource_id = int(item.get("resourceId"))
        except (TypeError, ValueError) as exc:
            raise BrpExigoPayloadError(
                f"En BRP/Exigo-hendelse på {expected_date} mangler gyldig ressurs-id."
            ) from exc
        if event_resource_id not in resource_names:
            raise BrpExigoPayloadError(
                f"En BRP/Exigo-hendelse på {expected_date} viser til en ukjent ressurs."
            )

    events: list[CalendarEvent] = []
    for group in _group_by_booking(raw_events, resource_names):
        item = group["item"]

        time_range = str(item.get("timeRange") or "")
        time_match = _TIME_RANGE_RE.match(time_range)
        if not time_match:
            raise BrpExigoPayloadError(
                f"En BRP/Exigo-hendelse på {expected_date} mangler gyldig tidsintervall."
            )

        sh, sm, eh, em = (int(value) for value in time_match.groups())
        duration_minutes = (eh * 60 + em) - (sh * 60 + sm)
        if duration_minutes < 0:
            duration_minutes += 24 * 60
        if duration_minutes <= 0:
            raise BrpExigoPayloadError(
                f"En BRP/Exigo-hendelse på {expected_date} har ikke-positiv varighet."
            )
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
