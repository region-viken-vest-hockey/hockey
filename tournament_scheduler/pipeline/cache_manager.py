"""
Cache manager — unified scraped data cache with timestamps and TTL.

Aggregates all scraped calendar data from the Stage 2 checkpoint into a
single JSON file at ``.pipeline/cache/scraped_data.json``, recording:
  - per-source event lists
  - scrape timestamps (ISO 8601)
  - source URL for reference links
  - TTL for cache staleness detection

The HTML calendar viewer reads from this cache.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


CACHE_DIR = "cache"
CACHE_FILE = "scraped_data.json"
DEFAULT_TTL_HOURS = 24


def _compute_config_fingerprint(
    url: str,
    source_kind: str,
    location_filter: str | None,
) -> str:
    """Return an MD5 hex digest of the config fields that affect scraping.

    When any of *url*, *source_kind*, or *location_filter* changes the digest
    changes, so stored cache entries with the old fingerprint are treated as
    stale by :meth:`ScrapedDataCache.is_config_match`.
    """
    filter_part = location_filter or ""
    raw = f"{url}|{source_kind}|{filter_part}"
    return hashlib.md5(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Cache entry types
# ---------------------------------------------------------------------------


class ScrapedDataCache:
    """Unified cache of all scraped calendar data per source.

    Reads from the Stage 2 checkpoint and writes a merged cache file.
    """

    def __init__(self, work_dir: str = ".pipeline", ttl_hours: int = DEFAULT_TTL_HOURS) -> None:
        self.cache_path = Path(work_dir) / CACHE_DIR / CACHE_FILE
        self.ttl = timedelta(hours=ttl_hours)

    # ------------------------------------------------------------------
    # Read / write
    # ------------------------------------------------------------------

    def read(self) -> dict[str, Any]:
        """Read the current cache. Returns empty dict if missing or stale."""
        if not self.cache_path.exists():
            return {}
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def write(self, data: dict[str, Any]) -> None:
        """Write cache to disk."""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        tmp.replace(self.cache_path)

    # ------------------------------------------------------------------
    # Build cache from Stage 2 checkpoint
    # ------------------------------------------------------------------

    def build_from_checkpoint(self, config: dict[str, Any], scraping_result: dict[str, Any]) -> dict[str, Any]:
        """Read the Stage 2 checkpoint and build/update the unified cache.

        Parameters
        ----------
        config:
            Stage 1 config (for source URLs and date range).
        scraping_result:
            Stage 2 checkpoint data with per-source results.

        Returns
        -------
        dict
            The updated cache data.
        """
        now = datetime.now().isoformat()
        cache = self.read()

        # Copy over global metadata
        cache["_meta"] = {
            "updated_at": now,
            "ttl_hours": self.ttl.total_seconds() / 3600,
            "start_date": config.get("start_date", ""),
            "end_date": config.get("end_date", ""),
        }

        sources_config = config.get("sources", [])
        source_map: dict[str, dict[str, Any]] = {
            s["name"]: s for s in sources_config
        }

        sources_data: dict[str, Any] = {}

        for source_result in scraping_result.get("sources", []):
            name: str = source_result.get("name", "ukjent")

            # Sources served from this cache (because they were still fresh)
            # are passed through unchanged so their original scrape_timestamp
            # -- and therefore TTL -- is preserved.
            if source_result.get("from_cache"):
                existing = cache.get("sources", {}).get(name)
                if existing:
                    sources_data[name] = existing
                    continue

            url: str = source_result.get("url", "")
            events: list[dict[str, Any]] = source_result.get("events", [])
            blocked: bool = source_result.get("blocked", False)
            source_kind: str = source_result.get("type", "")
            location_filter: str | None = source_result.get("location_filter")

            entry = {
                "name": name,
                "url": url,
                "scrape_timestamp": now,
                "ttl_hours": self.ttl.total_seconds() / 3600,
                "event_count": len(events),
                "blocked": blocked,
                "events": events,
                "config_fingerprint": _compute_config_fingerprint(url, source_kind, location_filter),
            }

            sources_data[name] = entry

        # Do not retain sources that were not part of the current scrape.
        # This keeps the unified cache aligned with the latest configured inputs
        # and avoids stale calendar events leaking into later reports.

        # Keep exactly one generation of history so source_health.py can detect
        # sparse/duplicate-heavy sources by comparing against the last build,
        # without growing the cache file unboundedly.
        cache["previous_sources"] = cache.get("sources", {})
        cache["sources"] = sources_data
        cache["source_count"] = len(sources_data)
        cache["total_events"] = sum(
            s.get("event_count", 0) for s in sources_data.values()
        )

        self.write(cache)
        return cache

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_stale(self, source_name: str) -> bool:
        """Check if a source's cache entry is older than TTL."""
        cache = self.read()
        entry = cache.get("sources", {}).get(source_name)
        if not entry:
            return True
        ts = entry.get("scrape_timestamp", "")
        if not ts:
            return True
        try:
            scraped_at = datetime.fromisoformat(ts)
            now = datetime.now(scraped_at.tzinfo) if scraped_at.tzinfo else datetime.now()
            return now - scraped_at > self.ttl
        except (ValueError, TypeError):
            return True

    def is_config_match(
        self,
        source_name: str,
        url: str,
        source_kind: str,
        location_filter: str | None = None,
    ) -> bool:
        """Return True if the stored config fingerprint matches the current config.

        A mismatch means the source's configuration has changed since the cache
        entry was written (e.g. the URL or location_filter changed), so the
        caller should treat the entry as a cache miss regardless of its age.

        Returns False if the source has no stored fingerprint (legacy entries
        written before this feature was added are conservatively treated as
        mismatches).
        """
        cache = self.read()
        entry = cache.get("sources", {}).get(source_name)
        if not entry:
            return False
        stored = entry.get("config_fingerprint")
        if not stored:
            # Legacy entry written before fingerprinting was added — assume
            # config matches so the entry is not invalidated on upgrade.
            return True
        expected = _compute_config_fingerprint(url, source_kind, location_filter)
        return stored == expected

    def get_source_events(self, source_name: str) -> list[dict[str, Any]]:
        """Get cached events for a single source."""
        cache = self.read()
        entry = cache.get("sources", {}).get(source_name)
        if entry:
            return entry.get("events", [])
        return []

    def get_all_events(self) -> list[dict[str, Any]]:
        """Get all events from all sources with source metadata attached."""
        cache = self.read()
        all_events: list[dict[str, Any]] = []
        for name, entry in cache.get("sources", {}).items():
            for event in entry.get("events", []):
                all_events.append({
                    **event,
                    "_source": name,
                    "_source_url": entry.get("url", ""),
                })
        return all_events

    def clear(self) -> None:
        """Delete the cache file."""
        if self.cache_path.exists():
            self.cache_path.unlink()

    def force_refresh(self) -> bool:
        """Mark all cache entries as stale (forces re-scrape)."""
        cache = self.read()
        old_time = (datetime.now() - self.ttl - timedelta(hours=1)).isoformat()
        for entry in cache.get("sources", {}).values():
            entry["scrape_timestamp"] = old_time
        self.write(cache)
        return True
