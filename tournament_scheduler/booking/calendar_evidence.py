"""Normalized calendar event access, fingerprints and source trust."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from tournament_scheduler.booking.interval_facts import _event_fingerprint_payload, event_fingerprint
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256
from tournament_scheduler.pipeline.source_integrity import fabricated_interval_signal


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
