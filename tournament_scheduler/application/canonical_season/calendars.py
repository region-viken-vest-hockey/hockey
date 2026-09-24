"""Promoted-season calendar evidence refresh and booking reconciliation."""

from __future__ import annotations

import copy
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from tournament_scheduler.calendar_bookings import (
    BOOKING_AMBIGUOUS,
    BOOKING_CONFIRMED_BOOKED,
    BOOKING_CONFIRMED_NOT_BOOKED,
    BOOKING_NOT_CHECKABLE,
    CALENDAR_BOOKING_ASSOCIATIONS_KEY,
    TOURNAMENT_BOOKING_EVIDENCE_KEY,
    association_findings,
    booking_status_report as _booking_status_report,
    event_covers_tournament_interval,
    event_fingerprint,
    find_event,
    iter_events,
    new_association_record,
    new_booking_evidence_record,
    valid_active_associations,
)
from tournament_scheduler.canonical_baseline import approval_fingerprint
from tournament_scheduler.canonical_state import (
    canonical_state_revision,
    schedule_fingerprint,
)
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.planning_contract import verify_candidate

from .shared import (
    APPROVED_STATUS,
    _operator_identity,
    _now_iso,
    _append_decision_history,
    _resolve_plan_problem,
    _parse_iso_date_for_move,
    _attributable_blockers,
)

_CALENDAR_PROBLEM_KEYS = (
    "club_busy_dates",
    "club_busy_intervals",
    "club_calendar_status",
    "unclassified_calendar_events",
)


def _calendar_problem_payload(problem: Mapping[str, Any] | None) -> dict[str, Any]:
    return {key: copy.deepcopy((problem or {}).get(key)) for key in _CALENDAR_PROBLEM_KEYS}


def _calendar_source_summaries(scrape: Mapping[str, Any], *, fetched_at: str) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for source in scrape.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        payload = {
            "name": source.get("name"),
            "url": source.get("url"),
            "type": source.get("type"),
            "events": source.get("events") or [],
            "blocked": bool(source.get("blocked")),
            "event_count": int(source.get("event_count") or len(source.get("events") or [])),
        }
        summaries.append(
            {
                "name": str(source.get("name") or ""),
                "type": str(source.get("type") or ""),
                "url": str(source.get("url") or ""),
                "event_count": payload["event_count"],
                "blocked": payload["blocked"],
                "fetched_at": str(source.get("scrape_timestamp") or fetched_at),
                "fingerprint": stable_payload_sha256(payload),
            }
        )
    return sorted(summaries, key=lambda item: (item["name"], item["type"], item["url"]))


def refresh_calendars(
    service,
    *,
    season: str,
    input_path: str | os.PathLike[str] = "input.xlsx",
    work_dir: str | os.PathLike[str] | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
    allow_missing_sources: bool = False,
) -> dict[str, Any]:
    """Refresh promoted-season calendar evidence without changing the schedule.

    The promoted plan/decision state remains the operational truth.  Only
    the verification-context calendar facts are rebuilt from a fresh Stage 2
    scrape, preserving the previous evidence fingerprint in history and
    advancing the canonical-state revision on commit.
    """

    from tournament_scheduler.pipeline import stage1_config, stage2_scraping
    from tournament_scheduler.pipeline.state import PipelineState
    from tournament_scheduler.planning_contract import build_planning_problem, verify_candidate
    from tournament_scheduler.season_maintenance import list_findings

    snapshot = service.load(season)
    schedule = copy.deepcopy(snapshot.schedule)
    decisions = copy.deepcopy(snapshot.decisions)
    plan = copy.deepcopy(schedule.get("plan") or {})
    context = copy.deepcopy(schedule.get("verification_context") or {})
    problem = copy.deepcopy(context.get("problem") or {})
    if not isinstance(problem, dict) or not problem:
        raise SeasonStateError("Canonical season carries no verification-context problem to refresh")

    start = _parse_iso_date_for_move(str(problem.get("start_date") or plan.get("start_date")), "start_date")
    end = _parse_iso_date_for_move(str(problem.get("end_date") or plan.get("end_date")), "end_date")
    before_calendar = _calendar_problem_payload(problem)
    before_fingerprint = stable_payload_sha256(before_calendar)
    before_schedule_fingerprint = schedule_fingerprint(plan)
    fetched_at = _now_iso()

    def _scrape_in(workspace: Path) -> dict[str, Any]:
        state = PipelineState(workspace)
        stage1_config.run(input_path, state, strict=True)
        config = stage1_config.load_effective_config(state, input_path=input_path)
        config["start_date"] = start.isoformat()
        config["end_date"] = end.isoformat()
        # Keep the promoted planning contract's non-source facts authoritative
        # for problem reconstruction; the current workbook supplies sources.
        for key in (
            "teams",
            "age_groups",
            "parallel_games",
            "rounds_per_tournament",
            "round_length_minutes",
            "ice_time_minutes",
            "participation_targets_by_age_group",
        ):
            if key in problem:
                config[key] = copy.deepcopy(problem[key])
        scrape = stage2_scraping.run(
            config,
            state,
            datetime.combine(start, datetime.min.time()),
            datetime.combine(end, datetime.min.time()),
            strict=not allow_missing_sources,
            allow_missing_sources=allow_missing_sources,
            force_refresh=True,
        )
        rebuilt = build_planning_problem(
            config,
            scrape,
            start,
            end,
            waivers=problem.get("operator_waivers") or [],
            canonical_baseline=problem.get("canonical_baseline") if isinstance(problem.get("canonical_baseline"), dict) else None,
        )
        return {"scrape": scrape, "rebuilt_problem": rebuilt}

    if work_dir is None:
        with tempfile.TemporaryDirectory(prefix="rvv-calendar-refresh-") as tmp:
            scrape_result = _scrape_in(Path(tmp))
    else:
        workspace = Path(work_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        scrape_result = _scrape_in(workspace)

    scrape = scrape_result["scrape"]
    rebuilt_problem = scrape_result["rebuilt_problem"]
    new_problem = copy.deepcopy(problem)
    for key in _CALENDAR_PROBLEM_KEYS:
        new_problem[key] = copy.deepcopy(rebuilt_problem.get(key))
    after_calendar = _calendar_problem_payload(new_problem)
    after_fingerprint = stable_payload_sha256(after_calendar)
    source_summaries = _calendar_source_summaries(scrape, fetched_at=fetched_at)
    evidence_record = {
        "schema_version": 1,
        "refreshed_at": fetched_at,
        "refreshed_by": _operator_identity(actor),
        "note": note or "",
        "input_path": str(input_path),
        "dry_run": bool(dry_run),
        "previous_calendar_fingerprint": before_fingerprint,
        "calendar_fingerprint": after_fingerprint,
        "stage2_fingerprint": stable_payload_sha256(scrape),
        "source_count": len(source_summaries),
        "blocked_sources": list(scrape.get("blocked") or []),
        "empty_sources": list(scrape.get("empty_sources") or []),
        "sources": source_summaries,
    }

    preview_context = copy.deepcopy(context)
    preview_context["problem"] = new_problem
    preview_context["problem_fingerprint"] = stable_payload_sha256(new_problem)
    preview_context.setdefault("calendar_evidence_history", [])
    current_evidence = preview_context.get("calendar_evidence")
    if isinstance(current_evidence, Mapping):
        preview_context["calendar_evidence_history"].append(copy.deepcopy(current_evidence))
    preview_context["calendar_evidence"] = evidence_record
    preview_schedule = copy.deepcopy(schedule)
    preview_schedule["verification_context"] = preview_context
    preview_schedule["updated_at"] = fetched_at

    resolved_problem = _resolve_plan_problem(preview_schedule, None, decisions)
    verification = verify_candidate(plan, resolved_problem)
    findings_before = list_findings(season, root=service.store.root)

    result = {
        "season": season,
        "dry_run": bool(dry_run),
        "changed": before_fingerprint != after_fingerprint,
        "schedule_fingerprint": before_schedule_fingerprint,
        "previous_calendar_fingerprint": before_fingerprint,
        "calendar_fingerprint": after_fingerprint,
        "source_count": len(source_summaries),
        "blocked_sources": list(scrape.get("blocked") or []),
        "empty_sources": list(scrape.get("empty_sources") or []),
        "sources": source_summaries,
        "verification_ok": bool(verification.get("ok")),
        "verification_violations": list(verification.get("violations") or []),
        "manual_external_conflict_placements": list(
            verification.get("manual_external_conflict_placements") or []
        ),
    }
    if dry_run:
        result["canonical_state_revision"] = canonical_state_revision(schedule, decisions)
        return result

    schedule = preview_schedule
    promoted_from = dict(schedule.get("promoted_from") or {})
    promoted_from["export_stale"] = True
    promoted_from["export_stale_reason"] = "calendar evidence refreshed"
    promoted_from["export_stale_at"] = fetched_at
    schedule["promoted_from"] = promoted_from
    decisions["updated_at"] = fetched_at
    decisions["export_state"] = {
        "status": "stale",
        "stale_reason": "calendar_evidence_refreshed",
        "stale_at": fetched_at,
        "requires_fresh_export": True,
        "requires_fresh_audit": True,
        "calendar_fingerprint": after_fingerprint,
    }
    _append_decision_history(
        decisions,
        event="refresh_calendar_evidence",
        tournament_id="",
        actor=actor,
        now=fetched_at,
        note=note,
        details={
            "previous_calendar_fingerprint": before_fingerprint,
            "calendar_fingerprint": after_fingerprint,
            "stage2_fingerprint": evidence_record["stage2_fingerprint"],
            "source_count": len(source_summaries),
            "blocked_sources": list(scrape.get("blocked") or []),
        },
    )
    committed = service._commit(snapshot.with_schedule(schedule).with_decisions(decisions))
    findings_after = list_findings(season, root=service.store.root)
    result["canonical_state_revision"] = canonical_state_revision(committed.schedule, committed.decisions)
    result["previous_canonical_state_revision"] = canonical_state_revision(snapshot.schedule, snapshot.decisions)
    result["findings_before"] = {
        "finding_count": findings_before.get("finding_count"),
        "baseline_comparison": findings_before.get("baseline_comparison"),
    }
    result["findings_after"] = {
        "finding_count": findings_after.get("finding_count"),
        "baseline_comparison": findings_after.get("baseline_comparison"),
    }
    result["export_state"] = decisions["export_state"]
    return result


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
    rows: list[dict[str, Any]] = []
    for event in iter_events(resolved_problem):
        if club and str(event.get("club") or "") != club:
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
            duration = int((ice.get(str(tournament.get("age_group") or "")) or 0) or 0)
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
                    },
                    "candidate_tournaments": candidates,
                }
            )
    return {
        "season": season,
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "booking_candidates": rows,
    }


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


def _tournament_interval(tournament: Mapping[str, Any], ice: Mapping[str, Any]) -> tuple[int, int] | None:
    start = str(tournament.get("start_time") or "")
    duration = int((ice.get(str(tournament.get("age_group") or "")) or 0) or 0)
    if duration <= 0 or ":" not in start:
        return None
    try:
        h, m = (int(part) for part in start.split(":", 1))
    except ValueError:
        return None
    t_start = h * 60 + m
    return t_start, t_start + duration


def _event_interval(event: Mapping[str, Any]) -> tuple[int, int] | None:
    try:
        s_h, s_m = (int(part) for part in str(event.get("start") or "").split(":", 1))
        e_h, e_m = (int(part) for part in str(event.get("end") or "").split(":", 1))
    except ValueError:
        return None
    return s_h * 60 + s_m, e_h * 60 + e_m


def _overlaps(tournament: Mapping[str, Any], event: Mapping[str, Any], ice: Mapping[str, Any]) -> bool:
    if str(tournament.get("date") or "") != str(event.get("date") or ""):
        return False
    t_interval = _tournament_interval(tournament, ice)
    e_interval = _event_interval(event)
    if not t_interval or not e_interval:
        return False
    return t_interval[0] < e_interval[1] and e_interval[0] < t_interval[1]


def reconcile_calendar_bookings(
    service,
    *,
    season: str,
    club: str,
    actor: str | None = None,
    note: str = "",
    problem: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Classify every hosted tournament for one club against current calendar evidence.

    The classification is evidence, not booking proof.  A lone busy event that
    merely overlaps a tournament is recorded as ``ambiguous`` so the operator /
    harness can own the semantic match through ``confirm-calendar-booking``;
    only an already-valid explicit association is reported ``confirmed_booked``.
    """

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule["plan"]
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions) or {}
    ice = resolved_problem.get("ice_time_minutes") or {}
    status = str((resolved_problem.get("club_calendar_status") or {}).get(club) or "")
    trustworthy = status == "known"
    events = [event for event in iter_events(resolved_problem) if str(event.get("club") or "") == club]
    confirmed_by_tournament: dict[str, Mapping[str, Any] | None] = {}
    for record in valid_active_associations(decisions, problem=resolved_problem, plan=plan):
        tournament_id = str(record.get("tournament_id") or "")
        if tournament_id:
            confirmed_by_tournament[tournament_id] = find_event(
                resolved_problem, str(record.get("event_fingerprint") or "")
            )
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    rows: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for tournament in plan.get("tournaments", []) or []:
        if str(tournament.get("host_club") or "") != club:
            continue
        tournament_id = str(tournament.get("id") or "")
        if tournament_id in confirmed_by_tournament:
            # Only an explicit operator/harness-validated association is proof that
            # an occupied interval is this tournament; raw overlap is not.
            booking_status = BOOKING_CONFIRMED_BOOKED
            matched_event = confirmed_by_tournament[tournament_id]
            reason = "explicit_calendar_booking_association"
        elif not trustworthy:
            booking_status = BOOKING_NOT_CHECKABLE
            matched_event = None
            reason = f"calendar_status:{status or 'missing'}"
        else:
            overlaps = [event for event in events if _overlaps(tournament, event, ice)]
            if len(overlaps) == 1:
                # Exactly one busy event overlaps, but occupancy is not proof that
                # the event is this RVV tournament. Keep the candidate visible and
                # require an explicit `confirm-calendar-booking` match.
                booking_status = BOOKING_AMBIGUOUS
                matched_event = overlaps[0]
                reason = "single_overlapping_event_requires_confirmation"
            elif len(overlaps) == 0:
                booking_status = BOOKING_CONFIRMED_NOT_BOOKED
                matched_event = None
                reason = "no_overlapping_event_in_trustworthy_calendar"
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
            source_revision=canonical_state_revision(schedule, decisions),
            event=matched_event,
            reason=reason,
        )
        records.append(record)
        rows.append({"tournament_id": tournament.get("id"), "status": booking_status, "reason": reason, "event_fingerprint": record.get("event_fingerprint")})
    result = {"season": season, "club": club, "dry_run": dry_run, "classified": rows, "count": len(rows)}
    if dry_run:
        return result
    updated = dict(decisions)
    prior = [dict(record) for record in updated.get(TOURNAMENT_BOOKING_EVIDENCE_KEY) or [] if not (isinstance(record, Mapping) and str(record.get("tournament_id") or "") in {str(r.get("tournament_id") or "") for r in records})]
    updated[TOURNAMENT_BOOKING_EVIDENCE_KEY] = prior + records
    updated["updated_at"] = now
    _append_decision_history(
        updated,
        event="reconcile_calendar_bookings",
        tournament_id="",
        actor=resolved_actor,
        now=now,
        note=note,
        details={"club": club, "classified": rows},
    )
    committed = service._commit(snapshot.with_decisions(updated))
    result["canonical_state_revision"] = canonical_state_revision(committed.schedule, committed.decisions)
    result["booking_status"] = _booking_status_report(problem=resolved_problem, plan=plan, decisions=committed.decisions)
    return result


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
    tournament = next(
        (t for t in plan.get("tournaments", []) if str(t.get("id")) == tournament_id), None
    )
    if tournament is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    diagnostics: list[str] = []
    if str(event.get("club") or "") != str(tournament.get("host_club") or ""):
        diagnostics.append("host_mismatch")
    if str(event.get("date") or "") != str(tournament.get("date") or ""):
        diagnostics.append("date_mismatch")
    elif not event_covers_tournament_interval(event, tournament, base_problem):
        diagnostics.append("interval_mismatch")
    if diagnostics:
        raise SeasonStateError("Calendar booking is not compatible with tournament: " + ", ".join(diagnostics))

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
    assoc = new_association_record(
        event=event,
        tournament=tournament,
        actor=resolved_actor,
        note=note,
        source_revision=canonical_state_revision(schedule, decisions),
        problem=base_problem,
    )
    updated = dict(decisions)
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
    checked_at = _now_iso()
    evidence = new_booking_evidence_record(
        tournament=tournament,
        status=BOOKING_CONFIRMED_BOOKED,
        problem=base_problem,
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

    temp_problem = _resolve_plan_problem(schedule, base_problem, updated)
    verification = verify_candidate(plan, temp_problem) if temp_problem else verify_candidate(plan)
    hard_blockers, unresolved_blockers = _attributable_blockers(verification, tournament_id)
    blockers = hard_blockers + unresolved_blockers
    if blockers:
        messages = "; ".join(str(blocker.get("message") or blocker.get("code")) for blocker in blockers)
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
        details={"event_fingerprint": event_fingerprint, "calendar_event": event.get("calendar_event")},
    )
    if dry_run:
        return {"season": season, "dry_run": True, "association": assoc, "approved": record}
    committed = service._commit(snapshot.with_decisions(updated))
    return {
        "season": season,
        "dry_run": False,
        "association": assoc,
        "approved": committed.decisions.get("decisions", {}).get(tournament_id),
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
