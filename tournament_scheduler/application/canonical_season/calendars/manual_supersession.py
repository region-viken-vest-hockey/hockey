"""Supersede provisional manual booking assertions that a calendar confirmation corrects."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.calendar_bookings import (
    BOOKING_MANUALLY_BOOKED,
    CALENDAR_BOOKING_ASSOCIATIONS_KEY,
    MANUAL_ASSERTION_ACTIVE,
    MANUAL_ASSERTION_SUPERSEDED,
    MANUAL_BOOKING_ASSERTIONS_KEY,
    manual_assertion_projection_status,
    manual_assertion_stale_reasons,
)

CALENDAR_INTERVAL_CORRECTION_REASON = "calendar_confirmation_corrected_interval"
_INTERVAL_STALE_REASONS = frozenset(
    f"tournament_{key}_changed" for key in ("date", "start_time", "duration_minutes", "end_time")
)


def record_calendar_association(
    decisions: Mapping[str, Any],
    *,
    records: list[dict[str, Any]],
    association: Mapping[str, Any],
    tournament: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
    now: str,
) -> dict[str, Any]:
    """Write a calendar association and supersede the manual positives it corrects.

    Both writes land in the same returned decisions mapping, so a confirmation
    either supersedes its duplicates together with the new association or not at all.
    """

    updated = dict(decisions)
    updated[CALENDAR_BOOKING_ASSOCIATIONS_KEY] = records
    manual_records = [
        dict(record)
        for record in updated.get(MANUAL_BOOKING_ASSERTIONS_KEY) or []
        if isinstance(record, Mapping)
    ]
    tournament_id = str(tournament.get("id") or "")
    active = [
        record
        for record in manual_records
        if str(record.get("tournament_id") or "") == tournament_id
        and str(record.get("status") or "") == MANUAL_ASSERTION_ACTIVE
    ]
    # A negative or otherwise conflicting active assertion is independent club
    # evidence. It is never revoked by a calendar correction; it stays active and
    # is reported by the booking status projection for review.
    if any(manual_assertion_projection_status(record) != BOOKING_MANUALLY_BOOKED for record in active):
        return updated
    for record in active:
        reasons = manual_assertion_stale_reasons(record, problem=problem, tournament=tournament)
        # Only a positive, tournament-scoped assertion whose sole staleness is the
        # interval the authoritative event just corrected describes the same booking.
        # Any other changed fact is material and stays active for review.
        if not reasons or not set(reasons) <= _INTERVAL_STALE_REASONS:
            continue
        if record.get("source_scope") != "tournament":
            continue
        record["status"] = MANUAL_ASSERTION_SUPERSEDED
        record["superseded_at"] = now
        record["superseded_by"] = str(association.get("id") or "")
        record["supersede_reason"] = CALENDAR_INTERVAL_CORRECTION_REASON
        record["superseded_stale_reasons"] = list(reasons)
    updated[MANUAL_BOOKING_ASSERTIONS_KEY] = manual_records
    return updated
