"""Regression tests for operator-auditable calendar evidence (issue #279)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

from tournament_scheduler.club_registry import CLUB_REGISTRY
from tournament_scheduler.data_sources.ical_scraper import ICalScraper
from tournament_scheduler.pipeline.calendar_viewer import _source_urls, generate_html
from tournament_scheduler.utils.calendar_cache import CalendarCache


FRISK_MULTI_ARENA_FEED = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Frisk Asker Teamup//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
UID:asker-timed@example.com
DTSTAMP:20260101T120000Z
DTSTART:20260912T100000Z
DTEND:20260912T120000Z
SUMMARY:Askerhallen trening
LOCATION:Idrettshallen
END:VEVENT
BEGIN:VEVENT
UID:varner@example.com
DTSTAMP:20260101T120000Z
DTSTART:20260912T130000Z
DTEND:20260912T150000Z
SUMMARY:Varner Arena kamp
LOCATION:Varner Arena
END:VEVENT
BEGIN:VEVENT
UID:legacy@example.com
DTSTAMP:20260101T120000Z
DTSTART:20260912T160000Z
DTEND:20260912T170000Z
SUMMARY:Legacy venue
LOCATION:Askerhallen
END:VEVENT
END:VCALENDAR
"""


def test_frisk_ical_location_mapping_keeps_relevant_arenas():
    response = MagicMock()
    response.status_code = 200
    response.content = FRISK_MULTI_ARENA_FEED
    response.raise_for_status.return_value = None

    with patch("tournament_scheduler.data_sources.ical_scraper.requests.get", return_value=response):
        scraper = ICalScraper(
            "https://example.test/frisk.ics",
            allowed_locations=CLUB_REGISTRY["Frisk Asker"].calendar_locations,
        )
        events = scraper.scrape_events()

    assert [event.name for event in events] == ["Askerhallen trening", "Varner Arena kamp"]
    assert {event.location for event in events} == {"Idrettshallen", "Varner Arena"}


def test_calendar_html_exposes_source_url_and_event_location(tmp_path):
    cache = CalendarCache(str(tmp_path / "calendar_cache"))
    cache.save_events(
        "Frisk Asker",
        [
            CalendarEvent(
                date="12.09.2026",
                name="Varner Arena kamp",
                datetime=datetime(2026, 9, 12, 13, 0),
                duration_hours=2.0,
                location="Varner Arena",
            )
        ],
        url="https://example.test/frisk.ics",
    )

    html = generate_html(
        {"Frisk Asker": cache.load("Frisk Asker")},
        source_urls={"Frisk Asker": "https://example.test/frisk.ics"},
    )

    assert "https://example.test/frisk.ics" in html
    assert "Varner Arena" in html


def test_source_urls_returns_configured_source_urls():
    urls = _source_urls(
        {
            "sources": [
                {"name": "Frisk Asker", "url": "https://example.test/frisk.ics"},
                {"name": "Missing URL", "url": ""},
            ]
        }
    )

    assert urls == {"Frisk Asker": "https://example.test/frisk.ics"}
