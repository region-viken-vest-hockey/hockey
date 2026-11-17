"""Calendar association records, validity, projection and identity findings."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from tournament_scheduler.booking.booking_cancellation import _cancelled_tournament_ids
from tournament_scheduler.booking.booking_facts import _tournaments_by_id, tournament_booking_facts
from tournament_scheduler.booking.booking_vocabulary import (
    ACTIVE,
    CALENDAR_BOOKING_ASSOCIATIONS_KEY,
    IDENTITY_DRIFT_ASSOCIATION_REASONS,
)
from tournament_scheduler.booking.calendar_evidence import iter_events
from tournament_scheduler.booking.interval_facts import (
    event_covers_tournament_interval,
    event_fingerprint,
    tournament_occupancy_interval_facts,
)


def active_associations(decisions: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    return [
        dict(record)
        for record in ((decisions or {}).get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or [])
        if isinstance(record, Mapping) and record.get("status", ACTIVE) == ACTIVE
    ]


def _association_stale_reasons(
    record: Mapping[str, Any],
    *,
    events: Mapping[str, Mapping[str, Any]],
    tournaments: Mapping[str, Mapping[str, Any]],
    problem: Mapping[str, Any] | None = None,
) -> list[str]:
    tid = str(record.get("tournament_id") or "")
    event_fp = str(record.get("event_fingerprint") or "")
    event = events.get(event_fp)
    tournament = tournaments.get(tid)
    reasons: list[str] = []
    if event is None:
        reasons.append("event_missing")
    if tournament is None:
        reasons.append("tournament_missing")
    facts = record.get("tournament_facts") or {}
    if tournament is not None:
        for key, tournament_key in (
            ("host_club", "host_club"),
            ("arena", "arena"),
            ("date", "date"),
            ("start_time", "start_time"),
            ("age_group", "age_group"),
        ):
            if str(facts.get(key) or "") != str(tournament.get(tournament_key) or ""):
                reasons.append(f"tournament_{key}_changed")
    if event is not None:
        for key, event_key in (
            ("club", "club"),
            ("date", "date"),
            ("start", "start"),
            ("end", "end"),
            ("title", "calendar_event"),
            ("availability", "availability"),
        ):
            if str(record.get(key) or "") != str(event.get(event_key) or ""):
                reasons.append(f"event_{key}_changed")
    if tournament is not None:
        stored_interval = record.get("tournament_interval") or {}
        current_interval = tournament_occupancy_interval_facts(tournament, problem)
        for key in ("date", "start_time", "duration_minutes", "end_time"):
            if key in stored_interval and str(stored_interval.get(key) or "") != current_interval[key]:
                reasons.append(f"tournament_{key}_changed")
        if event is not None and not event_covers_tournament_interval(event, tournament, problem):
            reasons.append("event_does_not_cover_tournament_interval")
    return sorted(set(reasons))


def valid_active_associations(
    decisions: Mapping[str, Any] | None,
    *,
    problem: Mapping[str, Any] | None,
    plan: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    events = {str(event.get("fingerprint") or ""): event for event in iter_events(problem)}
    tournaments = _tournaments_by_id(plan)
    valid: list[dict[str, Any]] = []
    seen_events: set[str] = set()
    for record in active_associations(decisions):
        event_fp = str(record.get("event_fingerprint") or "")
        if not event_fp or event_fp in seen_events:
            continue
        reasons = _association_stale_reasons(record, events=events, tournaments=tournaments, problem=problem)
        # Retitling or re-typing a confirmed event does not undo the confirmed
        # booking; only a changed interval, venue or missing event does (#847).
        if not [reason for reason in reasons if reason not in IDENTITY_DRIFT_ASSOCIATION_REASONS]:
            valid.append(record)
            seen_events.add(event_fp)
    return valid


def project_associations_into_problem(
    problem: Mapping[str, Any] | None,
    decisions: Mapping[str, Any] | None,
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Attach currently-valid booking associations to a copied verification problem.

    Associations are projected only when the current plan is supplied, so their
    tournament facts and occupied interval can be revalidated. Stale evidence
    remains visible through findings but is not projected into verifier evidence.
    """

    if problem is None:
        return None
    projected = dict(problem)
    projected[CALENDAR_BOOKING_ASSOCIATIONS_KEY] = (
        valid_active_associations(decisions, problem=projected, plan=plan)
        if plan is not None
        else []
    )
    return projected


def associated_tournament_for_event(
    problem: Mapping[str, Any] | None,
    event: Mapping[str, Any],
) -> str | None:
    fp = str(event.get("fingerprint") or event_fingerprint(event))
    for record in (problem or {}).get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or []:
        if not isinstance(record, Mapping):
            continue
        if str(record.get("event_fingerprint") or "") == fp and str(record.get("status") or ACTIVE) == ACTIVE:
            return str(record.get("tournament_id") or "") or None
    return None


def new_association_record(
    *,
    event: Mapping[str, Any],
    tournament: Mapping[str, Any],
    actor: str,
    note: str,
    source_revision: str,
    problem: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(tz=timezone.utc).isoformat()
    tournament_id = str(tournament.get("id") or "")
    event_fp = str(event.get("fingerprint") or event_fingerprint(event))
    return {
        "id": f"calendar_booking:{event_fp}:{tournament_id}",
        "status": ACTIVE,
        "event_fingerprint": event_fp,
        "tournament_id": tournament_id,
        "club": str(event.get("club") or tournament.get("host_club") or ""),
        "date": str(event.get("date") or ""),
        "start": str(event.get("start") or ""),
        "end": str(event.get("end") or ""),
        "title": str(event.get("calendar_event") or event.get("title") or ""),
        "availability": str(event.get("availability") or ""),
        "tournament_facts": tournament_booking_facts(tournament),
        "tournament_interval": tournament_occupancy_interval_facts(tournament, problem),
        "source_revision": source_revision,
        "note": note or "",
        "created_at": now,
        "created_by": actor,
    }


def association_findings(
    *,
    problem: Mapping[str, Any] | None,
    plan: Mapping[str, Any] | None,
    decisions: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return stale association findings caused by changed/removed facts."""

    events = {str(event.get("fingerprint") or ""): event for event in iter_events(problem)}
    tournaments = _tournaments_by_id(plan)
    findings: list[dict[str, Any]] = []
    # A cancelled tournament occupies no ice, so an association left on it is
    # historical provenance, not a current stale or duplicate finding (#842).
    cancelled_ids = _cancelled_tournament_ids(plan)
    active = [
        record
        for record in active_associations(decisions)
        if str(record.get("tournament_id") or "") not in cancelled_ids
    ]
    by_event: dict[str, list[dict[str, Any]]] = {}
    for record in active:
        by_event.setdefault(str(record.get("event_fingerprint") or ""), []).append(record)
    for event_fp, records in by_event.items():
        tournament_ids = sorted({str(record.get("tournament_id") or "") for record in records})
        if event_fp and len(tournament_ids) > 1:
            findings.append(
                {
                    "code": "duplicate_calendar_booking_association",
                    "event_fingerprint": event_fp,
                    "tournament_ids": tournament_ids,
                    "reasons": ["event_has_multiple_active_tournament_associations"],
                }
            )
    for record in active:
        tid = str(record.get("tournament_id") or "")
        event_fp = str(record.get("event_fingerprint") or "")
        reasons = _association_stale_reasons(record, events=events, tournaments=tournaments, problem=problem)
        if reasons:
            findings.append(
                {
                    "code": "stale_calendar_booking_association",
                    "association_id": record.get("id"),
                    "event_fingerprint": event_fp,
                    "tournament_id": tid,
                    "reasons": reasons,
                }
            )
    return findings
