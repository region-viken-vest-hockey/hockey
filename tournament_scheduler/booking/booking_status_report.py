"""Read-only season booking-status aggregation and cancellation history."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.accepted_provenance import with_acceptance_history
from tournament_scheduler.booking.booking_assessment import booking_assessment
from tournament_scheduler.booking.booking_facts import _tournaments_by_id
from tournament_scheduler.booking.booking_status_row import build_booking_status_row
from tournament_scheduler.booking.booking_vocabulary import (
    BOOKING_AMBIGUOUS,
    BOOKING_AUTHORITY_MANUAL,
    BOOKING_AUTHORITY_MANUAL_INTERPRETATION,
    BOOKING_CONFIRMED_BOOKED,
    BOOKING_CONFIRMED_NOT_BOOKED,
    BOOKING_MANUALLY_BOOKED,
    BOOKING_MANUALLY_NOT_BOOKED,
    BOOKING_NOT_CHECKABLE,
    BOOKING_UNKNOWN,
    OPERATIONAL_ACTION_REQUIRED,
    OPERATIONAL_BOOKED,
    OPERATIONAL_CHANGED_SLOT_REVIEW,
    OPERATIONAL_NOT_BOOKED,
    OPERATIONAL_PRESUMED_UNSCHEDULED,
    OPERATIONAL_UNKNOWN,
    STALE,
    TOURNAMENT_BOOKING_EVIDENCE_KEY,
)
from tournament_scheduler.booking.calendar_associations import association_findings, valid_active_associations
from tournament_scheduler.booking.calendar_audit import calendar_audit_summary
from tournament_scheduler.booking.club_source_projection import _club_booking_source_projection, _scope_club_sources
from tournament_scheduler.booking.manual_booking_queue import manual_booking_work_item
from tournament_scheduler.booking.rejected_booking_evidence import unresolved_rejected_booking_evidence
from tournament_scheduler.booking.status_crosswalk import attach_interval_comparison


def booking_status_report(
    *,
    problem: Mapping[str, Any] | None,
    plan: Mapping[str, Any] | None,
    decisions: Mapping[str, Any] | None,
    host_club: str | None = None,
) -> dict[str, Any]:
    """Project per-tournament booking evidence, optionally scoped to one host club.

    ``host_club`` narrows every output list (tournament rows, counts, manual
    queue, club source interpretations and rejected evidence) to tournaments
    hosted by that club. The assessment still runs over the whole plan so
    classifications that depend on neighbouring tournaments are unchanged.
    """
    tournaments = _tournaments_by_id(plan)
    host_key = _club_match_key(host_club)
    if host_key:
        tournaments = {
            tid: tournament
            for tid, tournament in tournaments.items()
            if _club_match_key(tournament.get("host_club")) == host_key
        }
    active_assoc_ids = {str(record.get("tournament_id") or "") for record in valid_active_associations(decisions, problem=problem, plan=plan)}
    assoc_stale_by_tournament = {
        str(finding.get("tournament_id") or "")
        for finding in association_findings(problem=problem, plan=plan, decisions=decisions)
        if finding.get("code") == "stale_calendar_booking_association"
    }
    latest: dict[str, dict[str, Any]] = {}
    for record in (decisions or {}).get(TOURNAMENT_BOOKING_EVIDENCE_KEY) or []:
        if not isinstance(record, Mapping):
            continue
        tid = str(record.get("tournament_id") or "")
        if not tid:
            continue
        previous = latest.get(tid)
        if previous is None or str(record.get("checked_at") or "") >= str(previous.get("checked_at") or ""):
            latest[tid] = dict(record)

    rows: list[dict[str, Any]] = []
    manual_queue: list[dict[str, Any]] = []
    counts: dict[str, int] = {
        BOOKING_UNKNOWN: 0,
        BOOKING_CONFIRMED_BOOKED: 0,
        BOOKING_CONFIRMED_NOT_BOOKED: 0,
        BOOKING_AMBIGUOUS: 0,
        BOOKING_NOT_CHECKABLE: 0,
        STALE: 0,
        BOOKING_MANUALLY_BOOKED: 0,
        BOOKING_MANUALLY_NOT_BOOKED: 0,
        "manual": 0,
        "conflicts": 0,
        "needs_attention": 0,
        OPERATIONAL_BOOKED: 0,
        OPERATIONAL_NOT_BOOKED: 0,
        OPERATIONAL_ACTION_REQUIRED: 0,
        OPERATIONAL_CHANGED_SLOT_REVIEW: 0,
        OPERATIONAL_PRESUMED_UNSCHEDULED: 0,
        OPERATIONAL_UNKNOWN: 0,
    }
    assessment_by_tournament: dict[str, dict[str, Any]] = {}
    unresolved_events: list[dict[str, Any]] = []
    try:
        assessment = booking_assessment(
            problem=problem,
            plan=plan,
            decisions=decisions,
            canonical_state_revision="booking-status-projection",
        )
        assessment_by_tournament = {
            str(row.get("tournament_id") or ""): dict(row)
            for row in assessment.get("tournaments") or []
            if isinstance(row, Mapping)
        }
        # The same unresolved set the assessment itself reports: no tournament claims
        # the event, or more than one does.
        unresolved_events = [
            dict(row)
            for row in assessment.get("events") or []
            if isinstance(row, Mapping) and (row.get("unmatched") or row.get("one_to_many"))
        ]
    except Exception:  # noqa: BLE001 - advisory crosswalk failure must not hide persisted booking status.
        assessment_by_tournament = {}
    # A cancelled tournament occupies no ice and has no current booking to
    # confirm, so it is not an active row, count or queue item. Its history is
    # reported separately below.
    cancelled_ids = {tid for tid, tournament in tournaments.items() if tournament.get("cancelled")}
    for tid, tournament in sorted(tournaments.items()):
        if tid in cancelled_ids:
            continue
        row = build_booking_status_row(
            tid=tid,
            tournament=tournament,
            calendar_record=latest.get(tid),
            decisions=decisions,
            problem=problem,
            tournaments=tournaments,
            active_assoc_ids=active_assoc_ids,
            assoc_stale_by_tournament=assoc_stale_by_tournament,
            assessment_row=assessment_by_tournament.get(tid),
        )
        status = row["status"]
        operational_state = row["operational_state"]
        conflict = row["conflict"]
        counts["manual"] += int(row["authority"] in (
            BOOKING_AUTHORITY_MANUAL, BOOKING_AUTHORITY_MANUAL_INTERPRETATION,
        ))
        work_item = manual_booking_work_item(
            row=row,
            tournament=tournament,
            problem=problem,
            assessment_row=assessment_by_tournament.get(tid),
        )
        if work_item is not None:
            row["manual_work"] = work_item
            manual_queue.append(work_item)
        if conflict:
            counts["conflicts"] += 1
        rows.append(attach_interval_comparison(with_acceptance_history(row, decisions, tid)))
        counts.setdefault(status, 0)
        counts[status] += 1
        counts[operational_state] = counts.get(operational_state, 0) + 1
        if row["needs_attention"]:
            counts["needs_attention"] += 1
    # Durable rejected source evidence stays visible as an actionable unresolved
    # conflict on the affected tournament row, without ever mutating the active
    # placement, approval/lock or reconciliation state (ADR 0005).
    rejected_by_tournament: dict[str, list[dict[str, Any]]] = {}
    for record in unresolved_rejected_booking_evidence(decisions):
        tid = str(record.get("tournament_id") or "")
        if tid:
            rejected_by_tournament.setdefault(tid, []).append(record)
    for row in rows:
        rejected = rejected_by_tournament.get(str(row.get("tournament_id") or ""))
        if not rejected:
            continue
        row["rejected_booking_evidence"] = rejected
        follow_up = list(row.get("follow_up_reasons") or [])
        if "rejected_booking_requires_resolution" not in follow_up:
            follow_up.append("rejected_booking_requires_resolution")
        row["follow_up_reasons"] = follow_up
        row["needs_attention"] = True
    # Rejected evidence can flag a row after the loop above, so the aggregate is
    # recounted from the final row flags to keep counts and rows consistent.
    counts["needs_attention"] = sum(1 for row in rows if row["needs_attention"])
    rejected_evidence = sorted(
        (
            record
            for tid, records in rejected_by_tournament.items()
            if tid not in cancelled_ids and (not host_key or tid in tournaments)
            for record in records
        ),
        key=lambda record: str(record.get("rejected_at") or ""),
    )
    counts["rejected_bookings"] = len(rejected_evidence)
    cancelled_history = [
        _cancelled_tournament_history(
            tournament,
            calendar_record=latest.get(tid),
            rejected=rejected_by_tournament.get(tid, []),
        )
        for tid, tournament in sorted(tournaments.items())
        if tid in cancelled_ids
    ]
    club_sources = _club_booking_source_projection(
        decisions=decisions, rows=rows, plan=plan, problem=problem
    )
    if host_key:
        club_sources = _scope_club_sources(club_sources, set(tournaments))
    report = {
        "tournaments": rows,
        "counts": counts,
        "calendar_audit": calendar_audit_summary(rows, cancelled_history=cancelled_history),
        "rejected_booking_evidence": rejected_evidence,
        # One structured work item per ``action_required`` tournament; the
        # per-row copy keeps consumers that iterate tournaments able to render
        # the reason/source/alternatives without recomputing the projection.
        "manual_booking_queue": manual_queue,
        # Club-wide source provenance, grouped per provided booking list. A
        # source whose own stated windows overlap is review-required.
        "club_booking_sources": club_sources,
        # Cancelled tournaments keep their historical provenance here, outside
        # the active rows, counts and queue (#842).
        "cancelled_tournaments": cancelled_history,
    }
    if host_key:
        report["host_club"] = host_club
        # Calendar events of this club that the assessment leaves unresolved, so the
        # host view shows every event not cleanly matched to one tournament.
        report["unresolved_calendar_events"] = [
            {
                **{key: row.get(key) for key in ("event_fingerprint", "date", "start", "end", "title", "availability")},
                "reason": "unmatched" if row.get("unmatched") else "multiple_tournaments",
            }
            for row in unresolved_events
            if _club_match_key(row.get("club")) == host_key
        ]
    return report


def _cancelled_tournament_history(
    tournament: Mapping[str, Any],
    *,
    calendar_record: Mapping[str, Any] | None,
    rejected: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the historical record of a cancelled tournament, outside active totals."""

    row: dict[str, Any] = {
        "tournament_id": str(tournament.get("id") or ""),
        "host_club": str(tournament.get("host_club") or ""),
        "age_group": str(tournament.get("age_group") or ""),
        "arena": str(tournament.get("arena") or ""),
        "date": str(tournament.get("date") or ""),
        "start_time": str(tournament.get("start_time") or ""),
        "cancellation_reason": str(tournament.get("cancellation_reason") or ""),
        "rejected_booking_evidence": list(rejected),
    }
    if calendar_record:
        row["calendar_evidence"] = dict(calendar_record)
    return row


def _club_match_key(club: Any) -> str:
    """Normalise a club name for host filtering (case and surrounding space)."""

    return " ".join(str(club or "").split()).casefold()
