"""Read-only calendar booking assessment and classification use cases."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.calendar_bookings import (
    BOOKING_AMBIGUOUS,
    BOOKING_CONFIRMED_BOOKED,
    BOOKING_NOT_CHECKABLE,
    association_findings,
    booking_assessment,
    booking_status_report as _booking_status_report,
    club_calendar_evidence_trusted,
    club_calendar_positive_evidence_usable,
    event_fingerprint,
    find_event,
    iter_events,
    new_booking_evidence_record,
    valid_active_associations,
)
from tournament_scheduler.canonical_ice_time_overrides import (
    overrides_from_problem,
)
from tournament_scheduler.canonical_state import (
    canonical_state_revision,
)

from ..shared import (
    _operator_identity,
    _resolve_plan_problem,
)

from .common import (
    _approved_placement_locked,
    _manual_conflict_with_classification,
    _overlaps,
    _tournament_duration_minutes,
)

def calendar_booking_candidates(
    service,
    *,
    season: str,
    club: str | None = None,
    problem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return deterministic candidate tournaments for scraped calendar bookings."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule["plan"]
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions) or {}
    ice = resolved_problem.get("ice_time_minutes") or {}
    overrides = overrides_from_problem(resolved_problem)
    rows: list[dict[str, Any]] = []
    for event in iter_events(resolved_problem):
        event_club = str(event.get("club") or "")
        if club and event_club != club:
            continue
        if not club_calendar_positive_evidence_usable(resolved_problem, event_club):
            continue
        candidates: list[dict[str, Any]] = []
        for tournament in plan.get("tournaments", []) or []:
            if str(tournament.get("host_club") or "") != str(event.get("club") or ""):
                continue
            if str(tournament.get("date") or "") != str(event.get("date") or ""):
                continue
            start = str(tournament.get("start_time") or "")
            if not start:
                continue
            duration = _tournament_duration_minutes(tournament, ice, overrides)
            if duration <= 0:
                continue
            try:
                t_start_h, t_start_m = (int(p) for p in start.split(":", 1))
                e_start_h, e_start_m = (int(p) for p in str(event.get("start") or "").split(":", 1))
                e_end_h, e_end_m = (int(p) for p in str(event.get("end") or "").split(":", 1))
            except ValueError:
                continue
            t_start = t_start_h * 60 + t_start_m
            t_end = t_start + duration
            e_start = e_start_h * 60 + e_start_m
            e_end = e_end_h * 60 + e_end_m
            if t_start < e_end and e_start < t_end:
                candidates.append(
                    {
                        "id": tournament.get("id"),
                        "age_group": tournament.get("age_group"),
                        "host_club": tournament.get("host_club"),
                        "arena": tournament.get("arena"),
                        "date": tournament.get("date"),
                        "start_time": tournament.get("start_time"),
                        "duration_minutes": duration,
                    }
                )
        if candidates:
            rows.append(
                {
                    "calendar_event": {
                        "title": event.get("calendar_event"),
                        "date": event.get("date"),
                        "start": event.get("start"),
                        "end": event.get("end"),
                        "club": event.get("club"),
                        "availability": event.get("availability"),
                        "fingerprint": event.get("fingerprint") or event_fingerprint(event),
                        "source_positive_evidence_usable": True,
                    },
                    "candidate_tournaments": candidates,
                }
            )
    return {
        "season": season,
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "booking_candidates": rows,
    }

def calendar_booking_assessment(
    service,
    *,
    season: str,
    club: str | None = None,
    problem: dict[str, Any] | None = None,
    date_window_days: int = 7,
) -> dict[str, Any]:
    """Return a read-only, revision/source-bound booking crosswalk for the season.

    This is the #453 assessment boundary: it proposes plausible
    tournament<->event relations (including changed-date/non-overlapping
    candidates, competing mappings, group bookings and unmatched events) but
    never persists an association or claims that calendar absence proves a
    tournament is unbooked. Only the exact canonical revision and each club's
    calendar fingerprint it reports are authoritative; a caller must re-run it
    after any change rather than trusting a cached result.
    """

    snapshot = service.load(season)
    resolved_problem = _resolve_plan_problem(snapshot.schedule, problem, snapshot.decisions)
    return booking_assessment(
        problem=resolved_problem,
        plan=snapshot.schedule.get("plan") or {},
        decisions=snapshot.decisions,
        canonical_state_revision=canonical_state_revision(snapshot.schedule, snapshot.decisions),
        season=season,
        clubs=[club] if club else None,
        date_window_days=date_window_days,
    )

def calendar_booking_findings(service, *, season: str, problem: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = service.load(season)
    resolved_problem = _resolve_plan_problem(snapshot.schedule, problem, snapshot.decisions)
    findings = association_findings(
        problem=resolved_problem,
        plan=snapshot.schedule.get("plan") or {},
        decisions=snapshot.decisions,
    )
    return {"season": season, "findings": findings, "count": len(findings)}

def booking_status_report(service, *, season: str, problem: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = service.load(season)
    resolved_problem = _resolve_plan_problem(snapshot.schedule, problem, snapshot.decisions)
    report = _booking_status_report(
        problem=resolved_problem,
        plan=snapshot.schedule.get("plan") or {},
        decisions=snapshot.decisions,
    )
    report["season"] = season
    report["canonical_state_revision"] = canonical_state_revision(snapshot.schedule, snapshot.decisions)
    return report

def _classify_club_calendar_bookings(
    *,
    plan: Mapping[str, Any],
    resolved_problem: Mapping[str, Any],
    decisions: Mapping[str, Any],
    club: str,
    actor: str | None,
    note: str,
    now: str,
    source_revision: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Classify every tournament hosted by *club* against calendar evidence.

    Pure/read-only: returns ``(rows, records)`` without persisting anything, so
    it can be reused both by the mutating :func:`reconcile_calendar_bookings`
    action and by evidence-only callers (e.g. a promoted-season calendar
    refresh) that must never alter booking decisions.

    The classification is evidence, not booking proof.  A lone busy event that
    merely overlaps a tournament is recorded as ``ambiguous`` so the operator /
    harness can own the semantic match through ``confirm-calendar-booking``;
    only an already-valid explicit association is reported ``confirmed_booked``.

    Absence of an overlapping event is an observation about the *current*
    canonical interval, not a booking outcome for the tournament: the booking
    may have moved, the calendar may be incomplete, or attribution may be
    unresolved.  It is therefore recorded as ``ambiguous`` for every approval
    state, and only explicit source-supported negative/rejection evidence may
    ever assert ``confirmed_not_booked``.
    """

    ice = resolved_problem.get("ice_time_minutes") or {}
    overrides = overrides_from_problem(resolved_problem)
    status = str((resolved_problem.get("club_calendar_status") or {}).get(club) or "")
    # `club_calendar_status` alone is the generator's own verdict; a verifier
    # must stay independent of it, so evidence whose normalized intervals still
    # carry the fabricated fallback fingerprint is not trustworthy either.
    trustworthy = club_calendar_evidence_trusted(resolved_problem, club)
    positive_evidence_usable = club_calendar_positive_evidence_usable(resolved_problem, club)
    fabricated_placeholder = not positive_evidence_usable and status == "known"
    events = [event for event in iter_events(resolved_problem) if str(event.get("club") or "") == club]
    confirmed_by_tournament: dict[str, Mapping[str, Any] | None] = {}
    for record in valid_active_associations(decisions, problem=resolved_problem, plan=plan):
        tournament_id = str(record.get("tournament_id") or "")
        if tournament_id:
            confirmed_by_tournament[tournament_id] = find_event(
                resolved_problem, str(record.get("event_fingerprint") or "")
            )
    resolved_actor = _operator_identity(actor)
    rows: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for tournament in plan.get("tournaments", []) or []:
        if str(tournament.get("host_club") or "") != club:
            continue
        if tournament.get("cancelled"):
            # A cancelled tournament occupies no ice; there is nothing to
            # confirm against a calendar event, and classifying it anyway
            # would persist a meaningless "needs confirmation" booking-
            # evidence record for a slot that no longer exists.
            continue
        tournament_id = str(tournament.get("id") or "")
        if tournament_id in confirmed_by_tournament:
            # Only an explicit operator/harness-validated association is proof that
            # an occupied interval is this tournament; raw overlap is not.
            booking_status = BOOKING_CONFIRMED_BOOKED
            matched_event = confirmed_by_tournament[tournament_id]
            reason = "explicit_calendar_booking_association"
        elif not positive_evidence_usable:
            booking_status = BOOKING_NOT_CHECKABLE
            matched_event = None
            if fabricated_placeholder:
                reason = "fabricated_calendar_placeholder_evidence"
            else:
                reason = f"calendar_status:{status or 'missing'}"
        else:
            overlaps = [event for event in events if _overlaps(tournament, event, ice, overrides)]
            if len(overlaps) == 1:
                # Exactly one busy event overlaps, but occupancy is not proof that
                # the event is this RVV tournament. Keep the candidate visible and
                # require an explicit `confirm-calendar-booking` match.
                booking_status = BOOKING_AMBIGUOUS
                matched_event = overlaps[0]
                reason = "single_overlapping_event_requires_confirmation"
            elif len(overlaps) == 0:
                matched_event = None
                # No event covers the *current* canonical interval. That is a
                # statement about this slot, not proof that the tournament is
                # unbooked elsewhere. Keep it as ambiguity that requires review
                # instead of a final negative; a changed slot, incomplete source
                # or incomplete attribution must remain visible.
                booking_status = BOOKING_AMBIGUOUS
                if not trustworthy:
                    reason = "source_coverage_unproven"
                elif _approved_placement_locked(decisions, tournament_id):
                    reason = "approved_placement_without_calendar_evidence"
                else:
                    reason = "no_covering_event_for_current_slot"
            else:
                booking_status = BOOKING_AMBIGUOUS
                matched_event = None
                reason = "multiple_overlapping_events"
        record = new_booking_evidence_record(
            tournament=tournament,
            status=booking_status,
            problem=resolved_problem,
            actor=resolved_actor,
            note=note,
            checked_at=now,
            source_revision=source_revision,
            event=matched_event,
            reason=reason,
        )
        records.append(record)
        manual_conflict = _manual_conflict_with_classification(
            decisions=decisions,
            resolved_problem=resolved_problem,
            tournament=tournament,
            classified_status=booking_status,
        )
        rows.append(
            {
                "tournament_id": tournament.get("id"),
                "status": booking_status,
                "reason": reason,
                "event_fingerprint": record.get("event_fingerprint"),
                "manual_conflict": manual_conflict,
                # A pre-existing valid association is checked *before* the
                # trustworthy gate above, so it can still report
                # confirmed_booked even when this club's calendar status has
                # since degraded to source_review_required/untrusted. The
                # booking authority is preserved (an association is not
                # invalidated by a later bad scrape), but the degraded source
                # must stay visible as its own concern rather than silently
                # making the row look fully green.
                "source_status": status,
                "source_integrity_concern": not trustworthy,
                "source_positive_evidence_usable": positive_evidence_usable,
                "evidence_reasons": (["event_evidence_usable"] if positive_evidence_usable and not trustworthy else [])
                + (["association_ambiguous"] if booking_status == BOOKING_AMBIGUOUS else []),
                "source_fabricated_placeholder": fabricated_placeholder,
            }
        )
    return rows, records
