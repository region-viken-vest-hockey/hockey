"""Registry-driven factory for building calendar data sources for RVV clubs.

This module centralises the mapping from a `ClubCalendarSource` registry entry
(`tournament_scheduler.clubs.registry`) to the concrete, scraper-backed
`CalendarDataSource` instance the rest of the pipeline (season planner,
conflict checkers, scheduling command, etc.) actually consumes.

It replaces the ad-hoc, hardcoded instantiation previously seen in
`scheduling_command.py`:

    scraper = CalendarScraper()
    kongsberg = get_club("Kongsberg")
    IceHallCalendar(kongsberg.source, scraper)

Both `CalendarSourceKind.OUTLOOK` and `CalendarSourceKind.ICAL` entries are
wired through `IceHallCalendar` so that tournament-keyword filtering stays
consistent across every club:

  - OUTLOOK -> IceHallCalendar(entry.source, OutlookCalendarScraper(cache))
  - ICAL    -> IceHallCalendar(entry.source, ICalScraper(entry.source, cache))

`UNKNOWN` and `skip=True` entries have no usable source yet; `build_calendar_source`
returns `None` for them so callers can simply skip those clubs (mirroring
`ClubCalendarSource.is_known` / `clubs.registry.missing_clubs`).

Note for clubs still pending a discovered source (Holmen, Frisk Asker, Jutul,
Jar, Tønsberg, Sandefjord Penguins — see notes on each `CLUB_REGISTRY` entry
for what was checked): no new scraper code or factory changes are needed to
activate them. Both `ICalScraper` (any iCal/.ics feed URL or Google-Calendar
email-style ID — see `ICalScraper._feed_url`) and `OutlookCalendarScraper`
(any Playwright-renderable Outlook-format webkalender URL) are fully generic
and URL-parameterised already. Once a real feed/calendar URL is found for a
club, activating it is just a matter of updating its registry entry
(`kind`, `source`, `skip=False`) — `build_calendar_source` /
`build_known_calendar_sources` will pick it up automatically.
"""

from typing import Dict, List, Optional, Tuple

from tournament_scheduler.clubs.registry import (
    CalendarSourceKind,
    ClubCalendarSource,
    known_clubs,
)
from tournament_scheduler.interfaces import CalendarDataSource
from tournament_scheduler.utils.calendar_cache import CalendarCache


def build_calendar_source(
    entry: ClubCalendarSource, cache: Optional[CalendarCache] = None
) -> Optional[CalendarDataSource]:
    """Build the appropriate calendar data source for a club registry entry.

    Args:
        entry: The club's `ClubCalendarSource` registry entry.
        cache: Optional shared `CalendarCache` to back the underlying scraper.
            When omitted, each scraper falls back to its own default cache.

    Returns:
        A `CalendarDataSource`-compatible instance (currently always an
        `IceHallCalendar` wrapping the kind-appropriate scraper), or `None`
        for `UNKNOWN`/`skip=True` entries that have no usable source yet.

    Raises:
        ValueError: if `entry.kind` is a recognised, "known" kind that this
            factory does not yet know how to build (defensive guard against
            registry/factory drift — should not happen in practice since
            `OUTLOOK` and `ICAL` are the only kinds that can be `is_known`).
    """
    if not entry.is_known:
        return None

    # Imported lazily to avoid pulling in Playwright/iCal dependencies for
    # callers that only need the registry (mirrors clubs.registry.build_data_source).
    from tournament_scheduler.data_sources.calendar_scraper import OutlookCalendarScraper
    from tournament_scheduler.data_sources.ical_scraper import ICalScraper
    from tournament_scheduler.data_sources.ice_hall_calendar import IceHallCalendar

    if entry.kind == CalendarSourceKind.OUTLOOK:
        return IceHallCalendar(entry.source, OutlookCalendarScraper(cache))

    if entry.kind == CalendarSourceKind.ICAL:
        return IceHallCalendar(entry.source, ICalScraper(entry.source, cache))

    raise ValueError(
        f"No calendar source factory wired up for kind={entry.kind!r} "
        f"(club={entry.club!r}). Known kinds: OUTLOOK, ICAL."
    )


def build_known_calendar_sources(
    cache: Optional[CalendarCache] = None,
) -> Tuple[List[CalendarDataSource], Dict[str, CalendarDataSource]]:
    """Build calendar data sources for every club with a usable, known source.

    Loops over `clubs.registry.known_clubs()` (i.e. every entry where
    `is_known` is true — `skip=False` and `source` is set) and builds a
    `CalendarDataSource` for each via `build_calendar_source`, sharing a single
    `CalendarCache` across all of them so repeated runs hit the on-disk cache
    consistently.

    This is the registry-driven replacement for hardcoding a single club
    (e.g. only Kongsberg) in CLI entry points — adding/activating a club is
    then just a matter of updating its `CLUB_REGISTRY` entry.

    Args:
        cache: Optional shared `CalendarCache`. When omitted, a single new
            `CalendarCache()` is created and shared across all sources.

    Returns:
        A tuple of:
          - a flat list of `CalendarDataSource` instances (in registry order)
          - a dict mapping club name -> its `CalendarDataSource`, so callers
            that need to single out a specific club (e.g. Kongsberg's ice
            hall for tournament/team-availability checking) can do so without
            re-instantiating it.
    """
    shared_cache = cache or CalendarCache()

    sources: List[CalendarDataSource] = []
    by_club: Dict[str, CalendarDataSource] = {}

    for entry in known_clubs():
        source = build_calendar_source(entry, shared_cache)
        if source is not None:
            sources.append(source)
            by_club[entry.club] = source

    return sources, by_club
