"""Tests for tournament_scheduler.utils.slot_finder."""

from datetime import date, datetime, timezone

from tournament_scheduler.models import CalendarEvent
from tournament_scheduler.utils.slot_finder import (
    _event_busy_range_on_date,
    find_available_slots,
    format_time,
    minutes_to_time,
    parse_time,
)


CHECK_DATE = date(2026, 9, 5)


def _event(hour, minute, duration_hours, day=CHECK_DATE):
    return CalendarEvent(
        date=day.strftime("%d.%m.%Y"),
        name="Booking",
        datetime=datetime(day.year, day.month, day.day, hour, minute),
        duration_hours=duration_hours,
    )


class TestHelperFunctions:
    def test_parse_time(self):
        t = parse_time("11:30")
        assert t.hour == 11
        assert t.minute == 30

    def test_format_time(self):
        t = parse_time("09:05")
        assert format_time(t) == "09:05"

    def test_minutes_to_time(self):
        assert minutes_to_time(0) == "00:00"
        assert minutes_to_time(75) == "01:15"
        assert minutes_to_time(600) == "10:00"


class TestFindAvailableSlots:
    def test_no_events_entire_window_available(self):
        slots = find_available_slots(
            events=[],
            check_date=CHECK_DATE,
            required_minutes=120,
            earliest_start="11:00",
            latest_start="15:30",
        )
        assert slots == [("11:00", "13:00")]

    def test_single_gap_fits_required_duration(self):
        # Busy 09:00-10:00 and 13:00-15:00 -- a 120-minute gap exists
        # 10:00-13:00.
        events = [
            _event(9, 0, 1.0),
            _event(13, 0, 2.0),
        ]
        slots = find_available_slots(
            events=events,
            check_date=CHECK_DATE,
            required_minutes=120,
            earliest_start="08:00",
            latest_start="20:00",
        )
        # A slot before the first event (08:00-09:00) is only 60 minutes,
        # too short for 120. The 10:00-13:00 gap fits.
        assert ("10:00", "12:00") in slots

    def test_multiple_gaps_each_produce_a_slot(self):
        # Busy 08:00-09:00, 11:00-12:00, 16:00-18:00.
        events = [
            _event(8, 0, 1.0),
            _event(11, 0, 1.0),
            _event(16, 0, 2.0),
        ]
        slots = find_available_slots(
            events=events,
            check_date=CHECK_DATE,
            required_minutes=60,
            earliest_start="08:00",
            latest_start="20:00",
        )
        # Gap 09:00-11:00 (2h), gap 12:00-16:00 (4h), and after 18:00.
        starts = [s[0] for s in slots]
        assert "09:00" in starts
        assert "12:00" in starts
        assert "18:00" in starts

    def test_fully_booked_day_returns_no_slots(self):
        # Busy the entire searchable window.
        events = [_event(0, 0, 24.0)]
        slots = find_available_slots(
            events=events,
            check_date=CHECK_DATE,
            required_minutes=60,
            earliest_start="08:00",
            latest_start="20:00",
        )
        assert slots == []

    def test_required_minutes_too_long_for_any_gap(self):
        # Busy 09:00-10:00 and 11:00-23:59 -- only a 60-minute gap
        # (10:00-11:00) and a slot before 09:00 (08:00-09:00, also 60min).
        # Neither fits a 120-minute requirement, and the after-last-event
        # check also fails since latest_start is exceeded.
        events = [
            _event(9, 0, 1.0),
            _event(11, 0, 12.0 + 12.0 - 11.0),  # ends at 23:59-ish
        ]
        slots = find_available_slots(
            events=events,
            check_date=CHECK_DATE,
            required_minutes=120,
            earliest_start="08:00",
            latest_start="20:00",
        )
        assert slots == []

    def test_events_on_other_dates_are_ignored(self):
        other_day = date(2026, 9, 6)
        events = [_event(8, 0, 24.0, day=other_day)]
        slots = find_available_slots(
            events=events,
            check_date=CHECK_DATE,
            required_minutes=60,
            earliest_start="08:00",
            latest_start="20:00",
        )
        # CHECK_DATE has no events -- entire window should be available.
        assert slots == [("08:00", "09:00")]

    def test_previous_day_overnight_event_blocks_after_midnight(self):
        previous_day = date(2026, 9, 4)
        events = [_event(23, 0, 3.0, day=previous_day)]
        slots = find_available_slots(
            events=events,
            check_date=CHECK_DATE,
            required_minutes=60,
            earliest_start="00:00",
            latest_start="03:00",
        )
        assert slots == [("02:00", "03:00")]

    def test_current_day_overnight_event_blocks_late_evening(self):
        events = [_event(23, 0, 3.0)]
        slots = find_available_slots(
            events=events,
            check_date=CHECK_DATE,
            required_minutes=60,
            earliest_start="22:30",
            latest_start="23:30",
        )
        assert slots == []

    def test_float_noise_does_not_shorten_exact_minute_busy_range(self):
        event = _event(11, 15, (12 + 55 / 60) - (11 + 15 / 60))

        assert _event_busy_range_on_date(event, CHECK_DATE) == (11 * 60 + 15, 12 * 60 + 55)

    def test_timezone_aware_event_is_compared_with_matching_day_bounds(self):
        events = [
            CalendarEvent(
                date=CHECK_DATE.strftime("%d.%m.%Y"),
                name="Aware booking",
                datetime=datetime(CHECK_DATE.year, CHECK_DATE.month, CHECK_DATE.day, 10, 0, tzinfo=timezone.utc),
                duration_hours=2.0,
            )
        ]
        slots = find_available_slots(
            events=events,
            check_date=CHECK_DATE,
            required_minutes=60,
            earliest_start="09:00",
            latest_start="13:00",
        )
        assert slots == [("09:00", "10:00"), ("12:00", "13:00")]
