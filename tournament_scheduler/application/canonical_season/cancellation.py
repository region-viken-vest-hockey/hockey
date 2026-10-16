"""Canonical tournament cancellation as an in-memory plan mutation.

A cancellation is the first-class ``cancelled`` flag on one tournament plus the
release of its active guest reservations. Batch maintenance applies it, and the
booking authority it ends is released after the candidate is accepted (see
``booking.calendar_bookings.release_booking_authority_for_new_cancellations``).
"""

from __future__ import annotations

from typing import Any

from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.shape.guest_slots import release_guest_slots_for_cancellation


def _apply_cancel_to_plan(
    plan: dict[str, Any],
    *,
    tournament_id: str,
    reason: str = "",
    actor: str = "",
    now: str = "",
) -> dict[str, Any]:
    """Mark one tournament cancelled in an in-memory plan.

    Cancellation is represented by the first-class ``cancelled`` flag the
    verifier already honours. The full canonical gates decide whether a batch
    containing a cancellation is acceptable; this helper only applies the
    domain fact.

    A cancelled tournament no longer needs ice or any reserved guest place,
    so any active (``open``/``filled``) guest reservation is released as part
    of the same cancellation instead of blocking it -- the cancellation
    already makes clear nothing further will happen there. Cancelling never
    silently resolves the real-world follow-up this implies: the host
    arena and any released guest-reservation holder still have to be told
    directly, which this returns as ``notifications_required`` so the
    operator/report surfaces it instead of losing it.
    """

    tournaments = plan.get("tournaments", []) or []
    target = next((t for t in tournaments if str(t.get("id")) == tournament_id), None)
    if target is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    if target.get("cancelled"):
        raise SeasonStateError(f"Tournament {tournament_id} is already cancelled")

    released_guest_reservations = release_guest_slots_for_cancellation(
        target,
        now=now,
        actor=actor,
        reason="tournament_cancelled",
    )

    target["cancelled"] = True
    if reason:
        target["cancellation_reason"] = reason
    else:
        target.pop("cancellation_reason", None)

    host_club = str(target.get("host_club") or "")
    arena = str(target.get("arena") or "")
    notifications_required = [
        f"Inform host club {host_club!r} (arena {arena!r}) that the "
        f"{target.get('date')} {target.get('age_group')} tournament {tournament_id} is cancelled "
        "and the booked ice is no longer needed."
    ]
    for record in released_guest_reservations:
        holder = str(record.get("reserved_by") or record.get("filled_by") or "")
        note = str(record.get("note") or "")
        external = record.get("external_team") or {}
        external_label = str(external.get("label") or "")
        detail = f"guest reservation {record.get('id')} on {tournament_id}"
        if external_label:
            detail += f" (filled by {external_label!r})"
        if note:
            detail += f" -- {note}"
        notifications_required.append(
            f"Inform {holder or 'the reserving contact'} that their {detail} is released "
            "because the tournament was cancelled."
        )

    return {
        "tournament_id": tournament_id,
        "reason": reason,
        "original_date": target.get("date"),
        "age_group": target.get("age_group"),
        "released_guest_reservations": released_guest_reservations,
        "notifications_required": notifications_required,
    }
