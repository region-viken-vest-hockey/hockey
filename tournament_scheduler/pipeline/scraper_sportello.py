"""Sportello GraphQL scraper for Stage 2 (Holmen).

Sportello's JS calendar is backed by a public GraphQL endpoint.  We query
``publicBookings`` directly in bounded date chunks, convert the returned UTC
timestamps to local Norwegian time, and normalize them into
:class:`~tournament_scheduler.models.CalendarEvent` objects.
"""

from __future__ import annotations

import json as _json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

from ..models import CalendarEvent

SPORTELLO_GRAPHQL_URL = "https://kalender.sportello.no/api/graphql/"
SPORTELLO_MAX_QUERY_DAYS = 90
_OSLO_TZ = ZoneInfo("Europe/Oslo")

_PUBLIC_BOOKINGS_QUERY = """
query PubBookings($filter: PublicBookingFilterInput!) {
  publicBookings(filter: $filter) {
    id
    displayName
    startTime
    endTime
    allDay
    team {
      id
      displayName
      teamColor
      __typename
    }
    location {
      id
      displayName
      __typename
    }
    activityType {
      displayName
      typeValue
      __typename
    }
    teamText
    awayTeamText
    wardrobe {
      id
      displayName
      __typename
    }
    awayTeamWardrobe {
      id
      displayName
      __typename
    }
    awayTeam {
      id
      displayName
      __typename
    }
    __typename
  }
}
""".strip()


@dataclass(frozen=True)
class _SportelloWindow:
    start: date
    end: date


def _parse_club_id(url: str) -> int:
    match = re.search(r"/booking/(\d+)", url)
    if not match:
        raise ValueError(f"Kunne ikke finne Sportello clubId i URL-en: {url}")
    return int(match.group(1))


def _localize(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_OSLO_TZ).replace(tzinfo=None)


def _parse_iso_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _chunk_windows(start_date: date, end_date: date) -> list[_SportelloWindow]:
    windows: list[_SportelloWindow] = []
    cursor = start_date
    while cursor <= end_date:
        window_end = min(cursor + timedelta(days=SPORTELLO_MAX_QUERY_DAYS - 1), end_date)
        windows.append(_SportelloWindow(start=cursor, end=window_end))
        cursor = window_end + timedelta(days=1)
    return windows


def _query_public_bookings(
    session: requests.Session,
    club_id: int,
    window: _SportelloWindow,
) -> tuple[list[dict[str, Any]], str]:
    start_utc = datetime.combine(window.start, time.min, tzinfo=_OSLO_TZ).astimezone(timezone.utc)
    end_utc = datetime.combine(window.end + timedelta(days=1), time.min, tzinfo=_OSLO_TZ).astimezone(timezone.utc)

    payload = {
        "operationName": "PubBookings",
        "variables": {
            "filter": {
                "clubIds": [club_id],
                "teamIds": None,
                "locationIds": None,
                "startTime": start_utc.isoformat().replace("+00:00", "Z"),
                "endTime": end_utc.isoformat().replace("+00:00", "Z"),
            }
        },
        "query": _PUBLIC_BOOKINGS_QUERY,
    }

    response = session.post(
        SPORTELLO_GRAPHQL_URL,
        json=payload,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://kalender.sportello.no",
            "Referer": f"https://kalender.sportello.no/booking/{club_id}",
        },
        timeout=30,
    )
    response.raise_for_status()

    try:
        data = response.json()
    except ValueError as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"Sportello returnerte ugyldig JSON: {response.text[:200]}") from exc

    if isinstance(data, dict) and data.get("errors"):
        first_error = data["errors"][0] if data["errors"] else {}
        message = str(first_error.get("message", "Ukjent GraphQL-feil"))
        raise RuntimeError(f"Sportello GraphQL-feil: {message}")

    bookings = data.get("data", {}).get("publicBookings", []) if isinstance(data, dict) else []
    if not isinstance(bookings, list):
        bookings = []
    return bookings, response.text


def _booking_name(booking: dict[str, Any]) -> str:
    # The team display name carries the age group (e.g. "U10 (2017)"), which is
    # the identity signal for matching tournaments; the activity type ("Kamp",
    # "Trening") is generic and must not shadow it.
    for key in ("displayName",):
        value = str(booking.get(key, "")).strip()
        if value:
            return value

    team = booking.get("team") or {}
    team_name = str(team.get("displayName", "")).strip()
    if team_name:
        return team_name

    for key in ("teamText", "awayTeamText"):
        value = str(booking.get(key, "")).strip()
        if value:
            return value

    activity_type = booking.get("activityType") or {}
    for key in ("displayName", "typeValue"):
        value = str(activity_type.get(key, "")).strip()
        if value:
            return value

    return "Sportello booking"


def _booking_location(booking: dict[str, Any]) -> str:
    location = booking.get("location") or {}
    return str(location.get("displayName", "")).strip()


def _booking_datetime_range(booking: dict[str, Any]) -> tuple[datetime, float]:
    start_raw = str(booking.get("startTime", "")).strip()
    end_raw = str(booking.get("endTime", "")).strip()
    all_day = bool(booking.get("allDay", False))

    if not start_raw:
        raise ValueError("Manglende startTime i Sportello-booking")

    start_dt = _localize(_parse_iso_datetime(start_raw))
    if all_day:
        duration_hours = 0.0
    elif end_raw:
        end_dt = _localize(_parse_iso_datetime(end_raw))
        duration_hours = max(0.0, (end_dt - start_dt).total_seconds() / 3600.0)
    else:
        duration_hours = 0.0
    return start_dt, duration_hours


def _run_sportello_scraper(
    url: str,
    name: str,
    start_date: datetime,
    end_date: datetime,
    *,
    session: requests.Session | None = None,
) -> tuple[list[CalendarEvent], str]:
    """Scrape a Sportello public booking calendar (Holmen).

    The public page is a JS SPA, but the actual bookings are available through
    Sportello's GraphQL API, so we can fetch them deterministically without a
    browser agent.
    """
    del name  # kept for parity with the other scraper helpers

    events: list[CalendarEvent] = []

    club_id = _parse_club_id(url)
    start_local = start_date.date()
    end_local = end_date.date()
    windows = _chunk_windows(start_local, end_local)
    session = session or requests.Session()

    seen_booking_ids: set[str] = set()
    for window in windows:
        bookings, _ = _query_public_bookings(session, club_id, window)

        for booking in bookings:
            try:
                event_start, duration_hours = _booking_datetime_range(booking)
            except ValueError:
                continue

            if not (window.start <= event_start.date() <= window.end):
                continue

            # Sportello can repeat a booking across chunk responses. Only mark
            # accepted records seen so an out-of-window copy cannot suppress one.
            booking_id = str(booking.get("id", "")).strip()
            if booking_id:
                if booking_id in seen_booking_ids:
                    continue
                seen_booking_ids.add(booking_id)

            event_name = _booking_name(booking)
            location = _booking_location(booking)
            events.append(
                CalendarEvent(
                    date=event_start.strftime("%d.%m.%Y"),
                    name=event_name,
                    datetime=event_start,
                    duration_hours=duration_hours,
                    location=location,
                )
            )

    unique = events

    raw_summary = _json.dumps(
        {
            "club_id": club_id,
            "chunks": len(windows),
            "events": len(unique),
            "url": url,
        },
        ensure_ascii=False,
    )
    return unique, raw_summary
