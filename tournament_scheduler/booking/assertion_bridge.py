"""Calendar baseline and pin for manual booking assertions (#860).

A manual assertion is a temporary bridge to the calendar unless it is pinned.
When one is made it records the calendar event it was made against, as inert
evidence: it never changes what the assertion means or authorizes supersession.
"""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.calendar_bookings import (
    club_calendar_fingerprint,
    club_calendar_positive_evidence_usable,
    iter_events,
)
from tournament_scheduler.booking.interval_facts import (
    _parse_hhmm,
    event_fingerprint,
    tournament_occupancy_interval_facts,
)


def validate_pin(pin: bool, pin_reason: str | None) -> str | None:
    """Return the normalized pin reason; a pin needs one and a reason needs a pin."""

    reason = str(pin_reason or "").strip() or None
    if pin and not reason:
        raise ValueError("Pinning a manual booking assertion requires --pin-reason")
    if reason and not pin:
        raise ValueError("--pin-reason requires --pin")
    return reason


def calendar_baseline_for_tournament(
    tournament: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the one trusted calendar event overlapping the tournament, else ``None``.

    The baseline records what the calendar showed when a manual assertion was
    made. It is an observation, not an event identity: the fingerprint covers
    the interval, so an edited event gets a different fingerprint. Zero, several
    or untrusted events yield ``None`` rather than a guess.
    """

    club = str(tournament.get("host_club") or "")
    if not club or not club_calendar_positive_evidence_usable(problem, club):
        return None
    interval = tournament_occupancy_interval_facts(tournament, problem)
    start, end = _parse_hhmm(interval["start_time"]), _parse_hhmm(interval["end_time"])
    if start is None or end is None:
        return None
    matches = []
    for event in iter_events(problem):
        if str(event.get("club") or "") != club or str(event.get("date") or "") != interval["date"]:
            continue
        event_start, event_end = _parse_hhmm(event.get("start")), _parse_hhmm(event.get("end"))
        if event_start is not None and event_end is not None and start < event_end and event_start < end:
            matches.append(event)
    if len(matches) != 1:
        return None
    event = matches[0]
    return {
        "event_fingerprint": str(event.get("fingerprint") or event_fingerprint(event)),
        "date": str(event.get("date") or ""),
        "start": str(event.get("start") or ""),
        "end": str(event.get("end") or ""),
        "resources": sorted(str(label) for label in event.get("resources") or ()),
        "calendar_fingerprint": club_calendar_fingerprint(problem, club),
        "source_calendar_status": str(((problem or {}).get("club_calendar_status") or {}).get(club) or ""),
    }


def bridge_fields(
    tournament: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
    pin_reason: str | None,
) -> dict[str, Any]:
    """Return the baseline (``None`` when unknown) and, only when pinned, the pin keys."""

    return {
        "calendar_baseline": calendar_baseline_for_tournament(tournament, problem),
        **({"pinned": True, "pin_reason": pin_reason} if pin_reason else {}),
    }
