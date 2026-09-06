"""Tests for tournament_scheduler.sandefjord_allocation (issue #261)."""

from __future__ import annotations

from datetime import date, datetime

from tournament_scheduler.sandefjord_allocation import (
    SANDEFJORD_ARENA,
    SANDEFJORD_CLUB_NAME,
    sandefjord_fixed_busy_events,
)
from tournament_scheduler.utils.slot_finder import find_available_slots


class TestSandefjordFixedBusyEvents:
    def test_generates_two_events_per_weekend_day(self):
        # 2025-09-06/07 is a Saturday/Sunday.
        events = sandefjord_fixed_busy_events(date(2025, 9, 6), date(2025, 9, 7))
        assert len(events) == 4
        for event in events:
            assert event.location == SANDEFJORD_ARENA

    def test_generates_one_full_day_event_per_weekday(self):
        # 2025-09-01..05 is Mon-Fri.
        events = sandefjord_fixed_busy_events(date(2025, 9, 1), date(2025, 9, 5))
        assert len(events) == 5
        for event in events:
            assert event.duration_hours == 24

    def test_accepts_datetime_bounds(self):
        events = sandefjord_fixed_busy_events(
            datetime(2025, 9, 6, 0, 0), datetime(2025, 9, 7, 0, 0)
        )
        assert len(events) == 4

    def test_weekend_leaves_15_to_18_free(self):
        events = sandefjord_fixed_busy_events(date(2025, 9, 6), date(2025, 9, 6))
        slots = find_available_slots(
            events, date(2025, 9, 6), required_minutes=180,
            earliest_start="10:00", latest_start="16:00",
        )
        assert slots == [("15:00", "18:00")]

    def test_weekend_window_too_long_for_required_duration_yields_no_slot(self):
        events = sandefjord_fixed_busy_events(date(2025, 9, 6), date(2025, 9, 6))
        slots = find_available_slots(
            events, date(2025, 9, 6), required_minutes=181,
            earliest_start="10:00", latest_start="16:00",
        )
        assert slots == []

    def test_weekday_has_no_available_slot(self):
        events = sandefjord_fixed_busy_events(date(2025, 9, 3), date(2025, 9, 3))
        slots = find_available_slots(
            events, date(2025, 9, 3), required_minutes=60,
            earliest_start="10:00", latest_start="16:00",
        )
        assert slots == []

    def test_club_name_matches_registry(self):
        from tournament_scheduler.club_registry import CLUB_REGISTRY

        assert SANDEFJORD_CLUB_NAME in CLUB_REGISTRY
        assert CLUB_REGISTRY[SANDEFJORD_CLUB_NAME].arena == SANDEFJORD_ARENA
