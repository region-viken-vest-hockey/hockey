"""Canonical interval identity and tournament occupancy facts for calendar booking.

These helpers define how one normalized calendar event is identified and how a
planned tournament's occupied interval is derived. Booking assessment and the
association overlay both depend on them, so they live below both.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256


def event_fingerprint(event: Mapping[str, Any]) -> str:
    """Return the stable identity for one normalized calendar interval."""

    payload = _event_fingerprint_payload(event)
    return stable_payload_sha256(payload)


def _event_fingerprint_payload(event: Mapping[str, Any]) -> dict[str, str]:
    payload = {
        "club": str(event.get("club") or ""),
        "date": str(event.get("date") or ""),
        "start": str(event.get("start") or ""),
        "end": str(event.get("end") or ""),
        "calendar_event": str(event.get("calendar_event") or event.get("title") or ""),
        "availability": str(event.get("availability") or ""),
    }
    # Two same-titled bookings on different resources are separate occupancies.
    # Only resource-reporting sources add this key, so other clubs keep their
    # existing fingerprints.
    resources = sorted(str(label) for label in event.get("resources") or ())
    if resources:
        payload["resources"] = ",".join(resources)
    # Preserve source booking order_id when present; it distinguishes
    # same-titled bookings on the same resources from different orders.
    order_id = str(event.get("order_id") or "")
    if order_id:
        payload["order_id"] = order_id
    return payload


def _parse_hhmm(value: Any) -> int | None:
    try:
        hour, minute = (int(part) for part in str(value or "").split(":", 1))
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def _format_hhmm(minutes: int) -> str:
    value = datetime(2000, 1, 1) + timedelta(minutes=minutes)
    return value.strftime("%H:%M")


def _game_round_count(tournament: Mapping[str, Any]) -> int:
    rounds: list[int] = []
    for game in tournament.get("games") or []:
        if isinstance(game, Mapping):
            try:
                rounds.append(int(game.get("round_number") or 0))
            except (TypeError, ValueError):
                pass
    return max(rounds, default=0)


def tournament_occupancy_interval_facts(
    tournament: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Return the canonical occupied interval facts for one tournament.

    A host-confirmed per-tournament override projected into the problem replaces
    the age-group default, so ``confirm-calendar-booking`` coverage and the
    arena-interval verifier describe the same booked window.
    """

    # Local import breaks the season.state <-> calendar_bookings import
    # cycle; the projection helper itself is the single key definition.
    from tournament_scheduler.season.ice_time_overrides import overrides_from_problem

    age_group = str(tournament.get("age_group") or "")
    start_time = str(tournament.get("start_time") or "")
    override = overrides_from_problem(problem).get(str(tournament.get("id") or ""))
    duration = 0
    if override:
        duration = int(override)
    else:
        ice_time = (problem or {}).get("ice_time_minutes") or {}
        if isinstance(ice_time, Mapping):
            try:
                duration = int((ice_time.get(age_group) or 0) or 0)
            except (TypeError, ValueError):
                duration = 0
    start_minutes = _parse_hhmm(start_time)
    end_time = _format_hhmm(start_minutes + duration) if start_minutes is not None and duration > 0 else ""
    return {
        "date": str(tournament.get("date") or ""),
        "start_time": start_time,
        "duration_minutes": str(duration),
        "end_time": end_time,
        "age_group": age_group,
        "round_count": str(_game_round_count(tournament)),
    }


def event_covers_tournament_interval(
    event: Mapping[str, Any],
    tournament: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
) -> bool:
    """Return whether the scraped event covers the canonical occupied interval."""

    interval = tournament_occupancy_interval_facts(tournament, problem)
    if str(event.get("date") or "") != interval["date"]:
        return False
    event_start = _parse_hhmm(event.get("start"))
    event_end = _parse_hhmm(event.get("end"))
    tournament_start = _parse_hhmm(interval["start_time"])
    tournament_end = _parse_hhmm(interval["end_time"])
    if None in (event_start, event_end, tournament_start, tournament_end):
        return False
    return event_start <= tournament_start and tournament_end <= event_end
