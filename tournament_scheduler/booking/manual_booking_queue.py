"""Operational booking state and actionable manual-booking queue projection."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.booking_vocabulary import (
    BOOKING_CONFIRMED_BOOKED,
    BOOKING_CONFIRMED_NOT_BOOKED,
    BOOKING_MANUALLY_BOOKED,
    BOOKING_MANUALLY_NOT_BOOKED,
    MANUAL_QUEUE_ACTION_BOOK_OR_RECONFIRM,
    MANUAL_QUEUE_CLEARS_WHEN,
    MANUAL_QUEUE_EXPLICIT_REJECTION,
    MANUAL_QUEUE_HOST_CONFIRMATION_REQUIRED,
    MANUAL_QUEUE_MANUAL_PLACEMENT,
    MANUAL_QUEUE_RECONFIRMATION_REQUIRED,
    OPERATIONAL_ACTION_REQUIRED,
    OPERATIONAL_BOOKED,
    OPERATIONAL_NOT_BOOKED,
    STALE,
)
from tournament_scheduler.booking.interval_facts import tournament_occupancy_interval_facts


def operational_booking_state(
    *,
    status: str,
    manual_booking_reason: str | None = None,
    requires_host_confirmation: bool = False,
) -> str:
    """Collapse detailed booking evidence into one operator-facing state.

    ``booked``
        An accepted confirmation exists -- a durable manual assertion or a
        currently valid calendar association. The canonical slot is protected:
        a missing or contradicting scrape is a detail, never a reason to move
        it or downgrade the confirmation. An accepted confirmation is checked
        first because a provisional ``manual_booking_reason`` or a
        ``requires_host_confirmation`` flag set when the plan was built can
        stay on the tournament after the operator confirms the slot; that
        retained metadata is historical, not active work.
    ``action_required``
        No accepted confirmation exists and the assigned slot is not
        established ice (the host calendar could not be read, or a
        host-controlled interval must be moved/confirmed), or a prior
        confirmation was explicitly rejected or invalidated. These are the
        manual booking queue / re-confirmation work items.
    ``not_booked``
        No accepted confirmation and no explicit rejection. Missing calendar
        evidence and unresolved ambiguity land here, muted, and must not be
        mistaken for a host rejection or silently pushed to the manual queue.
    """
    if status in (BOOKING_CONFIRMED_BOOKED, BOOKING_MANUALLY_BOOKED):
        return OPERATIONAL_BOOKED
    if str(manual_booking_reason or "").strip() or requires_host_confirmation:
        return OPERATIONAL_ACTION_REQUIRED
    if status in (BOOKING_CONFIRMED_NOT_BOOKED, BOOKING_MANUALLY_NOT_BOOKED, STALE):
        return OPERATIONAL_ACTION_REQUIRED
    return OPERATIONAL_NOT_BOOKED


def _action_required_reason_code(
    *,
    status: str,
    manual_booking_reason: str,
    requires_host_confirmation: bool,
) -> str:
    """Classify why an ``action_required`` tournament is manual work."""

    if status in (BOOKING_CONFIRMED_NOT_BOOKED, BOOKING_MANUALLY_NOT_BOOKED):
        return MANUAL_QUEUE_EXPLICIT_REJECTION
    if status == STALE:
        return MANUAL_QUEUE_RECONFIRMATION_REQUIRED
    if requires_host_confirmation and not manual_booking_reason.strip():
        return MANUAL_QUEUE_HOST_CONFIRMATION_REQUIRED
    return MANUAL_QUEUE_MANUAL_PLACEMENT


def _manual_work_source(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return the traceable source evidence behind one queue item.

    A manual assertion (rejection/confirmation) carries an authority, reference
    and author; calendar-derived evidence carries the checked fingerprint and
    reason. Keeping the two shapes explicit lets the queue stay auditable
    without inventing per-ID source detail that does not exist.
    """

    evidence = row.get("evidence")
    if isinstance(evidence, Mapping) and evidence.get("authority"):
        return {
            "authority": str(evidence.get("authority") or ""),
            "source_scope": str(evidence.get("source_scope") or ""),
            "reference": str(evidence.get("reference") or ""),
            "note": str(evidence.get("note") or ""),
            "asserted_by": str(evidence.get("asserted_by") or ""),
            "asserted_at": str(evidence.get("asserted_at") or ""),
            "assertion_id": str(evidence.get("id") or ""),
            "supersedes": str(evidence.get("supersedes") or ""),
            # Club-wide source provenance, denormalized from the linked source
            # assertion so the queue item is traceable without a second lookup.
            "source_assertion_id": str(row.get("source_assertion_id") or ""),
            "source_document": str(row.get("source_document") or ""),
            "source_version": str(row.get("source_version") or ""),
        }
    # A calendar-only row stores its booking-evidence record under
    # ``evidence`` (no authority); a manual row may additionally carry a
    # separate ``calendar_evidence`` observation. Prefer the explicit key.
    calendar = row.get("calendar_evidence")
    if not isinstance(calendar, Mapping):
        calendar = evidence
    if isinstance(calendar, Mapping):
        return {
            "source": str(calendar.get("source_calendar_status") or ""),
            "reason": str(calendar.get("reason") or ""),
            "note": str(calendar.get("note") or ""),
            "checked_by": str(calendar.get("checked_by") or ""),
            "checked_at": str(calendar.get("checked_at") or ""),
            "event_fingerprint": str(calendar.get("event_fingerprint") or ""),
        }
    return {}


def _manual_work_alternatives(assessment_row: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return actionable calendar alternatives for one queue item.

    Only candidates the read-only assessment marked actionable are offered; an
    untrusted-source observation or a title/arena contradiction stays in the
    assessment evidence but is never presented as a bookable alternative.
    """

    alternatives: list[dict[str, Any]] = []
    for candidate in (assessment_row or {}).get("candidates") or []:
        if not isinstance(candidate, Mapping) or not candidate.get("actionable"):
            continue
        alternatives.append(
            {
                "date": str(candidate.get("date") or ""),
                "start": str(candidate.get("start") or ""),
                "end": str(candidate.get("end") or ""),
                "title": str(candidate.get("title") or ""),
                "relation": str(candidate.get("relation") or ""),
                "event_fingerprint": str(candidate.get("event_fingerprint") or ""),
                "covers_current_interval": bool(candidate.get("covers_current_interval")),
            }
        )
    return alternatives


def manual_booking_work_item(
    *,
    row: Mapping[str, Any],
    tournament: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
    assessment_row: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the structured manual-booking queue item for one projected row.

    Only an ``action_required`` tournament becomes work. The item carries the
    *reason* it needs action, the *source* evidence (a manual assertion's
    reference/author/note or the calendar evidence), the *proposed
    alternatives* the read-only assessment found, the *owner* (host club), the
    required *action*, and how the item is *resolved*. It never persists
    anything or changes a booking decision; the queue is a projection over the
    same canonical evidence as :func:`booking_status_report`.
    """

    if str(row.get("operational_state") or "") != OPERATIONAL_ACTION_REQUIRED:
        return None
    status = str(row.get("status") or "")
    manual_booking_reason = str(tournament.get("manual_booking_reason") or "")
    requires_host_confirmation = bool(tournament.get("requires_host_confirmation"))
    reason_code = _action_required_reason_code(
        status=status,
        manual_booking_reason=manual_booking_reason,
        requires_host_confirmation=requires_host_confirmation,
    )
    source = _manual_work_source(row)
    interval = tournament_occupancy_interval_facts(tournament, problem)
    return {
        "tournament_id": str(row.get("tournament_id") or ""),
        "host_club": str(row.get("host_club") or ""),
        "age_group": str(row.get("age_group") or ""),
        "arena": str(row.get("arena") or ""),
        "canonical_interval": {
            "date": interval["date"],
            "start_time": interval["start_time"],
            "end_time": interval["end_time"],
        },
        "reason_code": reason_code,
        "reason_detail": manual_booking_reason,
        "owner": str(row.get("host_club") or ""),
        "action": MANUAL_QUEUE_ACTION_BOOK_OR_RECONFIRM,
        "source": source,
        "proposed_alternatives": _manual_work_alternatives(assessment_row),
        "stale_reasons": list(row.get("stale_reasons") or []),
        # How the item clears. The trace itself is the append-only canonical
        # decision history plus the source assertion chain recorded above.
        "resolution": {
            "status": "open",
            "clears_when": MANUAL_QUEUE_CLEARS_WHEN,
        },
    }
