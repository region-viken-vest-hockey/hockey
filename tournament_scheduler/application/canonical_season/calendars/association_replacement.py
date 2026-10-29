"""Replace an established calendar association when its event is edited (#869).

A confirmed calendar event that is edited in the calendar gets a new
fingerprint. Confirming the new fingerprint for the same tournament booking
retires the old association as superseded history, so exactly one association
stays active for the tournament. A different event that is still on the
calendar is a genuinely different booking, so the replacement is refused.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from tournament_scheduler.booking.calendar_bookings import (
    ACTIVE,
    CALENDAR_ASSOCIATION_SUPERSEDED,
    active_associations,
    iter_events,
)
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError


def replaced_association_ids(
    decisions: Mapping[str, Any],
    *,
    tournament_id: str,
    event: Mapping[str, Any],
    base_problem: Mapping[str, Any] | None,
) -> list[str]:
    """Return the active associations this confirmation replaces, or refuse.

    An edit is the only case that replaces. The stored event must be gone from
    the calendar, read from trusted evidence, and the new event must keep its
    club, date, start and calendar title, so only the end or availability moved.
    A vanished event is not proof of an edit by itself: a different start or
    title is a different booking. The calendar's source identity is a content
    hash, so these facts are the only continuity evidence available here.
    """

    new_fingerprint = str(event.get("fingerprint") or "")
    prior = [
        record
        for record in active_associations(decisions)
        if str(record.get("tournament_id") or "") == tournament_id
        and str(record.get("event_fingerprint") or "") != new_fingerprint
    ]
    if not prior:
        return []
    live = {str(candidate.get("fingerprint") or "") for candidate in iter_events(base_problem)}
    for record in prior:
        if str(record.get("event_fingerprint") or "") in live:
            raise SeasonStateError(
                f"Tournament {tournament_id} already has an active calendar association "
                f"{record.get('id')} for a different event still on the calendar; "
                "resolve that booking before confirming another event"
            )
    if len(prior) > 1:
        raise SeasonStateError(
            f"Tournament {tournament_id} has several active calendar associations "
            f"{[str(record.get('id') or '') for record in prior]}; resolve them before replacing"
        )
    replaced = prior[0]
    continuity = (
        ("club", str(event.get("club") or "")),
        ("date", str(event.get("date") or "")),
        ("start", str(event.get("start") or "")),
        ("title", str(event.get("calendar_event") or event.get("title") or "")),
    )
    if any(str(replaced.get(key) or "") != value for key, value in continuity):
        raise SeasonStateError(
            f"Tournament {tournament_id} active calendar association {replaced.get('id')} is a different "
            "booking from the confirmed event (club, date, start or title differ); confirm it explicitly instead of replacing"
        )
    return [str(replaced.get("id") or "")]


def retire_replaced_associations(
    decisions: Mapping[str, Any],
    records: Iterable[Mapping[str, Any]],
    *,
    tournament_id: str,
    event: Mapping[str, Any],
    base_problem: Mapping[str, Any] | None,
    superseded_by: str,
    now: str,
) -> list[dict[str, Any]]:
    """Refuse or retire the replaced associations, keeping their facts unchanged."""

    replaced = set(
        replaced_association_ids(decisions, tournament_id=tournament_id, event=event, base_problem=base_problem)
    )
    retired: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        if str(item.get("id") or "") in replaced and str(item.get("status") or ACTIVE) == ACTIVE:
            item["status"] = CALENDAR_ASSOCIATION_SUPERSEDED
            item["superseded_by"] = superseded_by
            item["superseded_at"] = now
        retired.append(item)
    return retired
