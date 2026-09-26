"""Tests for the `_run_ical_scraper` wrapper's `location_exclude_substring`.

Regression coverage for the Frisk Asker/Askerhallen away-fixture bug: the
Teamup feed is otherwise entirely Askerhallen evidence, but a handful of
entries are genuine away fixtures whose LOCATION carries the destination
city as a " - <city>" suffix on the resource label (e.g.
"FA Jentegarderoben - Stavanger 5") rather than a distinct venue name, so
the existing inclusion `location_filter` can't select them out.
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
        home_event = _event("U15 Kamp", "Jentegarderoben")
        away_event = _event("U15 Treningskamp - Stavanger", "FA Jentegarderoben - Stavanger 5")

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
        home_event = _event("U15 Kamp", "Jentegarderoben")
        away_event = _event("U15 Treningskamp - Stavanger", "FA Jentegarderoben - Stavanger 5")

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
        away_event = _event("Match", "fa jentegarderoben - stavanger 5")

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
