"""Regression tests for operator-auditable calendar evidence (issue #279)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

from tournament_scheduler.club_registry import CLUB_REGISTRY
from tournament_scheduler.data_sources.ical_scraper import ICalScraper
from tournament_scheduler.pipeline.calendar_viewer import _source_urls, generate_html
from tournament_scheduler.pipeline.scraper_event_helpers import _events_to_dicts
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
SUMMARY:Varner aktivitet
LOCATION:1
END:VEVENT
BEGIN:VEVENT
UID:asker-allday@example.com
DTSTAMP:20260101T120000Z
DTSTART;VALUE=DATE:20260913
DTEND;VALUE=DATE:20260914
SUMMARY:Askerhallen hele dagen
LOCATION:Idrettshallen
END:VEVENT
BEGIN:VEVENT
UID:asker-midnight@example.com
DTSTAMP:20260101T120000Z
DTSTART:20260914T000000Z
DTEND:20260914T010000Z
SUMMARY:Ekte midnatt
LOCATION:Idrettshallen
END:VEVENT
END:VCALENDAR
"""


def _response(content: bytes):
    response = MagicMock()
    response.status_code = 200
    response.content = content
    return response


def test_frisk_registry_models_only_bookable_askerhallen():
    frisk = CLUB_REGISTRY["Frisk Asker"]
    assert frisk.arena == "Askerhallen"
    assert frisk.location_filter == "Idrettshallen"
    assert frisk.human_url == "https://teamup.com/ksdwpwxysmxwnuftoy"
    assert frisk.source == "https://ics.teamup.com/feed/ksdwpwxysmxwnuftoy/0.ics"


def test_teamup_sources_expose_human_and_machine_urls():
    human, feed = _source_urls(
        "Frisk Asker",
        {"url": "https://ics.teamup.com/feed/ksdwpwxysmxwnuftoy/0.ics"},
    )
    assert human == "https://teamup.com/ksdwpwxysmxwnuftoy"
    assert feed == "https://ics.teamup.com/feed/ksdwpwxysmxwnuftoy/0.ics"

    ringerike_human, ringerike_feed = _source_urls(
        "Ringerike",
        {"url": "https://ics.teamup.com/feed/ksr8bg1tpn5s3npskw/0.ics"},
    )
    assert ringerike_human == "https://teamup.com/ksr8bg1tpn5s3npskw"
    assert ringerike_feed == "https://ics.teamup.com/feed/ksr8bg1tpn5s3npskw/0.ics"


def test_frisk_ical_filter_excludes_varner_and_preserves_all_day_vs_midnight(tmp_path):
    frisk = CLUB_REGISTRY["Frisk Asker"]
    cache = CalendarCache(cache_dir=str(tmp_path / "ical-cache"))
    scraper = ICalScraper(frisk.source or "", cache=cache)

    with patch(
        "tournament_scheduler.data_sources.ical_scraper.requests.get",
        return_value=_response(FRISK_MULTI_ARENA_FEED),
    ):
        events = scraper.scrape_calendar(
            frisk.source or "",
            "Frisk Asker",
            datetime(2026, 9, 1),
            datetime(2026, 9, 30),
            location_filter=frisk.location_filter,
        )

    names = {event.name for event in events}
    assert "Varner aktivitet" not in names
    assert names == {"Askerhallen trening", "Askerhallen hele dagen", "Ekte midnatt"}

    by_name = {event.name: event for event in events}
    assert getattr(by_name["Askerhallen hele dagen"], "all_day", False) is True
    assert by_name["Askerhallen hele dagen"].datetime.strftime("%H:%M") == "00:00"
    assert getattr(by_name["Ekte midnatt"], "all_day", False) is False
    assert by_name["Ekte midnatt"].datetime.strftime("%H:%M") == "00:00"
    assert by_name["Ekte midnatt"].duration_hours == 1

    serialized = _events_to_dicts(events, club_name="Frisk Asker")
    serialized_by_name = {event["name"]: event for event in serialized}
    assert serialized_by_name["Askerhallen hele dagen"]["all_day"] is True
    assert "all_day" not in serialized_by_name["Ekte midnatt"]
    assert {event.get("arena") for event in serialized} == {"Askerhallen"}


def test_calendar_cache_roundtrip_keeps_location_and_all_day(tmp_path):
    frisk = CLUB_REGISTRY["Frisk Asker"]
    cache = CalendarCache(cache_dir=str(tmp_path / "ical-cache"), ttl_minutes=60)
    scraper = ICalScraper(frisk.source or "", cache=cache)

    with patch(
        "tournament_scheduler.data_sources.ical_scraper.requests.get",
        return_value=_response(FRISK_MULTI_ARENA_FEED),
    ):
        first = scraper.scrape_calendar(
            frisk.source or "",
            "Frisk Asker",
            datetime(2026, 9, 1),
            datetime(2026, 9, 30),
            location_filter=frisk.location_filter,
        )

    # A second call must come exclusively from CalendarCache.
    with patch("tournament_scheduler.data_sources.ical_scraper.requests.get") as get:
        second = scraper.scrape_calendar(
            frisk.source or "",
            "Frisk Asker",
            datetime(2026, 9, 1),
            datetime(2026, 9, 30),
            location_filter=frisk.location_filter,
        )
    get.assert_not_called()

    assert [event.location for event in second] == [event.location for event in first]
    assert [bool(getattr(event, "all_day", False)) for event in second] == [
        bool(getattr(event, "all_day", False)) for event in first
    ]


def test_calendar_html_shows_human_calendar_and_ical_feed_and_not_fake_midnight(tmp_path):
    work_dir = tmp_path / "work"
    export_dir = tmp_path / "export"
    cache_path = work_dir / "cache" / "scraped_data.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        """{
  "_meta": {
    "updated_at": "2026-09-08T10:00:00",
    "start_date": "2026-09-01",
    "end_date": "2026-09-30"
  },
  "source_count": 1,
  "total_events": 2,
  "sources": {
    "Frisk Asker": {
      "name": "Frisk Asker",
      "url": "https://ics.teamup.com/feed/ksdwpwxysmxwnuftoy/0.ics",
      "scrape_timestamp": "2026-09-08T10:00:00",
      "event_count": 2,
      "blocked": false,
      "events": [
        {
          "date": "13.09.2026",
          "name": "Askerhallen hele dagen",
          "datetime": "2026-09-13T00:00:00",
          "duration_hours": 0,
          "all_day": true,
          "location": "Idrettshallen"
        },
        {
          "date": "14.09.2026",
          "name": "Ekte midnatt",
          "datetime": "2026-09-14T00:00:00",
          "duration_hours": 1,
          "location": "Idrettshallen"
        }
      ]
    }
  }
}""",
        encoding="utf-8",
    )

    output = generate_html(str(work_dir), str(export_dir))
    html = open(output, encoding="utf-8").read()

    assert "Hele dagen" in html
    assert "00:00-01:00" in html  # genuine midnight event remains a midnight event
    assert "https://teamup.com/ksdwpwxysmxwnuftoy" in html
    assert "https://ics.teamup.com/feed/ksdwpwxysmxwnuftoy/0.ics" in html
    assert "<span>Kalender</span>" in html
    assert "<span>iCal</span>" in html

    # The event-level link must use the human calendar. The machine feed is
    # only exposed by the source-level iCal audit link.
    assert (
        'class="ev-ext-link" href="https://teamup.com/ksdwpwxysmxwnuftoy"'
        in html
    )
