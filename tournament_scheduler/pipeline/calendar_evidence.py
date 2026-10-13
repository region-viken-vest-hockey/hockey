"""Calendar/source evidence inventory and coverage for the semantic audit (issue #679).

The audit must not perform fresh scraping, so this module only summarizes the
persisted scraping checkpoint and states how much calendar evidence was
actually checked against the export's manual external-conflict placements.
"""

from __future__ import annotations

from typing import Any


def calendar_evidence_coverage(
    calendar_evidence_summary: dict[str, Any] | None,
    *,
    external_conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Per-placement calendar-coverage boundary for manual external-conflict placements.

    A placement is verified only when at least one calendar source was scanned
    *and* its own host club's calendar is ``"known"`` -- the same fail-closed rule
    the planner applies (``club_calendar_status`` missing or non-``known`` means
    unknown). A global scan count is not enough: one unrelated scanned calendar
    must not cover a placement whose host calendar was blocked or never scanned
    (issue #679). Absence of evidence is never reported as a clear calendar check.
    Kept to a count and a club list so the bounded audit context stays under its
    serialized-size budget; the prompt policy explains what the fields mean.
    """
    summary = calendar_evidence_summary if isinstance(calendar_evidence_summary, dict) else {}
    try:
        sources_scanned = max(0, int(summary.get("sources_scanned") or 0))
    except (TypeError, ValueError):
        sources_scanned = 0
    club_status = summary.get("club_calendar_status")
    club_status = club_status if isinstance(club_status, dict) else {}
    unverified_rows = [
        item
        for item in external_conflicts
        if isinstance(item, dict)
        and not (sources_scanned and club_status.get(str(item.get("host_club") or "")) == "known")
    ]
    unverified_clubs = sorted({str(item.get("host_club") or "") for item in unverified_rows})
    return {"unverified_placements": len(unverified_rows), "unverified_clubs": unverified_clubs}


def summarize_scraping_checkpoint(scraping_checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    """Small source/calendar inventory for the semantic audit.

    The audit must not perform fresh scraping, but it should be able to see
    whether persisted calendar evidence was present and usable.  Keep this to
    counts/status/provenance rather than embedding thousands of raw events.
    """
    if not isinstance(scraping_checkpoint, dict):
        return {}

    sources = scraping_checkpoint.get("sources") or []
    per_source: list[dict[str, Any]] = []
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            continue
        per_source.append(
            {
                "name": source.get("name"),
                "type": source.get("type"),
                "event_count": source.get("event_count", len(source.get("events") or [])),
                "blocked": bool(source.get("blocked")),
                "block_reason": source.get("block_reason"),
                "from_cache": bool(source.get("from_cache")),
                "cache_age_hours": source.get("cache_age_hours"),
                "llm_fallback": bool(source.get("llm_fallback")),
            }
        )

    events_by_club = scraping_checkpoint.get("events_by_club") or {}
    event_counts_by_club = {
        str(club): len(events or [])
        for club, events in events_by_club.items()
        if isinstance(events_by_club, dict)
    }

    return {
        "sources_scanned": len(sources) if isinstance(sources, list) else 0,
        "blocked_sources": list(scraping_checkpoint.get("blocked") or []),
        "empty_sources": list(scraping_checkpoint.get("empty_sources") or []),
        "cached_sources": list(scraping_checkpoint.get("cached") or []),
        "club_calendar_status": dict(scraping_checkpoint.get("club_calendar_status") or {}),
        "event_counts_by_club": event_counts_by_club,
        "event_expectation_warnings": list(scraping_checkpoint.get("event_expectation_warnings") or []),
        "window": {
            "start_date": scraping_checkpoint.get("start_date"),
            "end_date": scraping_checkpoint.get("end_date"),
        },
        "per_source": per_source,
    }
