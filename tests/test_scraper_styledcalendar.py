"""Tests for the StyledCalendar (Bærum ishall/Jutul) scraper.

The scraper calls StyledCalendar's public JSON events API directly (no
browser) and expands recurring events against the requested date range.
``requests.get`` is mocked so these tests never hit the network; the
sample payload's shape (compressed ``lz-string``-encoded JSON with an
``RRULE``) mirrors what the real API returns -- see the operator
conversation that found the original bug: the DOM (month view) never
carries per-event times at all, only this JSON API does.

The API returns the complete underlying event set in one request, so a
successful decode is explicit coverage provenance. These tests pin the
coverage record (strategy, requested/observed window, completion, errors,
event counts/fingerprint) and the fail-closed behavior for failed/partial
payloads and invalid recurrence/exception data.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

from tournament_scheduler.pipeline.scraper_styledcalendar import (
    _run_styledcalendar_scraper,
)
from tournament_scheduler.pipeline.source_integrity import (
    INTEGRITY_COMPLETE,
    INTEGRITY_FAILED,
    INTEGRITY_PARTIAL,
    evaluate_source_integrity,
)
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


def _make_response_from_payload(payload: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json.return_value = payload
    return response


def _scrape(raw_events, *, start=datetime(2026, 9, 1), end=datetime(2026, 9, 30)):
    with patch(
        "tournament_scheduler.pipeline.scraper_styledcalendar.requests.get",
        return_value=_make_api_response(raw_events),
    ):
        return _run_styledcalendar_scraper("Jutul", start, end)


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

        events, _ = _scrape(raw_events)

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

        events, _ = _scrape(raw_events)

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

        events, _ = _scrape(raw_events)

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

        events, _ = _scrape(raw_events)

        assert events == []

    def test_network_failure_returns_empty_list_not_an_exception(self):
        with patch(
            "tournament_scheduler.pipeline.scraper_styledcalendar.requests.get",
            side_effect=ConnectionError("boom"),
        ):
            events, raw_html = _run_styledcalendar_scraper(
                "Jutul", datetime(2026, 9, 1), datetime(2026, 9, 30),
            )

        assert events == []
        assert raw_html == ""
        # A swallowed exception must not look like a successful empty scrape.
        assert events.coverage["status"] == INTEGRITY_FAILED
        assert events.coverage["navigation_complete"] is False
        assert events.coverage["exceptions"]


class TestStyledCalendarCoverage:
    """Explicit coverage provenance for the concrete StyledCalendar strategy."""

    def test_successful_full_season_scrape_proves_the_requested_window(self):
        raw_events = [{
            "id": "evt-1",
            "title": "Jutul ESH",
            "start": "2026-10-10T15:00:00+02:00",
            "end": "2026-10-10T15:50:00+02:00",
            "allDay": False,
        }]

        events, _ = _scrape(
            raw_events, start=datetime(2026, 10, 9), end=datetime(2027, 3, 28),
        )

        coverage = events.coverage
        assert coverage["strategy"] == "styledcalendar"
        assert coverage["status"] == INTEGRITY_COMPLETE
        assert coverage["navigation_complete"] is True
        assert coverage["requested_start"] == "2026-10-09"
        assert coverage["requested_end"] == "2027-03-28"
        # The processed window is the requested window, not the first/last
        # date that happens to contain an event.
        assert coverage["observed_start"] == "2026-10-09"
        assert coverage["observed_end"] == "2027-03-28"
        assert coverage["event_observed_start"] == "2026-10-10"
        assert coverage["event_observed_end"] == "2026-10-10"
        assert coverage["event_count"] == 1
        assert coverage["exceptions"] == []
        assert coverage["event_fingerprint"]

    def test_end_of_season_event_on_last_day_is_included(self):
        """Stage 2 passes the last season day at midnight; that day is inclusive."""
        raw_events = [{
            "id": "evt-last-day",
            "title": "Jutul A",
            "start": "2027-03-28T15:00:00+02:00",
            "end": "2027-03-28T16:00:00+02:00",
            "allDay": False,
        }]

        events, _ = _scrape(
            raw_events, start=datetime(2026, 10, 9), end=datetime(2027, 3, 28),
        )

        assert [e.datetime for e in events] == [datetime(2027, 3, 28, 15, 0)]
        assert events.coverage["event_observed_end"] == "2027-03-28"

    def test_overnight_event_overlapping_window_start_is_included(self):
        raw_events = [{
            "id": "evt-overnight",
            "title": "Jutul natt",
            "start": "2026-10-08T22:00:00+02:00",
            "end": "2026-10-09T02:00:00+02:00",
            "allDay": False,
        }]

        events, _ = _scrape(
            raw_events, start=datetime(2026, 10, 9), end=datetime(2026, 10, 9),
        )

        assert len(events) == 1
        assert events.coverage["status"] == INTEGRITY_COMPLETE

    def test_sparse_and_empty_dates_still_prove_full_window_coverage(self):
        raw_events = [{
            "id": "evt-sparse",
            "title": "Jutul A",
            "start": "2026-10-10T15:00:00+02:00",
            "end": "2026-10-10T16:00:00+02:00",
            "allDay": False,
        }]

        events, _ = _scrape(
            raw_events, start=datetime(2026, 10, 9), end=datetime(2027, 3, 28),
        )

        coverage = events.coverage
        assert coverage["status"] == INTEGRITY_COMPLETE
        assert coverage["observed_start"] == "2026-10-09"
        assert coverage["observed_end"] == "2027-03-28"
        # Only one date carried an event; that must not shrink the covered window.
        assert coverage["event_observed_start"] == "2026-10-10"
        assert coverage["event_observed_end"] == "2026-10-10"

    def test_payload_with_zero_events_is_a_complete_empty_calendar(self):
        response = _make_response_from_payload({
            "compressedEventsAndIds": [
                {"compressedEvents": compress_to_utf16("[]")},
            ],
        })

        with patch(
            "tournament_scheduler.pipeline.scraper_styledcalendar.requests.get",
            return_value=response,
        ):
            events, _ = _run_styledcalendar_scraper(
                "Jutul", datetime(2026, 10, 9), datetime(2027, 3, 28),
            )

        assert events == []
        coverage = events.coverage
        assert coverage["status"] == INTEGRITY_COMPLETE
        assert coverage["navigation_complete"] is True
        assert coverage["event_count"] == 0
        assert coverage["event_observed_start"] is None
        assert coverage["event_observed_end"] is None

    def test_missing_event_payload_fails_closed(self):
        response = _make_response_from_payload({})

        with patch(
            "tournament_scheduler.pipeline.scraper_styledcalendar.requests.get",
            return_value=response,
        ):
            events, _ = _run_styledcalendar_scraper(
                "Jutul", datetime(2026, 10, 9), datetime(2027, 3, 28),
            )

        assert events == []
        assert events.coverage["status"] == INTEGRITY_FAILED
        assert events.coverage["navigation_complete"] is False

    def test_undecodable_blob_fails_closed_as_partial(self):
        response = _make_response_from_payload({
            "compressedEventsAndIds": [
                {"compressedEvents": "not-a-valid-lzstring"},
            ],
        })

        with patch(
            "tournament_scheduler.pipeline.scraper_styledcalendar.requests.get",
            return_value=response,
        ):
            events, _ = _run_styledcalendar_scraper(
                "Jutul", datetime(2026, 10, 9), datetime(2027, 3, 28),
            )

        assert events == []
        assert events.coverage["status"] == INTEGRITY_PARTIAL
        assert events.coverage["navigation_complete"] is False

    def test_invalid_json_blob_fails_closed_as_partial(self):
        response = _make_response_from_payload({
            "compressedEventsAndIds": [
                {"compressedEvents": compress_to_utf16("{not valid json")},
            ],
        })

        with patch(
            "tournament_scheduler.pipeline.scraper_styledcalendar.requests.get",
            return_value=response,
        ):
            events, _ = _run_styledcalendar_scraper(
                "Jutul", datetime(2026, 10, 9), datetime(2027, 3, 28),
            )

        assert events.coverage["status"] == INTEGRITY_PARTIAL
        assert events.coverage["exceptions"]

    def test_invalid_recurrence_rule_fails_closed_as_partial(self):
        raw_events = [{
            "id": "evt-bad-rrule",
            "title": "Jutul U9",
            "start": "2026-10-10T15:00:00+02:00",
            "end": "2026-10-10T16:00:00+02:00",
            "allDay": False,
            "recurrence": ["RRULE:NOTAFREQ=WEEKLY"],
        }]

        events, _ = _scrape(
            raw_events, start=datetime(2026, 10, 9), end=datetime(2027, 3, 28),
        )

        assert events.coverage["status"] == INTEGRITY_PARTIAL
        assert any("FREQ" in problem for problem in events.coverage["exceptions"])

    def test_invalid_exdate_fails_closed_as_partial(self):
        raw_events = [{
            "id": "evt-bad-exdate",
            "title": "Jutul U9",
            "start": "2026-10-10T15:00:00+02:00",
            "end": "2026-10-10T16:00:00+02:00",
            "allDay": False,
            "exdate": ["not-a-date"],
        }]

        events, _ = _scrape(
            raw_events, start=datetime(2026, 10, 9), end=datetime(2027, 3, 28),
        )

        assert events.coverage["status"] == INTEGRITY_PARTIAL
        assert any("EXDATE" in problem for problem in events.coverage["exceptions"])

    def test_missing_start_or_end_fails_closed_as_partial(self):
        raw_events = [{
            "id": "evt-missing-times",
            "title": "Jutul U9",
            "allDay": False,
        }]

        events, _ = _scrape(
            raw_events, start=datetime(2026, 10, 9), end=datetime(2027, 3, 28),
        )

        assert events.coverage["status"] == INTEGRITY_PARTIAL
        assert any("start/slutt" in problem for problem in events.coverage["exceptions"])

    def test_recurrence_expansion_failure_fails_closed(self):
        """A lazy expansion error must not escape past the guarded fetch block."""
        class _ExplodingOccurrences:
            def __iter__(self):
                raise RuntimeError("recurrence expansion failed")

        class _FakeRecurringIcalEvents:
            def of(self, calendar):
                return self

            def between(self, start, end):
                return _ExplodingOccurrences()

        raw_events = [{
            "id": "evt-recurring",
            "title": "Jutul U9",
            "start": "2026-10-10T15:00:00+02:00",
            "end": "2026-10-10T16:00:00+02:00",
            "allDay": False,
            "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=SA"],
        }]

        with patch(
            "tournament_scheduler.pipeline.scraper_styledcalendar.requests.get",
            return_value=_make_api_response(raw_events),
        ), patch(
            "tournament_scheduler.pipeline.scraper_styledcalendar.recurring_ical_events",
            _FakeRecurringIcalEvents(),
        ):
            events, _ = _run_styledcalendar_scraper(
                "Jutul", datetime(2026, 10, 9), datetime(2027, 3, 28),
            )

        assert events == []
        assert events.coverage["status"] == INTEGRITY_FAILED
        assert events.coverage["navigation_complete"] is False

    def test_expanded_event_without_title_fails_closed_as_partial(self):
        raw_events = [{
            "id": "evt-no-title",
            "title": "",
            "start": "2026-10-10T15:00:00+02:00",
            "end": "2026-10-10T16:00:00+02:00",
            "allDay": False,
        }]

        events, _ = _scrape(
            raw_events, start=datetime(2026, 10, 9), end=datetime(2027, 3, 28),
        )

        assert events.coverage["status"] == INTEGRITY_PARTIAL
        assert any("tittel" in problem for problem in events.coverage["exceptions"])

    def test_expanded_event_with_zero_duration_fails_closed_as_partial(self):
        raw_events = [{
            "id": "evt-zero-duration",
            "title": "Jutul U9",
            "start": "2026-10-10T15:00:00+02:00",
            "end": "2026-10-10T15:00:00+02:00",
            "allDay": False,
        }]

        events, _ = _scrape(
            raw_events, start=datetime(2026, 10, 9), end=datetime(2027, 3, 28),
        )

        assert events == []
        assert events.coverage["status"] == INTEGRITY_PARTIAL
        assert any("varighet" in problem for problem in events.coverage["exceptions"])

    def test_coverage_proven_survives_the_source_integrity_gate(self):
        """The owner boundary: a complete Jutul scrape is trustworthy evidence."""
        raw_events = [{
            "id": "evt-1",
            "title": "Jutul A",
            "start": "2026-10-10T15:00:00+02:00",
            "end": "2026-10-10T16:00:00+02:00",
            "allDay": False,
        }]

        events, _ = _scrape(
            raw_events, start=datetime(2026, 10, 9), end=datetime(2027, 3, 28),
        )
        source = {
            "name": "Jutul",
            # Jutul's Stage 1 source type is the generic `outlook`; only the
            # concrete strategy's coverage record proves the window.
            "type": "outlook",
            "events": events,
            "event_count": len(events),
        }

        integrity = evaluate_source_integrity(
            source, requested_start="2026-10-09", requested_end="2027-03-28",
        )

        assert integrity["status"] == INTEGRITY_COMPLETE
        assert integrity["coverage_proven"] is True
        assert integrity["trusted_for_negative_claim"] is True
        assert integrity["requested_start"] == "2026-10-09"
        assert integrity["requested_end"] == "2027-03-28"

    def test_generic_outlook_source_without_coverage_stays_unproven(self):
        """Coverage must be attached to the concrete strategy, not all `outlook`."""
        from tournament_scheduler.pipeline.source_integrity import (
            club_coverage_proven,
            downgrade_calendar_status_for_integrity,
            integrity_by_source,
        )

        source = {
            "name": "Skien",
            "type": "outlook",
            "events": [{
                "date": "10.10.2026",
                "datetime": "2026-10-10T15:00:00",
                "name": "Skien booking",
                "duration_hours": 1.0,
            }],
            "event_count": 1,
        }
        integrity = integrity_by_source([source])

        assert integrity["Skien"]["status"] == INTEGRITY_COMPLETE
        assert integrity["Skien"]["coverage_proven"] is False
        assert club_coverage_proven([source], integrity)["Skien"] is False
        assert downgrade_calendar_status_for_integrity(
            {"Skien": "known"}, [source], integrity,
        )["Skien"] == "source_review_required"
