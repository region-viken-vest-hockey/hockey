"""Tests for the central club/calendar-source registry (club_registry.py)."""

import pytest

from tournament_scheduler.club_registry import (
    CLUB_REGISTRY,
    CalendarSourceKind,
    build_data_source,
    get_club,
    known_clubs,
    missing_clubs,
)
from tournament_scheduler.data_sources.calendar_scraper import OutlookCalendarScraper
from tournament_scheduler.data_sources.ical_scraper import ICalScraper
from tournament_scheduler.data_sources.ice_hall_calendar import IceHallCalendar

ALL_NINE_CLUBS = [
    "Ringerike", "Tønsberg", "Frisk Asker", "Sandefjord Penguins",
    "Jar", "Holmen", "Skien", "Jutul", "Kongsberg",
]

CLUBS_WITH_KNOWN_SOURCES = [
    "Kongsberg", "Skien", "Ringerike",
    "Jutul", "Jar", "Holmen", "Frisk Asker",
    "Tønsberg", "Sandefjord Penguins",
]
CLUBS_PENDING_URLS = []

# The expected concrete scraper class for each known club's CalendarSourceKind
# — guards against drift between the registry's declared `kind` and the
# scraper the factory actually wires up for it.
EXPECTED_SCRAPER_BY_KIND = {
    CalendarSourceKind.OUTLOOK: OutlookCalendarScraper,
    CalendarSourceKind.ICAL: ICalScraper,
}

RESEARCHED_PENDING_CLUBS = []
GENERIC_PLACEHOLDER_NOTE_FRAGMENT = "URL not yet provided"


class TestClubRegistry:
    """Test suite for the RVV club calendar-source registry."""

    def test_registry_covers_all_nine_rvv_clubs(self):
        assert len(CLUB_REGISTRY) == 9
        for club in ALL_NINE_CLUBS:
            assert club in CLUB_REGISTRY

    def test_get_club_returns_entry_for_each_known_name(self):
        for club in ALL_NINE_CLUBS:
            entry = get_club(club)
            assert entry.club == club
            assert entry.arena  # every entry records a home arena

    def test_get_club_normalizes_sandefjord_aliases(self):
        assert get_club("Sandefjord").club == "Sandefjord Penguins"
        assert get_club("Sandefjord Penguins Ishockeyklubb").club == "Sandefjord Penguins"

    def test_get_club_raises_helpful_error_for_unknown_club(self):
        with pytest.raises(KeyError):
            get_club("Not A Real Club")

    def test_clubs_with_known_sources_build_a_usable_data_source(self):
        for club in CLUBS_WITH_KNOWN_SOURCES:
            entry = get_club(club)
            assert entry.is_known, f"{club} should have a known source"
            assert entry.kind in (CalendarSourceKind.OUTLOOK, CalendarSourceKind.ICAL)

            source = build_data_source(entry)
            assert source is not None, f"{club} should produce a constructible CalendarDataSource"
            assert isinstance(source, IceHallCalendar)

    def test_known_clubs_build_the_scraper_matching_their_registered_kind(self):
        """build_data_source wires each known club to the scraper its `kind` declares."""
        for club in CLUBS_WITH_KNOWN_SOURCES:
            entry = get_club(club)
            expected_scraper_cls = EXPECTED_SCRAPER_BY_KIND[entry.kind]

            source = build_data_source(entry)
            assert isinstance(source, IceHallCalendar)
            assert isinstance(source.scraper, expected_scraper_cls), (
                f"{club} (kind={entry.kind}) should be backed by "
                f"{expected_scraper_cls.__name__}, got {type(source.scraper).__name__}"
            )
            assert source.url == entry.source

    def test_clubs_pending_urls_are_marked_skip_with_placeholder(self):
        for club in CLUBS_PENDING_URLS:
            entry = get_club(club)
            assert entry.skip is True
            assert entry.is_known is False
            assert entry.note  # documents that a URL is still needed
            assert build_data_source(entry) is None

    def test_clubs_pending_urls_have_generic_placeholder_note(self):
        """Tønsberg and Sandefjord still have generic placeholder notes."""
        for club in RESEARCHED_PENDING_CLUBS:
            entry = get_club(club)
            assert entry.skip is True
            assert entry.is_known is False


    def test_known_clubs_returns_only_constructible_entries(self):
        names = {entry.club for entry in known_clubs()}
        assert names == set(CLUBS_WITH_KNOWN_SOURCES)

    def test_missing_clubs_returns_only_skip_entries(self):
        names = {entry.club for entry in missing_clubs()}
        assert names == set(CLUBS_PENDING_URLS)

    def test_known_and_missing_partition_the_registry(self):
        assert len(known_clubs()) + len(missing_clubs()) == len(CLUB_REGISTRY)
