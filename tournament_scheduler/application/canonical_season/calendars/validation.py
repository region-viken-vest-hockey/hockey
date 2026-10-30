"""Reconciliation-specific validation over shared canonical rule owners."""

from __future__ import annotations

import re
from typing import Any, Mapping, NoReturn

from tournament_scheduler.booking.calendar_bookings import (
    BOOKING_AUTHORITY_CALENDAR,
    CALENDAR_BOOKING_ASSOCIATIONS_KEY,
    REJECTED_BOOKING_EVIDENCE_KEY,
    REJECTED_BOOKING_RESOLVED,
    REJECTED_BOOKING_UNRESOLVED,
    club_calendar_positive_evidence_usable,
)
from tournament_scheduler.season.change_protections import protection_violations
from tournament_scheduler.season.request_constraints import (
    compare_request_constraint_violations,
)
from tournament_scheduler.season.state import canonical_state_revision
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError

from ..shared import _now_iso, _operator_identity
from .common import _event_interval, _record_rejected_booking_evidence

_CALENDAR_EVENT_AGE_TOKEN_RE = re.compile(r"\b(J?U\d{1,2})\b", re.IGNORECASE)


def calendar_event_age_tokens(title: str) -> set[str]:
    """Extract age-group tokens (``U10``, ``JU12``, ...) from a calendar title."""

    return {match.upper() for match in _CALENDAR_EVENT_AGE_TOKEN_RE.findall(str(title or ""))}


def manual_assertion_matches_existing(
    existing: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
    *,
    reference: str,
) -> bool:
    """Return whether a repeat manual assertion is the same statement."""

    if existing is None:
        return False
    return (
        str(existing.get("booking_status") or "") == str(candidate.get("booking_status") or "")
        and str(existing.get("source_scope") or "tournament")
        == str(candidate.get("source_scope") or "tournament")
        and dict(existing.get("tournament_facts") or {})
        == dict(candidate.get("tournament_facts") or {})
        and dict(existing.get("asserted_interval") or {})
        == dict(candidate.get("asserted_interval") or {})
        and dict(existing.get("stated_interval") or {})
        == dict(candidate.get("stated_interval") or {})
        and str(existing.get("reference") or "") == reference
        and str(existing.get("source_assertion_id") or "")
        == str(candidate.get("source_assertion_id") or "")
        and bool(existing.get("pinned")) == bool(candidate.get("pinned"))
        and str(existing.get("pin_reason") or "") == str(candidate.get("pin_reason") or "")
    )


def reconciliation_policy_blockers(
    *,
    baseline_plan: Mapping[str, Any],
    candidate_plan: Mapping[str, Any],
    decisions: Mapping[str, Any],
    tournament_id: str,
) -> list[dict[str, Any]]:
    """Return canonical request/protection blockers for one exact source change.

    The planning verifier owns schedule legality, while these planner-independent
    owners guard durable operator requests and accepted changes. Reconciliation
    must use all three; authoritative booking evidence is not permission to
    bypass an unavailable-team request or undo a protected placement.
    """

    constraint_comparison = compare_request_constraint_violations(
        baseline_plan,
        candidate_plan,
        decisions,
    )
    blockers = [dict(item) for item in constraint_comparison["regressions"]]
    blockers.extend(
        dict(item)
        for item in protection_violations(candidate_plan, decisions)
        if str(item.get("tournament_id") or "") == tournament_id
    )
    return blockers


def resolve_matching_rejected_booking_evidence(
    decisions: dict[str, Any],
    *,
    tournament_id: str,
    proposed_interval: Mapping[str, Any] | None,
    authority: str,
    reference: str,
    source_assertion_id: str | None,
    source_evidence_id: str | None,
    actor: str,
    now: str,
) -> None:
    """Mark a previously rejected exact source fact resolved after a valid retry."""

    accepted_interval = dict(proposed_interval or {})
    records: list[dict[str, Any]] = []
    for raw_record in decisions.get(REJECTED_BOOKING_EVIDENCE_KEY) or []:
        if not isinstance(raw_record, Mapping):
            continue
        record = dict(raw_record)
        if (
            str(record.get("status") or "") == REJECTED_BOOKING_UNRESOLVED
            and str(record.get("tournament_id") or "") == tournament_id
            and dict(record.get("proposed_interval") or {}) == accepted_interval
            and str(record.get("authority") or "") == authority
            and str(record.get("reference") or "") == reference
            and str(record.get("source_assertion_id") or "")
            == str(source_assertion_id or "")
            and str(record.get("source_evidence_id") or "")
            == str(source_evidence_id or "")
        ):
            record.update(
                {
                    "status": REJECTED_BOOKING_RESOLVED,
                    "resolved_at": now,
                    "resolved_by": actor,
                    "resolution": "accepted_exact_source_interval_after_retry",
                }
            )
        records.append(record)
    if records:
        decisions[REJECTED_BOOKING_EVIDENCE_KEY] = records


def validate_calendar_event_authority(
    service,
    *,
    snapshot,
    problem: Mapping[str, Any] | None,
    event: Mapping[str, Any],
    event_fingerprint: str,
    tournament: Mapping[str, Any],
    actor: str | None,
    dry_run: bool,
) -> tuple[str, str]:
    """Reject wrong/untrusted/stale ownership or return actor and check time."""

    schedule, decisions = snapshot.schedule, snapshot.decisions
    tournament_id = str(tournament.get("id") or "")
    resolved_actor, checked_at = _operator_identity(actor), _now_iso()
    parsed_interval = _event_interval(event)
    proposed_interval = {
        "date": str(event.get("date") or ""),
        "start_time": str(event.get("start") or ""),
        "duration_minutes": (
            parsed_interval[1] - parsed_interval[0] if parsed_interval is not None else 0
        ),
        "end_time": str(event.get("end") or ""),
    }

    def reject(code: str, message: str) -> NoReturn:
        _record_rejected_booking_evidence(
            service,
            snapshot=snapshot,
            tournament=tournament,
            authority=BOOKING_AUTHORITY_CALENDAR,
            reference=str(event.get("calendar_event") or event.get("title") or "calendar event"),
            source_assertion_id=None,
            source_evidence_id=event_fingerprint,
            proposed_interval=proposed_interval,
            blockers=[{"code": code, "message": message, "tournament_id": tournament_id}],
            actor=resolved_actor,
            now=checked_at,
            source_revision=canonical_state_revision(schedule, decisions),
            dry_run=dry_run,
        )
        raise SeasonStateError(message)

    event_club = str(event.get("club") or "")
    if not club_calendar_positive_evidence_usable(problem, event_club):
        reject(
            "calendar_source_not_authoritative",
            "Calendar booking source cannot support positive evidence",
        )
    if event_club != str(tournament.get("host_club") or ""):
        reject(
            "host_mismatch",
            "Calendar booking is not compatible with tournament: host_mismatch",
        )
    for record in decisions.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or []:
        if not isinstance(record, Mapping) or record.get("status", "active") != "active":
            continue
        if str(record.get("event_fingerprint") or "") != event_fingerprint:
            continue
        owner = str(record.get("tournament_id") or "")
        if owner and owner != tournament_id:
            reject(
                "duplicate_calendar_booking_association",
                "Calendar event already has an active tournament association; "
                "release it before rebinding",
            )
    return resolved_actor, checked_at
