"""One booking-status row: accepted authority versus independent evidence."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.accepted_provenance import accepted_association_provenance, accepted_manual_provenance
from tournament_scheduler.booking.assessment_candidates import ASSESSMENT_PRESUMED_UNSCHEDULED
from tournament_scheduler.booking.booking_vocabulary import (
    _ATTENTION_BOOKING_STATUSES,
    ASSESSMENT_NOT_CHECKABLE,
    ASSESSMENT_PROPOSED_CHANGED_SLOT,
    ASSESSMENT_UNMATCHED,
    BOOKING_AUTHORITY_CALENDAR,
    BOOKING_AUTHORITY_MANUAL,
    BOOKING_AUTHORITY_MANUAL_INTERPRETATION,
    BOOKING_CONFIRMED_BOOKED,
    BOOKING_CONFIRMED_NOT_BOOKED,
    BOOKING_MANUALLY_BOOKED,
    BOOKING_MANUALLY_NOT_BOOKED,
    BOOKING_UNKNOWN,
    MANUAL_ASSERTION_ACTIVE,
    OPERATIONAL_BOOKED,
    OPERATIONAL_CHANGED_SLOT_REVIEW,
    OPERATIONAL_NOT_BOOKED,
    OPERATIONAL_PRESUMED_UNSCHEDULED,
    OPERATIONAL_UNKNOWN,
    STALE,
)
from tournament_scheduler.booking.calendar_associations import active_associations
from tournament_scheduler.booking.calendar_observations import _projected_calendar_status
from tournament_scheduler.booking.club_booking_sources import club_booking_source_record_by_id
from tournament_scheduler.booking.manual_assertions import (
    manual_assertion_for_tournament,
    manual_assertion_interval_follow_up,
    manual_assertion_projection_status,
    manual_assertion_stale_reasons,
)
from tournament_scheduler.booking.manual_booking_queue import operational_booking_state
from tournament_scheduler.booking.status_crosswalk import assessment_provenance_fields, explicit_interval_fields


def build_booking_status_row(
    *,
    tid: str,
    tournament: Mapping[str, Any],
    calendar_record: Mapping[str, Any] | None,
    decisions: Mapping[str, Any] | None,
    problem: Mapping[str, Any] | None,
    tournaments: Mapping[str, Mapping[str, Any]],
    active_assoc_ids: set[str],
    assoc_stale_by_tournament: set[str],
    assessment_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project accepted authority and independent calendar evidence for one row."""
    calendar_status, calendar_stale_reasons = _projected_calendar_status(
        calendar_record,
        tournament_id=tid,
        problem=problem,
        tournaments=tournaments,
        active_assoc_ids=active_assoc_ids,
        assoc_stale_by_tournament=assoc_stale_by_tournament,
    )
    manual = manual_assertion_for_tournament(decisions, tid)
    authority: str | None = None
    stale_reasons: list[str] = []
    follow_up_reasons: list[str] = []
    conflict = False
    if manual is not None:
        source_scope = str(manual.get("source_scope") or "tournament")
        # A deliberate per-tournament interpretation of a club-wide statement
        # is distinguishable from a direct per-tournament confirmation.
        authority = (
            BOOKING_AUTHORITY_MANUAL_INTERPRETATION
            if source_scope == "club_wide_interpretation"
            else BOOKING_AUTHORITY_MANUAL
        )
        source_record = club_booking_source_record_by_id(
            decisions, str(manual.get("source_assertion_id") or "")
        )
        source_is_current = (
            source_record is None
            or str(source_record.get("status") or "") == MANUAL_ASSERTION_ACTIVE
        )
        manual_stale = manual_assertion_stale_reasons(manual, problem=problem, tournament=tournament)
        if manual_stale:
            # The operator confirmed a specific slot; a changed canonical
            # slot invalidates the assertion rather than carrying it along.
            status = STALE
            stale_reasons = manual_stale
        else:
            status = manual_assertion_projection_status(manual)
            follow_up_reasons = manual_assertion_interval_follow_up(manual)
            follow_up_reasons.extend(str(code) for code in (manual.get("booking_feasibility_warnings") or []))
            if status == BOOKING_MANUALLY_BOOKED and calendar_status == BOOKING_CONFIRMED_NOT_BOOKED:
                conflict = True
                follow_up_reasons.append("calendar_negative_conflicts_with_manual_booking")
            elif status == BOOKING_MANUALLY_NOT_BOOKED and calendar_status == BOOKING_CONFIRMED_BOOKED:
                conflict = True
                follow_up_reasons.append("calendar_association_conflicts_with_manual_rejection")
        # Independent calendar warnings stay actionable next to the manual
        # authority; a failed/unverified scrape never downgrades the club's
        # explicit confirmation.
        follow_up_reasons.extend(calendar_stale_reasons)
        if not source_is_current:
            # The assertion is still valid authority, but it points at a
            # superseded club source version; surface it for relinking
            # rather than silently losing the provenance.
            follow_up_reasons.append("club_booking_source_superseded")
        operational_state = operational_booking_state(
            status=status,
            manual_booking_reason=str(tournament.get("manual_booking_reason") or ""),
            requires_host_confirmation=bool(tournament.get("requires_host_confirmation")),
        )
        row = {
            "tournament_id": tid,
            "status": status,
            "operational_state": operational_state,
            "operational_lock": operational_state == OPERATIONAL_BOOKED,
            "authority": authority,
            "source_scope": source_scope,
            "source_assertion_id": str(manual.get("source_assertion_id") or "") or None,
            "source_document": str((source_record or {}).get("source_document") or ""),
            "source_version": str((source_record or {}).get("source_version") or ""),
            "source_is_current": source_is_current,
            "host_club": str(tournament.get("host_club") or ""),
            "age_group": str(tournament.get("age_group") or ""),
            "arena": str(tournament.get("arena") or ""),
            "date": str(tournament.get("date") or ""),
            "start_time": str(tournament.get("start_time") or ""),
            "needs_attention": status in _ATTENTION_BOOKING_STATUSES or bool(follow_up_reasons),
            # The club's accepted authority and the independent calendar
            # actively disagree (a manual confirmation the calendar reads as
            # not-booked, or a manual rejection the calendar reads as
            # booked). This is a genuine contradiction, not mere ambiguity,
            # so it is exposed as a typed flag rather than left for a
            # consumer to re-parse the follow-up reason strings.
            "conflict": conflict,
            "stale_reasons": stale_reasons,
            "follow_up_reasons": follow_up_reasons,
            "calendar_status": calendar_status,
            "calendar_stale_reasons": calendar_stale_reasons,
            "evidence": manual,
        }
        # Carry advisory crosswalk provenance (classification, canonical and
        # latest observed interval, negative evidence) next to a manual
        # authority so a contradicting scrape stays visible without ever
        # demoting the accepted confirmation/rejection.
        row.update(assessment_provenance_fields(assessment_row))
        row["explicit_interval_fields"] = explicit_interval_fields(manual)  # #860
        if authority is not None:
            row["accepted_evidence"] = accepted_manual_provenance(manual)
        if calendar_record:
            row["calendar_evidence"] = calendar_record
    else:
        status = calendar_status
        authority = BOOKING_AUTHORITY_CALENDAR if status == BOOKING_CONFIRMED_BOOKED else None
        stale_reasons = calendar_stale_reasons
        operational_state = operational_booking_state(
            status=status,
            manual_booking_reason=str(tournament.get("manual_booking_reason") or ""),
            requires_host_confirmation=bool(tournament.get("requires_host_confirmation")),
        )
        assessment_row = assessment_row or {}
        assessment_classification = str(assessment_row.get("classification") or "")
        if status == BOOKING_UNKNOWN and operational_state == OPERATIONAL_NOT_BOOKED:
            if assessment_classification == ASSESSMENT_PROPOSED_CHANGED_SLOT:
                operational_state = OPERATIONAL_CHANGED_SLOT_REVIEW
            elif assessment_classification == ASSESSMENT_PRESUMED_UNSCHEDULED:
                operational_state = OPERATIONAL_PRESUMED_UNSCHEDULED
            elif assessment_classification in (ASSESSMENT_NOT_CHECKABLE, ASSESSMENT_UNMATCHED):
                operational_state = OPERATIONAL_UNKNOWN
        row = {
            "tournament_id": tid,
            "status": status,
            "operational_state": operational_state,
            "operational_lock": operational_state == OPERATIONAL_BOOKED,
            "authority": authority,
            "source_scope": "",
            "host_club": str(tournament.get("host_club") or ""),
            "age_group": str(tournament.get("age_group") or ""),
            "arena": str(tournament.get("arena") or ""),
            "date": str(tournament.get("date") or ""),
            "start_time": str(tournament.get("start_time") or ""),
            "needs_attention": status in _ATTENTION_BOOKING_STATUSES
            or operational_state in (OPERATIONAL_CHANGED_SLOT_REVIEW, OPERATIONAL_PRESUMED_UNSCHEDULED),
            "conflict": False,
            "stale_reasons": stale_reasons,
            "follow_up_reasons": [],
            "calendar_status": status,
            "calendar_stale_reasons": calendar_stale_reasons,
        }
        row.update(assessment_provenance_fields(assessment_row))
        if authority == BOOKING_AUTHORITY_CALENDAR:
            if (provenance := accepted_association_provenance(active_associations(decisions), tid)) is not None:
                row["accepted_evidence"] = provenance
        if calendar_record:
            row["evidence"] = calendar_record
    return row
