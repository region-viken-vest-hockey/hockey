"""Keep a repeat calendar confirmation's original association record."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.calendar_bookings import CALENDAR_BOOKING_ASSOCIATIONS_KEY

_PROVENANCE_KEYS = ("source_revision", "note", "created_at", "created_by")
_LIFECYCLE_KEYS = ("status",)


def _matches(record: Any, *, event_fingerprint: str, tournament_id: str) -> bool:
    return (
        isinstance(record, Mapping)
        and str(record.get("event_fingerprint") or "") == event_fingerprint
        and str(record.get("tournament_id") or "") == tournament_id
    )


def _same_facts(previous: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    """True when every non-provenance field of the candidate matches the stored record."""
    ignored = set(_PROVENANCE_KEYS) | set(_LIFECYCLE_KEYS)
    return all(previous.get(key) == value for key, value in candidate.items() if key not in ignored)


def retain_repeat_association(
    decisions: Mapping[str, Any],
    *,
    event_fingerprint: str,
    tournament_id: str,
    association: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the association to store and the association list it lands in.

    A repeat confirmation that restates the same booking facts and interval keeps the
    original record's provenance (source revision, note, creator and time) and its list
    position, so repeated confirmation neither rewrites the record nor churns the
    canonical revision. Any other confirmation replaces the matching records as before.
    """
    existing = list(decisions.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or [])
    previous = next(
        (
            record
            for record in existing
            if _matches(record, event_fingerprint=event_fingerprint, tournament_id=tournament_id)
            and str(record.get("status") or "active") == "active"
        ),
        None,
    )
    stored = dict(association)
    if previous is not None and _same_facts(previous, stored):
        stored.update({key: previous[key] for key in _PROVENANCE_KEYS if key in previous})
    position = next(
        (
            index
            for index, record in enumerate(existing)
            if _matches(record, event_fingerprint=event_fingerprint, tournament_id=tournament_id)
        ),
        len(existing),
    )
    records = [
        dict(record)
        for record in existing
        if not _matches(record, event_fingerprint=event_fingerprint, tournament_id=tournament_id)
    ]
    if previous is not None:
        # Keep the retained record at its existing position, counting only surviving records.
        records.insert(position, stored)
    else:
        records.append(stored)
    return stored, records
