"""Explicit calendar-event to canonical-tournament booking associations.

Raw scraped calendar evidence remains immutable.  A promoted season may instead
carry an audited overlay in decisions.json saying that one scraped busy event is
the actual booking for one canonical tournament.  The event remains busy for
every other tournament.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

CALENDAR_BOOKING_ASSOCIATIONS_KEY = "calendar_booking_associations"
TOURNAMENT_BOOKING_EVIDENCE_KEY = "tournament_booking_evidence"
# Explicit operator/club assertions live in their own durable decisions key so a
# routine calendar reconcile/refresh that rewrites ``TOURNAMENT_BOOKING_EVIDENCE_KEY``
# can never erase or demote them.  A manual assertion is a statement about the
# booking whose authority is a person, not an event fingerprint.  The projection
# keeps that typed authority distinct from a calendar-derived observation.
MANUAL_BOOKING_ASSERTIONS_KEY = "manual_booking_assertions"
MANUAL_SOURCE_CLUB_CONFIRMATION = "manual_club_confirmation"
MANUAL_ASSERTION_ACTIVE = "active"
MANUAL_ASSERTION_SUPERSEDED = "superseded"
MANUAL_ASSERTION_REVOKED = "revoked"
MANUAL_ASSERTION_SCOPES = ("tournament", "club_wide_interpretation")
BOOKING_AUTHORITY_MANUAL = MANUAL_SOURCE_CLUB_CONFIRMATION
BOOKING_AUTHORITY_MANUAL_INTERPRETATION = "manual_club_confirmation_interpretation"
BOOKING_AUTHORITY_CALENDAR = "calendar_event_association"
BOOKING_MANUALLY_BOOKED = "manually_booked"
BOOKING_MANUALLY_NOT_BOOKED = "manually_not_booked"
ACTIVE = "active"
STALE = "stale"

# Read-only booking assessment vocabulary -------------------------------------
# The assessment is an evidence/report projection: it proposes plausible
# tournament<->calendar-event relations and keeps competing or unresolved cases
# visible.  It never persists an association or claims that calendar absence
# proves a tournament is unbooked.
ASSESSMENT_SCHEMA_VERSION = 1
DEFAULT_ASSESSMENT_DATE_WINDOW_DAYS = 7

ASSESSMENT_ASSOCIATED = "associated"
ASSESSMENT_MANUALLY_ASSERTED = "manually_asserted"
ASSESSMENT_PROPOSED_UNCHANGED = "proposed_unchanged"
ASSESSMENT_PROPOSED_CHANGED_SLOT = "proposed_changed_slot"
ASSESSMENT_COMPETING_CANDIDATES = "competing_candidates"
ASSESSMENT_AMBIGUOUS = "ambiguous"
ASSESSMENT_UNMATCHED = "unmatched"
ASSESSMENT_NOT_CHECKABLE = "not_checkable"

RELATION_SAME_DATE_OVERLAP = "same_date_overlap"
RELATION_SAME_DATE_TIME_SHIFT = "same_date_time_shift"
RELATION_PROXIMATE_DATE_SHIFT = "proximate_date_shift"

_ASSESSMENT_UNRESOLVED = {
    ASSESSMENT_COMPETING_CANDIDATES,
    ASSESSMENT_AMBIGUOUS,
    ASSESSMENT_UNMATCHED,
    ASSESSMENT_NOT_CHECKABLE,
}
_ASSESSMENT_RELATION_RANK = {
    RELATION_SAME_DATE_OVERLAP: 0,
    RELATION_SAME_DATE_TIME_SHIFT: 1,
    RELATION_PROXIMATE_DATE_SHIFT: 2,
}
_AGE_TOKEN_RE = re.compile(r"\bU\s?(\d{1,2})\b", re.IGNORECASE)
BOOKING_CONFIRMED_BOOKED = "confirmed_booked"
BOOKING_CONFIRMED_NOT_BOOKED = "confirmed_not_booked"
BOOKING_AMBIGUOUS = "ambiguous"
BOOKING_NOT_CHECKABLE = "not_checkable"
BOOKING_UNKNOWN = "unknown"
BOOKING_MANUAL_UNKNOWN = "manual_unknown"

_STATUS_BOOKED = "booked"
_STATUS_NOT_BOOKED = "not-booked"
MANUAL_BOOKING_STATUS_CHOICES = (_STATUS_BOOKED, _STATUS_NOT_BOOKED)
_MANUAL_CHOICE_TO_PROJECTION = {
    _STATUS_BOOKED: BOOKING_MANUALLY_BOOKED,
    _STATUS_NOT_BOOKED: BOOKING_MANUALLY_NOT_BOOKED,
}

_ATTENTION_BOOKING_STATUSES = {
    BOOKING_CONFIRMED_NOT_BOOKED,
    BOOKING_AMBIGUOUS,
    BOOKING_NOT_CHECKABLE,
    STALE,
    BOOKING_MANUALLY_NOT_BOOKED,
}

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


def manual_assertion_records(decisions: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return every persisted manual booking assertion, newest state preserved."""

    return [
        dict(record)
        for record in ((decisions or {}).get(MANUAL_BOOKING_ASSERTIONS_KEY) or [])
        if isinstance(record, Mapping)
    ]


def active_manual_assertions(decisions: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    return [
        record
        for record in manual_assertion_records(decisions)
        if str(record.get("status") or "") == MANUAL_ASSERTION_ACTIVE
    ]


def manual_assertion_for_tournament(
    decisions: Mapping[str, Any] | None,
    tournament_id: str,
) -> dict[str, Any] | None:
    """Return the newest active manual assertion for one tournament, if any."""

    matches = [
        record
        for record in active_manual_assertions(decisions)
        if str(record.get("tournament_id") or "") == tournament_id
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda record: str(record.get("asserted_at") or ""))[-1]


def manual_assertion_projection_status(assertion: Mapping[str, Any]) -> str:
    """Map a persisted manual assertion to its booking-status projection."""

    return _MANUAL_CHOICE_TO_PROJECTION.get(
        str(assertion.get("booking_status") or ""),
        BOOKING_MANUAL_UNKNOWN,
    )


def manual_assertion_stale_reasons(
    assertion: Mapping[str, Any],
    *,
    problem: Mapping[str, Any] | None,
    tournament: Mapping[str, Any] | None,
) -> list[str]:
    """Return why a manual assertion no longer describes the canonical slot.

    A material change to the occupied interval (date/start/duration/end) or any
    other tournament fact invalidates the assertion: the operator confirmed a
    specific slot, so the same statement must not silently carry to a new one.
    """

    if tournament is None:
        return ["tournament_missing"]
    reasons: list[str] = []
    facts = assertion.get("tournament_facts") or {}
    for key, value in tournament_booking_facts(tournament).items():
        if str(facts.get(key) or "") != value:
            reasons.append(f"tournament_{key}_changed")
    stored_interval = assertion.get("asserted_interval") or {}
    current_interval = tournament_occupancy_interval_facts(tournament, problem)
    for key in ("date", "start_time", "duration_minutes", "end_time"):
        if str(stored_interval.get(key) or "") != current_interval[key]:
            reasons.append(f"tournament_{key}_changed")
    return sorted(set(reasons))


def validate_stated_interval(start: str | None, end: str | None) -> dict[str, str]:
    """Validate and normalize an optional source-stated booking window.

    Rejects malformed, zero-length and reversed windows. Overnight windows are
    deliberately not supported: ``end`` must be strictly after ``start`` on the
    same local date. A stated window that is longer or shorter than the
    canonical occupancy is allowed (it becomes an explicit follow-up), but it
    must be a real, positive interval so no silent zero-duration evidence is
    persisted.
    """

    if not start and not end:
        return {}
    if not (start and end):
        raise ValueError("A stated source interval requires both --stated-start and --stated-end")
    start_minutes = _parse_hhmm(start)
    end_minutes = _parse_hhmm(end)
    if start_minutes is None:
        raise ValueError(f"Invalid stated start time: {start!r}; expected HH:MM")
    if end_minutes is None:
        raise ValueError(f"Invalid stated end time: {end!r}; expected HH:MM")
    if end_minutes <= start_minutes:
        raise ValueError(
            f"Invalid stated interval {start}-{end}: end must be after start on the same day; "
            "overnight intervals are not supported"
        )
    return {"start": str(start), "end": str(end)}


def _normalized_stated_interval(stated_interval: Mapping[str, Any] | None) -> dict[str, str]:
    """Normalize an optional source-stated interval for durable comparison."""

    if not isinstance(stated_interval, Mapping):
        return {}
    start = str(stated_interval.get("start") or "")
    end = str(stated_interval.get("end") or "")
    date = str(stated_interval.get("date") or "")
    duration = 0
    if start and end:
        start_minutes = _parse_hhmm(start)
        end_minutes = _parse_hhmm(end)
        if start_minutes is not None and end_minutes is not None and end_minutes >= start_minutes:
            duration = end_minutes - start_minutes
    return {"date": date, "start": start, "end": end, "duration_minutes": str(duration)}


def manual_assertion_interval_follow_up(assertion: Mapping[str, Any]) -> list[str]:
    """Return follow-up reasons for a stated interval that differs from canonical.

    A source may explicitly state an interval that disagrees with the canonical
    occupancy (for example a 75-minute emailed booking against a 100-minute
    canonical block).  The assertion stays booked; the discrepancy is surfaced
    as follow-up instead of silently shrinking the canonical occupancy.
    """

    if manual_assertion_projection_status(assertion) != BOOKING_MANUALLY_BOOKED:
        return []
    stated = _normalized_stated_interval(assertion.get("stated_interval"))
    if not any(stated.get(key) for key in ("date", "start", "end")):
        return []
    canonical = assertion.get("asserted_interval") or {}
    reasons: list[str] = []
    if stated.get("date") and stated["date"] != str(canonical.get("date") or ""):
        reasons.append("manual_booking_stated_date_differs_from_canonical")
    if stated.get("start") and stated["start"] != str(canonical.get("start_time") or ""):
        reasons.append("manual_booking_stated_start_differs_from_canonical")
    if stated.get("end") and stated["end"] != str(canonical.get("end_time") or ""):
        reasons.append("manual_booking_stated_end_differs_from_canonical")
    return reasons


def new_manual_assertion_record(
    *,
    tournament: Mapping[str, Any],
    booking_status: str,
    problem: Mapping[str, Any] | None,
    actor: str,
    note: str,
    reference: str,
    source_scope: str,
    stated_interval: Mapping[str, Any] | None,
    asserted_at: str,
    source_revision: str,
    supersedes: str | None = None,
) -> dict[str, Any]:
    """Build one durable, revision-bound manual booking assertion record."""

    tournament_id = str(tournament.get("id") or "")
    if booking_status not in _MANUAL_CHOICE_TO_PROJECTION:
        raise ValueError(f"Unknown manual booking status: {booking_status!r}")
    return {
        "id": f"manual_booking:{tournament_id}:{asserted_at}",
        "schema_version": 1,
        "status": MANUAL_ASSERTION_ACTIVE,
        "booking_status": booking_status,
        "authority": BOOKING_AUTHORITY_MANUAL,
        "source": MANUAL_SOURCE_CLUB_CONFIRMATION,
        "source_scope": source_scope if source_scope in MANUAL_ASSERTION_SCOPES else "tournament",
        "tournament_id": tournament_id,
        "host_club": str(tournament.get("host_club") or ""),
        "arena": str(tournament.get("arena") or ""),
        "date": str(tournament.get("date") or ""),
        "start_time": str(tournament.get("start_time") or ""),
        "tournament_facts": tournament_booking_facts(tournament),
        "asserted_interval": tournament_occupancy_interval_facts(tournament, problem),
        "stated_interval": _normalized_stated_interval(stated_interval),
        "reference": str(reference or ""),
        "note": note or "",
        "asserted_at": asserted_at,
        "asserted_by": actor,
        "source_revision": source_revision,
        "supersedes": supersedes,
    }


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


def _projected_calendar_status(
    record: Mapping[str, Any] | None,
    *,
    tournament_id: str,
    problem: Mapping[str, Any] | None,
    tournaments: Mapping[str, Mapping[str, Any]],
    active_assoc_ids: set[str],
    assoc_stale_by_tournament: set[str],
) -> tuple[str, list[str]]:
    """Project one calendar-derived evidence record to an effective status.

    Kept as the single implementation of the calendar-observation downgrades so
    the manual-assertion branch and the calendar-only branch agree on what a
    stored record actually means.
    """

    if tournament_id in active_assoc_ids:
        return BOOKING_CONFIRMED_BOOKED, []
    if tournament_id in assoc_stale_by_tournament:
        return STALE, ["calendar_booking_association_stale"]
    if record is None:
        return BOOKING_UNKNOWN, []
    stale_reasons = _booking_record_stale_reasons(record, problem=problem, tournaments=tournaments)
    if stale_reasons:
        return STALE, stale_reasons
    record_status = str(record.get("status") or BOOKING_UNKNOWN)
    if record_status == BOOKING_CONFIRMED_BOOKED:
        # ``confirmed_booked`` is a statement about *current* proof: it requires
        # a currently-valid explicit event-to-tournament association (handled
        # above). A legacy single-overlap positive record, or one whose
        # association was released without rebinding, is only weak positive
        # evidence and must surface as requiring review.
        return BOOKING_AMBIGUOUS, ["confirmed_booking_without_valid_association"]
    if record_status == BOOKING_CONFIRMED_NOT_BOOKED and str(record.get("reason") or "") in _ABSENCE_ONLY_NEGATIVE_REASONS:
        # Absence of an event at the current canonical slot is not authoritative
        # negative evidence: the booking may have moved or the source may be
        # incomplete. Surface it as ambiguity requiring review instead.
        return BOOKING_AMBIGUOUS, ["absence_only_negative_booking_requires_review"]
    return record_status, []


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
    }
    for tid, tournament in sorted(tournaments.items()):
        calendar_record = latest.get(tid)
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
            counts["manual"] += 1
            source_scope = str(manual.get("source_scope") or "tournament")
            # A deliberate per-tournament interpretation of a club-wide statement
            # is distinguishable from a direct per-tournament confirmation.
            authority = (
                BOOKING_AUTHORITY_MANUAL_INTERPRETATION
                if source_scope == "club_wide_interpretation"
                else BOOKING_AUTHORITY_MANUAL
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
            row = {
                "tournament_id": tid,
                "status": status,
                "authority": authority,
                "source_scope": source_scope,
                "host_club": str(tournament.get("host_club") or ""),
                "age_group": str(tournament.get("age_group") or ""),
                "arena": str(tournament.get("arena") or ""),
                "date": str(tournament.get("date") or ""),
                "start_time": str(tournament.get("start_time") or ""),
                "needs_attention": status in _ATTENTION_BOOKING_STATUSES or bool(follow_up_reasons),
                "stale_reasons": stale_reasons,
                "follow_up_reasons": follow_up_reasons,
                "calendar_status": calendar_status,
                "calendar_stale_reasons": calendar_stale_reasons,
                "evidence": manual,
            }
            if calendar_record:
                row["calendar_evidence"] = calendar_record
        else:
            status = calendar_status
            authority = BOOKING_AUTHORITY_CALENDAR if status == BOOKING_CONFIRMED_BOOKED else None
            stale_reasons = calendar_stale_reasons
            row = {
                "tournament_id": tid,
                "status": status,
                "authority": authority,
                "source_scope": "",
                "host_club": str(tournament.get("host_club") or ""),
                "age_group": str(tournament.get("age_group") or ""),
                "arena": str(tournament.get("arena") or ""),
                "date": str(tournament.get("date") or ""),
                "start_time": str(tournament.get("start_time") or ""),
                "needs_attention": status in _ATTENTION_BOOKING_STATUSES,
                "stale_reasons": stale_reasons,
                "follow_up_reasons": [],
                "calendar_status": status,
                "calendar_stale_reasons": calendar_stale_reasons,
            }
            if calendar_record:
                row["evidence"] = calendar_record
        if conflict:
            counts["conflicts"] += 1
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


# ---------------------------------------------------------------------------
# Read-only crosswalk assessment
# ---------------------------------------------------------------------------


def _assessment_parse_date(value: Any):
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _assessment_age_tokens(value: Any) -> set[str]:
    return {f"U{match}" for match in _AGE_TOKEN_RE.findall(str(value or ""))}


def _assessment_overlaps(
    tournament: Mapping[str, Any],
    event: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
) -> bool:
    interval = tournament_occupancy_interval_facts(tournament, problem)
    if str(event.get("date") or "") != interval["date"]:
        return False
    tournament_span = _parse_hhmm(interval["start_time"]), _parse_hhmm(interval["end_time"])
    event_span = _parse_hhmm(event.get("start")), _parse_hhmm(event.get("end"))
    if None in tournament_span or None in event_span:
        return False
    return tournament_span[0] < event_span[1] and event_span[0] < tournament_span[1]


def _assessment_candidate(
    tournament: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    problem: Mapping[str, Any] | None,
    date_window_days: int,
) -> dict[str, Any] | None:
    """Return one deterministic, bounded event<->tournament candidate row.

    The candidate set deliberately includes non-overlapping same-day and
    nearby-date possibilities so a changed slot never hides a booking.  It does
    **not** decide the semantic match: every row carries the evidence and
    counterevidence a harness/operator needs to adjudicate it.
    """

    if str(event.get("club") or "") != str(tournament.get("host_club") or ""):
        return None
    event_arena = str(event.get("arena") or event.get("location") or "").strip()
    tournament_arena = str(tournament.get("arena") or "").strip()
    # An arena mismatch is counterevidence, not a reason to drop the event: a
    # genuinely moved booking may have changed venue as well as date/time, and
    # hiding it would leave a stale canonical row looking unmatched.
    arena_mismatch = bool(
        event_arena and tournament_arena and event_arena.lower() != tournament_arena.lower()
    )
    event_date = _assessment_parse_date(event.get("date"))
    tournament_date = _assessment_parse_date(tournament.get("date"))
    if event_date is None or tournament_date is None:
        return None
    date_delta_days = (event_date - tournament_date).days
    if abs(date_delta_days) > date_window_days:
        return None

    covers = event_covers_tournament_interval(event, tournament, problem)
    overlaps = _assessment_overlaps(tournament, event, problem)
    if date_delta_days == 0:
        relation = RELATION_SAME_DATE_OVERLAP if overlaps else RELATION_SAME_DATE_TIME_SHIFT
    else:
        relation = RELATION_PROXIMATE_DATE_SHIFT

    age_group = str(tournament.get("age_group") or "")
    age_tokens = _assessment_age_tokens(event.get("calendar_event") or event.get("title"))
    age_group_conflict = bool(age_tokens) and bool(age_group) and age_group.upper() not in age_tokens

    evidence: list[str] = []
    counterevidence: list[str] = []
    if relation == RELATION_SAME_DATE_OVERLAP:
        evidence.append("same_date_interval_overlap")
    if covers:
        evidence.append("event_covers_canonical_interval")
    if age_tokens and age_group and not age_group_conflict:
        evidence.append("event_title_age_group_matches")
    if date_delta_days == 0:
        evidence.append("same_calendar_date")
    else:
        counterevidence.append("event_date_differs_from_canonical")
    if not covers:
        counterevidence.append("canonical_slot_not_covered")
    if age_group_conflict:
        counterevidence.append("event_title_age_group_differs")
    if arena_mismatch:
        counterevidence.append("event_arena_differs_from_canonical")

    return {
        "event_fingerprint": str(event.get("fingerprint") or event_fingerprint(event)),
        "club": str(event.get("club") or ""),
        "date": str(event.get("date") or ""),
        "start": str(event.get("start") or ""),
        "end": str(event.get("end") or ""),
        "title": str(event.get("calendar_event") or event.get("title") or ""),
        "availability": str(event.get("availability") or ""),
        "relation": relation,
        "date_delta_days": date_delta_days,
        "covers_current_interval": bool(covers),
        "overlaps_current_interval": bool(overlaps),
        "age_group_conflict": age_group_conflict,
        "arena_mismatch": arena_mismatch,
        "evidence": sorted(evidence),
        "counterevidence": sorted(counterevidence),
    }


def _assessment_tournament_row(
    tournament: Mapping[str, Any],
    *,
    candidates: list[dict[str, Any]],
    problem: Mapping[str, Any] | None,
    decisions: Mapping[str, Any] | None,
    associated_event: Mapping[str, Any] | None,
    source_trusted: bool,
    shared_event_fingerprints: set[str] | None = None,
) -> dict[str, Any]:
    tournament_id = str(tournament.get("id") or "")
    shared_event_fingerprints = shared_event_fingerprints or set()
    manual = manual_assertion_for_tournament(decisions, tournament_id)
    manual_stale_reasons = (
        manual_assertion_stale_reasons(manual, problem=problem, tournament=tournament)
        if manual is not None
        else []
    )
    has_manual_authority = manual is not None and not manual_stale_reasons
    # Observations with contradicting title/arena evidence stay in the report
    # but are not credible competing matches; only actionable candidates may
    # contest or resolve a proposal.
    actionable = [candidate for candidate in candidates if candidate.get("actionable")]
    if has_manual_authority:
        classification = ASSESSMENT_MANUALLY_ASSERTED
    elif associated_event is not None:
        classification = ASSESSMENT_ASSOCIATED
    elif not source_trusted:
        classification = ASSESSMENT_NOT_CHECKABLE
    elif not candidates:
        classification = ASSESSMENT_UNMATCHED
    elif len(actionable) > 1:
        # Several credible events map to the same tournament; the assessment
        # refuses to pick one and leaves the competition visible.
        classification = ASSESSMENT_COMPETING_CANDIDATES
    elif len(actionable) == 1:
        candidate = actionable[0]
        if candidate["event_fingerprint"] in shared_event_fingerprints:
            # One credible event plausibly belongs to more than one tournament
            # (one-to-many or group booking); do not silently bind it here.
            classification = ASSESSMENT_COMPETING_CANDIDATES
        elif candidate["covers_current_interval"] and candidate["relation"] == RELATION_SAME_DATE_OVERLAP:
            classification = ASSESSMENT_PROPOSED_UNCHANGED
        else:
            classification = ASSESSMENT_PROPOSED_CHANGED_SLOT
    else:
        # Only conflicting/observation candidates remain; keep the row
        # reviewable rather than resolving it or calling it contested.
        classification = ASSESSMENT_AMBIGUOUS

    if has_manual_authority:
        authority: str | None = (
            BOOKING_AUTHORITY_MANUAL_INTERPRETATION
            if str(manual.get("source_scope") or "") == "club_wide_interpretation"
            else BOOKING_AUTHORITY_MANUAL
        )
    elif associated_event is not None:
        authority = BOOKING_AUTHORITY_CALENDAR
    else:
        authority = None

    interval = tournament_occupancy_interval_facts(tournament, problem)
    row: dict[str, Any] = {
        "tournament_id": tournament_id,
        "classification": classification,
        "host_club": str(tournament.get("host_club") or ""),
        "age_group": str(tournament.get("age_group") or ""),
        "arena": str(tournament.get("arena") or ""),
        "date": interval["date"],
        "start_time": interval["start_time"],
        "duration_minutes": interval["duration_minutes"],
        "end_time": interval["end_time"],
        "canonical_interval": interval,
        # Authority (a deliberate association/assertion) is reported separately
        # from whether the current calendar source is checkable.  An explicit
        # authority can stand even when the scrape is untrusted; the source
        # review flag never silently demotes it.
        "authority": authority,
        "source_trusted": source_trusted,
        "calendar_source_checkable": source_trusted,
        "proposal_is_binding": False,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    if associated_event is not None:
        row["associated_event_fingerprint"] = str(
            associated_event.get("fingerprint") or event_fingerprint(associated_event)
        )
    if manual is not None:
        row["manual_authority"] = str(manual.get("authority") or "")
        row["manual_booking_status"] = manual_assertion_projection_status(manual)
        if manual_stale_reasons:
            row["manual_assertion_stale_reasons"] = manual_stale_reasons
    return row


def booking_assessment(
    *,
    problem: Mapping[str, Any] | None,
    plan: Mapping[str, Any] | None,
    decisions: Mapping[str, Any] | None,
    canonical_state_revision: str,
    season: str = "",
    clubs: Iterable[str] | None = None,
    date_window_days: int = DEFAULT_ASSESSMENT_DATE_WINDOW_DAYS,
) -> dict[str, Any]:
    """Build a deterministic, read-only tournament<->event booking crosswalk.

    The result is bound to the exact canonical revision and to each club's
    calendar fingerprint, so a second agent rerunning it against the same frozen
    inputs reproduces the same classifications.  It never writes canonical
    state, never confirms a booking and never claims that calendar absence proves
    a tournament is unbooked -- suspicious/untrusted sources fail closed to
    ``not_checkable`` and unresolved semantics stay visible.
    """

    tournaments = list(_tournaments_by_id(plan).values())
    events = list(iter_events(problem))
    if clubs is not None:
        wanted = {str(club) for club in clubs}
        tournaments = [t for t in tournaments if str(t.get("host_club") or "") in wanted]
        events = [e for e in events if str(e.get("club") or "") in wanted]

    calendar_status = (problem or {}).get("club_calendar_status") or {}
    if not isinstance(calendar_status, Mapping):
        calendar_status = {}

    all_clubs = sorted(
        {
            str(t.get("host_club") or "")
            for t in tournaments
            if str(t.get("host_club") or "")
        }
        | {str(e.get("club") or "") for e in events if str(e.get("club") or "")}
    )

    sources: dict[str, dict[str, Any]] = {}
    trusted_clubs: set[str] = set()
    for club in all_clubs:
        status = str(calendar_status.get(club) or "missing")
        trusted = status == "known"
        if trusted:
            trusted_clubs.add(club)
        sources[club] = {
            "club": club,
            "status": status,
            "source_trust": "trusted" if trusted else ("untrusted" if status == "untrusted" else "unknown"),
            "source_review_required": not trusted,
            # Absence in an otherwise trustworthy source is still only an
            # observation about the current slot; a negative booking claim needs
            # independent proof that the source covered the whole booking window.
            "trusted_for_negative_claim": False,
            "event_count": sum(1 for e in events if str(e.get("club") or "") == club),
            "calendar_fingerprint": club_calendar_fingerprint(problem, club),
        }

    events_by_club: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        events_by_club.setdefault(str(event.get("club") or ""), []).append(event)

    valid_associations = valid_active_associations(decisions, problem=problem, plan=plan)
    association_by_tournament = {
        str(record.get("tournament_id") or ""): str(record.get("event_fingerprint") or "")
        for record in valid_associations
        if str(record.get("tournament_id") or "")
    }
    event_by_fingerprint = {
        str(event.get("fingerprint") or event_fingerprint(event)): event for event in events
    }

    tournament_by_id: dict[str, Mapping[str, Any]] = {
        str(tournament.get("id") or ""): tournament for tournament in tournaments
    }
    candidates_by_tournament: dict[str, list[dict[str, Any]]] = {}
    candidates_by_event: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for tournament in tournaments:
        tournament_id = str(tournament.get("id") or "")
        club = str(tournament.get("host_club") or "")
        candidates: list[dict[str, Any]] = []
        for event in events_by_club.get(club, []):
            candidate = _assessment_candidate(
                tournament,
                event,
                problem=problem,
                date_window_days=date_window_days,
            )
            if candidate is not None:
                # An observation from an untrusted source stays visible but is
                # never an actionable proposal; contradicting title/arena
                # evidence has the same effect.
                candidate["source_trusted"] = club in trusted_clubs
                candidate["actionable"] = bool(
                    candidate["source_trusted"]
                    and not candidate["age_group_conflict"]
                    and not candidate["arena_mismatch"]
                )
                candidates.append(candidate)
                candidates_by_event.setdefault(candidate["event_fingerprint"], []).append(
                    (tournament_id, candidate)
                )
        candidates.sort(
            key=lambda item: (
                _ASSESSMENT_RELATION_RANK.get(item["relation"], 99),
                abs(int(item["date_delta_days"])),
                item["date"],
                item["start"],
                item["end"],
                item["event_fingerprint"],
            )
        )
        candidates_by_tournament[tournament_id] = candidates

    # An actionable event that plausibly belongs to more than one tournament is
    # one-to-many (or a group booking); every affected tournament competes for
    # it instead of the assessment silently binding it to whichever row sorts
    # first. Non-actionable observations do not create competition.
    shared_event_fingerprints = {
        event_fp
        for event_fp, entries in candidates_by_event.items()
        if len(
            {tournament_id for tournament_id, candidate in entries if candidate.get("actionable")}
        )
        > 1
    }

    tournament_rows: list[dict[str, Any]] = []
    for tournament_id, tournament in tournament_by_id.items():
        club = str(tournament.get("host_club") or "")
        associated_fp = association_by_tournament.get(tournament_id)
        associated_event = event_by_fingerprint.get(associated_fp) if associated_fp else None
        tournament_rows.append(
            _assessment_tournament_row(
                tournament,
                candidates=candidates_by_tournament.get(tournament_id, []),
                problem=problem,
                decisions=decisions,
                associated_event=associated_event,
                source_trusted=club in trusted_clubs,
                shared_event_fingerprints=shared_event_fingerprints,
            )
        )
    tournament_rows.sort(key=lambda row: row["tournament_id"])

    events_rows: list[dict[str, Any]] = []
    for event in events:
        event_fp = str(event.get("fingerprint") or event_fingerprint(event))
        club = str(event.get("club") or "")
        candidate_tournaments: list[dict[str, Any]] = []
        covered_ids: list[str] = []
        for tournament_id, candidate in candidates_by_event.get(event_fp, []):
            tournament = tournament_by_id[tournament_id]
            if candidate["covers_current_interval"]:
                covered_ids.append(tournament_id)
            candidate_tournaments.append(
                {
                    "tournament_id": tournament_id,
                    "age_group": str(tournament.get("age_group") or ""),
                    "arena": str(tournament.get("arena") or ""),
                    "date": str(tournament.get("date") or ""),
                    "start_time": str(tournament.get("start_time") or ""),
                    "relation": candidate["relation"],
                    "date_delta_days": candidate["date_delta_days"],
                    "covers_current_interval": candidate["covers_current_interval"],
                    "age_group_conflict": candidate["age_group_conflict"],
                    "arena_mismatch": candidate["arena_mismatch"],
                    "source_trusted": candidate["source_trusted"],
                    "actionable": candidate["actionable"],
                }
            )
        candidate_tournaments.sort(key=lambda item: (item["date"], item["start_time"], item["tournament_id"]))
        events_rows.append(
            {
                "event_fingerprint": event_fp,
                "club": club,
                "date": str(event.get("date") or ""),
                "start": str(event.get("start") or ""),
                "end": str(event.get("end") or ""),
                "title": str(event.get("calendar_event") or event.get("title") or ""),
                "availability": str(event.get("availability") or ""),
                "candidate_count": len(candidate_tournaments),
                "candidate_tournaments": candidate_tournaments,
                "covered_tournament_ids": sorted(covered_ids),
                "one_to_many": len(candidate_tournaments) > 1,
                "group_booking": len(covered_ids) > 1,
                "unmatched": not candidate_tournaments,
            }
        )
    events_rows.sort(key=lambda row: (row["club"], row["date"], row["start"], row["end"], row["event_fingerprint"]))

    counts: dict[str, int] = {
        ASSESSMENT_ASSOCIATED: 0,
        ASSESSMENT_MANUALLY_ASSERTED: 0,
        ASSESSMENT_PROPOSED_UNCHANGED: 0,
        ASSESSMENT_PROPOSED_CHANGED_SLOT: 0,
        ASSESSMENT_COMPETING_CANDIDATES: 0,
        ASSESSMENT_AMBIGUOUS: 0,
        ASSESSMENT_UNMATCHED: 0,
        ASSESSMENT_NOT_CHECKABLE: 0,
    }
    for row in tournament_rows:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    counts["unresolved_tournaments"] = sum(
        1 for row in tournament_rows if row["classification"] in _ASSESSMENT_UNRESOLVED
    )
    counts["unresolved_events"] = sum(
        1 for row in events_rows if row["unmatched"] or row["one_to_many"]
    )
    findings = association_findings(problem=problem, plan=plan, decisions=decisions)
    counts["stale_associations"] = sum(
        1 for finding in findings if finding.get("code") == "stale_calendar_booking_association"
    )

    unresolved = {
        "tournament_ids": sorted(
            row["tournament_id"] for row in tournament_rows if row["classification"] in _ASSESSMENT_UNRESOLVED
        ),
        "event_fingerprints": sorted(
            row["event_fingerprint"] for row in events_rows if row["unmatched"] or row["one_to_many"]
        ),
    }

    assessment: dict[str, Any] = {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "season": season,
        "canonical_state_revision": str(canonical_state_revision),
        "date_window_days": int(date_window_days),
        "clubs": all_clubs,
        "sources": sources,
        "tournaments": tournament_rows,
        "events": events_rows,
        "counts": counts,
        "unresolved": unresolved,
        "findings": findings,
        "limitations": [
            "calendar_absence_is_not_proof_the_tournament_is_unbooked",
            "proposals_are_advisory_until_an_explicit_operator_confirmation",
            "source_coverage_proof_is_not_established_by_this_assessment",
        ],
    }
    assessment["assessment_fingerprint"] = stable_payload_sha256(assessment)
    return assessment
