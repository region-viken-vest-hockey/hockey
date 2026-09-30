"""Manual booking assertion mutation use cases."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.calendar_bookings import (
    BOOKING_AUTHORITY_MANUAL,
    BOOKING_AUTHORITY_MANUAL_INTERPRETATION,
    MANUAL_ASSERTION_REVOKED,
    MANUAL_ASSERTION_SCOPES,
    MANUAL_ASSERTION_SUPERSEDED,
    MANUAL_BOOKING_ASSERTIONS_KEY,
    MANUAL_BOOKING_STATUS_CHOICES,
    booking_status_report as _booking_status_report,
    club_booking_source_by_id,
    manual_assertion_for_tournament,
    manual_assertion_stale_reasons,
    new_manual_assertion_record,
    validate_stated_interval,
)
from tournament_scheduler.canonical_exception_policy import reclassify_accepted_source_interval
from tournament_scheduler.canonical_state import (
    canonical_state_revision,
)
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.planning_contract import verify_candidate

from ..shared import (
    _operator_identity,
    _now_iso,
    _append_decision_history,
    _resolve_plan_problem,
    _attributable_blockers,
)

from .associations import _align_tournament_to_authoritative_interval
from .common import _record_rejected_booking_evidence

def _manual_assertion_matches_existing(
    existing: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
    *,
    reference: str,
) -> bool:
    """Return whether a repeat assertion is the same deliberate statement."""

    if existing is None:
        return False
    return (
        str(existing.get("booking_status") or "") == str(candidate.get("booking_status") or "")
        and str(existing.get("source_scope") or "tournament") == str(candidate.get("source_scope") or "tournament")
        and dict(existing.get("tournament_facts") or {}) == dict(candidate.get("tournament_facts") or {})
        and dict(existing.get("asserted_interval") or {}) == dict(candidate.get("asserted_interval") or {})
        and dict(existing.get("stated_interval") or {}) == dict(candidate.get("stated_interval") or {})
        and str(existing.get("reference") or "") == reference
        and str(existing.get("source_assertion_id") or "") == str(candidate.get("source_assertion_id") or "")
    )

def set_manual_booking_assertion(
    service,
    *,
    season: str,
    tournament_id: str,
    booking_status: str,
    actor: str | None = None,
    note: str = "",
    reference: str = "",
    source_scope: str = "tournament",
    source_assertion_id: str | None = None,
    stated_date: str | None = None,
    stated_start: str | None = None,
    stated_end: str | None = None,
    expected_revision: str | None = None,
    supersede: bool = False,
    problem: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Record an explicit operator/club booking assertion for one tournament.

    The assertion is durable, revision-bound source evidence rather than a
    scrape result.  It never changes the schedule, approval state or
    participants; the booking-status projection reports it as
    ``manually_booked``/``manually_not_booked`` and a routine calendar
    reconcile/refresh cannot erase or demote it.  A material canonical change
    (date/start/duration/host/arena) invalidates it until the operator confirms
    the new slot again; a stale assertion is replaced directly (retaining its
    audit history) rather than requiring ``--supersede``, which is reserved for
    changing the conclusion about the same still-current slot. Repeating the
    identical assertion is a no-op; changing it requires an explicit
    ``supersede`` with a reason. Positive confirmations must carry a traceable
    source reference or rationale.
    """

    if booking_status not in MANUAL_BOOKING_STATUS_CHOICES:
        raise SeasonStateError(
            "Unknown manual booking status: "
            f"{booking_status!r}; expected one of {', '.join(MANUAL_BOOKING_STATUS_CHOICES)}"
        )
    if source_scope not in MANUAL_ASSERTION_SCOPES:
        raise SeasonStateError(
            f"Unknown manual assertion source scope: {source_scope!r}; "
            f"expected one of {', '.join(MANUAL_ASSERTION_SCOPES)}"
        )
    linked_source_id = str(source_assertion_id or "").strip() or None
    try:
        stated_interval = validate_stated_interval(stated_start, stated_end, stated_date) or None
    except ValueError as exc:
        raise SeasonStateError(str(exc)) from exc

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    current_revision = canonical_state_revision(schedule, decisions)
    if expected_revision and expected_revision != current_revision:
        raise SeasonStateError(
            f"Stale canonical revision: expected {expected_revision}, current is {current_revision}"
        )
    plan = schedule["plan"]
    tournament = next(
        (t for t in plan.get("tournaments", []) or [] if str(t.get("id")) == tournament_id),
        None,
    )
    if tournament is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    if tournament.get("cancelled"):
        raise SeasonStateError(
            f"Tournament {tournament_id} is cancelled and cannot carry a booking assertion"
        )
    if linked_source_id is not None:
        source = club_booking_source_by_id(decisions, linked_source_id)
        if source is None:
            raise SeasonStateError(
                f"Unknown active club booking source assertion: {linked_source_id}"
            )
        if str(source.get("host_club") or "") != str(tournament.get("host_club") or ""):
            raise SeasonStateError(
                "A manual booking assertion may only link to a club booking source for "
                "the same host club"
            )
        if source_scope != "club_wide_interpretation":
            raise SeasonStateError(
                "A manual assertion linked to a club-wide booking source must use "
                "--source-scope club_wide_interpretation"
            )
    if not (str(reference).strip() or str(note).strip()):
        raise SeasonStateError(
            "A manual booking assertion requires a traceable source reference (--reference) "
            "or rationale (--note)"
        )
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    source_interval_alignment: dict[str, Any] | None = None
    override_record: dict[str, Any] | None = None
    booking_feasibility_warnings: list[dict[str, Any]] = []
    if booking_status == "booked" and stated_interval:
        interval_date = str(stated_interval.get("date") or tournament.get("date") or "")
        request_payload = {
            "source_assertion_id": linked_source_id or "",
            "reference": reference or "",
            "date": interval_date,
            "start": stated_interval.get("start"),
            "end": stated_interval.get("end"),
        }
        updated_schedule, updated_decisions_for_interval, aligned_tournament, source_interval_alignment, override_record = _align_tournament_to_authoritative_interval(
            schedule=schedule,
            decisions=decisions,
            tournament_id=tournament_id,
            interval={**dict(stated_interval), "date": interval_date},
            actor=resolved_actor,
            note=note,
            now=now,
            problem=resolved_problem,
            authority="manual_club_confirmation_interval",
            request_id=f"manual-booking:{tournament_id}:{stable_payload_sha256(request_payload)}",
            reference=reference or note or "manual booking assertion",
            accepted_key="accepted_source_interval",
            freeze_default_equal_interval=True,
        )
        verification_problem = _resolve_plan_problem(updated_schedule, resolved_problem, updated_decisions_for_interval)
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
            accepted_interval=(source_interval_alignment or {}).get("accepted_source_interval"),
            authority=(
                BOOKING_AUTHORITY_MANUAL_INTERPRETATION
                if source_scope == "club_wide_interpretation"
                else BOOKING_AUTHORITY_MANUAL
            ),
        )
        booking_feasibility_warnings = classified["accepted_exceptions"]
        if booking_feasibility_warnings:
            verification = {**verification, "violations": classified["blocking_violations"]}
        hard_blockers, unresolved_blockers = _attributable_blockers(verification, tournament_id)
        blockers = hard_blockers + unresolved_blockers
        if blockers:
            messages = "; ".join(str(blocker.get("message") or blocker.get("code")) for blocker in blockers)
            _record_rejected_booking_evidence(
                service,
                snapshot=snapshot,
                tournament=tournament,
                authority=(
                    BOOKING_AUTHORITY_MANUAL_INTERPRETATION
                    if source_scope == "club_wide_interpretation"
                    else BOOKING_AUTHORITY_MANUAL
                ),
                reference=reference or note,
                source_assertion_id=linked_source_id,
                proposed_interval=(source_interval_alignment or {}).get("accepted_source_interval"),
                blockers=blockers,
                actor=resolved_actor,
                now=now,
                source_revision=current_revision,
                dry_run=dry_run,
            )
            raise SeasonStateError(f"Refusing to record booking assertion for {tournament_id}: {messages}")
        schedule = updated_schedule
        decisions = updated_decisions_for_interval
        plan = schedule["plan"]
        resolved_problem = verification_problem
        tournament = aligned_tournament
    existing = manual_assertion_for_tournament(decisions, tournament_id)
    existing_stale_reasons = (
        manual_assertion_stale_reasons(existing, problem=resolved_problem, tournament=tournament)
        if existing is not None
        else []
    )
    candidate = new_manual_assertion_record(
        tournament=tournament,
        booking_status=booking_status,
        problem=resolved_problem,
        actor=resolved_actor,
        note=note,
        reference=reference,
        source_scope=source_scope,
        stated_interval=stated_interval,
        asserted_at=now,
        source_revision=current_revision,
        source_assertion_id=linked_source_id,
    )
    if booking_feasibility_warnings:
        candidate["booking_feasibility_warnings"] = [
            str(blocker.get("code") or "") for blocker in booking_feasibility_warnings
        ]
    if _manual_assertion_matches_existing(existing, candidate, reference=reference):
        return {
            "season": season,
            "dry_run": bool(dry_run),
            "changed": False,
            "idempotent": True,
            "tournament_id": tournament_id,
            "assertion": existing,
            "canonical_state_revision": current_revision,
        }
    if existing is not None:
        if not existing_stale_reasons and not supersede:
            raise SeasonStateError(
                f"Tournament {tournament_id} already has an active manual booking assertion "
                f"({existing.get('booking_status')!r}); repeat it unchanged or pass --supersede "
                "with a reason to replace it"
            )
        if not existing_stale_reasons and not note:
            raise SeasonStateError("Superseding a manual booking assertion requires a --note reason")
        candidate["supersedes"] = str(existing.get("id") or "")
        candidate["reconfirms_stale_reasons"] = list(existing_stale_reasons)

    updated = dict(decisions)
    records = [
        dict(record)
        for record in updated.get(MANUAL_BOOKING_ASSERTIONS_KEY) or []
        if isinstance(record, Mapping)
    ]
    if existing is not None:
        existing_id = str(existing.get("id") or "")
        supersede_reason = note or (
            "re-confirmed after canonical slot change: " + ", ".join(existing_stale_reasons)
            if existing_stale_reasons
            else "superseded"
        )
        for record in records:
            if str(record.get("id") or "") == existing_id:
                record["status"] = MANUAL_ASSERTION_SUPERSEDED
                record["superseded_at"] = now
                record["superseded_by"] = candidate["id"]
                record["supersede_reason"] = supersede_reason
                if existing_stale_reasons:
                    record["superseded_stale_reasons"] = list(existing_stale_reasons)
    records.append(candidate)
    updated[MANUAL_BOOKING_ASSERTIONS_KEY] = records
    updated["updated_at"] = now
    _append_decision_history(
        updated,
        event="set_manual_booking_assertion",
        tournament_id=tournament_id,
        actor=resolved_actor,
        now=now,
        note=note,
        details={
            "booking_status": booking_status,
            "authority": candidate["authority"],
            "source_scope": candidate["source_scope"],
            "reference": reference or "",
            "asserted_interval": candidate["asserted_interval"],
            "stated_interval": candidate["stated_interval"],
            "supersedes": candidate.get("supersedes") or "",
            "reconfirms_stale_reasons": candidate.get("reconfirms_stale_reasons") or [],
            "interval_alignment": source_interval_alignment,
            "ice_time_override_id": (override_record or {}).get("id") or "",
            "booking_feasibility_warnings": booking_feasibility_warnings,
        },
    )
    result: dict[str, Any] = {
        "season": season,
        "dry_run": bool(dry_run),
        "changed": True,
        "idempotent": False,
        "tournament_id": tournament_id,
        "assertion": candidate,
        "previous_assertion": existing,
        "canonical_state_revision": current_revision,
        "interval_alignment": source_interval_alignment,
        "ice_time_override": override_record,
        "booking_feasibility_warnings": booking_feasibility_warnings,
    }
    result["booking_status"] = _booking_status_report(
        problem=resolved_problem,
        plan=plan,
        decisions=updated,
    )
    if dry_run:
        return result
    updated_snapshot = snapshot.with_schedule(schedule).with_decisions(updated)
    if source_interval_alignment is not None:
        service._assert_published_sealed_reconciliation(updated_snapshot, action="set_manual_booking_assertion")
    committed = service._commit(updated_snapshot)
    committed_problem = _resolve_plan_problem(committed.schedule, problem, committed.decisions)
    result["canonical_state_revision"] = canonical_state_revision(committed.schedule, committed.decisions)
    result["booking_status"] = _booking_status_report(
        problem=committed_problem,
        plan=committed.schedule.get("plan") or {},
        decisions=committed.decisions,
    )
    return result

def clear_manual_booking_assertion(
    service,
    *,
    season: str,
    tournament_id: str,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Revoke one active manual booking assertion without touching the schedule."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    current_revision = canonical_state_revision(schedule, decisions)
    tournament = next(
        (t for t in schedule["plan"].get("tournaments", []) or [] if str(t.get("id")) == tournament_id),
        None,
    )
    if tournament is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    existing = manual_assertion_for_tournament(decisions, tournament_id)
    if existing is None:
        return {
            "season": season,
            "dry_run": bool(dry_run),
            "changed": False,
            "tournament_id": tournament_id,
            "revoked": None,
            "canonical_state_revision": current_revision,
        }
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    updated = dict(decisions)
    records = [
        dict(record)
        for record in updated.get(MANUAL_BOOKING_ASSERTIONS_KEY) or []
        if isinstance(record, Mapping)
    ]
    existing_id = str(existing.get("id") or "")
    revoked: dict[str, Any] | None = None
    for record in records:
        if str(record.get("id") or "") == existing_id:
            record["status"] = MANUAL_ASSERTION_REVOKED
            record["revoked_at"] = now
            record["revoked_by"] = resolved_actor
            record["revoke_reason"] = note
            revoked = record
    updated[MANUAL_BOOKING_ASSERTIONS_KEY] = records
    updated["updated_at"] = now
    _append_decision_history(
        updated,
        event="clear_manual_booking_assertion",
        tournament_id=tournament_id,
        actor=resolved_actor,
        now=now,
        note=note,
        details={"assertion_id": existing_id},
    )
    result: dict[str, Any] = {
        "season": season,
        "dry_run": bool(dry_run),
        "changed": True,
        "tournament_id": tournament_id,
        "revoked": revoked,
        "previous_assertion": existing,
        "canonical_state_revision": current_revision,
    }
    if dry_run:
        return result
    committed = service._commit(snapshot.with_decisions(updated))
    result["canonical_state_revision"] = canonical_state_revision(committed.schedule, committed.decisions)
    return result
