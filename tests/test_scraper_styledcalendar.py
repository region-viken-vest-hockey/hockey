"""Tests for the StyledCalendar (Bærum ishall/Jutul) scraper.

The scraper calls StyledCalendar's public JSON events API directly (no
browser) and expands recurring events against the requested date range.
``requests.get`` is mocked so these tests never hit the network; the
sample payload's shape (compressed ``lz-string``-encoded JSON with an
``RRULE``) mirrors what the real API returns -- see the operator
conversation that found the original bug: the DOM (month view) never
carries per-event times at all, only this JSON API does.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

from tournament_scheduler.pipeline.scraper_styledcalendar import _run_styledcalendar_scraper
from tournament_scheduler.utils.lzstring import compress_to_utf16


def _make_api_response(raw_events: list[dict]) -> MagicMock:
    compressed = compress_to_utf16(json.dumps(raw_events))
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "compressedEventsAndIds": [
            {"compressedEvents": compressed, "sourceCalendarGoogleId": "test@example.com"},
        ],
    }
    return response


class TestStyledCalendarScraper:
    def test_single_timed_event_keeps_real_start_time_and_duration(self):
        """The original bug: DOM scraping hardcoded every event to 00:00/1h.

        The JSON API carries the real start/end, so the parsed event must
        not collapse to midnight with a flat one-hour duration.
        """
        raw_events = [{
            "id": "evt-1",
            "title": "Jutul ESH",
            "start": "2026-09-21T15:00:00+02:00",
            "end": "2026-09-21T15:50:00+02:00",
            "allDay": False,
        }]

        with patch("tournament_scheduler.pipeline.scraper_styledcalendar.requests.get",
                   return_value=_make_api_response(raw_events)):
            events, _ = _run_styledcalendar_scraper(
                "Jutul", datetime(2026, 9, 1), datetime(2026, 9, 30),
            )

        assert len(events) == 1
        event = events[0]
        assert event.name == "Jutul ESH"
        assert event.datetime == datetime(2026, 9, 21, 15, 0)
        assert abs(event.duration_hours - (50 / 60)) < 1e-9

    def test_recurring_event_expands_to_one_occurrence_per_week(self):
        raw_events = [{
            "id": "evt-recurring",
            "title": "Jutul U9+U10",
            "start": "2026-09-05T17:30:00+02:00",
            "end": "2026-09-05T18:20:00+02:00",
            "allDay": False,
            "recurrence": ["RRULE:FREQ=WEEKLY;UNTIL=20260930T000000Z;BYDAY=SA"],
        }]

        with patch("tournament_scheduler.pipeline.scraper_styledcalendar.requests.get",
                   return_value=_make_api_response(raw_events)):
            events, _ = _run_styledcalendar_scraper(
                "Jutul", datetime(2026, 9, 1), datetime(2026, 9, 30),
            )

        starts = sorted(e.datetime for e in events)
        assert starts == [
            datetime(2026, 9, 5, 17, 30),
            datetime(2026, 9, 12, 17, 30),
            datetime(2026, 9, 19, 17, 30),
            datetime(2026, 9, 26, 17, 30),
        ]

    def test_exdate_excludes_one_occurrence(self):
        raw_events = [{
            "id": "evt-recurring-exdate",
            "title": "Jutul U11",
            "start": "2026-09-05T08:00:00+02:00",
            "end": "2026-09-05T09:20:00+02:00",
            "allDay": False,
            "recurrence": ["RRULE:FREQ=WEEKLY;UNTIL=20260930T000000Z;BYDAY=SA"],
            "exdate": ["2026-09-12T08:00:00+02:00"],
        }]

        with patch("tournament_scheduler.pipeline.scraper_styledcalendar.requests.get",
                   return_value=_make_api_response(raw_events)):
            events, _ = _run_styledcalendar_scraper(
                "Jutul", datetime(2026, 9, 1), datetime(2026, 9, 30),
            )

        starts = sorted(e.datetime for e in events)
        assert datetime(2026, 9, 12, 8, 0) not in starts
        assert len(starts) == 3

    def test_events_outside_requested_range_are_excluded(self):
        raw_events = [{
            "id": "evt-out-of-range",
            "title": "Jutul A",
            "start": "2025-01-01T10:00:00+01:00",
            "end": "2025-01-01T11:00:00+01:00",
            "allDay": False,
        }]

        with patch("tournament_scheduler.pipeline.scraper_styledcalendar.requests.get",
                   return_value=_make_api_response(raw_events)):
            events, _ = _run_styledcalendar_scraper(
                "Jutul", datetime(2026, 9, 1), datetime(2026, 9, 30),
            )

        assert events == []

    def test_network_failure_returns_empty_list_not_an_exception(self):
        with patch("tournament_scheduler.pipeline.scraper_styledcalendar.requests.get",
                   side_effect=ConnectionError("boom")):
            events, raw_html = _run_styledcalendar_scraper(
                "Jutul", datetime(2026, 9, 1), datetime(2026, 9, 30),
            )

        assert events == []
        assert raw_html == ""
