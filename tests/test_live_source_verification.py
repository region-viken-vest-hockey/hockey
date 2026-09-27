"""Live source-versus-scraper QA for high-risk arena calendars.

Marked ``live`` and excluded from hermetic CI. These tests fetch public sources
and compare independent feed/source evidence with the records produced by the
Stage 2 scrapers. They are intentionally bounded to the configured season window
and never mutate canonical season state.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
import requests
from icalendar import Calendar as ICalendar
import recurring_ical_events

from tournament_scheduler.pipeline.input_workbook import load_workbook_config
from tournament_scheduler.pipeline.scraper_ical import _run_ical_scraper
from tournament_scheduler.pipeline.scraper_outlook import _run_outlook_scraper
from tournament_scheduler.pipeline.source_integrity import evaluate_source_integrity
from tournament_scheduler.utils.calendar_cache import CalendarCache

pytestmark = pytest.mark.live

_USER_AGENT = "Mozilla/5.0 (compatible; rvv-miniputt-live-source-qa)"


def _configured_sources() -> dict[str, dict[str, Any]]:
    raw = load_workbook_config("input.xlsx")
    return {str(source["name"]): source for source in raw.get("sources", [])}


def _season_window() -> tuple[datetime, datetime]:
    raw = load_workbook_config("input.xlsx")
    start = _as_date(raw["start_date"])
    end = _as_date(raw["end_date"])
    return datetime.combine(start, time.min), datetime.combine(end, time.max.replace(microsecond=0))


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _raw_ical_identity(event: Any) -> tuple[str, str, str, str, bool]:
    start = event.get("DTSTART").dt
    end = event.get("DTEND").dt if event.get("DTEND") else None
    all_day = not hasattr(start, "hour")
    start_iso = start.isoformat()
    end_iso = "" if all_day else (end.isoformat() if end is not None else "")
    return (
        start_iso,
        end_iso,
        str(event.get("SUMMARY", "")),
        str(event.get("LOCATION", "")),
        all_day,
    )


def _scraped_ical_identity(event: Any) -> tuple[str, str, str, str, bool]:
    start = event.datetime
    all_day = bool(getattr(event, "all_day", False))
    if all_day:
        start_iso = start.date().isoformat()
        end_iso = ""
    else:
        end_iso = (start + timedelta(hours=event.duration_hours)).isoformat() if event.duration_hours else ""
        start_iso = start.isoformat()
    return (start_iso, end_iso, event.name, event.location or "", all_day)


def test_ringerike_teamup_feed_matches_ical_scraper_expansion(tmp_path: Path) -> None:
    sources = _configured_sources()
    source = sources["Ringerike"]
    start, end = _season_window()

    response = requests.get(source["url"], timeout=30, headers={"User-Agent": _USER_AGENT})
    assert response.status_code == 200
    assert b"BEGIN:VCALENDAR" in response.content
    assert response.content.count(b"BEGIN:VEVENT") > 0

    calendar = ICalendar.from_ical(response.content)
    raw_expanded = recurring_ical_events.of(calendar).between(start, end + timedelta(days=1))
    raw_identities = [_raw_ical_identity(event) for event in raw_expanded]
    assert len(raw_identities) >= 1
    assert len(raw_identities) == len(set(raw_identities)), "expanded Teamup feed contains duplicate event identities"

    scraped = _run_ical_scraper(
        source["url"],
        source["name"],
        start,
        end,
        source["type"],
        cache=CalendarCache(cache_dir=str(tmp_path / "ical-cache")),
    )
    scraped_identities = [_scraped_ical_identity(event) for event in scraped]

    assert len(scraped_identities) == len(raw_identities)
    assert set(scraped_identities) == set(raw_identities)


def test_kongsberg_outlook_live_scrape_fails_closed_without_full_coverage() -> None:
    pytest.importorskip("playwright.sync_api")
    sources = _configured_sources()
    source = sources["Kongsberg"]
    start, end = _season_window()

    events, raw_html = _run_outlook_scraper(source["url"], source["name"], start, end)
    source_result = {
        "name": source["name"],
        "type": source["type"],
        "events": [
            {
                "date": event.date,
                "datetime": event.datetime.isoformat(),
                "name": event.name,
                "duration_hours": event.duration_hours,
                "location": event.location,
            }
            for event in events
        ],
        "event_count": len(events),
        "coverage": getattr(events, "coverage", None),
    }
    integrity = evaluate_source_integrity(
        source_result,
        requested_start=start.date().isoformat(),
        requested_end=end.date().isoformat(),
    )

    assert raw_html or integrity["exceptions"]
    assert integrity["requested_end"] == end.date().isoformat()
    if integrity["coverage_proven"]:
        assert integrity["observed_end"] == end.date().isoformat()
        assert integrity["trusted_for_negative_claim"] is True
    else:
        assert integrity["status"] in {"partial", "failed", "suspicious"}
        assert integrity["trusted_for_negative_claim"] is False
