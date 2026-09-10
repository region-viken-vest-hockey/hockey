"""iCal scraper wrapper for Stage 2."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..models import CalendarEvent
from ..utils.calendar_cache import CalendarCache


def _run_ical_scraper(
    url: str,
    name: str,
    start_date: datetime,
    end_date: datetime,
    source_type: str,
    cache: CalendarCache | None = None,
    location_filter: Optional[str] = None,
) -> list[CalendarEvent]:
    """Run the iCal scraper for ``ical`` and ``google`` source types.

    Both use :class:`ICalScraper` (HTTP-fetched iCal feeds). The ``url`` may
    be a full feed URL (https://ics.teamup.com/...) or a Google Calendar
    email-style ID (``name@gmail.com``) which ``ICalScraper`` expands into the
    public Google Calendar iCal feed URL automatically.

    When *location_filter* is provided, only events whose LOCATION field
    contains the filter string (case-insensitive) are returned.
    """
    from ..data_sources.ical_scraper import ICalScraper

    scraper = ICalScraper(url, cache=cache)
    return scraper.scrape_calendar(url, name, start_date, end_date, location_filter=location_filter)
