"""Calendar observation records, freshness and effective observation status."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.booking_facts import tournament_booking_facts

from tournament_scheduler.booking.booking_vocabulary import (
    _ABSENCE_ONLY_NEGATIVE_REASONS,
    BOOKING_AMBIGUOUS,
    BOOKING_CONFIRMED_BOOKED,
    BOOKING_CONFIRMED_NOT_BOOKED,
    BOOKING_UNKNOWN,
    STALE,
)

from tournament_scheduler.booking.calendar_evidence import club_calendar_fingerprint, find_event

from tournament_scheduler.booking.interval_facts import event_fingerprint



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
    event_fp = str(record.get("event_fingerprint") or "")
    if club and str(record.get("calendar_fingerprint") or "") != club_calendar_fingerprint(problem, club):
        # The club-wide calendar fingerprint is source-version/provenance
        # metadata.  It covers every event in the club feed, so an unrelated
        # practice/game changing must not stale a tournament-level observation
        # whose exact matched event is still present unchanged.  Missing
        # event-level evidence still fails closed below by retaining the stale
        # source-change reason for records that had no resolvable event.
        if not event_fp or find_event(problem, event_fp) is None:
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
