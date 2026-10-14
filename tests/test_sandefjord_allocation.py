"""Tests for tournament_scheduler.booking.sandefjord_allocation (issue #261)."""

from __future__ import annotations

from datetime import date, datetime

from tournament_scheduler.booking.sandefjord_allocation import (
    SANDEFJORD_ARENA,
    SANDEFJORD_CLUB_NAME,
    sandefjord_fixed_busy_events,
)
from tournament_scheduler.planning.contract import verify_candidate
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

    def test_planning_verification_reports_profile_authority_not_verified_free(self):
        teams = [
            {"club": "Sandefjord Penguins", "label": "Sandefjord 1", "age_group": "U10"},
            {"club": "Jar", "label": "Jar 1", "age_group": "U10"},
        ]
        candidate = {
            "tournaments": [
                {
                    "id": "sandefjord-u10",
                    "date": "2026-09-05",
                    "arena": SANDEFJORD_ARENA,
                    "age_group": "U10",
                    "host_club": SANDEFJORD_CLUB_NAME,
                    "start_time": "15:00",
                    "teams": teams,
                    "games": [
                {"home": "Sandefjord 1", "away": "Jar 1", "parallel_slot": 0, "round_number": 1}
            ],
                }
            ]
        }
        problem = {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "teams": teams,
            "age_groups": ["U10"],
            "clubs": {SANDEFJORD_CLUB_NAME: SANDEFJORD_ARENA, "Jar": "Jar Isforum"},
            "ice_time_minutes": {"U10": 180},
            "round_length_minutes": {"U10": 20},
            "rounds_per_tournament": {"U10": 1},
            "parallel_games": {"U10": 1},
            "club_calendar_status": {SANDEFJORD_CLUB_NAME: "known", "Jar": "known"},
            "club_busy_intervals": {SANDEFJORD_CLUB_NAME: []},
        }

        result = verify_candidate(candidate, problem)

        used = result["club_controlled_available_allocations_used"]
        assert len(used) == 1
        assert used[0]["availability"] == "club_controlled_available"
        assert used[0]["planning_semantics"] == "available_for_internal_allocation"
        assert used[0]["evidence_reference"].endswith("/issues/261")
        assert result["movable_allocations_used"] == []

    def test_club_name_matches_registry(self):
        from tournament_scheduler.clubs.registry import CLUB_REGISTRY

        assert SANDEFJORD_CLUB_NAME in CLUB_REGISTRY
        assert CLUB_REGISTRY[SANDEFJORD_CLUB_NAME].arena == SANDEFJORD_ARENA
