"""Central registry of Region Viken Vest (RVV) clubs and their calendar sources.

This module maps each of the nine RVV clubs (https://www.rvvhockey.no/) to the
right calendar data-source construction so the rest of the pipeline (season
planner, conflict checkers, etc.) can simply look a club up by name instead of
re-deriving URLs / scraper types inline.

Each entry records:
  - the club's home arena name (used by the season planner to verify that
    every arena gets at least one tournament)
  - the kind of calendar source ("outlook" for Outlook/Playwright-based ice
    halls via `IceHallCalendar` + `CalendarScraper`, "ical" for generic iCal
    feeds via `ICalScraper`, or "unknown" where no source URL/ID is known yet)
  - the URL (for "outlook"/generic feeds) or calendar_id (for "ical")
  - a `skip` flag for clubs that cannot yet be scraped, so the rest of the
    pipeline can simply filter them out and continue working for the clubs
    with known sources.

Known/working sources today: Kongsberg, Skien, Ringerike (Teamup iCal export).

Registered but not yet scrapeable live (kind is known but the feed needs more
work — see notes on each entry):
  - Jutul: baerumishall.no/kalender/ embeds a StyledCalendar JS widget with no
    discoverable .ics/iCal export.
  - Jar: Forumbooking's ical.aspx export returns only an empty placeholder
    VEVENT regardless of date-range params.
  - Frisk Asker: friskaskerhockey.no ("Aktivitetskalender") is a JS-rendered
    Sportality/s8y SPA with no static calendar markup or export API found;
    varnerarena.no is blocked by a WAF page.
  - Holmen: holmenhockey.no links to a Sportello booking widget
    (kalender.sportello.no/booking/11055) whose public GraphQL API is now
    scraped deterministically.

The remaining JS-rendered widgets (Jutul, Jar, Frisk Asker) still need either
a discovered platform-specific export API or a Playwright-based scraper for
their booking widgets — analogous to Kongsberg's OUTLOOK integration but for
different platforms/markup.

Still missing URLs entirely (no calendar/booking system identified at all):
Tønsberg, Sandefjord Penguins.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


class CalendarSourceKind(Enum):
    """The kind of calendar data source a club uses."""

    # Outlook/Playwright-based webkalender, consumed via IceHallCalendar(url, CalendarScraper())
    OUTLOOK = "outlook"
    # Generic iCal feed (Google Calendar, Forumbooking, Teamup, etc.), consumed via ICalScraper(calendar_id)
    ICAL = "ical"
    # No known calendar source yet — registry entry exists as a documented placeholder
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClubCalendarSource:
    """Registry entry describing how to build a calendar data source for a club."""

    club: str
    arena: str
    kind: CalendarSourceKind
    # For OUTLOOK / generic-feed sources: the calendar URL.
    # For ICAL sources: the calendar_id (e.g. a Google Calendar email-style ID, or feed URL).
    source: Optional[str] = None
    # True when no usable source is known yet — pipeline should skip this club
    # rather than fail, and surface a TODO so the source can be added later.
    skip: bool = False
    note: Optional[str] = None
    # When set, the iCal scraper will discard any event whose LOCATION field
    # does not contain this string (case-insensitive substring match).  Useful
    # when a club's feed covers multiple arenas and only one is relevant.
    location_filter: Optional[str] = None

    @property
    def is_known(self) -> bool:
        """Whether this club has a usable, known calendar source today."""
        return not self.skip and self.source is not None


# The nine RVV clubs. Order follows the PROJECT.md / plan listing.
CLUB_REGISTRY: Dict[str, ClubCalendarSource] = {
    "Ringerike": ClubCalendarSource(
        club="Ringerike",
        arena="Ringerikshallen",
        kind=CalendarSourceKind.ICAL,
        # The public calendar page (https://teamup.com/ksr8bg1tpn5s3npskw) is an
        # HTML/JS view, not a feed — Teamup exposes the actual iCal export at
        # ics.teamup.com using the same calendar key (verified: returns
        # text/calendar with hundreds of parseable VEVENTs).
        source="https://ics.teamup.com/feed/ksr8bg1tpn5s3npskw/0.ics",
        note="Teamup iCal export feed (verified working — confirm parses via icalendar/recurring_ical_events).",
    ),
    "Tønsberg": ClubCalendarSource(
        club="Tønsberg",
        arena="Tonsberghallen",
        kind=CalendarSourceKind.OUTLOOK,
        source="https://www.bookup.no/utleie/Index/860",
        skip=False,
        note=(
            "BookUp SPA -- the Pi-driven ScraperAgent navigates the "
            "JS-rendered booking widget to extract ice hall bookings."
        ),
    ),
    "Frisk Asker": ClubCalendarSource(
        club="Frisk Asker",
        arena="Varner Arena / Askerhallen",
        kind=CalendarSourceKind.ICAL,
        source="https://ics.teamup.com/feed/ksdwpwxysmxwnuftoy/0.ics",
        skip=False,
        note=(
            "Teamup iCal export feed. Feed covers both Varner Arena and Askerhallen. "
            "The LOCATION field uses room/surface numbers ('1', '2', '4', '5') for "
            "Varner Arena ice surfaces, and 'Idrettshallen' for Askerhallen. "
            "Away-game entries use 'FA <rink> - <opponent> <locker>' format. "
            "No location_filter — previous filter 'Askerhallen' matched nothing in "
            "the feed (the name does not appear in LOCATION values)."
        ),
        location_filter=None,
    ),
    "Sandefjord Penguins": ClubCalendarSource(
        club="Sandefjord Penguins",
        arena="Sandefjord ishall",
        kind=CalendarSourceKind.OUTLOOK,
        source="https://www.bookup.no/Utleie/#Bug%C3%A5rdshallen___/view:item/id:4497/part:/place:3907:SANDEFJORD/q:sandefjord/r:31/mod:book",
        skip=False,
        note=(
            "BookUp SPA -- the Pi-driven ScraperAgent navigates the "
            "JS-rendered booking widget to extract ice hall bookings."
        ),
    ),
    "Jar": ClubCalendarSource(
        club="Jar",
        arena="Jarhallen",
        kind=CalendarSourceKind.OUTLOOK,
        source="https://www.forumbooking.no/schema.aspx?obj=2&schema=Jarhallen%20(ishall)&kalender=true&safarifix=true",
        skip=False,
        note=(
            "Forumbooking HTML schema viewer -- currently scraped via "
            "Playwright (iframe-based). Maps to 368 events."
        ),
    ),
    "Holmen": ClubCalendarSource(
        club="Holmen",
        arena="Holmenkollen ishall",
        kind=CalendarSourceKind.OUTLOOK,
        source="https://kalender.sportello.no/booking/11055",
        skip=False,
        note=(
            "Sportello booking widget -- scraped deterministically via the public GraphQL API. "
            "Maps to 176 events."
        ),
    ),
    "Skien": ClubCalendarSource(
        club="Skien",
        arena="Skien ishall",
        kind=CalendarSourceKind.OUTLOOK,
        source="https://skienfritidspark.brp.exigo.no/ishallen",
        note=(
            "brp.exigo.no Next.js app with ?date=YYYY-MM-DD parameter -- "
            "scraped via date-parameter approach."
        ),
    ),
    "Jutul": ClubCalendarSource(
        club="Jutul",
        arena="Baerum ishall",
        kind=CalendarSourceKind.OUTLOOK,
        source="https://baerumishall.no/kalender/",
        skip=False,
        note=(
            "StyledCalendar JS widget -- needs ScraperAgent (Pi-driven browser). "
            "Currently blocked in deterministic scraping (0 events)."
        ),
    ),
    "Kongsberg": ClubCalendarSource(
        club="Kongsberg",
        arena="Kongsberghallen",
        kind=CalendarSourceKind.OUTLOOK,
        source="https://kongsberghallen.no/webkalender/ishall/",
        note="Outlook/Playwright-based webkalender (existing integration). Also has a ball hall calendar.",
    ),
}


def club_for_source_name(source_name: str) -> Optional[str]:
    """Map a Stage 1 calendar-source ``name`` to its RVV club name.

    Source names in ``input.xlsx`` are usually the club name itself
    (e.g. ``"Jutul"``, ``"Frisk Asker"``), but some legacy entries append the
    arena/hall (e.g. ``"Kongsberg ishall"``, ``"Skien ishall"``). This looks
    up ``CLUB_REGISTRY`` for an exact match first, then falls back to a
    case-insensitive prefix match against each registered club name.

    Returns ``None`` if no club matches.
    """
    if source_name in CLUB_REGISTRY:
        return source_name

    lowered = source_name.strip().lower()
    for club_name in CLUB_REGISTRY:
        if lowered.startswith(club_name.lower()):
            return club_name
    return None


def club_for_arena(arena_name: str) -> Optional[str]:
    """Return the club name that owns *arena_name*, or ``None`` if not found.

    Performs a case-insensitive match against each entry's ``arena`` field in
    :data:`CLUB_REGISTRY`. This is the authoritative reverse-lookup for
    resolving a venue name to the owning club; downstream callers should prefer
    this over maintaining a separate arena→club dictionary.

    Returns the canonical club name (as registered in :data:`CLUB_REGISTRY`)
    on a match, or ``None`` if no entry matches. Multi-arena labels that are
    stored as ``"Arena A / Arena B"`` are treated as aliases for each
    individual component.
    """
    lowered = arena_name.strip().lower()
    for club_name, entry in CLUB_REGISTRY.items():
        entry_lower = entry.arena.lower()
        if entry_lower == lowered:
            return club_name
        parts = [part.strip() for part in entry_lower.split("/")]
        if lowered in parts:
            return club_name
    return None


def arenas_for_date_search(host_club: str) -> List[ClubCalendarSource]:
    """Return arena candidates for a date/slot search, host first.

    Returns the *host_club*'s own :class:`ClubCalendarSource` entry first
    (if it has a known calendar source), followed by every other club with a
    known calendar source as fallback hosts -- in :data:`CLUB_REGISTRY`
    iteration order.

    This preserves the existing single-arena-per-club model (each registry
    entry still has exactly one ``arena``); it simply orders the known
    entries so a slot-finder can try the preferred host first and fall back
    to other clubs' arenas/calendars for the same date if needed.

    Clubs without a usable calendar source (``skip=True`` or ``source is
    None``) are omitted entirely, including the host itself if it has no
    known source.

    Raises ``KeyError`` (via :func:`get_club`) if *host_club* is not in
    :data:`CLUB_REGISTRY`.
    """
    host_entry = get_club(host_club)

    candidates: List[ClubCalendarSource] = []
    if host_entry.is_known:
        candidates.append(host_entry)

    for club_name, entry in CLUB_REGISTRY.items():
        if club_name == host_club:
            continue
        if entry.is_known:
            candidates.append(entry)

    return candidates


# Short-name aliases that some config files use instead of the full registry name
_CLUB_ALIASES: Dict[str, str] = {
    "Sandefjord": "Sandefjord Penguins",
    "Sandefjord Penguins Ishockeyklubb": "Sandefjord Penguins",
}


def get_club(name: str) -> ClubCalendarSource:
    """Look up a club's registry entry by name.

    Raises KeyError with a helpful message if the club is not in the registry.
    """
    resolved = _CLUB_ALIASES.get(name, name)
    try:
        return CLUB_REGISTRY[resolved]
    except KeyError:
        raise KeyError(
            f"Unknown club '{name}'. Known clubs: {', '.join(sorted(CLUB_REGISTRY))}"
        )


def known_clubs() -> List[ClubCalendarSource]:
    """Return registry entries for clubs with a usable, known calendar source."""
    return [entry for entry in CLUB_REGISTRY.values() if entry.is_known]


def missing_clubs() -> List[ClubCalendarSource]:
    """Return registry entries for clubs that are missing a calendar source (skip=True)."""
    return [entry for entry in CLUB_REGISTRY.values() if entry.skip]


def build_data_source(entry: ClubCalendarSource, cache=None):
    """Construct the appropriate CalendarDataSource for a registry entry.

    Thin wrapper around the registry-driven
    `data_sources.calendar_source_factory.build_calendar_source`, kept here for
    backward compatibility with existing callers/tests that import
    `build_data_source` from `club_registry`.

    For OUTLOOK sources this builds an `IceHallCalendar` backed by a shared
    `OutlookCalendarScraper` (Playwright-based webkalender).
    For ICAL sources this builds an `IceHallCalendar` backed by an `ICalScraper`
    constructed from the entry's `source` (calendar_id or feed URL), matching
    the existing Skien integration pattern.

    Args:
        entry: The club's `ClubCalendarSource` registry entry.
        cache: Optional shared `CalendarCache` to back the underlying scraper.

    Returns None for UNKNOWN/skip entries.
    """
    from tournament_scheduler.data_sources.calendar_source_factory import build_calendar_source

    return build_calendar_source(entry, cache)
