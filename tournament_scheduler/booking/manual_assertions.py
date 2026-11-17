"""Manual booking assertion records and their placement-bound authority."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.accepted_interval_evidence import accepted_booking_interval_evidence
from tournament_scheduler.booking.booking_facts import tournament_booking_facts
from tournament_scheduler.booking.booking_vocabulary import (
    _MANUAL_CHOICE_TO_PROJECTION,
    BOOKING_AUTHORITY_CALENDAR,
    BOOKING_AUTHORITY_MANUAL,
    BOOKING_MANUAL_UNKNOWN,
    BOOKING_MANUALLY_BOOKED,
    MANUAL_ASSERTION_ACTIVE,
    MANUAL_ASSERTION_SCOPES,
    MANUAL_BOOKING_ASSERTIONS_KEY,
    MANUAL_SOURCE_CLUB_CONFIRMATION,
)
from tournament_scheduler.booking.interval_facts import tournament_occupancy_interval_facts
from tournament_scheduler.booking.stated_intervals import _normalized_stated_interval
from tournament_scheduler.booking.status_crosswalk import explicit_interval_fields


def manual_assertion_records(decisions: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return every persisted manual booking assertion, newest state preserved."""

    return [
        dict(record)
        for record in ((decisions or {}).get(MANUAL_BOOKING_ASSERTIONS_KEY) or [])
        if isinstance(record, Mapping)
    ]


def active_manual_assertions(decisions: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    return [
        record
        for record in manual_assertion_records(decisions)
        if str(record.get("status") or "") == MANUAL_ASSERTION_ACTIVE
    ]


def manual_assertion_for_tournament(
    decisions: Mapping[str, Any] | None,
    tournament_id: str,
) -> dict[str, Any] | None:
    """Return the newest active manual assertion for one tournament, if any."""

    matches = [
        record
        for record in active_manual_assertions(decisions)
        if str(record.get("tournament_id") or "") == tournament_id
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda record: str(record.get("asserted_at") or ""))[-1]


def manual_assertion_projection_status(assertion: Mapping[str, Any]) -> str:
    """Map a persisted manual assertion to its booking-status projection."""

    return _MANUAL_CHOICE_TO_PROJECTION.get(
        str(assertion.get("booking_status") or ""),
        BOOKING_MANUAL_UNKNOWN,
    )


def manual_assertion_stale_reasons(
    assertion: Mapping[str, Any],
    *,
    problem: Mapping[str, Any] | None,
    tournament: Mapping[str, Any] | None,
) -> list[str]:
    """Return why a manual assertion no longer describes the canonical slot.

    A material change to the occupied interval (date/start/duration/end) or any
    other tournament fact invalidates the assertion: the operator confirmed a
    specific slot, so the same statement must not silently carry to a new one.
    """

    if tournament is None:
        return ["tournament_missing"]
    reasons: list[str] = []
    facts = assertion.get("tournament_facts") or {}
    for key, value in tournament_booking_facts(tournament).items():
        if str(facts.get(key) or "") != value:
            reasons.append(f"tournament_{key}_changed")
    stored_interval = assertion.get("asserted_interval") or {}
    current_interval = tournament_occupancy_interval_facts(tournament, problem)
    # Only an assertion that stated no interval is superseded; an explicit stated window stays authoritative.
    superseded = explicit_interval_fields(assertion) == [] and (accepted_booking_interval_evidence(problem, tournament) or {}).get("authority") == BOOKING_AUTHORITY_CALENDAR
    for key in ("date", "start_time", "duration_minutes", "end_time"):
        if not superseded and str(stored_interval.get(key) or "") != current_interval[key]:
            reasons.append(f"tournament_{key}_changed")
    return sorted(set(reasons))


def manual_assertion_interval_follow_up(assertion: Mapping[str, Any]) -> list[str]:
    """Return follow-up reasons for a stated interval that differs from canonical.

    A source may explicitly state an interval that disagrees with the canonical
    occupancy (for example a 75-minute emailed booking against a 100-minute
    canonical block).  The assertion stays booked; the discrepancy is surfaced
    as follow-up instead of silently shrinking the canonical occupancy.
    """

    if manual_assertion_projection_status(assertion) != BOOKING_MANUALLY_BOOKED:
        return []
    stated = _normalized_stated_interval(assertion.get("stated_interval"))
    if not any(stated.get(key) for key in ("date", "start", "end")):
        return []
    canonical = assertion.get("asserted_interval") or {}
    reasons: list[str] = []
    if stated.get("date") and stated["date"] != str(canonical.get("date") or ""):
        reasons.append("manual_booking_stated_date_differs_from_canonical")
    if stated.get("start") and stated["start"] != str(canonical.get("start_time") or ""):
        reasons.append("manual_booking_stated_start_differs_from_canonical")
    if stated.get("end") and stated["end"] != str(canonical.get("end_time") or ""):
        reasons.append("manual_booking_stated_end_differs_from_canonical")
    return reasons


def new_manual_assertion_record(
    *,
    tournament: Mapping[str, Any],
    booking_status: str,
    problem: Mapping[str, Any] | None,
    actor: str,
    note: str,
    reference: str,
    source_scope: str,
    stated_interval: Mapping[str, Any] | None,
    asserted_at: str,
    source_revision: str,
    supersedes: str | None = None,
    source_assertion_id: str | None = None,
) -> dict[str, Any]:
    """Build one durable, revision-bound manual booking assertion record."""

    tournament_id = str(tournament.get("id") or "")
    if booking_status not in _MANUAL_CHOICE_TO_PROJECTION:
        raise ValueError(f"Unknown manual booking status: {booking_status!r}")
    return {
        "id": f"manual_booking:{tournament_id}:{asserted_at}",
        "schema_version": 1,
        "status": MANUAL_ASSERTION_ACTIVE,
        "booking_status": booking_status,
        "authority": BOOKING_AUTHORITY_MANUAL,
        "source": MANUAL_SOURCE_CLUB_CONFIRMATION,
        "source_scope": source_scope if source_scope in MANUAL_ASSERTION_SCOPES else "tournament",
        "source_assertion_id": str(source_assertion_id or "") or None,
        "tournament_id": tournament_id,
        "host_club": str(tournament.get("host_club") or ""),
        "arena": str(tournament.get("arena") or ""),
        "date": str(tournament.get("date") or ""),
        "start_time": str(tournament.get("start_time") or ""),
        "tournament_facts": tournament_booking_facts(tournament),
        "asserted_interval": tournament_occupancy_interval_facts(tournament, problem),
        "stated_interval": _normalized_stated_interval(stated_interval),
        "booking_feasibility_warnings": [],
        "reference": str(reference or ""),
        "note": note or "",
        "asserted_at": asserted_at,
        "asserted_by": actor,
        "source_revision": source_revision,
        "supersedes": supersedes,
    }
