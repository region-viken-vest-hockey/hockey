"""Release operative booking authority on cancellation; retain source history."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.booking_vocabulary import (
    ACTIVE,
    CALENDAR_ASSOCIATION_SUPERSEDED,
    CALENDAR_BOOKING_ASSOCIATIONS_KEY,
    MANUAL_ASSERTION_ACTIVE,
    MANUAL_ASSERTION_SUPERSEDED,
    MANUAL_BOOKING_ASSERTIONS_KEY,
    TOURNAMENT_CANCELLED_RELEASE_REASON,
)


def _cancelled_tournament_ids(plan: Mapping[str, Any] | None) -> set[str]:
    return {
        str(tournament.get("id") or "")
        for tournament in (plan or {}).get("tournaments", []) or []
        if tournament.get("cancelled")
    } - {""}


def release_booking_authority_for_new_cancellations(
    decisions: Mapping[str, Any],
    *,
    before_plan: Mapping[str, Any] | None,
    after_plan: Mapping[str, Any] | None,
    now: str,
    operation: str,
) -> dict[str, Any]:
    """Return ``decisions`` with booking authority released for new cancellations.

    Only tournaments cancelled in ``after_plan`` but not in ``before_plan`` are
    affected.  Their ``active`` manual assertions and calendar associations move
    to ``superseded`` with the cancellation as the reason, and each record keeps
    its provenance.  A replay finds nothing left to release.  The raw calendar
    event is never touched: a cancelled tournament no longer operates, but the
    event may be retained ice that still blocks other tournaments, so only the
    link from that event to this tournament is released.  The input is not
    mutated; the returned mapping replaces only the changed lists.
    """

    cancelled = _cancelled_tournament_ids(after_plan) - _cancelled_tournament_ids(before_plan)
    released: dict[str, Any] = dict(decisions)
    if not cancelled:
        return released

    def _release(key: str, active_status: str, released_status: str) -> None:
        records = [dict(record) for record in decisions.get(key) or [] if isinstance(record, Mapping)]
        changed = False
        for record in records:
            if str(record.get("tournament_id") or "") not in cancelled:
                continue
            if str(record.get("status") or active_status) != active_status:
                continue
            record["status"] = released_status
            record["superseded_at"] = now
            record["supersede_reason"] = TOURNAMENT_CANCELLED_RELEASE_REASON
            record["superseded_by_operation"] = operation
            changed = True
        if changed:
            released[key] = records

    _release(MANUAL_BOOKING_ASSERTIONS_KEY, MANUAL_ASSERTION_ACTIVE, MANUAL_ASSERTION_SUPERSEDED)
    _release(CALENDAR_BOOKING_ASSOCIATIONS_KEY, ACTIVE, CALENDAR_ASSOCIATION_SUPERSEDED)
    return released
