"""Tests for the `_run_ical_scraper` wrapper's `location_exclude_substring`.

The exclusion hook is a generic iCal ingestion option for sources that really
need LOCATION-based subtraction. Frisk Asker's configured Askerhallen source
intentionally does not use it: that source's identity, not ambiguous resource
labels, owns the physical arena.
"""

from datetime import datetime
from unittest.mock import patch

from tournament_scheduler.models import CalendarEvent
from tournament_scheduler.pipeline.scraper_ical import _run_ical_scraper


def _event(name: str, location: str) -> CalendarEvent:
    return CalendarEvent(
        date="21.09.2026",
        name=name,
        datetime=datetime(2026, 9, 21, 17, 0),
        duration_hours=1.0,
        location=location,
    )


class TestLocationExcludeSubstring:
    def test_drops_events_matching_exclude_substring(self):
        home_event = _event("U15 Kamp", "Home rink")
        away_event = _event("U15 Treningskamp - Stavanger", "Away arena - Stavanger 5")

        with patch(
            "tournament_scheduler.data_sources.ical_scraper.ICalScraper"
        ) as mock_scraper_cls:
            mock_scraper_cls.return_value.scrape_calendar.return_value = [
                home_event, away_event,
            ]
            events = _run_ical_scraper(
                "https://example.com/feed.ics", "Frisk Asker",
                datetime(2026, 9, 1), datetime(2026, 9, 30),
                "ical",
                location_exclude_substring=" - ",
            )

        names = {e.name for e in events}
        assert names == {"U15 Kamp"}

    def test_no_exclude_substring_keeps_every_event(self):
        home_event = _event("U15 Kamp", "Home rink")
        away_event = _event("U15 Treningskamp - Stavanger", "Away arena - Stavanger 5")

        with patch(
            "tournament_scheduler.data_sources.ical_scraper.ICalScraper"
        ) as mock_scraper_cls:
            mock_scraper_cls.return_value.scrape_calendar.return_value = [
                home_event, away_event,
            ]
            events = _run_ical_scraper(
                "https://example.com/feed.ics", "Frisk Asker",
                datetime(2026, 9, 1), datetime(2026, 9, 30),
                "ical",
            )

        assert len(events) == 2

    def test_exclude_match_is_case_insensitive_on_location(self):
        away_event = _event("Match", "away arena - stavanger 5")

        with patch(
            "tournament_scheduler.data_sources.ical_scraper.ICalScraper"
        ) as mock_scraper_cls:
            mock_scraper_cls.return_value.scrape_calendar.return_value = [away_event]
            events = _run_ical_scraper(
                "https://example.com/feed.ics", "Frisk Asker",
                datetime(2026, 9, 1), datetime(2026, 9, 30),
                "ical",
                location_exclude_substring=" - ",
            )

        assert events == []
