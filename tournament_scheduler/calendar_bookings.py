"""Explicit calendar-event to canonical-tournament booking associations.

Raw scraped calendar evidence remains immutable.  A promoted season may instead
carry an audited overlay in decisions.json saying that one scraped busy event is
the actual booking for one canonical tournament.  The event remains busy for
every other tournament.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

CALENDAR_BOOKING_ASSOCIATIONS_KEY = "calendar_booking_associations"
TOURNAMENT_BOOKING_EVIDENCE_KEY = "tournament_booking_evidence"
ACTIVE = "active"
STALE = "stale"
BOOKING_CONFIRMED_BOOKED = "confirmed_booked"
BOOKING_CONFIRMED_NOT_BOOKED = "confirmed_not_booked"
BOOKING_AMBIGUOUS = "ambiguous"
BOOKING_NOT_CHECKABLE = "not_checkable"
BOOKING_UNKNOWN = "unknown"

_ATTENTION_BOOKING_STATUSES = {BOOKING_CONFIRMED_NOT_BOOKED, BOOKING_AMBIGUOUS, BOOKING_NOT_CHECKABLE, STALE}

# A ``confirmed_not_booked`` record is authoritative only when an explicit
# source/operator rejected the booking. These reasons instead derive the
# conclusion from the absence of an overlapping event at the current canonical
# slot, which is an observation about that slot rather than proof about the
# tournament. The projection keeps them as ambiguity requiring review.
_ABSENCE_ONLY_NEGATIVE_REASONS = frozenset(
    {
        "no_overlapping_event_in_trustworthy_calendar",
        "no_covering_event_for_current_slot",
        "approved_placement_without_calendar_evidence",
    }
)


def event_fingerprint(event: Mapping[str, Any]) -> str:
    """Return the stable identity for one normalized calendar interval."""

    payload = _event_fingerprint_payload(event)
    return stable_payload_sha256(payload)


def _event_fingerprint_payload(event: Mapping[str, Any]) -> dict[str, str]:
    return {
        "club": str(event.get("club") or ""),
        "date": str(event.get("date") or ""),
        "start": str(event.get("start") or ""),
        "end": str(event.get("end") or ""),
        "calendar_event": str(event.get("calendar_event") or event.get("title") or ""),
        "availability": str(event.get("availability") or ""),
    }


def club_calendar_fingerprint(problem: Mapping[str, Any] | None, club: str) -> str:
    """Fingerprint one club's current calendar evidence for booking review."""

    events = sorted(
        (_event_fingerprint_payload(event) for event in iter_events(problem) if str(event.get("club") or "") == club),
        key=lambda item: (item["date"], item["start"], item["end"], item["calendar_event"]),
    )
    payload = {
        "club": club,
        "status": str(((problem or {}).get("club_calendar_status") or {}).get(club) or ""),
        "events": events,
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


def _tournaments_by_id(plan: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    return {
        str(t.get("id") or ""): t
        for t in ((plan or {}).get("tournaments") or [])
        if isinstance(t, Mapping) and str(t.get("id") or "")
    }


def _parse_hhmm(value: Any) -> int | None:
    try:
        hour, minute = (int(part) for part in str(value or "").split(":", 1))
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def _format_hhmm(minutes: int) -> str:
    value = datetime(2000, 1, 1) + timedelta(minutes=minutes)
    return value.strftime("%H:%M")


def _game_round_count(tournament: Mapping[str, Any]) -> int:
    rounds: list[int] = []
    for game in tournament.get("games") or []:
        if isinstance(game, Mapping):
            try:
                rounds.append(int(game.get("round_number") or 0))
            except (TypeError, ValueError):
                pass
    return max(rounds, default=0)


def tournament_occupancy_interval_facts(
    tournament: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Return the canonical occupied interval facts for one tournament."""

    age_group = str(tournament.get("age_group") or "")
    start_time = str(tournament.get("start_time") or "")
    duration = 0
    ice_time = (problem or {}).get("ice_time_minutes") or {}
    if isinstance(ice_time, Mapping):
        try:
            duration = int((ice_time.get(age_group) or 0) or 0)
        except (TypeError, ValueError):
            duration = 0
    start_minutes = _parse_hhmm(start_time)
    end_time = _format_hhmm(start_minutes + duration) if start_minutes is not None and duration > 0 else ""
    return {
        "date": str(tournament.get("date") or ""),
        "start_time": start_time,
        "duration_minutes": str(duration),
        "end_time": end_time,
        "age_group": age_group,
        "round_count": str(_game_round_count(tournament)),
    }


def event_covers_tournament_interval(
    event: Mapping[str, Any],
    tournament: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
) -> bool:
    """Return whether the scraped event covers the canonical occupied interval."""

    interval = tournament_occupancy_interval_facts(tournament, problem)
    if str(event.get("date") or "") != interval["date"]:
        return False
    event_start = _parse_hhmm(event.get("start"))
    event_end = _parse_hhmm(event.get("end"))
    tournament_start = _parse_hhmm(interval["start_time"])
    tournament_end = _parse_hhmm(interval["end_time"])
    if None in (event_start, event_end, tournament_start, tournament_end):
        return False
    return event_start <= tournament_start and tournament_end <= event_end


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
        if not _association_stale_reasons(record, events=events, tournaments=tournaments, problem=problem):
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


def tournament_booking_facts(tournament: Mapping[str, Any]) -> dict[str, str]:
    return {
        "host_club": str(tournament.get("host_club") or ""),
        "arena": str(tournament.get("arena") or ""),
        "date": str(tournament.get("date") or ""),
        "start_time": str(tournament.get("start_time") or ""),
        "age_group": str(tournament.get("age_group") or ""),
    }


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


def new_booking_evidence_record(
    *,
    tournament: Mapping[str, Any],
    status: str,
    problem: Mapping[str, Any] | None,
    actor: str,
    note: str,
    checked_at: str,
    source_revision: str,
    event: Mapping[str, Any] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    club = str(tournament.get("host_club") or "")
    tournament_id = str(tournament.get("id") or "")
    event_fp = str(event.get("fingerprint") or event_fingerprint(event)) if event is not None else ""
    return {
        "id": f"booking_evidence:{tournament_id}:{checked_at}",
        "status": status,
        "tournament_id": tournament_id,
        "host_club": club,
        "arena": str(tournament.get("arena") or ""),
        "date": str(tournament.get("date") or ""),
        "start_time": str(tournament.get("start_time") or ""),
        "tournament_facts": tournament_booking_facts(tournament),
        "calendar_fingerprint": club_calendar_fingerprint(problem, club),
        "event_fingerprint": event_fp,
        "source_calendar_status": str(((problem or {}).get("club_calendar_status") or {}).get(club) or ""),
        "checked_at": checked_at,
        "checked_by": actor,
        "note": note or "",
        "reason": reason,
        "source_revision": source_revision,
    }


def _booking_record_stale_reasons(
    record: Mapping[str, Any],
    *,
    problem: Mapping[str, Any] | None,
    tournaments: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    tid = str(record.get("tournament_id") or "")
    tournament = tournaments.get(tid)
    reasons: list[str] = []
    if tournament is None:
        reasons.append("tournament_missing")
    else:
        facts = record.get("tournament_facts") or {}
        for key, value in tournament_booking_facts(tournament).items():
            if str(facts.get(key) or "") != value:
                reasons.append(f"tournament_{key}_changed")
    club = str(record.get("host_club") or (tournament or {}).get("host_club") or "")
    if club and str(record.get("calendar_fingerprint") or "") != club_calendar_fingerprint(problem, club):
        reasons.append("calendar_evidence_changed")
    return sorted(set(reasons))


def booking_status_report(
    *,
    problem: Mapping[str, Any] | None,
    plan: Mapping[str, Any] | None,
    decisions: Mapping[str, Any] | None,
) -> dict[str, Any]:
    tournaments = _tournaments_by_id(plan)
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
    counts = {BOOKING_UNKNOWN: 0, BOOKING_CONFIRMED_BOOKED: 0, BOOKING_CONFIRMED_NOT_BOOKED: 0, BOOKING_AMBIGUOUS: 0, BOOKING_NOT_CHECKABLE: 0, STALE: 0, "needs_attention": 0}
    for tid, tournament in sorted(tournaments.items()):
        record = latest.get(tid)
        status = BOOKING_UNKNOWN
        stale_reasons: list[str] = []
        if tid in active_assoc_ids:
            status = BOOKING_CONFIRMED_BOOKED
        elif tid in assoc_stale_by_tournament:
            status = STALE
            stale_reasons = ["calendar_booking_association_stale"]
        elif record:
            stale_reasons = _booking_record_stale_reasons(record, problem=problem, tournaments=tournaments)
            record_status = str(record.get("status") or BOOKING_UNKNOWN)
            if stale_reasons:
                status = STALE
            elif record_status == BOOKING_CONFIRMED_BOOKED:
                # Historical evidence is preserved in ``row["evidence"]``, but
                # ``confirmed_booked`` is a statement about *current* proof: it
                # requires a currently-valid explicit event-to-tournament
                # association (handled above). A legacy single-overlap positive
                # record, or one whose association was released without
                # rebinding, is only weak positive evidence and must surface as
                # requiring review instead of staying confirmed.
                status = BOOKING_AMBIGUOUS
                stale_reasons = ["confirmed_booking_without_valid_association"]
            elif (
                record_status == BOOKING_CONFIRMED_NOT_BOOKED
                and str(record.get("reason") or "") in _ABSENCE_ONLY_NEGATIVE_REASONS
            ):
                # Absence of an event at the current canonical slot is not
                # authoritative negative evidence: the booking may have moved or
                # the source may be incomplete. Surface it as ambiguity instead of
                # a final "not booked" outcome; the raw evidence stays visible in
                # ``row`` for provenance.
                status = BOOKING_AMBIGUOUS
                stale_reasons = ["absence_only_negative_booking_requires_review"]
            else:
                status = record_status
        row = {
            "tournament_id": tid,
            "status": status,
            "host_club": str(tournament.get("host_club") or ""),
            "age_group": str(tournament.get("age_group") or ""),
            "arena": str(tournament.get("arena") or ""),
            "date": str(tournament.get("date") or ""),
            "start_time": str(tournament.get("start_time") or ""),
            "needs_attention": status in _ATTENTION_BOOKING_STATUSES,
            "stale_reasons": stale_reasons,
        }
        if record:
            row["evidence"] = record
        rows.append(row)
        counts.setdefault(status, 0)
        counts[status] += 1
        if row["needs_attention"]:
            counts["needs_attention"] += 1
    return {"tournaments": rows, "counts": counts}


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
    active = active_associations(decisions)
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
