"""Event serialisation and grouping helpers for Stage 2 scraping.

Provides :func:`_events_to_dicts` for serialising :class:`CalendarEvent` objects
to plain JSON-compatible dicts, and :func:`_group_events_by_club` for mapping
per-source results to RVV club names.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..club_registry import club_for_source_name
from ..models import CalendarEvent

# Frisk Asker's TeamUp feed uses room/surface names rather than arena names.
# "Idrettshallen" (and variants) is Askerhallen; numbered surfaces and "FA"-prefixed
# rooms belong to Varner Arena. Away-game entries ("FA 1 - Opponent 5") are also at
# Varner Arena. Locker-room-only entries ("Jentegarderoben" etc.) are not classified.
_FRISK_ASKER_ASKERHALLEN_MARKERS = {"idrettshallen"}
_FRISK_ASKER_VARNER_MARKERS = {"1", "2", "4", "5"}  # standalone ice-surface numbers


def _classify_frisk_asker_arena(location: str) -> str | None:
    """Return 'Askerhallen', 'Varner Arena', or None if unclassifiable."""
    loc = location.strip().lower()
    if not loc:
        return None
    if any(m in loc for m in _FRISK_ASKER_ASKERHALLEN_MARKERS):
        return "Askerhallen"
    # Standalone surface numbers like "1", "2", "4 og 5", "1 og 2"
    surfaces = {p.strip() for p in loc.replace(" og ", " ").split()}
    if surfaces and surfaces <= _FRISK_ASKER_VARNER_MARKERS:
        return "Varner Arena"
    # "fa 1", "fa 1 - opponent 5", "fa jentegarderoben ..." → home rooms at Varner Arena
    if loc.startswith("fa "):
        return "Varner Arena"
    return None


def _events_to_dicts(
    events: list[CalendarEvent],
    club_name: str | None = None,
) -> list[dict[str, Any]]:
    """Serialise :class:`CalendarEvent` objects to plain dicts for JSON output."""
    result = []
    for e in events:
        d: dict[str, Any] = {
            "date": e.date,
            "name": e.name,
            "datetime": e.datetime.isoformat(),
            "duration_hours": e.duration_hours,
        }
        if e.location:
            d["location"] = e.location
            if club_name == "Frisk Asker":
                arena = _classify_frisk_asker_arena(e.location)
                if arena:
                    d["arena"] = arena
        result.append(d)
    return result


def _event_date_from_dict(event: dict[str, Any]) -> date | None:
    """Extract a date from a serialized event dict."""
    raw_value = event.get("date") or event.get("datetime")
    if raw_value is None:
        return None
    if isinstance(raw_value, datetime):
        return raw_value.date()
    if isinstance(raw_value, date):
        return raw_value
    if isinstance(raw_value, str):
        try:
            return date.fromisoformat(raw_value[:10])
        except ValueError:
            try:
                return datetime.fromisoformat(raw_value).date()
            except ValueError:
                return None
    return None


def _scraped_date_range(source_results: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Return the min/max event date seen across a list of source results."""
    dates: list[date] = []
    for source_result in source_results:
        for event in source_result.get("events", []):
            event_date = _event_date_from_dict(event)
            if event_date is not None:
                dates.append(event_date)
    if not dates:
        return None, None
    return min(dates).isoformat(), max(dates).isoformat()


def _group_events_by_club(
    source_results: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group already-serialised source events by RVV club name.

    Each entry in *source_results* has a ``"name"`` (the Stage 1 source name,
    e.g. ``"Kongsberg ishall"`` or ``"Frisk Asker"``) and an ``"events"`` list
    of event dicts (as produced by :func:`_events_to_dicts`). This maps each
    source's events to the matching :data:`CLUB_REGISTRY` club name (via
    :func:`club_for_source_name`) so downstream code can look up
    "all events for Frisk Asker's Varner Arena" without re-filtering the flat
    per-source list.

    Sources that don't match any known club (or carry no events) are simply
    omitted -- existing flat-list (``"sources"``) consumers are unaffected.
    """
    by_club: dict[str, list[dict[str, Any]]] = {}
    for source_result in source_results:
        source_name = source_result.get("name", "")
        club_name = club_for_source_name(source_name)
        if club_name is None:
            continue
        events = source_result.get("events", [])
        if not events:
            continue
        by_club.setdefault(club_name, []).extend(events)
    return by_club


def _group_club_calendar_status(
    source_results: list[dict[str, Any]],
) -> dict[str, str]:
    """Map each RVV club with a configured source to a calendar-evidence status.

    ``"known"`` means the source completed this run without being blocked,
    skipped, or erroring -- including a source that genuinely came back with
    zero events (a real "known free" result), and fixed-allocation sources
    (e.g. Sandefjord, issue #261) whose availability is deterministic rather
    than scraped.

    ``"unknown"`` means we have no trustworthy evidence for that club this
    run (blocked, skipped, or a scraper error). Callers (slot search,
    candidate verification) must treat ``"unknown"`` as *not* available --
    conflating a missing/blocked scrape with "entire window is free" is the
    root cause of issue #262's Tønsberg over-scheduling bug.

    A club can have multiple source results (rare, but possible for
    multi-arena clubs); if any of them is unknown, the whole club is treated
    as unknown -- partial evidence is not sufficient evidence.
    """
    status: dict[str, str] = {}
    for source_result in source_results:
        source_name = source_result.get("name", "")
        club_name = club_for_source_name(source_name)
        if club_name is None:
            continue
        is_unknown = bool(
            source_result.get("blocked")
            or source_result.get("skipped")
            or source_result.get("scraper_error")
        )
        if status.get(club_name) == "unknown":
            continue
        status[club_name] = "unknown" if is_unknown else "known"
    return status
