"""Explicit calendar-event to canonical-tournament booking associations.

Raw scraped calendar evidence remains immutable.  A promoted season may instead
carry an audited overlay in decisions.json saying that one scraped busy event is
the actual booking for one canonical tournament.  The event remains busy for
every other tournament.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

CALENDAR_BOOKING_ASSOCIATIONS_KEY = "calendar_booking_associations"
ACTIVE = "active"
STALE = "stale"


def event_fingerprint(event: Mapping[str, Any]) -> str:
    """Return the stable identity for one normalized calendar interval."""

    payload = {
        "club": str(event.get("club") or ""),
        "date": str(event.get("date") or ""),
        "start": str(event.get("start") or ""),
        "end": str(event.get("end") or ""),
        "calendar_event": str(event.get("calendar_event") or event.get("title") or ""),
        "availability": str(event.get("availability") or ""),
    }
    return stable_payload_sha256(payload)


def with_event_fingerprint(club: str, event: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(event)
    row.setdefault("club", club)
    row["fingerprint"] = event_fingerprint(row)
    return row


def iter_events(problem: Mapping[str, Any] | None) -> Iterable[dict[str, Any]]:
    intervals = (problem or {}).get("club_busy_intervals") or {}
    if not isinstance(intervals, Mapping):
        return ()
    rows: list[dict[str, Any]] = []
    for club, entries in intervals.items():
        for entry in entries or []:
            if isinstance(entry, Mapping):
                rows.append(with_event_fingerprint(str(club), entry))
    return rows


def find_event(problem: Mapping[str, Any] | None, fingerprint: str) -> dict[str, Any] | None:
    for event in iter_events(problem):
        if event.get("fingerprint") == fingerprint:
            return event
    return None


def active_associations(decisions: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    return [
        dict(record)
        for record in ((decisions or {}).get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or [])
        if isinstance(record, Mapping) and record.get("status", ACTIVE) == ACTIVE
    ]


def project_associations_into_problem(
    problem: Mapping[str, Any] | None,
    decisions: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Attach active booking associations to a copied verification problem."""

    if problem is None:
        return None
    projected = dict(problem)
    projected[CALENDAR_BOOKING_ASSOCIATIONS_KEY] = active_associations(decisions)
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
        "tournament_facts": {
            "host_club": str(tournament.get("host_club") or ""),
            "arena": str(tournament.get("arena") or ""),
            "date": str(tournament.get("date") or ""),
            "start_time": str(tournament.get("start_time") or ""),
            "age_group": str(tournament.get("age_group") or ""),
        },
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
    tournaments = {
        str(t.get("id") or ""): t
        for t in ((plan or {}).get("tournaments") or [])
        if isinstance(t, Mapping)
    }
    findings: list[dict[str, Any]] = []
    for record in active_associations(decisions):
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
            ):
                if str(facts.get(key) or "") != str(tournament.get(tournament_key) or ""):
                    reasons.append(f"tournament_{key}_changed")
        if event is not None:
            for key, event_key in (("date", "date"), ("start", "start"), ("end", "end"), ("title", "calendar_event")):
                if str(record.get(key) or "") != str(event.get(event_key) or ""):
                    reasons.append(f"event_{key}_changed")
        if reasons:
            findings.append(
                {
                    "code": "stale_calendar_booking_association",
                    "association_id": record.get("id"),
                    "event_fingerprint": event_fp,
                    "tournament_id": tid,
                    "reasons": sorted(set(reasons)),
                }
            )
    return findings
