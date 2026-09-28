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
from tournament_scheduler.pipeline.source_integrity import fabricated_interval_signal

CALENDAR_BOOKING_ASSOCIATIONS_KEY = "calendar_booking_associations"
TOURNAMENT_BOOKING_EVIDENCE_KEY = "tournament_booking_evidence"
# Explicit operator/club assertions live in their own durable decisions key so a
# routine calendar reconcile/refresh that rewrites ``TOURNAMENT_BOOKING_EVIDENCE_KEY``
# can never erase or demote them.  A manual assertion is a statement about the
# booking whose authority is a person, not an event fingerprint.  The projection
# keeps that typed authority distinct from a calendar-derived observation.
MANUAL_BOOKING_ASSERTIONS_KEY = "manual_booking_assertions"
# A club-wide booking list (for example a spreadsheet or an email listing every
# home tournament) is a single source document, not a bag of independent
# per-tournament confirmations.  The source assertion is persisted separately so
# the original evidence, its version/fingerprint and the club scope survive even
# after every per-tournament interpretation is re-confirmed.  Each individual
# manual assertion may link to it, which keeps club provenance out of the
# assertion itself while preserving the per-ID authority.
CLUB_BOOKING_SOURCE_ASSERTIONS_KEY = "club_booking_source_assertions"
CLUB_BOOKING_SOURCE_SCOPE = "club_wide"
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
# visible.  It never persists an association or cancels a tournament.  Only a
# complete, coverage-proven, fresh trusted host-calendar window may contribute
# explicit absence evidence, and even then the result is a non-binding
# presumed-unscheduled review state rather than a canonical booking decision.
ASSESSMENT_SCHEMA_VERSION = 1
DEFAULT_ASSESSMENT_DATE_WINDOW_DAYS = 7

ASSESSMENT_ASSOCIATED = "associated"
ASSESSMENT_MANUALLY_ASSERTED = "manually_asserted"
ASSESSMENT_PROPOSED_UNCHANGED = "proposed_unchanged"
ASSESSMENT_PROPOSED_CHANGED_SLOT = "proposed_changed_slot"
ASSESSMENT_COMPETING_CANDIDATES = "competing_candidates"
ASSESSMENT_AMBIGUOUS = "ambiguous"
ASSESSMENT_UNMATCHED = "unmatched"
ASSESSMENT_PRESUMED_UNSCHEDULED = "presumed_unscheduled"
ASSESSMENT_NOT_CHECKABLE = "not_checkable"

RELATION_SAME_DATE_OVERLAP = "same_date_overlap"
RELATION_SAME_DATE_TIME_SHIFT = "same_date_time_shift"
RELATION_PROXIMATE_DATE_SHIFT = "proximate_date_shift"

_ASSESSMENT_UNRESOLVED = {
    ASSESSMENT_COMPETING_CANDIDATES,
    ASSESSMENT_AMBIGUOUS,
    ASSESSMENT_UNMATCHED,
    ASSESSMENT_PRESUMED_UNSCHEDULED,
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

# Unified operational booking state -------------------------------------------
# One operator-facing projection of the detailed booking evidence below. The
# detailed status/authority/follow-up fields stay available for audit and
# export details; this single value is what the season plan renders as the one
# top-level booking badge, so an operator can tell at a glance whether ice time
# is confirmed and protected or needs action.
OPERATIONAL_BOOKED = "booked"
OPERATIONAL_NOT_BOOKED = "not_booked"
OPERATIONAL_ACTION_REQUIRED = "action_required"
OPERATIONAL_CHANGED_SLOT_REVIEW = "changed_slot_review"
OPERATIONAL_PRESUMED_UNSCHEDULED = "presumed_unscheduled"
OPERATIONAL_UNKNOWN = "unknown"

# Manual-booking queue vocabulary --------------------------------------------
# A single operational ``action_required`` state covers different kinds of
# operator work. The queue distinguishes *why* a slot needs action -- an
# explicit host rejection, a confirmation invalidated by a slot change, a
# retained manual placement or a movable host interval -- so the operator sees
# the reason, the source evidence, the plausible calendar alternatives and the
# action that closes the item. These are derived projections of the same
# canonical evidence, never a second booking status or a persisted decision.
MANUAL_QUEUE_EXPLICIT_REJECTION = "explicit_rejection"
MANUAL_QUEUE_RECONFIRMATION_REQUIRED = "reconfirmation_required"
MANUAL_QUEUE_MANUAL_PLACEMENT = "manual_placement"
MANUAL_QUEUE_HOST_CONFIRMATION_REQUIRED = "host_confirmation_required"
MANUAL_QUEUE_ACTION_BOOK_OR_RECONFIRM = "book_or_reconfirm"
MANUAL_QUEUE_CLEARS_WHEN = "accepted_booking_assertion_or_valid_calendar_association"

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
        if isinstance(entries, (str, bytes, Mapping)) or not hasattr(entries, "__iter__"):
            # Structurally malformed interval evidence is not silently skipped:
            # `club_calendar_evidence_trusted` detects it separately and fails
            # the club closed, but this reader must not crash the whole report on
            # one bad club.
            continue
        for entry in entries:
            if isinstance(entry, Mapping):
                rows.append(with_event_fingerprint(str(club), entry))
    return rows


def club_calendar_status(problem: Mapping[str, Any] | None, club: str) -> str:
    """Return the stored calendar-evidence status for one host club."""
    return str(((problem or {}).get("club_calendar_status") or {}).get(club) or "")


def _club_fabricated_interval_signal(problem: Mapping[str, Any] | None, club: str) -> str | None:
    intervals = ((problem or {}).get("club_busy_intervals") or {}).get(club)
    return fabricated_interval_signal(intervals)


def club_calendar_evidence_trusted(problem: Mapping[str, Any] | None, club: str) -> bool:
    """Whether one club's stored calendar evidence may support any booking claim.

    The pipeline's own ``club_calendar_status`` is necessary but not
    sufficient: a verifier must stay independent of the generator that wrote
    it. Evidence whose normalized intervals still carry the fabricated
    fallback fingerprint is never trustworthy, even when the stored status
    says ``known`` from a scrape that predates the fingerprint detector.
    """
    if club_calendar_status(problem, club) != "known":
        return False
    return _club_fabricated_interval_signal(problem, club) is None


def club_calendar_positive_evidence_usable(problem: Mapping[str, Any] | None, club: str) -> bool:
    """Whether observed events may be offered as positive booking candidates.

    Full-window trust is required for negative/free-ice claims, but a partially
    navigated source can still contain authentic observed events.  Those events
    may enter bounded positive reconciliation when source integrity says the
    scrape is structurally complete or partial and the normalized intervals do
    not carry the fabricated-placeholder fingerprint.  Suspicious, failed,
    untrusted, malformed or unknown sources remain unusable for positive
    evidence.
    """

    if _club_fabricated_interval_signal(problem, club) is not None:
        return False
    status = club_calendar_status(problem, club)
    if status == "known":
        return True
    if status != "source_review_required":
        return False
    source_integrity = (problem or {}).get("club_source_integrity") or {}
    if not isinstance(source_integrity, Mapping):
        return False
    return str(source_integrity.get(club) or "") in {"complete", "partial"}


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


def club_booking_source_id(club: str, source_fingerprint: str) -> str:
    """Stable identity for one club-wide booking source document."""

    return f"club_booking_source:{club}:{source_fingerprint}"


def _club_booking_source_fingerprint(
    *,
    club: str,
    source_document: str,
    source_version: str,
) -> str:
    """Deterministic identity of a club booking source when none is supplied.

    The fingerprint names the source identity, not its volatile file bytes, so a
    re-import of the same document/version is idempotent while a new version or
    a different document is a distinct, superseding assertion.
    """

    return stable_payload_sha256(
        {"club": club, "source_document": source_document, "source_version": source_version}
    )


def new_club_booking_source_record(
    *,
    club: str,
    source_document: str,
    source_version: str,
    actor: str,
    asserted_at: str,
    source_revision: str,
    reference: str = "",
    note: str = "",
    source_fingerprint: str | None = None,
    supersedes: str | None = None,
) -> dict[str, Any]:
    """Build one durable, revision-bound club-wide booking source assertion.

    The record carries the original source identity/version plus its optional
    explicit fingerprint, so a later operator can tell which club-provided
    booking list a per-tournament interpretation was derived from.
    """

    fingerprint = str(source_fingerprint or "").strip() or _club_booking_source_fingerprint(
        club=club,
        source_document=source_document,
        source_version=source_version,
    )
    return {
        "id": club_booking_source_id(club, fingerprint),
        "schema_version": 1,
        "status": MANUAL_ASSERTION_ACTIVE,
        "authority": BOOKING_AUTHORITY_MANUAL_INTERPRETATION,
        "source_scope": CLUB_BOOKING_SOURCE_SCOPE,
        "host_club": club,
        "source_document": source_document,
        "source_version": source_version,
        "source_fingerprint": fingerprint,
        "reference": str(reference or ""),
        "note": note or "",
        "asserted_at": asserted_at,
        "asserted_by": actor,
        "source_revision": source_revision,
        "supersedes": supersedes,
    }


def club_booking_source_records(decisions: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return every persisted club-wide booking source assertion."""

    return [
        dict(record)
        for record in ((decisions or {}).get(CLUB_BOOKING_SOURCE_ASSERTIONS_KEY) or [])
        if isinstance(record, Mapping)
    ]


def active_club_booking_source_assertions(decisions: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    return [
        record
        for record in club_booking_source_records(decisions)
        if str(record.get("status") or "") == MANUAL_ASSERTION_ACTIVE
    ]


def club_booking_source_by_id(
    decisions: Mapping[str, Any] | None,
    source_id: str,
) -> dict[str, Any] | None:
    """Return one active club-wide source assertion by id, if present.

    Used for link validation: a new per-tournament interpretation may only be
    linked to a currently active source version.
    """

    if not source_id:
        return None
    for record in active_club_booking_source_assertions(decisions):
        if str(record.get("id") or "") == source_id:
            return record
    return None


def club_booking_source_record_by_id(
    decisions: Mapping[str, Any] | None,
    source_id: str,
) -> dict[str, Any] | None:
    """Return one club-wide source assertion by id regardless of status.

    Historical provenance lookup: after a source is superseded, existing
    per-tournament interpretations still reference the old id and must keep
    resolving to the document/version they were derived from.
    """

    if not source_id:
        return None
    for record in club_booking_source_records(decisions):
        if str(record.get("id") or "") == source_id:
            return record
    return None


def active_club_booking_source_for_club(
    decisions: Mapping[str, Any] | None,
    club: str,
) -> dict[str, Any] | None:
    """Return the newest active club-wide source assertion for one club."""

    matches = [
        record
        for record in active_club_booking_source_assertions(decisions)
        if str(record.get("host_club") or "") == club
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda record: str(record.get("asserted_at") or ""))[-1]


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
    source_assertion_id: str | None = None,
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
        "source_assertion_id": str(source_assertion_id or "") or None,
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
    """Return the canonical occupied interval facts for one tournament.

    A host-confirmed per-tournament override projected into the problem replaces
    the age-group default, so ``confirm-calendar-booking`` coverage and the
    arena-interval verifier describe the same booked window.
    """

    # Local import breaks the canonical_state <-> calendar_bookings import
    # cycle; the projection helper itself is the single key definition.
    from tournament_scheduler.canonical_ice_time_overrides import overrides_from_problem

    age_group = str(tournament.get("age_group") or "")
    start_time = str(tournament.get("start_time") or "")
    override = overrides_from_problem(problem).get(str(tournament.get("id") or ""))
    duration = 0
    if override:
        duration = int(override)
    else:
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


def operational_booking_state(
    *,
    status: str,
    manual_booking_reason: str | None = None,
    requires_host_confirmation: bool = False,
) -> str:
    """Collapse detailed booking evidence into one operator-facing state.

    ``booked``
        An accepted confirmation exists -- a durable manual assertion or a
        currently valid calendar association. The canonical slot is protected:
        a missing or contradicting scrape is a detail, never a reason to move
        it or downgrade the confirmation. An accepted confirmation is checked
        first because a provisional ``manual_booking_reason`` or a
        ``requires_host_confirmation`` flag set when the plan was built can
        stay on the tournament after the operator confirms the slot; that
        retained metadata is historical, not active work.
    ``action_required``
        No accepted confirmation exists and the assigned slot is not
        established ice (the host calendar could not be read, or a
        host-controlled interval must be moved/confirmed), or a prior
        confirmation was explicitly rejected or invalidated. These are the
        manual booking queue / re-confirmation work items.
    ``not_booked``
        No accepted confirmation and no explicit rejection. Missing calendar
        evidence and unresolved ambiguity land here, muted, and must not be
        mistaken for a host rejection or silently pushed to the manual queue.
    """
    if status in (BOOKING_CONFIRMED_BOOKED, BOOKING_MANUALLY_BOOKED):
        return OPERATIONAL_BOOKED
    if str(manual_booking_reason or "").strip() or requires_host_confirmation:
        return OPERATIONAL_ACTION_REQUIRED
    if status in (BOOKING_CONFIRMED_NOT_BOOKED, BOOKING_MANUALLY_NOT_BOOKED, STALE):
        return OPERATIONAL_ACTION_REQUIRED
    return OPERATIONAL_NOT_BOOKED


def _action_required_reason_code(
    *,
    status: str,
    manual_booking_reason: str,
    requires_host_confirmation: bool,
) -> str:
    """Classify why an ``action_required`` tournament is manual work."""

    if status in (BOOKING_CONFIRMED_NOT_BOOKED, BOOKING_MANUALLY_NOT_BOOKED):
        return MANUAL_QUEUE_EXPLICIT_REJECTION
    if status == STALE:
        return MANUAL_QUEUE_RECONFIRMATION_REQUIRED
    if requires_host_confirmation and not manual_booking_reason.strip():
        return MANUAL_QUEUE_HOST_CONFIRMATION_REQUIRED
    return MANUAL_QUEUE_MANUAL_PLACEMENT


def _manual_work_source(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return the traceable source evidence behind one queue item.

    A manual assertion (rejection/confirmation) carries an authority, reference
    and author; calendar-derived evidence carries the checked fingerprint and
    reason. Keeping the two shapes explicit lets the queue stay auditable
    without inventing per-ID source detail that does not exist.
    """

    evidence = row.get("evidence")
    if isinstance(evidence, Mapping) and evidence.get("authority"):
        return {
            "authority": str(evidence.get("authority") or ""),
            "source_scope": str(evidence.get("source_scope") or ""),
            "reference": str(evidence.get("reference") or ""),
            "note": str(evidence.get("note") or ""),
            "asserted_by": str(evidence.get("asserted_by") or ""),
            "asserted_at": str(evidence.get("asserted_at") or ""),
            "assertion_id": str(evidence.get("id") or ""),
            "supersedes": str(evidence.get("supersedes") or ""),
            # Club-wide source provenance, denormalized from the linked source
            # assertion so the queue item is traceable without a second lookup.
            "source_assertion_id": str(row.get("source_assertion_id") or ""),
            "source_document": str(row.get("source_document") or ""),
            "source_version": str(row.get("source_version") or ""),
        }
    # A calendar-only row stores its booking-evidence record under
    # ``evidence`` (no authority); a manual row may additionally carry a
    # separate ``calendar_evidence`` observation. Prefer the explicit key.
    calendar = row.get("calendar_evidence")
    if not isinstance(calendar, Mapping):
        calendar = evidence
    if isinstance(calendar, Mapping):
        return {
            "source": str(calendar.get("source_calendar_status") or ""),
            "reason": str(calendar.get("reason") or ""),
            "note": str(calendar.get("note") or ""),
            "checked_by": str(calendar.get("checked_by") or ""),
            "checked_at": str(calendar.get("checked_at") or ""),
            "event_fingerprint": str(calendar.get("event_fingerprint") or ""),
        }
    return {}


def _manual_work_alternatives(assessment_row: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return actionable calendar alternatives for one queue item.

    Only candidates the read-only assessment marked actionable are offered; an
    untrusted-source observation or a title/arena contradiction stays in the
    assessment evidence but is never presented as a bookable alternative.
    """

    alternatives: list[dict[str, Any]] = []
    for candidate in (assessment_row or {}).get("candidates") or []:
        if not isinstance(candidate, Mapping) or not candidate.get("actionable"):
            continue
        alternatives.append(
            {
                "date": str(candidate.get("date") or ""),
                "start": str(candidate.get("start") or ""),
                "end": str(candidate.get("end") or ""),
                "title": str(candidate.get("title") or ""),
                "relation": str(candidate.get("relation") or ""),
                "event_fingerprint": str(candidate.get("event_fingerprint") or ""),
                "covers_current_interval": bool(candidate.get("covers_current_interval")),
            }
        )
    return alternatives


def manual_booking_work_item(
    *,
    row: Mapping[str, Any],
    tournament: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
    assessment_row: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the structured manual-booking queue item for one projected row.

    Only an ``action_required`` tournament becomes work. The item carries the
    *reason* it needs action, the *source* evidence (a manual assertion's
    reference/author/note or the calendar evidence), the *proposed
    alternatives* the read-only assessment found, the *owner* (host club), the
    required *action*, and how the item is *resolved*. It never persists
    anything or changes a booking decision; the queue is a projection over the
    same canonical evidence as :func:`booking_status_report`.
    """

    if str(row.get("operational_state") or "") != OPERATIONAL_ACTION_REQUIRED:
        return None
    status = str(row.get("status") or "")
    manual_booking_reason = str(tournament.get("manual_booking_reason") or "")
    requires_host_confirmation = bool(tournament.get("requires_host_confirmation"))
    reason_code = _action_required_reason_code(
        status=status,
        manual_booking_reason=manual_booking_reason,
        requires_host_confirmation=requires_host_confirmation,
    )
    source = _manual_work_source(row)
    interval = tournament_occupancy_interval_facts(tournament, problem)
    return {
        "tournament_id": str(row.get("tournament_id") or ""),
        "host_club": str(row.get("host_club") or ""),
        "age_group": str(row.get("age_group") or ""),
        "arena": str(row.get("arena") or ""),
        "canonical_interval": {
            "date": interval["date"],
            "start_time": interval["start_time"],
            "end_time": interval["end_time"],
        },
        "reason_code": reason_code,
        "reason_detail": manual_booking_reason,
        "owner": str(row.get("host_club") or ""),
        "action": MANUAL_QUEUE_ACTION_BOOK_OR_RECONFIRM,
        "source": source,
        "proposed_alternatives": _manual_work_alternatives(assessment_row),
        "stale_reasons": list(row.get("stale_reasons") or []),
        # How the item clears. The trace itself is the append-only canonical
        # decision history plus the source assertion chain recorded above.
        "resolution": {
            "status": "open",
            "clears_when": MANUAL_QUEUE_CLEARS_WHEN,
        },
    }


def _assessment_provenance_fields(assessment_row: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the non-binding assessment provenance for one booking-status row.

    The crosswalk is advisory: attaching these fields never changes an accepted
    booking status or authority. It carries the classification and canonical
    interval and, only when a plausible observation exists, the latest
    *actionable* calendar observation. A negative finding deliberately carries
    no observed interval, because an unrelated or wrong-arena event must never
    be presented as this tournament's observed booking.
    """

    if not assessment_row:
        return {}
    fields: dict[str, Any] = {}
    classification = str(assessment_row.get("classification") or "")
    if classification:
        fields["booking_assessment_classification"] = classification
    canonical_interval = assessment_row.get("canonical_interval")
    if isinstance(canonical_interval, Mapping):
        fields["canonical_interval"] = dict(canonical_interval)
    actionable = [
        candidate
        for candidate in assessment_row.get("candidates") or []
        if isinstance(candidate, Mapping) and candidate.get("actionable")
    ]
    if actionable:
        candidate = max(
            actionable,
            key=lambda item: (
                str(item.get("date") or ""),
                str(item.get("start") or ""),
                str(item.get("end") or ""),
            ),
        )
        fields["latest_observed_interval"] = {
            "date": str(candidate.get("date") or ""),
            "start": str(candidate.get("start") or ""),
            "end": str(candidate.get("end") or ""),
            "event_fingerprint": str(candidate.get("event_fingerprint") or ""),
            "title": str(candidate.get("title") or ""),
            "source_trusted": bool(candidate.get("source_trusted")),
            "actionable": True,
        }
    if classification == ASSESSMENT_PRESUMED_UNSCHEDULED:
        fields["negative_evidence"] = assessment_row.get("negative_evidence")
    return fields


def _club_booking_source_projection(
    *,
    decisions: Mapping[str, Any] | None,
    rows: list[dict[str, Any]],
    plan: Mapping[str, Any] | None,
    problem: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Group per-tournament booking authority under its club-wide source.

    The projection is read-only: it reports which club-provided booking list
    each interpretation came from, the per-ID canonical interval and booking
    state, and any interval follow-up (for example a duration discrepancy the
    club stated but the canonical occupancy deliberately did not adopt). It also
    flags two linked IDs whose club-stated (or canonical) intervals overlap, so an
    ambiguous club list is review-required instead of silently treated as two
    bookings.
    """

    tournaments = _tournaments_by_id(plan)
    rows_by_id = {str(row.get("tournament_id") or ""): row for row in rows}
    active = active_manual_assertions(decisions)
    all_sources = {
        str(record.get("id") or ""): record
        for record in club_booking_source_records(decisions)
    }
    active_source_ids = {
        str(record.get("id") or "")
        for record in active_club_booking_source_assertions(decisions)
    }
    referenced_ids = {
        str(assertion.get("source_assertion_id") or "")
        for assertion in active
        if str(assertion.get("source_assertion_id") or "")
    }
    # Report every active source plus any superseded source still referenced by
    # an active assertion, so replacing a source version never silently drops
    # the provenance (and the review signal) for already-linked IDs.
    report_ids = sorted(active_source_ids) + sorted(referenced_ids - active_source_ids)
    sources: list[dict[str, Any]] = []
    for source_id in report_ids:
        record = all_sources.get(source_id) or {}
        is_active = source_id in active_source_ids
        items: list[dict[str, Any]] = []
        for assertion in sorted(active, key=lambda item: str(item.get("tournament_id") or "")):
            if str(assertion.get("source_assertion_id") or "") != source_id:
                continue
            tid = str(assertion.get("tournament_id") or "")
            tournament = tournaments.get(tid)
            row = rows_by_id.get(tid) or {}
            interval = (
                tournament_occupancy_interval_facts(tournament, problem)
                if tournament is not None
                else {}
            )
            stated = assertion.get("stated_interval") or {}
            # The club's own window is the review subject when it stated one; a
            # club-wide list commonly overlaps even though the canonical plan
            # was shortened to fit. Fall back to the canonical interval when the
            # source did not state a window.
            effective_date = str(stated.get("date") or interval.get("date") or "")
            effective_start = str(stated.get("start") or interval.get("start_time") or "")
            effective_end = str(stated.get("end") or interval.get("end_time") or "")
            items.append(
                {
                    "tournament_id": tid,
                    "age_group": str((tournament or {}).get("age_group") or ""),
                    "date": str((tournament or {}).get("date") or ""),
                    "canonical_interval": interval,
                    "stated_interval": dict(stated) if isinstance(stated, Mapping) else {},
                    "effective_source_interval": {
                        "date": effective_date,
                        "start_time": effective_start,
                        "end_time": effective_end,
                    },
                    "booking_status": row.get("status"),
                    "operational_state": row.get("operational_state"),
                    "authority": row.get("authority"),
                    "stale_reasons": list(row.get("stale_reasons") or []),
                    "interval_follow_up": manual_assertion_interval_follow_up(assertion),
                }
            )
        overlaps: list[list[str]] = []
        for left_index, left in enumerate(items):
            for right in items[left_index + 1 :]:
                same_date = (
                    left["effective_source_interval"]["date"]
                    and left["effective_source_interval"]["date"]
                    == right["effective_source_interval"]["date"]
                )
                if not same_date:
                    continue
                left_start = _parse_hhmm(left["effective_source_interval"]["start_time"])
                left_end = _parse_hhmm(left["effective_source_interval"]["end_time"])
                right_start = _parse_hhmm(right["effective_source_interval"]["start_time"])
                right_end = _parse_hhmm(right["effective_source_interval"]["end_time"])
                if None in (left_start, left_end, right_start, right_end):
                    continue
                if left_start < right_end and right_start < left_end:
                    overlaps.append([left["tournament_id"], right["tournament_id"]])
        stale_link = bool(items) and not is_active
        sources.append(
            {
                "id": source_id,
                "host_club": str(record.get("host_club") or ""),
                "source_document": str(record.get("source_document") or ""),
                "source_version": str(record.get("source_version") or ""),
                "source_fingerprint": str(record.get("source_fingerprint") or ""),
                "reference": str(record.get("reference") or ""),
                "asserted_at": str(record.get("asserted_at") or ""),
                "asserted_by": str(record.get("asserted_by") or ""),
                "status": str(record.get("status") or ""),
                "is_active": is_active,
                "superseded_by": str(record.get("superseded_by") or ""),
                "stale_link": stale_link,
                "tournament_ids": [item["tournament_id"] for item in items],
                "tournaments": items,
                "overlapping_source_intervals": overlaps,
                "requires_operator_review": bool(overlaps) or stale_link,
            }
        )
    # A club-wide interpretation recorded before source documents existed (or
    # whose source was not linked) stays visible as unprovenanced so it is not
    # mistaken for a direct per-tournament confirmation with a known source.
    unlinked = sorted(
        str(assertion.get("tournament_id") or "")
        for assertion in active
        if str(assertion.get("source_scope") or "") == "club_wide_interpretation"
        and not str(assertion.get("source_assertion_id") or "")
    )
    if unlinked:
        sources.append(
            {
                "id": "club_booking_source:unlinked",
                "host_club": "",
                "source_document": "",
                "source_version": "",
                "source_fingerprint": "",
                "reference": "",
                "asserted_at": "",
                "asserted_by": "",
                "tournament_ids": unlinked,
                "tournaments": [],
                "overlapping_source_intervals": [],
                "requires_operator_review": True,
                "unprovenanced": True,
            }
        )
    return sources


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
    except Exception:  # noqa: BLE001 - advisory crosswalk failure must not hide persisted booking status.
        assessment_by_tournament = {}
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
            source_record = club_booking_source_record_by_id(
                decisions, str(manual.get("source_assertion_id") or "")
            )
            source_is_current = (
                source_record is None
                or str(source_record.get("status") or "") == MANUAL_ASSERTION_ACTIVE
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
            if not source_is_current:
                # The assertion is still valid authority, but it points at a
                # superseded club source version; surface it for relinking
                # rather than silently losing the provenance.
                follow_up_reasons.append("club_booking_source_superseded")
            operational_state = operational_booking_state(
                status=status,
                manual_booking_reason=str(tournament.get("manual_booking_reason") or ""),
                requires_host_confirmation=bool(tournament.get("requires_host_confirmation")),
            )
            row = {
                "tournament_id": tid,
                "status": status,
                "operational_state": operational_state,
                "operational_lock": operational_state == OPERATIONAL_BOOKED,
                "authority": authority,
                "source_scope": source_scope,
                "source_assertion_id": str(manual.get("source_assertion_id") or "") or None,
                "source_document": str((source_record or {}).get("source_document") or ""),
                "source_version": str((source_record or {}).get("source_version") or ""),
                "source_is_current": source_is_current,
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
            # Carry advisory crosswalk provenance (classification, canonical and
            # latest observed interval, negative evidence) next to a manual
            # authority so a contradicting scrape stays visible without ever
            # demoting the accepted confirmation/rejection.
            row.update(_assessment_provenance_fields(assessment_by_tournament.get(tid)))
            if calendar_record:
                row["calendar_evidence"] = calendar_record
        else:
            status = calendar_status
            authority = BOOKING_AUTHORITY_CALENDAR if status == BOOKING_CONFIRMED_BOOKED else None
            stale_reasons = calendar_stale_reasons
            operational_state = operational_booking_state(
                status=status,
                manual_booking_reason=str(tournament.get("manual_booking_reason") or ""),
                requires_host_confirmation=bool(tournament.get("requires_host_confirmation")),
            )
            assessment_row = assessment_by_tournament.get(tid) or {}
            assessment_classification = str(assessment_row.get("classification") or "")
            if status == BOOKING_UNKNOWN and operational_state == OPERATIONAL_NOT_BOOKED:
                if assessment_classification == ASSESSMENT_PROPOSED_CHANGED_SLOT:
                    operational_state = OPERATIONAL_CHANGED_SLOT_REVIEW
                elif assessment_classification == ASSESSMENT_PRESUMED_UNSCHEDULED:
                    operational_state = OPERATIONAL_PRESUMED_UNSCHEDULED
                elif assessment_classification in (ASSESSMENT_NOT_CHECKABLE, ASSESSMENT_UNMATCHED):
                    operational_state = OPERATIONAL_UNKNOWN
            row = {
                "tournament_id": tid,
                "status": status,
                "operational_state": operational_state,
                "operational_lock": operational_state == OPERATIONAL_BOOKED,
                "authority": authority,
                "source_scope": "",
                "host_club": str(tournament.get("host_club") or ""),
                "age_group": str(tournament.get("age_group") or ""),
                "arena": str(tournament.get("arena") or ""),
                "date": str(tournament.get("date") or ""),
                "start_time": str(tournament.get("start_time") or ""),
                "needs_attention": status in _ATTENTION_BOOKING_STATUSES
                or operational_state in (OPERATIONAL_CHANGED_SLOT_REVIEW, OPERATIONAL_PRESUMED_UNSCHEDULED),
                "stale_reasons": stale_reasons,
                "follow_up_reasons": [],
                "calendar_status": status,
                "calendar_stale_reasons": calendar_stale_reasons,
            }
            row.update(_assessment_provenance_fields(assessment_row))
            if calendar_record:
                row["evidence"] = calendar_record
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
        rows.append(row)
        counts.setdefault(status, 0)
        counts[status] += 1
        counts[operational_state] = counts.get(operational_state, 0) + 1
        if row["needs_attention"]:
            counts["needs_attention"] += 1
    return {
        "tournaments": rows,
        "counts": counts,
        # One structured work item per ``action_required`` tournament; the
        # per-row copy keeps consumers that iterate tournaments able to render
        # the reason/source/alternatives without recomputing the projection.
        "manual_booking_queue": manual_queue,
        # Club-wide source provenance, grouped per provided booking list. A
        # source whose own stated windows overlap is review-required.
        "club_booking_sources": _club_booking_source_projection(
            decisions=decisions, rows=rows, plan=plan, problem=problem
        ),
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


def _date_window_covered(
    *,
    tournament_date: Any,
    source: Mapping[str, Any],
    date_window_days: int,
) -> bool:
    center = _assessment_parse_date(tournament_date)
    if center is None:
        return False
    window_start = center - timedelta(days=max(0, int(date_window_days)))
    window_end = center + timedelta(days=max(0, int(date_window_days)))
    # The navigated/observed window is the scraper's actual coverage claim.
    # ``event_observed_window`` is merely the first/last event date and would
    # let two sparse events bracket an unobserved gap, so it must never prove
    # that the intervening period was actually observed.
    observed = source.get("observed_window") or {}
    if not isinstance(observed, Mapping):
        return False
    observed_start = _assessment_parse_date(observed.get("start"))
    observed_end = _assessment_parse_date(observed.get("end"))
    if observed_start is None or observed_end is None:
        return False
    return observed_start <= window_start and observed_end >= window_end


def _source_supports_negative_absence(
    *,
    tournament: Mapping[str, Any],
    source: Mapping[str, Any] | None,
    date_window_days: int,
) -> tuple[bool, list[str]]:
    if not source:
        return False, ["source_missing"]
    reasons: list[str] = []
    if source.get("source_trust") != "trusted":
        reasons.append("source_not_trusted")
    if source.get("source_integrity") != "complete":
        reasons.append("source_integrity_not_complete")
    if not source.get("coverage_proven"):
        reasons.append("source_coverage_not_proven")
    try:
        event_count = int(source.get("event_count") or 0)
    except (TypeError, ValueError):
        event_count = 0
    if event_count <= 0:
        reasons.append("no_other_events_in_source")
    if not _date_window_covered(
        tournament_date=tournament.get("date"),
        source=source,
        date_window_days=date_window_days,
    ):
        reasons.append("tournament_window_not_covered")
    return not reasons, reasons


def _assessment_tournament_row(
    tournament: Mapping[str, Any],
    *,
    candidates: list[dict[str, Any]],
    problem: Mapping[str, Any] | None,
    decisions: Mapping[str, Any] | None,
    associated_event: Mapping[str, Any] | None,
    source_trusted: bool,
    source_positive_evidence_usable: bool,
    source: Mapping[str, Any] | None = None,
    date_window_days: int = DEFAULT_ASSESSMENT_DATE_WINDOW_DAYS,
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
    source_supports_absence, absence_blockers = _source_supports_negative_absence(
        tournament=tournament,
        source=source,
        date_window_days=date_window_days,
    )
    # Observations with contradicting title/arena evidence stay in the report
    # but are not credible competing matches; only actionable candidates may
    # contest or resolve a proposal.
    actionable = [candidate for candidate in candidates if candidate.get("actionable")]
    if has_manual_authority:
        classification = ASSESSMENT_MANUALLY_ASSERTED
    elif associated_event is not None:
        classification = ASSESSMENT_ASSOCIATED
    elif not source_positive_evidence_usable:
        classification = ASSESSMENT_NOT_CHECKABLE
    elif not candidates:
        classification = ASSESSMENT_PRESUMED_UNSCHEDULED if source_supports_absence else ASSESSMENT_UNMATCHED
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
        # Only conflicting/observation candidates remain.  If the source is
        # complete for this tournament's window, non-actionable observations at
        # the wrong arena/age group do not block a negative finding for the
        # canonical booking; otherwise keep the row ambiguous.
        classification = (
            ASSESSMENT_PRESUMED_UNSCHEDULED
            if source_supports_absence
            else ASSESSMENT_AMBIGUOUS
        )

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
        "source_positive_evidence_usable": source_positive_evidence_usable,
        "proposal_is_binding": False,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    if classification == ASSESSMENT_PRESUMED_UNSCHEDULED:
        row["negative_evidence"] = {
            "reason": "complete_trusted_calendar_window_without_plausible_match",
            "absence_window_days": int(date_window_days),
            "source_fingerprint": (source or {}).get("calendar_fingerprint"),
            "source_event_count": (source or {}).get("event_count"),
            # The verified navigated window that passed `_date_window_covered`,
            # never the first/last-event extent it deliberately ignores.
            "observed_window": (source or {}).get("observed_window"),
        }
    elif absence_blockers:
        row["absence_evidence_blockers"] = absence_blockers
    if has_manual_authority:
        row["evidence_reason"] = "accepted_manual_authority"
    elif candidates and source_positive_evidence_usable and not source_trusted:
        row["evidence_reason"] = "event_evidence_usable"
    elif not source_trusted:
        row["evidence_reason"] = "source_coverage_unproven"
    if classification in {ASSESSMENT_AMBIGUOUS, ASSESSMENT_COMPETING_CANDIDATES}:
        row.setdefault("evidence_reasons", []).append("association_ambiguous")
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
    source_integrity = (problem or {}).get("club_source_integrity") or {}
    if not isinstance(source_integrity, Mapping):
        source_integrity = {}
    coverage_proven = (problem or {}).get("club_coverage_proven") or {}
    if not isinstance(coverage_proven, Mapping):
        coverage_proven = {}
    source_integrity_details = (problem or {}).get("club_source_integrity_details") or {}
    if not isinstance(source_integrity_details, Mapping):
        source_integrity_details = {}

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
    positive_evidence_usable_clubs: set[str] = set()
    busy_intervals = (problem or {}).get("club_busy_intervals") or {}
    if not isinstance(busy_intervals, Mapping):
        busy_intervals = {}
    for club in all_clubs:
        status = str(calendar_status.get(club) or "missing")
        fabricated_signal = fabricated_interval_signal(busy_intervals.get(club))
        trusted = status == "known" and fabricated_signal is None
        positive_evidence_usable = club_calendar_positive_evidence_usable({**(problem or {}), "club_busy_intervals": busy_intervals}, club)
        if trusted:
            trusted_clubs.add(club)
        if positive_evidence_usable:
            positive_evidence_usable_clubs.add(club)
        if trusted:
            source_trust = "trusted"
        elif status == "untrusted":
            source_trust = "untrusted"
        elif status == "source_review_required" or fabricated_signal:
            source_trust = "source_review_required"
        else:
            source_trust = "unknown"
        integrity_detail = source_integrity_details.get(club) or {}
        if not isinstance(integrity_detail, Mapping):
            integrity_detail = {}
        source_supports_negative_claim = bool(
            trusted
            and str(source_integrity.get(club) or "unknown") == "complete"
            and bool(coverage_proven.get(club, False))
        )
        sources[club] = {
            "club": club,
            "status": status,
            "source_trust": source_trust,
            "source_review_required": not trusted,
            # The deterministic per-club integrity verdict (complete/suspicious/
            # partial/failed) and whether the source structurally covered the
            # whole requested window. A source that is not coverage-proven can
            # never support a negative occupancy claim.
            "source_integrity": str(source_integrity.get(club) or "unknown"),
            # Independent verifier signal: the stored intervals still look like a
            # scraper's fabricated fallback (identical duration + literal
            # midnight) even though the status above may predate the detector.
            "fabricated_placeholder_signal": fabricated_signal,
            "source_integrity_fingerprint": integrity_detail.get("fingerprint"),
            "requested_window": {
                "start": integrity_detail.get("requested_start"),
                "end": integrity_detail.get("requested_end"),
            },
            "observed_window": {
                "start": integrity_detail.get("observed_start"),
                "end": integrity_detail.get("observed_end"),
            },
            "event_observed_window": {
                "start": integrity_detail.get("event_observed_start"),
                "end": integrity_detail.get("event_observed_end"),
            },
            "source_event_count": integrity_detail.get("event_count"),
            "coverage_proven": bool(coverage_proven.get(club, False)),
            # Absence may contribute only a derived, non-binding
            # presumed-unscheduled state when integrity and coverage prove the
            # requested tournament window was complete. It still never mutates
            # canonical cancellation or creates accepted booking authority.
            "trusted_for_negative_claim": source_supports_negative_claim,
            "event_evidence_usable": positive_evidence_usable,
            "trusted_for_positive_claim": positive_evidence_usable,
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
                # Full-window trust is required for negative claims, but a
                # partially covered source may still contribute authentic
                # observed events to positive reconciliation. Contradicting
                # title/arena evidence still blocks actionability.
                candidate["source_trusted"] = club in trusted_clubs
                candidate["source_positive_evidence_usable"] = club in positive_evidence_usable_clubs
                candidate["actionable"] = bool(
                    candidate["source_positive_evidence_usable"]
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
                source_positive_evidence_usable=club in positive_evidence_usable_clubs,
                source=sources.get(club),
                date_window_days=date_window_days,
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
        ASSESSMENT_PRESUMED_UNSCHEDULED: 0,
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
            "calendar_absence_is_non_binding_negative_evidence_only_when_source_window_is_complete",
            "presumed_unscheduled_never_cancels_or_deletes_a_tournament",
            "proposals_are_advisory_until_an_explicit_operator_confirmation",
            "source_coverage_proof_is_not_established_by_this_assessment",
        ],
    }
    assessment["assessment_fingerprint"] = stable_payload_sha256(assessment)
    return assessment
