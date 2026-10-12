"""Tests for the registry-driven calendar-source factory (calendar_source_factory.py)."""

import pytest

from tournament_scheduler.clubs.registry import (
    CLUB_REGISTRY,
    CalendarSourceKind,
    ClubCalendarSource,
    known_clubs,
    missing_clubs,
)
from tournament_scheduler.data_sources.calendar_source_factory import (
    build_calendar_source,
    build_known_calendar_sources,
)
from tournament_scheduler.data_sources.calendar_scraper import OutlookCalendarScraper
from tournament_scheduler.data_sources.ical_scraper import ICalScraper
from tournament_scheduler.data_sources.ice_hall_calendar import IceHallCalendar
from tournament_scheduler.utils.calendar_cache import CalendarCache


OUTLOOK_ENTRY = ClubCalendarSource(
    club="Test Outlook Club",
    arena="Test Arena",
    kind=CalendarSourceKind.OUTLOOK,
    source="https://example.com/webkalender/ishall/",
)

ICAL_ENTRY = ClubCalendarSource(
    club="Test iCal Club",
    arena="Test Arena",
    kind=CalendarSourceKind.ICAL,
    source="https://example.com/feed.ics",
)

UNKNOWN_ENTRY = ClubCalendarSource(
    club="Test Unknown Club",
    arena="Test Arena",
    kind=CalendarSourceKind.UNKNOWN,
    source=None,
    skip=True,
)

SKIPPED_KNOWN_KIND_ENTRY = ClubCalendarSource(
    club="Test Skipped Club",
    arena="Test Arena",
    kind=CalendarSourceKind.ICAL,
    source="https://example.com/feed.ics",
    skip=True,
)


class TestBuildCalendarSource:
    """build_calendar_source maps a registry entry to the right scraper-backed source."""

    def test_outlook_entry_builds_ice_hall_with_outlook_scraper(self):
        source = build_calendar_source(OUTLOOK_ENTRY)
        assert isinstance(source, IceHallCalendar)
        assert isinstance(source.scraper, OutlookCalendarScraper)
        assert source.url == OUTLOOK_ENTRY.source

    def test_ical_entry_builds_ice_hall_with_ical_scraper(self):
        source = build_calendar_source(ICAL_ENTRY)
        assert isinstance(source, IceHallCalendar)
        assert isinstance(source.scraper, ICalScraper)
        assert source.url == ICAL_ENTRY.source
        assert source.scraper.calendar_id == ICAL_ENTRY.source

    def test_unknown_entry_returns_none(self):
        assert build_calendar_source(UNKNOWN_ENTRY) is None

    def test_skipped_entry_returns_none_even_with_known_kind(self):
        assert build_calendar_source(SKIPPED_KNOWN_KIND_ENTRY) is None

    def test_shared_cache_is_passed_through_to_scraper(self):
        cache = CalendarCache()
        outlook_source = build_calendar_source(OUTLOOK_ENTRY, cache)
        ical_source = build_calendar_source(ICAL_ENTRY, cache)

        assert outlook_source.scraper.cache is cache
        assert ical_source.scraper.cache is cache

    def test_without_cache_each_scraper_gets_its_own_default(self):
        outlook_source = build_calendar_source(OUTLOOK_ENTRY)
        ical_source = build_calendar_source(ICAL_ENTRY)

        assert isinstance(outlook_source.scraper.cache, CalendarCache)
        assert isinstance(ical_source.scraper.cache, CalendarCache)
        assert outlook_source.scraper.cache is not ical_source.scraper.cache

    def test_unrecognised_known_kind_raises_value_error(self):
        # Defensive guard: an entry that claims to be "known" but has a kind
        # the factory doesn't know how to build should fail loudly rather than
        # silently produce a broken/empty source.
        class FakeKind:
            pass

        weird_entry = ClubCalendarSource(
            club="Weird Club",
            arena="Weird Arena",
            kind=CalendarSourceKind.UNKNOWN,  # placeholder; we monkeypatch below
            source="https://example.com/feed",
        )
        # Force is_known True with an unhandled kind via object.__setattr__
        # (ClubCalendarSource is frozen).
        object.__setattr__(weird_entry, "kind", "not-a-real-kind")
        object.__setattr__(weird_entry, "skip", False)

        with pytest.raises(ValueError):
            build_calendar_source(weird_entry)


class TestBuildKnownCalendarSources:
    """build_known_calendar_sources loops the registry and builds every known source."""

    def test_returns_a_source_for_every_known_club(self):
        sources, by_club = build_known_calendar_sources()

        known_names = {entry.club for entry in known_clubs()}
        assert set(by_club.keys()) == known_names
        assert len(sources) == len(known_names)
        for source in sources:
            assert isinstance(source, IceHallCalendar)

    def test_skip_and_unknown_clubs_are_excluded_without_raising(self):
        sources, by_club = build_known_calendar_sources()

        skipped_names = {entry.club for entry in missing_clubs()}
        assert skipped_names.isdisjoint(by_club.keys())
        # Sanity: every club in the registry is accounted for by exactly one
        # of known_clubs()/missing_clubs(), and none raised while building.
        assert len(by_club) + len(skipped_names) == len(CLUB_REGISTRY)

    def test_sources_share_a_single_cache_instance(self):
        sources, by_club = build_known_calendar_sources()

        caches = {source.scraper.cache for source in sources}
        assert len(caches) == 1, "expected every built source to share one CalendarCache"

    def test_accepts_an_explicit_shared_cache(self):
        cache = CalendarCache()
        sources, by_club = build_known_calendar_sources(cache)

        for source in sources:
            assert source.scraper.cache is cache
