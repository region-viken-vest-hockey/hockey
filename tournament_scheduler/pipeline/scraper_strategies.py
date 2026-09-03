"""Per-club scraper strategies that describe how to navigate each calendar system.

Each :class:`ScraperStrategy` tells the Pi-driven ScraperAgent how to interact
with a particular club's calendar site — what kind of page it is, what
navigation patterns to expect, and whether there's a simple deterministic
fallback (iframes, date params, iCal feed).

Strategies are organised by **calendar engine** since clubs using the same
platform family usually share navigation patterns or API shapes (for example,
Sportello's public GraphQL endpoint) even though their URLs differ.

The ScraperAgent in the extension:
  1. Looks up the strategy for the current source
  2. Launches the Python browserWorker
  3. Uses Pi's model to evaluate page content and decide the next action
  4. Uses the strategy hints to guide the LLM

Sources with ``direct_ical`` or ``direct_iframe`` strategies can fall back to
the existing deterministic scraping in *stage2_scraping.py* when no LLM is
available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CalendarEngine(str, Enum):
    """The booking/calendar platform a club's site uses."""

    OUTLOOK_IFRAME = "outlook_iframe"
    """Outlook Web Calendar rendered inside an iframe (Kongsberg)."""

    DATE_PARAM = "date_param"
    """Plain web page navigable via ?date=YYYY-MM-DD (Skien brp.exigo.no)."""

    TEAMUP_ICAL = "teamup_ical"
    """Teamup iCal feed — deterministic, no browser needed."""

    FORUMBOOKING = "forumbooking"
    """Forumbooking HTML schema viewer with JS month navigation (Jar)."""

    BOOKUP_SPA = "bookup_spa"
    """BookUp SPA with JS-rendered booking widget (Tønsberg, Sandefjord)."""

    SPORTELLO = "sportello"
    """Sportello booking widget SPA (Holmen)."""

    STYLED_CALENDAR = "styled_calendar"
    """StyledCalendar JS widget (Jutul / Bærum ishall)."""

    GENERIC_ICAL = "generic_ical"
    """Any other deterministic iCal feed."""


@dataclass
class ScraperStrategy:
    """Config describing how to scrape a club's calendar.

    Attributes
    ----------
    engine:
        The calendar system type.
    url:
        The base URL of the calendar.
    has_iframe:
        Whether the calendar content lives inside an ``<iframe>``.
    date_param:
        The query parameter name for date navigation (e.g. ``"date"``).
        Empty string means no date parameter.
    month_selector:
        Playwright selector for the "next month" button.
    event_pattern:
        Hint for the LLM about what event data looks like (free text).
    direct_ical_feed:
        If set, an iCal feed URL that can be used instead of browser scraping.
    direct_scraper:
        If true, the existing deterministic scraper can handle this source.
    initial_navigation:
        Optional list of actions to perform before scraping starts.
        Each action is a dict with ``cmd``, ``selector``, ``url``, etc.
    note:
        Free-text notes for the developer.
    """

    engine: CalendarEngine
    url: str
    has_iframe: bool = False
    date_param: str = ""
    month_selector: str = 'button[aria-label*="next month"]'
    event_pattern: str = ""
    direct_ical_feed: str | None = None
    direct_scraper: bool = False
    initial_navigation: list[dict[str, Any]] = field(default_factory=list)
    credential_env_vars: list[str] = field(default_factory=list)
    note: str = ""


# ---------------------------------------------------------------------------
# Strategy definitions per club
# ---------------------------------------------------------------------------

STRATEGIES: dict[str, ScraperStrategy] = {
    "Kongsberg": ScraperStrategy(
        engine=CalendarEngine.OUTLOOK_IFRAME,
        url="https://kongsberghallen.no/webkalender/ishall/",
        has_iframe=True,
        month_selector='button[aria-label*="next month"]',
        event_pattern="Outlook Web Calendar aria-label attributes",
        direct_scraper=True,
        note="Works with existing iframe-based deterministic scraper.",
    ),
    "Kongsberg ballhall": ScraperStrategy(
        engine=CalendarEngine.OUTLOOK_IFRAME,
        url="https://kongsberghallen.no/webkalender/ballhall-dagtid-og-helg/",
        has_iframe=True,
        month_selector='button[aria-label*="next month"]',
        event_pattern="Outlook Web Calendar aria-label attributes",
        direct_scraper=True,
        note="Works with existing iframe-based deterministic scraper.",
    ),
    "Skien": ScraperStrategy(
        engine=CalendarEngine.DATE_PARAM,
        url="https://skienfritidspark.brp.exigo.no/ishallen",
        date_param="date",
        month_selector="",
        event_pattern="BRP/Exigo Next.js page with embedded daily events JSON and ?date=YYYY-MM-DD navigation",
        direct_scraper=True,
        note="BRP/Exigo daily booking page. Scrape each day because the page does not expose a full month at once.",
    ),
    "Ringerike": ScraperStrategy(
        engine=CalendarEngine.TEAMUP_ICAL,
        url="https://ics.teamup.com/feed/ksr8bg1tpn5s3npskw/0.ics",
        direct_ical_feed="https://ics.teamup.com/feed/ksr8bg1tpn5s3npskw/0.ics",
        direct_scraper=True,
        note="Deterministic iCal feed — already integrated.",
    ),
    "Tønsberg": ScraperStrategy(
        engine=CalendarEngine.BOOKUP_SPA,
        url="https://www.bookup.no/utleie/Index/860#___/view:item/id:860/part:/r:8/mod:book",
        has_iframe=True,
        date_param="",
        month_selector=".fc-next-button",
        event_pattern="FullCalendar timeGrid fc-bgevent bookings in iframe",
        initial_navigation=[
            {"cmd": "goto", "url": "https://www.bookup.no/utleie/Index/860#___/view:item/id:860/part:/r:8/mod:book", "wait_ms": 3000},
            {"cmd": "click", "selector": "a:has-text('logge inn')", "wait_ms": 3000},
            {"cmd": "type", "selector": "#email", "text": "${BOOKUP_EMAIL}", "wait_ms": 1000},
            {"cmd": "click", "selector": "button:has-text('Fortsett')", "wait_ms": 3000},
            {"cmd": "type", "selector": "#password", "text": "${BOOKUP_PASSWORD}", "wait_ms": 1000},
            {"cmd": "click", "selector": "button:has-text('Fortsett')", "wait_ms": 5000},
            {"cmd": "goto", "url": "https://www.bookup.no/utleie/Index/860#___/view:item/id:860/part:/r:8/mod:book", "wait_ms": 3000},
            {"cmd": "click", "selector": "text=Se tilgjengelighet", "wait_ms": 5000},
        ],
        credential_env_vars=["BOOKUP_EMAIL", "BOOKUP_PASSWORD"],
        direct_scraper=True,
        note="Bookup SPA — Tønsberg ishall krever BOOKUP_EMAIL/BOOKUP_PASSWORD for full kalender.",
    ),
    "Sandefjord Penguins": ScraperStrategy(
        engine=CalendarEngine.BOOKUP_SPA,
        url="https://www.bookup.no/Utleie/#Bug%C3%A5rdshallen___/view:item/id:4497/part:/place:3907:SANDEFJORD/q:sandefjord/r:31/mod:book",
        has_iframe=True,
        date_param="",
        month_selector=".fc-next-button",
        event_pattern="FullCalendar timeGrid fc-bgevent bookings in iframe",
        initial_navigation=[
            {"cmd": "goto", "url": "https://www.bookup.no/Utleie/#Bug%C3%A5rdshallen___/view:item/id:4497/part:/place:3907:SANDEFJORD/q:sandefjord/r:31/mod:book", "wait_ms": 3000},
            {"cmd": "click", "selector": "a:has-text('logge inn')", "wait_ms": 3000},
            {"cmd": "type", "selector": "#email", "text": "${BOOKUP_EMAIL}", "wait_ms": 1000},
            {"cmd": "click", "selector": "button:has-text('Fortsett')", "wait_ms": 3000},
            {"cmd": "type", "selector": "#password", "text": "${BOOKUP_PASSWORD}", "wait_ms": 1000},
            {"cmd": "click", "selector": "button:has-text('Fortsett')", "wait_ms": 5000},
            {"cmd": "goto", "url": "https://www.bookup.no/Utleie/#Bug%C3%A5rdshallen___/view:item/id:4497/part:/place:3907:SANDEFJORD/q:sandefjord/r:31/mod:book", "wait_ms": 3000},
            {"cmd": "click", "selector": "text=Se tilgjengelighet", "wait_ms": 5000},
        ],
        credential_env_vars=["BOOKUP_EMAIL", "BOOKUP_PASSWORD"],
        direct_scraper=True,
        note="Bookup SPA — Index/4497 = Bugårdshallen. BOOKUP_EMAIL/BOOKUP_PASSWORD hvis innlogging kreves.",
    ),
    "Jar": ScraperStrategy(
        engine=CalendarEngine.FORUMBOOKING,
        url="https://www.forumbooking.no/schema.aspx?obj=2&schema=Jarhallen%20(ishall)&kalender=true&safarifix=true",
        has_iframe=False,
        month_selector="#lbnNext",
        event_pattern="Forumbooking weekly schedule div.bokning elements with YYYYMMDD ids and tooltip time/customer text",
        direct_scraper=True,
        note="Forumbooking HTML schema viewer scraped directly week-by-week.",
    ),
    "Holmen": ScraperStrategy(
        engine=CalendarEngine.SPORTELLO,
        url="https://kalender.sportello.no/booking/11055",
        has_iframe=False,
        month_selector="",
        event_pattern="",
        direct_scraper=True,
        note="Sportello SPA booking widget. Public GraphQL API is scraped deterministically.",
    ),
    "Jutul": ScraperStrategy(
        engine=CalendarEngine.STYLED_CALENDAR,
        url="https://baerumishall.no/kalender/",
        has_iframe=False,
        month_selector="",
        event_pattern="",
        note="StyledCalendar JS widget. Pi's model navigates the embedded calendar.",
    ),
    "Frisk Asker": ScraperStrategy(
        engine=CalendarEngine.TEAMUP_ICAL,
        url="https://teamup.com/ksdwpwxysmxwnuftoy",
        direct_ical_feed="https://ics.teamup.com/feed/ksdwpwxysmxwnuftoy/0.ics",
        direct_scraper=False,
        note="Teamup page — check if iCal feed URL pattern works. If not, use browser.",
    ),
}


def get_strategy(club_name: str) -> ScraperStrategy | None:
    """Look up the scraper strategy for a club by name."""
    return STRATEGIES.get(club_name)


def has_direct_scraper(strategy: ScraperStrategy) -> bool:
    """Whether this strategy can be handled by the existing deterministic scraper."""
    return strategy.direct_scraper or strategy.direct_ical_feed is not None


def requires_credentials(strategy: ScraperStrategy) -> bool:
    """Whether this strategy needs environment-variable credentials for scraping."""
    return len(strategy.credential_env_vars) > 0


def needs_llm_agent(strategy: ScraperStrategy) -> bool:
    """Whether this strategy requires the Pi-driven LLM agent to scrape."""
    return not has_direct_scraper(strategy)


def get_deterministic_scraper_type(strategy: ScraperStrategy) -> str | None:
    """Return a string identifier for the direct (non-LLM) scraper type, or None.

    Maps ``strategy.engine`` to the scraper type string used by the pipeline
    dispatch layer:

    * ``STYLED_CALENDAR``          → ``"styledcalendar"``
    * ``BOOKUP_SPA``               → ``"bookup"``
    * ``OUTLOOK_IFRAME``           → ``"browser"``
    * ``DATE_PARAM``               → ``"brp_exigo"``
    * ``FORUMBOOKING``             → ``"forumbooking"``
    * ``SPORTELLO``                → ``"sportello"``
    * ``TEAMUP_ICAL``              → ``"ical"``
    * ``GENERIC_ICAL``             → ``"ical"``

    Returns ``None`` for engines that have no direct scraper (i.e. those that
    require the LLM agent).
    """
    _MAP: dict[CalendarEngine, str] = {
        CalendarEngine.STYLED_CALENDAR: "styledcalendar",
        CalendarEngine.BOOKUP_SPA: "bookup",
        CalendarEngine.OUTLOOK_IFRAME: "browser",
        CalendarEngine.DATE_PARAM: "brp_exigo",
        CalendarEngine.FORUMBOOKING: "forumbooking",
        CalendarEngine.SPORTELLO: "sportello",
        CalendarEngine.TEAMUP_ICAL: "ical",
        CalendarEngine.GENERIC_ICAL: "ical",
    }
    return _MAP.get(strategy.engine)


def strategy_to_dict(strategy: ScraperStrategy) -> dict[str, Any]:
    """Serialize a :class:`ScraperStrategy` to a JSON-safe dict.

    Includes ``initial_navigation`` steps for the Pi ScraperAgent.
    """
    return {
        "engine": strategy.engine.value,
        "url": strategy.url,
        "has_iframe": strategy.has_iframe,
        "date_param": strategy.date_param,
        "month_selector": strategy.month_selector,
        "event_pattern": strategy.event_pattern,
        "direct_ical_feed": strategy.direct_ical_feed,
        "direct_scraper": strategy.direct_scraper,
        "initial_navigation": strategy.initial_navigation,
        "credential_env_vars": strategy.credential_env_vars,
        "requires_credentials": requires_credentials(strategy),
        "note": strategy.note,
    }


def list_strategies() -> dict[str, dict[str, Any]]:
    """Return a JSON-serialisable summary of all strategies."""
    return {
        name: {
            "engine": s.engine.value,
            "direct_scraper": s.direct_scraper,
            "direct_ical_feed": bool(s.direct_ical_feed),
            "has_iframe": s.has_iframe,
            "date_param": bool(s.date_param),
            "requires_credentials": requires_credentials(s),
            "credential_env_vars": s.credential_env_vars,
        }
        for name, s in STRATEGIES.items()
    }


# ---------------------------------------------------------------------------
# CLI entry point — dumps strategy JSON for the Pi ScraperAgent
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="Eksporter scraper-strategi som JSON for Pi ScraperAgent"
    )
    parser.add_argument(
        "--name", type=str, default="",
        help="Klubbnavn (f.eks. 'Tønsberg', 'Sandefjord Penguins')"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Eksporter alle strategier"
    )
    args = parser.parse_args()

    if args.all:
        result = {
            name: strategy_to_dict(s)
            for name, s in STRATEGIES.items()
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit(0)

    if args.name:
        strategy = get_strategy(args.name)
        if not strategy:
            print(f"Ukjent klubb: '{args.name}'. Kjente: {', '.join(STRATEGIES.keys())}",
                  file=sys.stderr)
            sys.exit(1)
        print(json.dumps(strategy_to_dict(strategy), indent=2, ensure_ascii=False))
        sys.exit(0)

    parser.print_help()
    sys.exit(0)
