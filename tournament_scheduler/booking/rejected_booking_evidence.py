"""Durable rejected source evidence: retain facts without accepting placement."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


from tournament_scheduler.booking.booking_vocabulary import (
    REJECTED_BOOKING_EVIDENCE_KEY,
    REJECTED_BOOKING_UNRESOLVED,
)





def new_rejected_booking_evidence_record(
    *,
    tournament: Mapping[str, Any],
    authority: str,
    reference: str,
    source_assertion_id: str | None,
    proposed_interval: Mapping[str, Any],
    conflicts: Iterable[Mapping[str, Any]],
    rejected_at: str,
    rejected_by: str,
    canonical_state_revision: str,
    source_evidence_id: str | None = None,
) -> dict[str, Any]:
    """Build one durable unresolved rejected-booking-evidence record.

    The record is decision-only audit evidence: it captures the exact source
    statement (authority, reference, linked source assertion) and the exact
    proposed interval plus the hard conflicts that refused it. It is surfaced
    as an actionable unresolved conflict by the booking-status projection and
    must never be mistaken for an accepted placement, approval/lock or
    reconciled state.
    """

    tournament_id = str(tournament.get("id") or "")
    return {
        "id": f"rejected_booking:{tournament_id}:{rejected_at}",
        "schema_version": 1,
        "status": REJECTED_BOOKING_UNRESOLVED,
        "tournament_id": tournament_id,
        "host_club": str(tournament.get("host_club") or ""),
        "arena": str(tournament.get("arena") or ""),
        "age_group": str(tournament.get("age_group") or ""),
        "authority": authority,
        "source_assertion_id": str(source_assertion_id or "") or None,
        "source_evidence_id": str(source_evidence_id or "") or None,
        "reference": str(reference or ""),
        "proposed_interval": dict(proposed_interval or {}),
        "conflicts": [
            {
                "code": str(conflict.get("code") or ""),
                "message": str(conflict.get("message") or ""),
            }
            for conflict in conflicts
            if isinstance(conflict, Mapping)
        ],
        "rejected_at": rejected_at,
        "rejected_by": rejected_by,
        "canonical_state_revision": canonical_state_revision,
    }



def rejected_booking_evidence_records(
    decisions: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return every persisted rejected-booking-evidence record."""

    return [
        dict(record)
        for record in ((decisions or {}).get(REJECTED_BOOKING_EVIDENCE_KEY) or [])
        if isinstance(record, Mapping)
    ]



def unresolved_rejected_booking_evidence(
    decisions: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return the rejected-booking-evidence records still awaiting resolution."""

    return [
        record
        for record in rejected_booking_evidence_records(decisions)
        if str(record.get("status") or "") == REJECTED_BOOKING_UNRESOLVED
    ]
