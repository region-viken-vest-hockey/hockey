"""Deterministic "fixed ice allocation" pseudo-scraper (issue #261).

Some clubs are not scraped through BookUp or any other live calendar because
their availability is already a known, deterministic fact instead of
something that needs discovering — e.g. Sandefjord Penguins' fixed weekend
ice-time allocation (see `tournament_scheduler.sandefjord_allocation`).

This module is Stage 2's dispatch target for the ``"fixed_allocation"``
source type (`scraper_constants.SOURCE_FIXED_ALLOCATION`): given a source
name, it looks up the matching generator and returns busy `CalendarEvent`s
for the requested date range exactly like any other deterministic scraper —
no network access, no credentials, no blocked/recovery path — so the rest of
the Stage 2 -> Stage 3 pipeline treats it identically to a scraped source.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, List

from ..club_registry import club_for_source_name, get_club
from ..models import CalendarEvent
from ..sandefjord_allocation import SANDEFJORD_CLUB_NAME, sandefjord_fixed_busy_events

_FIXED_ALLOCATION_GENERATORS: Dict[str, Callable[[datetime, datetime], List[CalendarEvent]]] = {
    SANDEFJORD_CLUB_NAME: sandefjord_fixed_busy_events,
}


def _resolve_club_name(name: str) -> str:
    """Resolve a ``"Kilder"`` sheet source name to its canonical RVV club name.

    Tries the same source-name resolution every other source uses
    (`club_registry.club_for_source_name`) first, then falls back to
    `club_registry.get_club`'s short-name alias table (e.g. ``"Sandefjord"``
    -> ``"Sandefjord Penguins"``), since a fixed-allocation source's name may
    be an alias rather than the exact registry key or a prefix of it.
    """
    resolved = club_for_source_name(name)
    if resolved is not None:
        return resolved
    try:
        return get_club(name).club
    except KeyError:
        return name


def run_fixed_allocation_source(
    name: str, start_date: datetime, end_date: datetime
) -> List[CalendarEvent]:
    """Return the deterministic busy events registered for *name*.

    *name* is the ``"Kilder"`` sheet source name (e.g. ``"Sandefjord
    Penguins"`` or the ``"Sandefjord"`` alias), resolved to its canonical
    RVV club name. Returns an empty list if no generator is registered for
    that club.
    """
    club_name = _resolve_club_name(name)
    generator = _FIXED_ALLOCATION_GENERATORS.get(club_name)
    if generator is None:
        return []
    return generator(start_date, end_date)
