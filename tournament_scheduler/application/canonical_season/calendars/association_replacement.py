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

    Only an active association for the same tournament, on a different
    fingerprint, whose event is no longer on the calendar and which shares the
    new event's date and club, is an edited version of the same booking. Anything
    else is a different booking and needs an explicit operator resolution.
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
    if str(replaced.get("date") or "") != str(event.get("date") or "") or str(replaced.get("club") or "") != str(event.get("club") or ""):
        raise SeasonStateError(
            f"Tournament {tournament_id} active calendar association {replaced.get('id')} is on a different "
            "date or club from the confirmed event; confirm that booking explicitly instead of replacing it"
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
