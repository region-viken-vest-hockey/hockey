"""Pure exact-interval authority check over already-projected evidence."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.booking_facts import tournament_booking_facts
from tournament_scheduler.booking.booking_vocabulary import (
    _STATUS_BOOKED,
    ACTIVE,
    BOOKING_AUTHORITY_CALENDAR,
    BOOKING_AUTHORITY_MANUAL,
    CALENDAR_BOOKING_ASSOCIATIONS_KEY,
    MANUAL_ASSERTION_ACTIVE,
    MANUAL_BOOKING_ASSERTIONS_KEY,
)
from tournament_scheduler.booking.interval_facts import tournament_occupancy_interval_facts


def accepted_booking_interval_evidence(
    problem: Mapping[str, Any] | None,
    tournament: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return exact accepted-booking evidence for this tournament's interval.

    This is the reconciliation/audit boundary classifier: it only accepts
    projected, currently-valid source evidence (a calendar association or a
    manual booked assertion) whose stored tournament facts and occupied
    interval match the candidate's exact id, host, arena, date, start, end and
    duration. It never proves booking authority from occupancy alone and is
    not used by the planning verifier.
    """

    tournament_id = str(tournament.get("id") or "")
    if not tournament_id:
        return None
    current_interval = tournament_occupancy_interval_facts(tournament, problem)
    current_facts = tournament_booking_facts(tournament)

    def _facts_match(record: Mapping[str, Any]) -> bool:
        stored_facts = record.get("tournament_facts") or {}
        if any(str(stored_facts.get(key) or "") != value for key, value in current_facts.items()):
            return False
        stored_interval = record.get("tournament_interval") or record.get("asserted_interval") or {}
        return all(
            str(stored_interval.get(key) or "") == current_interval[key]
            for key in ("date", "start_time", "duration_minutes", "end_time")
        )

    for record in (problem or {}).get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or []:
        if not isinstance(record, Mapping) or str(record.get("status") or ACTIVE) != ACTIVE:
            continue
        if str(record.get("tournament_id") or "") != tournament_id or not _facts_match(record):
            continue
        if str(record.get("club") or "") != current_facts["host_club"]:
            continue
        if str(record.get("date") or "") != current_interval["date"]:
            continue
        if str(record.get("start") or "") != current_interval["start_time"]:
            continue
        if str(record.get("end") or "") != current_interval["end_time"]:
            continue
        return {
            "authority": BOOKING_AUTHORITY_CALENDAR,
            "record_id": str(record.get("id") or ""),
            "event_fingerprint": str(record.get("event_fingerprint") or ""),
        }

    for record in (problem or {}).get(MANUAL_BOOKING_ASSERTIONS_KEY) or []:
        if not isinstance(record, Mapping) or str(record.get("status") or "") != MANUAL_ASSERTION_ACTIVE:
            continue
        if str(record.get("booking_status") or "") != _STATUS_BOOKED:
            continue
        if str(record.get("tournament_id") or "") != tournament_id or not _facts_match(record):
            continue
        if not (str(record.get("source_assertion_id") or "") or str(record.get("reference") or "") or str(record.get("note") or "")):
            continue
        return {
            "authority": str(record.get("authority") or BOOKING_AUTHORITY_MANUAL),
            "record_id": str(record.get("id") or ""),
            "source_assertion_id": str(record.get("source_assertion_id") or ""),
        }
    return None
