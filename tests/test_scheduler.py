"""Tests for TournamentScheduler.find_arena_slot_for_date."""

from datetime import date, datetime

from tournament_scheduler.conflict_checkers.holiday_checker import HolidayConflictChecker
from tournament_scheduler.models import CalendarEvent
from tournament_scheduler.sandefjord_allocation import sandefjord_fixed_busy_events
from tournament_scheduler.scheduler import TournamentScheduler
from tournament_scheduler.utils.date_parser import DateParser


CHECK_DATE = date(2026, 9, 5)


def _make_scheduler():
    return TournamentScheduler(
        calendar_sources=[],
        conflict_checkers=[HolidayConflictChecker()],
        date_parser=DateParser(),
    )


def _busy_all_day(day=CHECK_DATE):
    return [
        CalendarEvent(
            date=day.strftime("%d.%m.%Y"),
            name="Booket hele dagen",
            datetime=datetime(day.year, day.month, day.day, 0, 0),
            duration_hours=24.0,
        )
    ]


class TestFindArenaSlotForDate:
    def test_host_has_a_fitting_slot(self):
        scheduler = _make_scheduler()
        # Frisk Asker has a small morning booking, leaving plenty of room.
        events_by_club = {
            "Frisk Asker": [
                CalendarEvent(
                    date=CHECK_DATE.strftime("%d.%m.%Y"),
                    name="Morgentrening",
                    datetime=datetime(2026, 9, 5, 8, 0),
                    duration_hours=2.0,
                )
            ],
        }

        result = scheduler.find_arena_slot_for_date(
            CHECK_DATE, "Frisk Asker", 120, events_by_club
        )

        assert result is not None
        host_used, start, end = result
        assert host_used == "Frisk Asker"
        assert start and end

    def test_host_fully_booked_does_not_use_another_arena(self):
        scheduler = _make_scheduler()
        events_by_club = {
            "Frisk Asker": _busy_all_day(),
            "Ringerike": [],  # Ringerike has nothing booked.
        }

        result = scheduler.find_arena_slot_for_date(
            CHECK_DATE, "Frisk Asker", 150, events_by_club
        )

        assert result is None

    def test_no_arena_has_a_fitting_slot_returns_none(self):
        scheduler = _make_scheduler()
        from tournament_scheduler.club_registry import CLUB_REGISTRY

        events_by_club = {
            club: _busy_all_day()
            for club, entry in CLUB_REGISTRY.items()
            if entry.is_known
        }

        result = scheduler.find_arena_slot_for_date(
            CHECK_DATE, "Frisk Asker", 150, events_by_club
        )

        assert result is None

    def test_unregistered_host_club_returns_none_instead_of_raising(self):
        """A host name not in CLUB_REGISTRY at all (e.g. a joint-club team
        name like 'Jar/Jutul') must be treated like any other club with no
        known calendar source, per this method's own documented contract —
        not raise KeyError (regression for a real Stage 3 planning crash)."""
        scheduler = _make_scheduler()

        result = scheduler.find_arena_slot_for_date(CHECK_DATE, "Jar/Jutul", 90, {})

        assert result is None

    def test_host_with_no_calendar_data_falls_back(self):
        scheduler = _make_scheduler()
        # No events at all for any club -- every arena is "free", so the
        # host's own (empty) calendar should yield a fitting slot and be
        # returned without needing a fallback.
        result = scheduler.find_arena_slot_for_date(
            CHECK_DATE, "Frisk Asker", 90, {}
        )

        assert result is not None
        host_used, _start, _end = result
        assert host_used == "Frisk Asker"

    def test_prefers_slot_closest_to_optimal_time(self):
        scheduler = _make_scheduler()
        # Host (Frisk Asker) is busy until late morning, leaving only an
        # afternoon/evening slot far from the 11:00 optimum. A fallback
        # arena (Ringerike) is completely free, so its slot starting at
        # 08:00 should be evaluated too -- but the host's slot starting
        # closer to 11:00 should win if it scores better.
        events_by_club = {
            "Frisk Asker": [
                CalendarEvent(
                    date=CHECK_DATE.strftime("%d.%m.%Y"),
                    name="Morgentrening",
                    datetime=datetime(2026, 9, 5, 8, 0),
                    duration_hours=2.5,  # busy 08:00-10:30
                )
            ],
            "Ringerike": [],
        }

        result = scheduler.find_arena_slot_for_date(
            CHECK_DATE, "Frisk Asker", 60, events_by_club
        )

        assert result is not None
        host_used, start, _end = result
        # Frisk Asker's earliest fitting slot after its booking (10:30)
        # is closer to 11:00 than Ringerike's earliest slot (08:00).
        assert host_used == "Frisk Asker"
        assert start == "10:30"

    def test_prefers_the_requested_later_start_when_available(self):
        scheduler = _make_scheduler()
        events_by_club = {
            "Frisk Asker": [
                CalendarEvent(
                    date=CHECK_DATE.strftime("%d.%m.%Y"),
                    name="Morgentrening",
                    datetime=datetime(2026, 9, 5, 8, 0),
                    duration_hours=2.0,
                ),
                CalendarEvent(
                    date=CHECK_DATE.strftime("%d.%m.%Y"),
                    name="Kort pause",
                    datetime=datetime(2026, 9, 5, 11, 45),
                    duration_hours=0.25,
                ),
            ],
        }

        result = scheduler.find_arena_slot_for_date(
            CHECK_DATE, "Frisk Asker", 105, events_by_club, preferred_start="12:00"
        )

        assert result is not None
        host_used, start, _end = result
        assert host_used == "Frisk Asker"


class TestClubCalendarStatusGate:
    """An empty events_by_club entry is only searched as "known free" when
    club_calendar_status says so -- a missing/blocked scrape must not be
    silently treated as a verified-free calendar. Instead of blocking the
    slot outright, an unknown-status club gets a *provisional* slot at
    preferred_start, so it still gets to host (flagged for manual booking
    downstream) rather than being excluded."""

    def test_unknown_status_returns_provisional_slot_at_preferred_start(self):
        scheduler = _make_scheduler()
        events_by_club = {"Frisk Asker": []}

        result = scheduler.find_arena_slot_for_date(
            CHECK_DATE, "Frisk Asker", 120, events_by_club,
            preferred_start="12:00",
            club_calendar_status={"Frisk Asker": "unknown"},
        )

        assert result is not None
        host_used, start, end = result
        assert host_used == "Frisk Asker"
        assert start == "12:00"
        assert end == "14:00"

    def test_empty_status_map_disables_check(self):
        scheduler = _make_scheduler()
        events_by_club = {"Frisk Asker": []}

        result = scheduler.find_arena_slot_for_date(
            CHECK_DATE, "Frisk Asker", 120, events_by_club,
            club_calendar_status={},
        )

        # An empty status map (no entries at all) disables the check
        # entirely -- only a non-empty map that omits this specific club
        # should block it. This exercises that specific case.
        assert result is not None

    def test_club_absent_from_nonempty_status_map_returns_provisional_slot(self):
        scheduler = _make_scheduler()
        events_by_club = {"Frisk Asker": []}

        result = scheduler.find_arena_slot_for_date(
            CHECK_DATE, "Frisk Asker", 120, events_by_club,
            club_calendar_status={"Ringerike": "known"},
        )

        assert result is not None
        host_used, _start, _end = result
        assert host_used == "Frisk Asker"

    def test_known_status_with_empty_events_allows_slot(self):
        scheduler = _make_scheduler()
        events_by_club = {"Frisk Asker": []}

        result = scheduler.find_arena_slot_for_date(
            CHECK_DATE, "Frisk Asker", 120, events_by_club,
            club_calendar_status={"Frisk Asker": "known"},
        )

        assert result is not None

    def test_no_status_argument_preserves_legacy_behavior(self):
        scheduler = _make_scheduler()
        events_by_club = {"Frisk Asker": []}

        result = scheduler.find_arena_slot_for_date(
            CHECK_DATE, "Frisk Asker", 120, events_by_club,
        )

        assert result is not None


class TestSandefjordFixedAllocation:
    """Issue #261: Sandefjord's fixed weekend allocation feeds slot search
    exactly like a scraped calendar -- no Sandefjord-specific code needed
    here, just its synthetic busy events plugged into events_by_club."""

    def test_weekend_slot_fits_inside_15_to_18_window(self):
        scheduler = _make_scheduler()
        events_by_club = {
            "Sandefjord Penguins": sandefjord_fixed_busy_events(CHECK_DATE, CHECK_DATE),
        }

        result = scheduler.find_arena_slot_for_date(
            CHECK_DATE, "Sandefjord Penguins", 180, events_by_club
        )

        assert result is not None
        host_used, start, end = result
        assert host_used == "Sandefjord Penguins"
        assert start == "15:00"
        assert end == "18:00"

    def test_weekday_has_no_valid_slot(self):
        scheduler = _make_scheduler()
        weekday = date(2026, 9, 3)  # Thursday
        events_by_club = {
            "Sandefjord Penguins": sandefjord_fixed_busy_events(weekday, weekday),
        }

        result = scheduler.find_arena_slot_for_date(
            weekday, "Sandefjord Penguins", 60, events_by_club
        )

        assert result is None

    def test_duration_exceeding_the_window_has_no_valid_slot(self):
        scheduler = _make_scheduler()
        events_by_club = {
            "Sandefjord Penguins": sandefjord_fixed_busy_events(CHECK_DATE, CHECK_DATE),
        }

        result = scheduler.find_arena_slot_for_date(
            CHECK_DATE, "Sandefjord Penguins", 181, events_by_club
        )

        assert result is None
