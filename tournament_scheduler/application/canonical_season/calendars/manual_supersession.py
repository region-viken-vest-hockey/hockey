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

_SUPERSEDABLE_SCOPES = frozenset({"tournament", "club_wide_interpretation"})
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
    association_id = str(association.get("id") or "")
    if not str(association.get("note") or "").strip():
        raise SeasonStateError("Superseding a manual booking assertion requires a --note attestation")
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
    # superseded by a calendar correction, so the operator must resolve it first. These checks
    # run on a retry too, so a later conflict is not silently accepted.
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
    # Several active positives would leave an independent one in force after the named
    # one is superseded, so the operator must resolve them first.
    if len(active) > 1:
        raise SeasonStateError(
            f"Tournament {tournament_id} has multiple active manual booking assertions "
            f"{[str(record.get('id') or '') for record in active]}; resolve all but one before superseding"
        )
    # A retry of this same confirmation finds its assertion already superseded by it, and no other
    # active assertion has appeared since: nothing new to do. Any remaining active record refuses below.
    if not active and any(
        str(record.get("id") or "") == supersede_manual_assertion_id
        and str(record.get("tournament_id") or "") == tournament_id
        and str(record.get("status") or "") == MANUAL_ASSERTION_SUPERSEDED
        and str(record.get("superseded_by") or "") == association_id
        for record in manual_records
    ):
        return updated, []
    target = next(
        (record for record in active if str(record.get("id") or "") == supersede_manual_assertion_id),
        None,
    )
    if target is None:
        raise SeasonStateError(
            f"Manual booking assertion {supersede_manual_assertion_id!r} is not active for tournament {tournament_id}"
        )
    # A club-wide interpretation is one record per tournament under a shared source statement
    # (#490). Superseding it touches only this tournament's record; the source statement and
    # the other tournaments' interpretations stay as they are. A source-level statement is not
    # a per-tournament record, so it is never superseded here.
    if str(target.get("source_scope") or "") not in _SUPERSEDABLE_SCOPES:
        raise SeasonStateError(f"Manual booking assertion {supersede_manual_assertion_id!r} is not tournament-scoped")
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
    target["superseded_by"] = association_id
    target["supersede_reason"] = str(association.get("note") or "").strip()
    target["superseded_stale_reasons"] = list(reasons)
    updated[MANUAL_BOOKING_ASSERTIONS_KEY] = manual_records
    return updated, [str(target.get("id") or "")]
