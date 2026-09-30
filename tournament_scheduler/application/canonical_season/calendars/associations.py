"""Calendar event association confirmation and release use cases."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from tournament_scheduler.calendar_bookings import (
    BOOKING_AUTHORITY_CALENDAR,
    BOOKING_CONFIRMED_BOOKED,
    CALENDAR_BOOKING_ASSOCIATIONS_KEY,
    TOURNAMENT_BOOKING_EVIDENCE_KEY,
    club_calendar_positive_evidence_usable,
    event_fingerprint,
    find_event,
    new_association_record,
    new_booking_evidence_record,
    tournament_occupancy_interval_facts,
)
from tournament_scheduler.canonical_exception_policy import reclassify_accepted_source_interval
from tournament_scheduler.calendar_availability import CalendarAvailability, interval_availability
from tournament_scheduler.canonical_baseline import approval_fingerprint
from tournament_scheduler.canonical_ice_time_overrides import (
    project_overrides_into_problem,
    override_for_tournament,
    validate_and_normalize as validate_ice_time_override,
)
from tournament_scheduler.canonical_state import (
    canonical_state_revision,
    schedule_fingerprint,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.planning_contract import verify_candidate

from ..ice_time import _minimum_override_minutes, _record_override_decision
from ..shared import (
    APPROVED_STATUS,
    _operator_identity,
    _now_iso,
    _append_decision_history,
    _resolve_plan_problem,
    _attributable_blockers,
)

from .common import _event_interval, _record_rejected_booking_evidence

_CALENDAR_EVENT_AGE_TOKEN_RE = re.compile(r"\b(J?U\d{1,2})\b", re.IGNORECASE)

def _calendar_event_age_tokens(title: str) -> set[str]:
    """Extract age-group tokens (``U10``, ``JU12``, ...) from a calendar title."""

    return {match.upper() for match in _CALENDAR_EVENT_AGE_TOKEN_RE.findall(str(title or ""))}

def _auto_associate_own_calendar_event(
    *,
    decisions: dict[str, Any],
    problem: Mapping[str, Any] | None,
    tournament: Mapping[str, Any],
    actor: str,
    note: str,
    source_revision: str,
) -> dict[str, Any]:
    """Link an unambiguous same-club/date/age-group calendar event to *tournament*.

    A manual/worksheet-confirmed interval has no calendar event of its own, so a
    still-unassociated scraped event for the same host on the same date -- almost
    always this tournament's own real Askerhallen block -- would otherwise be
    misread as a third-party ``manual_external_conflict_placements`` conflict
    once the interval is aligned (issue #556 follow-up). Only ever link when
    exactly one fixed-busy event on that date carries this tournament's exact
    age-group token, the club's calendar evidence is trustworthy enough to
    support a positive claim (mirrors ``confirm_calendar_booking``'s own gate),
    the event's arena/location does not contradict the tournament's arena when
    the event actually states one, and the event is not already claimed by a
    different tournament. A genuinely ambiguous day (several same-age events,
    an untrusted source, a venue mismatch, or none) is left for the operator
    rather than guessed at.
    """

    club = str(tournament.get("host_club") or "")
    date = str(tournament.get("date") or "")
    arena = str(tournament.get("arena") or "")
    age_group = str(tournament.get("age_group") or "").upper()
    tournament_id = str(tournament.get("id") or "")
    if not club or not date or not age_group or not tournament_id or problem is None:
        return decisions
    if not club_calendar_positive_evidence_usable(problem, club):
        return decisions
    busy = ((problem.get("club_busy_intervals") or {}).get(club)) or []
    candidates = []
    for entry in busy:
        if not isinstance(entry, Mapping) or str(entry.get("date") or "") != date:
            continue
        if interval_availability(entry) != CalendarAvailability.FIXED_BUSY:
            continue
        event_venue = str(entry.get("arena") or entry.get("location") or "").strip()
        if event_venue and arena and event_venue.lower() != arena.strip().lower():
            continue
        if age_group in _calendar_event_age_tokens(entry.get("calendar_event") or entry.get("title")):
            candidates.append(entry)
    if len(candidates) != 1:
        return decisions
    event = dict(candidates[0])
    event["club"] = club
    event["fingerprint"] = event_fingerprint(event)
    # Raw, unfiltered ownership check: a staleness-projected "valid
    # associations" view can omit a still-active record held by a different
    # tournament (issue #558 review), which must never be silently reclaimed.
    for record in decisions.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or []:
        if not isinstance(record, Mapping) or str(record.get("status") or "active") != "active":
            continue
        if str(record.get("event_fingerprint") or "") != event["fingerprint"]:
            continue
        owner = str(record.get("tournament_id") or "")
        if owner:
            return decisions
    updated = dict(decisions)
    records = [
        dict(record)
        for record in updated.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or []
        if isinstance(record, Mapping)
    ]
    records.append(
        new_association_record(
            event=event,
            tournament=tournament,
            actor=actor,
            note=note
            or "Auto-linked: unambiguous same-club/date/age-group calendar event "
            "matches this manually confirmed booking",
            source_revision=source_revision,
            problem=problem,
        )
    )
    updated[CALENDAR_BOOKING_ASSOCIATIONS_KEY] = records
    return updated

def _align_tournament_to_authoritative_interval(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    tournament_id: str,
    interval: Mapping[str, Any],
    actor: str,
    note: str,
    now: str,
    problem: Mapping[str, Any] | None,
    authority: str,
    request_id: str,
    reference: str,
    accepted_key: str,
    freeze_default_equal_interval: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Return schedule/decisions aligned to an accepted authoritative interval."""

    updated_schedule = copy.deepcopy(dict(schedule))
    updated_plan = updated_schedule.setdefault("plan", {})
    tournaments = updated_plan.setdefault("tournaments", [])
    target = next((t for t in tournaments if str(t.get("id") or "") == tournament_id), None)
    if target is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    before = dict(target)
    interval_date = str(interval.get("date") or "")
    interval_start = str(interval.get("start") or interval.get("start_time") or "")
    interval_end = str(interval.get("end") or interval.get("end_time") or "")
    parsed = _event_interval({"start": interval_start, "end": interval_end})
    if not interval_date or not interval_start or not interval_end or parsed is None or parsed[1] <= parsed[0]:
        raise SeasonStateError("Authoritative booking interval has an invalid date/start/end interval")
    interval_minutes = parsed[1] - parsed[0]

    target["date"] = interval_date
    target["start_time"] = interval_start

    updated_decisions = dict(decisions)
    override_record: dict[str, Any] | None = None
    existing_override = override_for_tournament(decisions, tournament_id)
    previous_interval = tournament_occupancy_interval_facts(before, problem)
    try:
        effective_minutes = int(previous_interval.get("duration_minutes") or 0)
    except (TypeError, ValueError):
        effective_minutes = 0
    if effective_minutes != interval_minutes or (freeze_default_equal_interval and existing_override is None):
        normalized = validate_ice_time_override(
            {
                "tournament_id": tournament_id,
                "minutes": interval_minutes,
                "request_id": request_id,
                "note": note,
                "reference": reference,
            }
        )
        age_group = str(target.get("age_group") or "")
        # Source-authoritative booked intervals are recorded exactly. Planning
        # floors remain visible as independent feasibility warnings instead of
        # stretching real ice time to the default occupancy.
        default_minutes = ((problem or {}).get("ice_time_minutes") or {}).get(age_group)
        override_record = _record_override_decision(
            updated_decisions,
            normalized=normalized,
            tournament_id=tournament_id,
            age_group=age_group,
            default_minutes=default_minutes,
            minimum_minutes=_minimum_override_minutes(target, problem),
            existing=existing_override,
            actor=actor,
            now=now,
            note=note,
            authority=authority,
        )

    alignment = {
        "previous_interval": previous_interval,
        accepted_key: {
            "date": interval_date,
            "start_time": interval_start,
            "duration_minutes": interval_minutes,
            "end_time": interval_end,
        },
        "changed": before.get("date") != target.get("date")
        or before.get("start_time") != target.get("start_time")
        or override_record is not None,
    }
    updated_decisions["schedule_fingerprint"] = schedule_fingerprint(updated_plan)
    if authority == "manual_club_confirmation_interval":
        updated_decisions = _auto_associate_own_calendar_event(
            decisions=updated_decisions,
            problem=project_overrides_into_problem(problem, updated_decisions) if problem is not None else None,
            tournament=target,
            actor=actor,
            note=note,
            source_revision=canonical_state_revision(schedule, decisions),
        )
    return updated_schedule, updated_decisions, target, alignment, override_record

def _align_tournament_to_calendar_event(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    tournament_id: str,
    event: Mapping[str, Any],
    actor: str,
    note: str,
    now: str,
    problem: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Return schedule/decisions aligned to an accepted authoritative event."""

    return _align_tournament_to_authoritative_interval(
        schedule=schedule,
        decisions=decisions,
        tournament_id=tournament_id,
        interval={"date": event.get("date"), "start": event.get("start"), "end": event.get("end")},
        actor=actor,
        note=note,
        now=now,
        problem=problem,
        authority="calendar_event_association",
        request_id=f"calendar-booking:{str(event.get('fingerprint') or event_fingerprint(event))}",
        reference=str(event.get("calendar_event") or event.get("title") or "calendar event"),
        accepted_key="accepted_calendar_interval",
        freeze_default_equal_interval=True,
    )

def confirm_calendar_booking(
    service,
    *,
    season: str,
    event_fingerprint: str,
    tournament_id: str,
    actor: str | None = None,
    note: str = "",
    problem: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Bind one scraped calendar event to one canonical tournament and approve it."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule["plan"]
    base_problem = _resolve_plan_problem(schedule, problem, decisions)
    event = find_event(base_problem, event_fingerprint)
    if event is None:
        raise SeasonStateError(f"Unknown calendar event fingerprint: {event_fingerprint}")
    original_tournament = next(
        (t for t in plan.get("tournaments", []) if str(t.get("id")) == tournament_id), None
    )
    if original_tournament is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    # A cancelled tournament is not an active placement. Binding and approving
    # it here would leave a spurious approval/placement lock that the candidate
    # verifier (which excludes cancelled tournaments) never sees, but that
    # ``canonical_locked_tournament_missing`` then reports as a hard violation.
    # Validate the lifecycle fact before any alignment or decision write, the
    # same way the other canonical mutation use cases refuse a cancelled target.
    if original_tournament.get("cancelled"):
        raise SeasonStateError(
            f"Tournament {tournament_id} is cancelled and cannot be confirmed as a booking"
        )
    event_club = str(event.get("club") or "")
    if not club_calendar_positive_evidence_usable(base_problem, event_club):
        raise SeasonStateError(
            "Calendar booking source cannot support positive evidence"
        )
    if event_club != str(original_tournament.get("host_club") or ""):
        raise SeasonStateError("Calendar booking is not compatible with tournament: host_mismatch")

    for record in decisions.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or []:
        if not isinstance(record, Mapping) or record.get("status", "active") != "active":
            continue
        if str(record.get("event_fingerprint") or "") != event_fingerprint:
            continue
        existing_tournament_id = str(record.get("tournament_id") or "")
        if existing_tournament_id and existing_tournament_id != tournament_id:
            raise SeasonStateError(
                "Calendar event already has an active tournament association; "
                "release it before rebinding"
            )

    resolved_actor = _operator_identity(actor)
    checked_at = _now_iso()
    updated_schedule, updated, tournament, interval_alignment, override_record = _align_tournament_to_calendar_event(
        schedule=schedule,
        decisions=decisions,
        tournament_id=tournament_id,
        event=event,
        actor=resolved_actor,
        note=note,
        now=checked_at,
        problem=base_problem,
    )
    updated_problem = _resolve_plan_problem(updated_schedule, base_problem, updated)
    assoc = new_association_record(
        event=event,
        tournament=tournament,
        actor=resolved_actor,
        note=note,
        source_revision=canonical_state_revision(schedule, decisions),
        problem=updated_problem,
    )
    records = [
        dict(record)
        for record in (updated.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or [])
        if not (
            str(record.get("event_fingerprint") or "") == event_fingerprint
            and str(record.get("tournament_id") or "") == tournament_id
        )
    ]
    records.append(assoc)
    updated[CALENDAR_BOOKING_ASSOCIATIONS_KEY] = records
    evidence = new_booking_evidence_record(
        tournament=tournament,
        status=BOOKING_CONFIRMED_BOOKED,
        problem=updated_problem,
        actor=resolved_actor,
        note=note,
        checked_at=checked_at,
        source_revision=canonical_state_revision(schedule, decisions),
        event=event,
        reason="operator_confirmed_calendar_booking_association",
    )
    prior_evidence = [
        dict(record)
        for record in updated.get(TOURNAMENT_BOOKING_EVIDENCE_KEY) or []
        if not (isinstance(record, Mapping) and str(record.get("tournament_id") or "") == tournament_id)
    ]
    updated[TOURNAMENT_BOOKING_EVIDENCE_KEY] = prior_evidence + [evidence]

    verification_problem = _resolve_plan_problem(updated_schedule, base_problem, updated)
    verification = verify_candidate(updated_schedule["plan"], verification_problem) if verification_problem else verify_candidate(updated_schedule["plan"])
    # A source-authoritative interval below the governing *planning* floor is
    # recorded exactly and surfaced as a durable feasibility finding, never a
    # new-placement planning violation. The shared policy owner matches the
    # exact accepted facts from the projected canonical evidence; the planning
    # verifier stays strict for proposals, and genuine playing shortfalls or
    # overlaps remain hard blockers.
    classified = reclassify_accepted_source_interval(
        verification_problem,
        updated_schedule["plan"],
        list(verification.get("violations") or []),
        tournament_id=tournament_id,
        accepted_interval=(interval_alignment or {}).get("accepted_calendar_interval"),
        authority=BOOKING_AUTHORITY_CALENDAR,
    )
    booking_feasibility_warnings = classified["accepted_exceptions"]
    if booking_feasibility_warnings:
        verification = {**verification, "violations": classified["blocking_violations"]}
    hard_blockers, unresolved_blockers = _attributable_blockers(verification, tournament_id)
    # The deviation must stay durably attached to the booking evidence as
    # independent follow-up rather than being silently absorbed. The persisted
    # override already records its `minimum_minutes`.
    if booking_feasibility_warnings:
        warning_codes = [str(blocker.get("code") or "") for blocker in booking_feasibility_warnings]
        assoc["booking_feasibility_warnings"] = warning_codes
        evidence["booking_feasibility_warnings"] = warning_codes
    blockers = hard_blockers + unresolved_blockers
    if blockers:
        messages = "; ".join(str(blocker.get("message") or blocker.get("code")) for blocker in blockers)
        _record_rejected_booking_evidence(
            service,
            snapshot=snapshot,
            tournament=tournament,
            authority=BOOKING_AUTHORITY_CALENDAR,
            reference=str(event.get("calendar_event") or event.get("title") or "calendar event"),
            source_assertion_id=None,
            proposed_interval=(interval_alignment or {}).get("accepted_calendar_interval"),
            blockers=blockers,
            actor=resolved_actor,
            now=checked_at,
            source_revision=canonical_state_revision(schedule, decisions),
            dry_run=dry_run,
        )
        raise SeasonStateError(f"Refusing to confirm booking for {tournament_id}: {messages}")

    approved_at = checked_at
    tournament_fingerprint = approval_fingerprint(tournament)
    previous = dict(decisions["decisions"].get(tournament_id, {}))
    record = dict(previous)
    record.update(
        {
            "status": APPROVED_STATUS,
            "placement_locked": True,
            "participants_locked": False,
            "approved_fingerprint": tournament_fingerprint,
            "approved_at": approved_at,
            "approved_by": resolved_actor,
            "note": note,
        }
    )
    record.pop("stale_at", None)
    record.pop("stale_reason", None)
    record.pop("unapproved_at", None)
    record.pop("unapproved_by", None)
    updated["decisions"] = dict(updated.get("decisions", {}))
    updated["decisions"][tournament_id] = record
    updated["updated_at"] = approved_at
    _append_decision_history(
        updated,
        event="confirm_calendar_booking",
        tournament_id=tournament_id,
        actor=resolved_actor,
        now=approved_at,
        tournament_fingerprint=tournament_fingerprint,
        previous_fingerprint=previous.get("approved_fingerprint"),
        note=note,
        details={
            "event_fingerprint": event_fingerprint,
            "calendar_event": event.get("calendar_event"),
            "interval_alignment": interval_alignment,
            "ice_time_override_id": (override_record or {}).get("id") or "",
            "booking_feasibility_warnings": booking_feasibility_warnings,
        },
    )
    result_preview = {
        "season": season,
        "dry_run": bool(dry_run),
        "association": assoc,
        "approved": record,
        "interval_alignment": interval_alignment,
        "ice_time_override": override_record,
        "booking_feasibility_warnings": booking_feasibility_warnings,
    }
    if dry_run:
        return result_preview
    updated_snapshot = snapshot.with_schedule(updated_schedule).with_decisions(updated)
    service._assert_published_sealed_reconciliation(updated_snapshot, action="confirm_calendar_booking")
    committed = service._commit(updated_snapshot)
    return {
        "season": season,
        "dry_run": False,
        "association": assoc,
        "approved": committed.decisions.get("decisions", {}).get(tournament_id),
        "interval_alignment": interval_alignment,
        "ice_time_override": override_record,
        "booking_feasibility_warnings": booking_feasibility_warnings,
        "canonical_state_revision": canonical_state_revision(committed.schedule, committed.decisions),
    }

def release_calendar_booking(
    service,
    *,
    season: str,
    event_fingerprint: str,
    tournament_id: str | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Release an active event->tournament association before rebinding."""

    snapshot = service.load(season)
    decisions = snapshot.decisions
    resolved_actor = _operator_identity(actor)
    now = _now_iso()
    updated = dict(decisions)
    records: list[dict[str, Any]] = []
    released: list[dict[str, Any]] = []
    for record in updated.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or []:
        if not isinstance(record, Mapping):
            continue
        row = dict(record)
        matches = str(row.get("event_fingerprint") or "") == event_fingerprint and row.get("status", "active") == "active"
        if tournament_id is not None:
            matches = matches and str(row.get("tournament_id") or "") == tournament_id
        if matches:
            row["status"] = "released"
            row["released_at"] = now
            row["released_by"] = resolved_actor
            row["release_note"] = note or ""
            released.append(row)
        records.append(row)
    if not released:
        raise SeasonStateError("No active calendar booking association matched the release request")
    updated[CALENDAR_BOOKING_ASSOCIATIONS_KEY] = records
    updated["updated_at"] = now
    for row in released:
        _append_decision_history(
            updated,
            event="release_calendar_booking",
            tournament_id=str(row.get("tournament_id") or ""),
            actor=resolved_actor,
            now=now,
            note=note,
            details={"event_fingerprint": event_fingerprint, "association_id": row.get("id")},
        )
    result = {"season": season, "dry_run": dry_run, "released": released}
    if dry_run:
        return result
    committed = service._commit(snapshot.with_decisions(updated))
    result["canonical_state_revision"] = canonical_state_revision(committed.schedule, committed.decisions)
    return result
