"""Supersede a manual booking assertion that an operator attests a calendar confirmation replaces."""

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
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError

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
    supersede_manual_assertion_id: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Write a calendar association and, when the operator names one, supersede that manual positive.

    Supersession needs an explicit ``supersede_manual_assertion_id`` because no record links a
    manual assertion to a calendar event, so interval changes alone cannot prove the same booking.
    The operator's note is the audited attestation. Both writes land in the same returned
    decisions mapping, so the association and the supersession commit together or not at all.
    """

    updated = dict(decisions)
    updated[CALENDAR_BOOKING_ASSOCIATIONS_KEY] = records
    if not supersede_manual_assertion_id:
        return updated, []
    tournament_id = str(tournament.get("id") or "")
    manual_records = [
        dict(record)
        for record in updated.get(MANUAL_BOOKING_ASSERTIONS_KEY) or []
        if isinstance(record, Mapping)
    ]
    active = [
        record
        for record in manual_records
        if str(record.get("tournament_id") or "") == tournament_id
        and str(record.get("status") or "") == MANUAL_ASSERTION_ACTIVE
    ]
    # A negative or conflicting active assertion is independent club evidence. It is never
    # superseded by a calendar correction, so the operator must resolve it first.
    conflicting = [
        str(record.get("id") or "")
        for record in active
        if manual_assertion_projection_status(record) != BOOKING_MANUALLY_BOOKED
    ]
    if conflicting:
        raise SeasonStateError(
            f"Tournament {tournament_id} has active non-positive manual assertions {conflicting}; "
            "resolve them before superseding a positive assertion"
        )
    target = next(
        (record for record in active if str(record.get("id") or "") == supersede_manual_assertion_id),
        None,
    )
    if target is None:
        raise SeasonStateError(
            f"Manual booking assertion {supersede_manual_assertion_id!r} is not active for tournament {tournament_id}"
        )
    if str(target.get("source_scope") or "") != "tournament":
        raise SeasonStateError(f"Manual booking assertion {supersede_manual_assertion_id!r} is not tournament-scoped")
    if not str(association.get("note") or "").strip():
        raise SeasonStateError("Superseding a manual booking assertion requires a --note attestation")
    reasons = manual_assertion_stale_reasons(target, problem=problem, tournament=tournament)
    # A changed fact other than the interval may mean two different bookings. Refuse rather than
    # supersede evidence that the calendar correction does not explain.
    if not set(reasons) <= _INTERVAL_STALE_REASONS:
        raise SeasonStateError(
            f"Manual booking assertion {supersede_manual_assertion_id!r} also changed {sorted(set(reasons) - _INTERVAL_STALE_REASONS)}; "
            "it is not superseded"
        )
    target["status"] = MANUAL_ASSERTION_SUPERSEDED
    target["superseded_at"] = now
    target["superseded_by"] = str(association.get("id") or "")
    target["supersede_reason"] = str(association.get("note") or "").strip()
    target["superseded_stale_reasons"] = list(reasons)
    updated[MANUAL_BOOKING_ASSERTIONS_KEY] = manual_records
    return updated, [str(target.get("id") or "")]
