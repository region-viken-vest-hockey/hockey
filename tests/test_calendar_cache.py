from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tournament_scheduler.models import CalendarEvent
from tournament_scheduler.utils.calendar_cache import CalendarCache


class TestCacheKeyLocationFilter:
    """_get_cache_key must produce distinct digests for different location_filter values."""

    def _make_cache(self, tmp_path):
        return CalendarCache(work_dir=str(tmp_path))

    def _key(self, cache, location_filter):
        return cache._get_cache_key(
            url="https://example.com/feed.ics",
            calendar_name="Test Arena",
            start_date=datetime(2025, 9, 1),
            end_date=datetime(2025, 12, 1),
            location_filter=location_filter,
        )

    def test_different_filters_produce_different_keys(self, tmp_path):
        cache = self._make_cache(tmp_path)
        key_none = self._key(cache, None)
        key_a = self._key(cache, "ArenaA")
        key_b = self._key(cache, "ArenaB")
        assert key_none != key_a
        assert key_none != key_b
        assert key_a != key_b

    def test_same_filter_produces_same_key(self, tmp_path):
        cache = self._make_cache(tmp_path)
        assert self._key(cache, "ArenaA") == self._key(cache, "ArenaA")

    def test_none_and_empty_string_produce_same_key(self, tmp_path):
        """None and empty string are both treated as 'no filter'."""
        cache = self._make_cache(tmp_path)
        assert self._key(cache, None) == self._key(cache, "")


class TestCalendarCacheMinutePrecision:
    def test_exact_minute_duration_round_trips_through_cache(self, tmp_path):
        cache = CalendarCache(work_dir=str(tmp_path))
        start = datetime(2026, 11, 1)
        end = datetime(2026, 12, 1)
        event = CalendarEvent(
            date="15.11.2026",
            name="JU12 11:15-12:55",
            datetime=datetime(2026, 11, 15, 11, 15),
            duration_hours=(12 + 55 / 60) - (11 + 15 / 60),
        )

        cache.set("https://example.com/feed", "Skien", start, end, [event])
        restored = cache.get("https://example.com/feed", "Skien", start, end)

        assert restored is not None
        assert restored[0].duration_minutes == 100
        assert restored[0].end_datetime == datetime(2026, 11, 15, 12, 55)


class TestCalendarCachePaths:
    def test_default_cache_dir_is_project_local(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        cache = CalendarCache()

        assert cache.cache_dir == Path(".pipeline") / "cache" / "calendars"
        assert cache.cache_dir.exists()

    def test_work_dir_places_cache_under_pipeline_root(self, tmp_path):
        work_dir = tmp_path / "custom-work"

        cache = CalendarCache(work_dir=str(work_dir))

        assert cache.cache_dir == work_dir / "cache" / "calendars"
        assert cache.cache_dir.exists()
