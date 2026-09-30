"""Shared helpers for canonical-season calendar use cases."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from tournament_scheduler.calendar_bookings import (
    BOOKING_CONFIRMED_BOOKED,
    BOOKING_CONFIRMED_NOT_BOOKED,
    BOOKING_MANUALLY_BOOKED,
    BOOKING_MANUALLY_NOT_BOOKED,
    REJECTED_BOOKING_EVIDENCE_KEY,
    manual_assertion_for_tournament,
    manual_assertion_projection_status,
    manual_assertion_stale_reasons,
    new_rejected_booking_evidence_record,
)
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

from ..shared import (
    APPROVED_STATUS,
    _append_decision_history,
)

_AUTO_REFRESH_RECONCILE_CLUBS = ("Kongsberg", "Ringerike")

_CALENDAR_PROBLEM_KEYS = (
    "club_busy_dates",
    "club_busy_intervals",
    "club_calendar_status",
    "club_source_integrity",
    "club_source_integrity_details",
    "club_coverage_proven",
    "unclassified_calendar_events",
)

def _record_rejected_booking_evidence(
    service,
    *,
    snapshot,
    tournament: Mapping[str, Any],
    authority: str,
    reference: str,
    source_assertion_id: str | None,
    proposed_interval: Mapping[str, Any] | None,
    blockers: list[dict[str, Any]],
    actor: str,
    now: str,
    source_revision: str,
    dry_run: bool,
) -> None:
    """Persist refused source evidence before the operation raises (ADR 0005).

    Evidence acceptance and active placement acceptance are separate outcomes:
    a source-confirmed booking whose exact change fails a hard operational check
    is retained as an unresolved conflict for operator resolution, without
    mutating the active placement, approval/lock or reconciliation state. The
    rejected record is decision-only, so the canonical schedule is untouched.
    """

    if dry_run:
        return
    updated = dict(snapshot.decisions)
    records = [
        dict(record)
        for record in updated.get(REJECTED_BOOKING_EVIDENCE_KEY) or []
        if isinstance(record, Mapping)
    ]
    records.append(
        new_rejected_booking_evidence_record(
            tournament=tournament,
            authority=authority,
            reference=reference,
            source_assertion_id=source_assertion_id,
            proposed_interval=proposed_interval or {},
            conflicts=blockers,
            rejected_at=now,
            rejected_by=actor,
            canonical_state_revision=source_revision,
        )
    )
    updated[REJECTED_BOOKING_EVIDENCE_KEY] = records
    updated["updated_at"] = now
    _append_decision_history(
        updated,
        event="reject_booking_evidence",
        tournament_id=str(tournament.get("id") or ""),
        actor=actor,
        now=now,
        note="source-confirmed booking rejected by a hard operational check",
        details={
            "authority": authority,
            "reference": reference,
            "source_assertion_id": source_assertion_id or "",
            "proposed_interval": dict(proposed_interval or {}),
            "conflicts": [str(blocker.get("code") or "") for blocker in blockers],
        },
    )
    service._commit(snapshot.with_decisions(updated))

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
            "integrity": source.get("integrity"),
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

def _tournament_duration_minutes(
    tournament: Mapping[str, Any],
    ice: Mapping[str, Any],
    overrides: Mapping[str, int] | None = None,
) -> int:
    override = (overrides or {}).get(str(tournament.get("id") or ""))
    if isinstance(override, int) and override > 0:
        return override
    try:
        return int((ice.get(str(tournament.get("age_group") or "")) or 0) or 0)
    except (TypeError, ValueError):
        return 0

def _tournament_interval(
    tournament: Mapping[str, Any],
    ice: Mapping[str, Any],
    overrides: Mapping[str, int] | None = None,
) -> tuple[int, int] | None:
    start = str(tournament.get("start_time") or "")
    duration = _tournament_duration_minutes(tournament, ice, overrides)
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

def _overlaps(
    tournament: Mapping[str, Any],
    event: Mapping[str, Any],
    ice: Mapping[str, Any],
    overrides: Mapping[str, int] | None = None,
) -> bool:
    if str(tournament.get("date") or "") != str(event.get("date") or ""):
        return False
    t_interval = _tournament_interval(tournament, ice, overrides)
    e_interval = _event_interval(event)
    if not t_interval or not e_interval:
        return False
    return t_interval[0] < e_interval[1] and e_interval[0] < t_interval[1]

def _approved_placement_locked(decisions: Mapping[str, Any], tournament_id: str) -> bool:
    """Return whether the tournament already has an approved placement lock.

    Public-calendar absence is source-specific follow-up evidence. It must not
    be projected as a negative booking conclusion for an already approved slot,
    but approval-note prose is not parsed as booking authority either.
    """

    record = (decisions.get("decisions") or {}).get(tournament_id) or {}
    if not isinstance(record, Mapping):
        return False
    return str(record.get("status") or "") == APPROVED_STATUS and bool(record.get("placement_locked"))

def _manual_conflict_with_classification(
    *,
    decisions: Mapping[str, Any],
    resolved_problem: Mapping[str, Any],
    tournament: Mapping[str, Any],
    classified_status: str,
) -> bool:
    """Return whether an active manual assertion conflicts with *classified_status*.

    ``_classify_club_calendar_bookings`` only looks at calendar-derived evidence
    (associations/overlaps); it does not know about a durable manual booking
    assertion (#454), so a valid calendar association can silently coexist with
    a later active ``manually_not_booked`` assertion. Mirrors the conflict
    semantics already used by :func:`~tournament_scheduler.calendar_bookings.booking_status_report`,
    applied to a freshly computed (not-yet-persisted) classification rather
    than the last persisted calendar evidence.
    """

    tournament_id = str(tournament.get("id") or "")
    manual = manual_assertion_for_tournament(decisions, tournament_id)
    if manual is None:
        return False
    if manual_assertion_stale_reasons(manual, problem=resolved_problem, tournament=tournament):
        # A stale manual assertion itself requires operator re-confirmation.
        return True
    manual_status = manual_assertion_projection_status(manual)
    if manual_status == BOOKING_MANUALLY_NOT_BOOKED and classified_status == BOOKING_CONFIRMED_BOOKED:
        return True
    if manual_status == BOOKING_MANUALLY_BOOKED and classified_status == BOOKING_CONFIRMED_NOT_BOOKED:
        return True
    return False
