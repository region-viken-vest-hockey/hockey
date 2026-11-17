"""Assessment source coverage and trust, separate from event matching."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.calendar_evidence import (
    club_calendar_fingerprint,
    club_calendar_positive_evidence_usable,
)
from tournament_scheduler.pipeline.source_integrity import fabricated_interval_signal


def assessment_sources(
    *,
    problem: Mapping[str, Any] | None,
    tournaments: list[Mapping[str, Any]],
    events: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], set[str], set[str]]:
    """Project source trust and coverage without inferring event identity."""
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
    return sources, trusted_clubs, positive_evidence_usable_clubs
