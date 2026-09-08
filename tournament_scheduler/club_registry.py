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

Known/working deterministic sources today include Ringerike/Frisk Asker
(Teamup iCal), Kongsberg (Outlook iframe), Holmen (Sportello GraphQL), Jar
(Forumbooking weekly HTML), Skien (BRP/Exigo daily Next.js payload), and Jutul
(StyledCalendar widget).

A BookUp source for Tønsberg has a known URL but requires
BOOKUP_EMAIL/BOOKUP_PASSWORD and may still need manual/MFA recovery before the
full private booking calendar can be trusted.

Sandefjord Penguins is *not* a BookUp/credentialed source for the RVV
Miniputt pipeline (issue #261): the club has a known, fixed weekly ice-time
allocation instead of a bookable calendar, modelled deterministically in
`tournament_scheduler.sandefjord_allocation` and fed into Stage 2 as a
`"fixed_allocation"` source (`pipeline.fixed_allocation_source`). The entry
below keeps `source`/`kind=OUTLOOK` only so `is_known` stays true for the
older, non-pipeline `scheduling_command`/`season_command`/`reschedule_command`
CLI tools that still build a live `CalendarDataSource` from the registry --
the Stage 1-4 pipeline never scrapes it.
"""

import logging
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


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
    # Machine-readable source used by the scraper. For ICAL sources this can
    # be a Google Calendar ID or direct iCal feed URL.
    source: Optional[str] = None
    # Human-readable calendar page used for operator audit/cross-checking.
    # When omitted, callers should fall back to ``source``.
    human_url: Optional[str] = None
    # True when no usable source is known yet — pipeline should skip this club
    # rather than fail, and surface a TODO so the source can be added later.
    skip: bool = False
    note: Optional[str] = None
    # When set, the iCal scraper will discard any event whose LOCATION field
    # does not contain this string (case-insensitive substring match). Useful
    # when a feed covers multiple arenas but RVV can only use one of them.
    location_filter: Optional[str] = None
    # issue #264: whether this club's *scraped* calendar entries represent a
    # generic club allocation it controls (busy in the public/arena calendar
    # so other bookers can't take it, but usable by the club itself for its
    # own miniputt tournaments) rather than a genuine external booking. When
    # True, `pipeline.stage3_helpers._build_club_busy_intervals` tags this
    # club's busy intervals `"kind": "club_controlled"`, and
    # `planning_contract.external_calendar_conflict` no longer treats them as
    # a hard conflict for tournaments this same club hosts (though
    # `verify_candidate` still records their use for the evidence bundle).
    # Defaults to False -- flip per club only once a run's calendar evidence
    # has concretely established club control, never as a blanket
    # assumption. Deliberately independent of Sandefjord Penguins' fixed
    # weekly allocation (`sandefjord_allocation.py`): that club's busy
    # windows represent ice genuinely unavailable to it and must stay a hard
    # conflict, not club-controlled.
    club_controlled_calendar: bool = False
    # issue #274: whether a successful scrape of `source` is trustworthy
    # enough evidence for *automatic* tournament placement. Separate from
    # `is_known` on purpose -- a source can return real rows (so the scraper
    # itself "succeeds") while still not being the calendar that actually
    # needs to be trusted for booking decisions (e.g. Tønsberg's BookUp URL
    # currently only exposes generic/public placeholder data, not the full
    # authenticated booking calendar). Defaults to True; flip to False only
    # when a club's returned data is known not to be the real, complete
    # calendar. The club still counts fully toward hosting fairness/coverage
    # -- this only forces every one of its hosted tournaments into manual
    # placement (`pipeline.scraper_event_helpers._group_club_calendar_status`).
    trusted_for_auto_placement: bool = True

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
        # The public calendar page is the operator-audit view; Teamup exposes
        # the actual machine-readable iCal export at ics.teamup.com using the
        # same calendar key.
        source="https://ics.teamup.com/feed/ksr8bg1tpn5s3npskw/0.ics",
        human_url="https://teamup.com/ksr8bg1tpn5s3npskw",
        note="Teamup iCal export feed with explicit human calendar URL for audit/cross-checking.",
    ),
    "Tønsberg": ClubCalendarSource(
        club="Tønsberg",
        arena="Tonsberghallen",
        kind=CalendarSourceKind.OUTLOOK,
        source="https://www.bookup.no/utleie/Index/860#___/view:item/id:860/part:/r:8/mod:book",
        skip=False,
        note=(
            "BookUp SPA -- full Tønsberg ishall availability is behind "
            "BookUp login and requires BOOKUP_EMAIL/BOOKUP_PASSWORD. Until an "
            "authenticated full-calendar scrape is proven trustworthy, a "
            "successful scrape of this URL only returns BookUp's generic/"
            "public placeholder data, not real availability -- issue #274 "
            "requires every Tønsberg-hosted tournament to be manual until "
            "that is fixed (trusted_for_auto_placement=False)."
        ),
        trusted_for_auto_placement=False,
    ),
    "Frisk Asker": ClubCalendarSource(
        club="Frisk Asker",
        # RVV can book Askerhallen only. Varner Arena appears in the same
        # Teamup calendar but must never become RVV scheduling evidence.
        arena="Askerhallen",
        kind=CalendarSourceKind.ICAL,
        source="https://ics.teamup.com/feed/ksdwpwxysmxwnuftoy/0.ics",
        human_url="https://teamup.com/ksdwpwxysmxwnuftoy",
        skip=False,
        note=(
            "Teamup feed covers both Varner Arena and Askerhallen, but RVV can "
            "book Askerhallen only. In the feed Askerhallen is represented by "
            "LOCATION values containing 'Idrettshallen'; numbered surfaces and "
            "'FA ...' rooms belong to Varner Arena and are excluded from RVV "
            "availability evidence."
        ),
        location_filter="Idrettshallen",
    ),
    "Sandefjord Penguins": ClubCalendarSource(
        club="Sandefjord Penguins",
        arena="Sandefjord ishall",
        kind=CalendarSourceKind.OUTLOOK,
        source="https://www.bookup.no/Utleie/#Bug%C3%A5rdshallen___/view:item/id:4497/part:/place:3907:SANDEFJORD/q:sandefjord/r:31/mod:book",
        skip=False,
        note=(
            "Issue #261: the RVV Miniputt pipeline no longer scrapes this "
            "BookUp URL -- Sandefjord Penguins has a known, fixed weekend "
            "ice-time allocation instead (see sandefjord_allocation.py), fed "
            "into Stage 2 via a 'fixed_allocation' source. The BookUp URL is "
            "kept only so legacy, non-pipeline CLI commands still see this "
            "club as 'known'."
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

    canonical = canonicalize_club_name(source_name)
    if canonical in CLUB_REGISTRY:
        return canonical

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


# Known name variants per canonical RVV club (issue #272). This is the single
# source of truth for club-identity aliases across the whole pipeline --
# registration/team import, workbook/Stage 1 roster, Stage 2 calendar/source
# data, arena lookup, Stage 3 host assignment/fairness, manual-placement
# diagnostics, and exports/reports all resolve through
# `canonicalize_club_name`/`get_club` instead of maintaining their own alias
# tables.
CLUB_ALIASES: Dict[str, List[str]] = {
    "Ringerike": [],
    "Tønsberg": ["Tonsberg"],
    "Frisk Asker": [],
    "Sandefjord Penguins": ["Sandefjord", "Sandefjord Penguins Ishockeyklubb"],
    "Jar": [],
    "Holmen": [],
    "Skien": [],
    "Jutul": [],
    "Kongsberg": [],
}


def _normalize(text: str) -> str:
    """Unicode NFKC + casefold + collapsed whitespace."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


# normalized alias/canonical-name -> canonical name, built once at import time
_NORMALIZED_LOOKUP: Dict[str, str] = {}
for _canonical, _aliases in CLUB_ALIASES.items():
    _NORMALIZED_LOOKUP[_normalize(_canonical)] = _canonical
    for _alias in _aliases:
        _NORMALIZED_LOOKUP[_normalize(_alias)] = _canonical

# normalized token set per canonical club, for unique-containment matching
_CANONICAL_TOKENS: Dict[str, set] = {
    canonical: set(_normalize(canonical).split()) for canonical in CLUB_ALIASES
}


def canonicalize_club_name(raw: str) -> str:
    """Resolve *raw* to a canonical RVV club name (issue #272).

    Deterministic resolution order:
      1. Unicode/case/whitespace normalization.
      2. Exact canonical-name match.
      3. Exact alias match.
      4. Unique word/token containment match (every token of exactly one
         canonical name is present in *raw*'s tokens).
      5. Otherwise the name is left unresolved: the whitespace-trimmed
         original is returned unchanged, and an ambiguous containment match
         is logged rather than silently guessed at. Fuzzy/Levenshtein
         matching is intentionally never used here -- see
         :func:`suggest_club_name` for diagnostics-only suggestions.
    """
    cleaned = raw.strip()
    normalized = _normalize(cleaned)
    if normalized in _NORMALIZED_LOOKUP:
        return _NORMALIZED_LOOKUP[normalized]

    raw_tokens = set(normalized.split())
    matches = [
        canonical
        for canonical, tokens in _CANONICAL_TOKENS.items()
        if tokens and tokens <= raw_tokens
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.warning(
            "Ambiguous club name %r matches multiple canonical clubs %s; leaving unresolved",
            raw,
            sorted(matches),
        )
    return cleaned


def suggest_club_name(raw: str) -> Optional[str]:
    """Diagnostic-only fuzzy suggestion for an unresolved club name.

    E.g. ``suggest_club_name("Sandefjord Penguns")`` may suggest
    ``"Sandefjord Penguins"``. This is never used to resolve identity
    automatically -- only :func:`canonicalize_club_name`'s deterministic
    steps do that.
    """
    import difflib

    normalized = _normalize(raw.strip())
    best = difflib.get_close_matches(normalized, _NORMALIZED_LOOKUP.keys(), n=1, cutoff=0.75)
    return _NORMALIZED_LOOKUP[best[0]] if best else None


def get_club(name: str) -> ClubCalendarSource:
    """Look up a club's registry entry by name.

    Raises KeyError with a helpful message if the club is not in the registry.
    """
    resolved = canonicalize_club_name(name)
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
