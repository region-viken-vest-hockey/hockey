"""Recovery event injector and Stage 2 checkpoint normalizer.

Browser-capable harnesses may inject recovered events into the shared cache,
but Python owns the resulting Stage 2 readiness decision.  The normalizer uses
the same source-readiness policy as ``stage2_scraping`` rather than inventing a
second blocked-source rule.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .cache_manager import ScrapedDataCache
from .scraper_event_helpers import _group_events_by_club, _scraped_date_range
from .source_readiness import classify_unresolved_sources, source_names
from .state import PipelineState, StageName, StageStatus


def inject_recovered_events(
    source_name: str,
    events: list[dict[str, Any]],
    work_dir: str = ".pipeline",
) -> None:
    """Patch the ScrapedDataCache entry for *source_name* with recovered events."""
    cache = ScrapedDataCache(work_dir=work_dir)
    data = cache.read()
    now_iso = datetime.now(timezone.utc).isoformat()

    sources: dict[str, Any] = data.get("sources", {})
    existing = sources.get(source_name, {})
    sources[source_name] = {
        **existing,
        "name": source_name,
        "scrape_timestamp": now_iso,
        "event_count": len(events),
        "blocked": False,
        "events": events,
    }
    data["sources"] = sources

    meta: dict[str, Any] = data.get("_meta", {})
    meta["updated_at"] = now_iso
    data["_meta"] = meta
    cache.write(data)


def normalize_stage2_checkpoint(work_dir: str = ".pipeline") -> dict[str, Any]:
    """Rewrite Stage 2 from recovered cache data using canonical readiness policy."""
    state = PipelineState(work_dir)
    checkpoint_path = state.checkpoint_path(StageName.SCRAPING)
    if not checkpoint_path.exists():
        raise FileNotFoundError(f"Stage 2 checkpoint not found: {checkpoint_path}")

    envelope = state.read_envelope(StageName.SCRAPING)
    data = envelope.get("data", envelope) or {}
    sources = list(data.get("sources", []))
    cache = ScrapedDataCache(work_dir=work_dir).read()
    cached_sources: dict[str, Any] = cache.get("sources", {})

    recovered_sources: list[str] = []
    normalized_sources: list[dict[str, Any]] = []

    for source in sources:
        merged = dict(source)
        name = merged.get("name", "")
        cache_entry = cached_sources.get(name, {})
        cached_events = cache_entry.get("events", []) or []

        if cached_events:
            original_event_count = int(merged.get("event_count", 0) or 0)
            merged["events"] = cached_events
            merged["event_count"] = int(cache_entry.get("event_count", len(cached_events)) or len(cached_events))
            merged["blocked"] = False
            merged.pop("scraper_error", None)
            merged["block_reason"] = None
            if original_event_count == 0 or source.get("blocked"):
                recovered_sources.append(name)
        else:
            merged["event_count"] = len(merged.get("events", []) or [])

        normalized_sources.append(merged)

    data["sources"] = normalized_sources
    data["events_by_club"] = _group_events_by_club(normalized_sources)

    unresolved = [source for source in normalized_sources if source.get("blocked")]
    readiness = classify_unresolved_sources(unresolved)
    blocking = readiness["blocking"]
    temporary = readiness["temporary"]
    assert isinstance(blocking, list)
    assert isinstance(temporary, list)

    data["blocked"] = source_names(unresolved)
    data["blocking_sources"] = source_names(blocking)
    data["temporarily_unresolved_sources"] = source_names(temporary)
    data["operator_allowed_missing_sources"] = []
    data["planning_ready"] = bool(readiness["planning_ready"])

    start_date, end_date = _scraped_date_range(normalized_sources)
    if start_date is not None:
        data["start_date"] = start_date
    if end_date is not None:
        data["end_date"] = end_date

    data["checkpoint_path"] = str(checkpoint_path)
    if blocking:
        data["warning"] = f"Stage 2 fortsatt blokkert: {', '.join(source_names(blocking))}"
        status = StageStatus.FAILED
    elif temporary:
        data["warning"] = (
            "Stage 2 har fortsatt midlertidig uløste BookUp-kilder: "
            f"{', '.join(source_names(temporary))}. Python tillater planlegging, men kildene forblir recovery-targets."
        )
        status = StageStatus.DONE
    else:
        data.pop("warning", None)
        status = StageStatus.DONE

    state.write_stage(StageName.SCRAPING, data, status=status)

    return {
        "status": status.value,
        "work_dir": work_dir,
        "checkpoint_path": str(checkpoint_path),
        "source_count": len(normalized_sources),
        "event_count": sum(int(source.get("event_count", 0) or 0) for source in normalized_sources),
        "recovered_sources": recovered_sources,
        "unresolved_sources": source_names(unresolved),
        "blocked_sources": source_names(blocking),
        "temporarily_unresolved_sources": source_names(temporary),
        "planning_ready": bool(readiness["planning_ready"]),
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
    }
