"""Tests for tournament_scheduler.pipeline.stage2_scraping."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tournament_scheduler.pipeline.cache_manager import ScrapedDataCache
from tournament_scheduler.pipeline.scraper_forumbooking import _parse_forumbooking_schedule
from tournament_scheduler.pipeline.stage2_scraping import (
    SOURCE_GOOGLE,
    SOURCE_ICAL,
    SOURCE_OUTLOOK,
    Stage2Error,
    _events_to_dicts,
    _scrape_source,
    run,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.models import CalendarEvent
from tournament_scheduler.utils.calendar_cache import CalendarCache


def _make_event(name: str = "Booking") -> CalendarEvent:
    return CalendarEvent(
        date="01.01.2025",
        name=name,
        datetime=datetime(2025, 1, 1, 10, 0),
        duration_hours=2.0,
    )


def _make_config_with_sources(sources):
    return {
        "start_date": "2025-09-01",
        "end_date": "2025-12-01",
        "teams": [{"club": "Kongsberg", "label": "Kongsberg U10", "age_group": "U10"}],
        "sources": sources,
    }


class TestRunStage2:
    def test_empty_sources_produces_done_checkpoint(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        cfg = {
            "start_date": "2025-09-01",
            "end_date": "2025-12-01",
            "teams": [{"club": "Kongsberg", "label": "Kongsberg U10", "age_group": "U10"}],
        }
        result = run(
            cfg, state,
            datetime(2025, 9, 1), datetime(2025, 12, 1),
            strict=False,
        )
        assert state.is_done(StageName.SCRAPING)
        assert result["sources"] == []

    def test_empty_roster_skips_scraping_even_when_sources_are_configured(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        cfg = {
            "start_date": "2025-09-01",
            "end_date": "2025-12-01",
            "teams": [],
            "sources": [{"name": "Teamup", "type": SOURCE_ICAL, "url": "https://example.com/ical"}],
        }

        with patch("tournament_scheduler.pipeline.stage2_scraping._run_ical_scraper") as scraper:
            result = run(cfg, state, datetime(2025, 9, 1), datetime(2025, 12, 1))

        assert state.is_done(StageName.SCRAPING)
        assert result["skipped"] is True
        assert result["sources"] == []
        scraper.assert_not_called()

    def test_ical_source_has_no_llm_confidence(self, tmp_path):
        """iCal/Google sources do not get LLM confidence logged (informational)."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "Teamup", "type": SOURCE_ICAL, "url": "https://example.com/ical"},
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_ical_scraper",
            return_value=[_make_event()],
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
            )

        # iCal sources should not have LLM confidence key
        src = result["sources"][0]
        assert "llm_confidence" not in src
        assert src["event_count"] == 1

    def test_zero_events_records_empty_calendar(self, tmp_path):
        """A direct deterministic source returning zero events is tracked as empty, not blocked."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "HallX", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            return_value=([], ""),
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=True,
            )

        assert state.checkpoint_path(StageName.SCRAPING).exists()
        assert state.is_done(StageName.SCRAPING)
        envelope = state.read_envelope(StageName.SCRAPING)
        assert envelope["status"] == "done"
        assert envelope["data"]["blocked"] == []
        assert envelope["data"]["empty_sources"] == ["HallX"]
        assert result["sources"][0]["empty_calendar"] is True
        assert result["sources"][0]["blocked"] is False

    def test_zero_events_strict_false_does_not_raise(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "HallY", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            return_value=([], ""),
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )
        assert result.get("blocked", []) == []
        assert result.get("empty_sources", []) == ["HallY"]
        assert result["sources"][0]["empty_calendar"] is True

    def test_allow_missing_sources_keeps_partial_results(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {
                "name": "Sandefjord Penguins",
                "type": SOURCE_OUTLOOK,
                "url": "https://www.bookup.no/Utleie/#Bug%C3%A5rdshallen___/view:item/id:4497/part:/place:3907:SANDEFJORD/q:sandefjord/r:31/mod:book",
            },
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_bookup_scraper",
            return_value=([], ""),
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=True,
                allow_missing_sources=True,
            )

        assert state.is_done(StageName.SCRAPING)
        assert not state.is_failed(StageName.SCRAPING)
        assert result["blocked"] == []
        assert result["empty_sources"] == ["Sandefjord Penguins"]
        assert "tomme kilder" in result["warning"].lower()
        src = result["sources"][0]
        assert src.get("empty_calendar") is True
        assert "BOOKUP_EMAIL" not in src
        assert "BOOKUP_PASSWORD" not in src

    def test_outlook_source_with_events_passes(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])
        events = [_make_event("Hockey practice")]

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            return_value=(events, ""),
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
            )

        assert state.is_done(StageName.SCRAPING)
        src = result["sources"][0]
        assert src["event_count"] == 1
        assert src["blocked"] is False

    def test_html_source_dispatches_to_browser_scraper(self, tmp_path):
        """'html' source type dispatches to the browser scraper (same as 'outlook')."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "OtherHall", "type": "html", "url": "https://example.com/kalender/"},
        ])
        events = [_make_event("Booking")]

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            return_value=(events, ""),
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
            )

        assert state.is_done(StageName.SCRAPING)
        src = result["sources"][0]
        assert src["event_count"] == 1
        assert src["blocked"] is False
        assert src["type"] == "html"

    def test_ical_source_skipped_by_browser_scraper(self, tmp_path):
        """iCal sources do NOT use the browser scraper."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "Teamup", "type": SOURCE_ICAL, "url": "https://ics.teamup.com/feed/key"},
        ])

        with (
            patch(
                "tournament_scheduler.pipeline.stage2_scraping._run_ical_scraper",
                return_value=[_make_event("iCal event")],
            ),
            patch(
                "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            ) as mock_browser,
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
            )

        mock_browser.assert_not_called()
        src = result["sources"][0]
        assert src["event_count"] == 1
        assert src["events"][0]["name"] == "iCal event"


class TestParallelExecution:
    """Tests for the ThreadPoolExecutor-based parallel dispatch."""

    def test_multiple_sources_all_collected(self, tmp_path):
        """All results from multiple sources are collected regardless of order."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_OUTLOOK, "url": "https://a.example.com"},
            {"name": "Skien", "type": SOURCE_ICAL, "url": "https://b.example.com"},
            {"name": "Ringerike", "type": SOURCE_ICAL, "url": "https://c.example.com"},
            {"name": "Jar", "type": SOURCE_OUTLOOK, "url": "https://d.example.com"},
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            return_value=([_make_event("Outlook")], "<html>"),
        ), patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_ical_scraper",
            return_value=[_make_event("iCal")],
        ), patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_forumbooking_scraper",
            return_value=([_make_event("Forumbooking")], "<html>"),
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
            )

        assert len(result["sources"]) == 4
        names = {s["name"] for s in result["sources"]}
        assert names == {"Kongsberg", "Skien", "Ringerike", "Jar"}
        assert sum(s["event_count"] for s in result["sources"]) == 4
        assert state.is_done(StageName.SCRAPING)

    def test_crashed_scraper_does_not_block_others(self, tmp_path):
        """A scraper that raises is caught and other sources still succeed."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "GoodSource", "type": SOURCE_ICAL, "url": "https://good.example.com"},
            {"name": "Crashy", "type": SOURCE_OUTLOOK, "url": "https://crash.example.com"},
            {"name": "AlsoGood", "type": SOURCE_ICAL, "url": "https://also.example.com"},
        ])

        original = __import__(
            "tournament_scheduler.pipeline.stage2_scraping", fromlist=["_scrape_source"]
        )._scrape_source

        call_count = {"count": 0}

        def crashing_scraper(source_cfg, *, start_date, end_date, calendar_cache=None):
            call_count["count"] += 1
            if source_cfg.get("name") == "Crashy":
                raise RuntimeError("simulated crash")
            return original(source_cfg, start_date=start_date, end_date=end_date, calendar_cache=calendar_cache)

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_ical_scraper",
            return_value=[_make_event("iCal event")],
        ), patch(
            "tournament_scheduler.pipeline.stage2_scraping._scrape_source",
            side_effect=crashing_scraper,
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        assert len(result["sources"]) == 3
        blocked_names = result.get("blocked", [])
        assert "Crashy" in blocked_names
        # GoodSource and AlsoGood should have 1 event each
        good_events = sum(
            s["event_count"]
            for s in result["sources"]
            if s["name"] != "Crashy"
        )
        assert good_events == 2

    def test_sources_run_in_different_threads(self, tmp_path):
        """Sources dispatched via ThreadPoolExecutor use multiple OS threads."""
        import threading

        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": f"Source{i}", "type": SOURCE_ICAL, "url": f"https://s{i}.example.com"}
            for i in range(5)
        ])

        thread_ids: set[int] = set()
        call_state = {"count": 0}
        start_barrier = threading.Barrier(2)

        def record_thread(*args, **kwargs):
            call_state["count"] += 1
            thread_ids.add(threading.get_ident())
            if call_state["count"] <= 2:
                try:
                    start_barrier.wait(timeout=5)
                except threading.BrokenBarrierError as exc:
                    raise AssertionError("Expected at least two worker threads") from exc
            return [_make_event("ev")]

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_ical_scraper",
            side_effect=record_thread,
        ):
            run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
            )

        # With 5 sources and max_workers=4, at least 2 different threads must be used
        assert len(thread_ids) >= 2, f"Expected >=2 threads, got {len(thread_ids)}"


class TestUnifiedCache:
    """Stage 2 should reuse fresh unified-cache entries instead of re-scraping."""

    def _seed_cache(self, work_dir, *, start_date, end_date, source_name,
                     events, scrape_timestamp=None, blocked=False):
        cache = ScrapedDataCache(work_dir=work_dir)
        ts = scrape_timestamp or datetime.now().isoformat()
        cache.write({
            "_meta": {
                "updated_at": ts,
                "ttl_hours": 6,
                "start_date": start_date,
                "end_date": end_date,
            },
            "sources": {
                source_name: {
                    "name": source_name,
                    "url": "https://example.com",
                    "scrape_timestamp": ts,
                    "ttl_hours": 6,
                    "event_count": len(events),
                    "blocked": blocked,
                    "events": events,
                },
            },
            "source_count": 1,
            "total_events": len(events),
        })

    def test_fresh_cache_skips_scraping(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        state = PipelineState(work_dir)
        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])
        cached_events = _events_to_dicts([_make_event("Cached practice")])
        self._seed_cache(
            work_dir,
            start_date=cfg["start_date"], end_date=cfg["end_date"],
            source_name="Kongsberg", events=cached_events,
        )

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
        ) as mock_scraper:
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        mock_scraper.assert_not_called()
        assert result["cached"] == ["Kongsberg"]
        src = result["sources"][0]
        assert src["from_cache"] is True
        assert src["event_count"] == 1
        assert src["events"][0]["name"] == "Cached practice"

    def test_blocked_fresh_cache_is_rescraped_and_not_reused(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        state = PipelineState(work_dir)
        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])
        cached_events = _events_to_dicts([_make_event("Cached practice")])
        self._seed_cache(
            work_dir,
            start_date=cfg["start_date"], end_date=cfg["end_date"],
            source_name="Kongsberg", events=cached_events, blocked=True,
        )

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            return_value=([_make_event("Fresh practice")], ""),
        ) as mock_scraper:
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        mock_scraper.assert_called_once()
        assert result["cached"] == []
        assert "from_cache" not in result["sources"][0]
        assert result["sources"][0]["events"][0]["name"] == "Fresh practice"
        cache = ScrapedDataCache(work_dir=work_dir).read()
        assert cache["sources"]["Kongsberg"]["blocked"] is False
        assert cache["sources"]["Kongsberg"]["events"][0]["name"] == "Fresh practice"

    def test_blocked_scrape_clears_previous_events_and_refreshes_timestamp(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        state = PipelineState(work_dir)
        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])
        cached_events = _events_to_dicts([_make_event("Cached practice")])
        old_ts = (datetime.now() - timedelta(hours=12)).isoformat()
        self._seed_cache(
            work_dir,
            start_date=cfg["start_date"], end_date=cfg["end_date"],
            source_name="Kongsberg", events=cached_events,
            scrape_timestamp=old_ts,
            blocked=True,
        )

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            return_value=([], ""),
        ):
            run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
                force_refresh=True,
            )

        cache = ScrapedDataCache(work_dir=work_dir).read()
        refreshed_ts = cache["sources"]["Kongsberg"]["scrape_timestamp"]
        assert datetime.fromisoformat(refreshed_ts) > datetime.fromisoformat(old_ts)
        assert cache["sources"]["Kongsberg"]["blocked"] is False
        assert cache["sources"]["Kongsberg"]["events"] == []

    def test_removed_source_is_pruned_from_unified_cache(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        state = PipelineState(work_dir)
        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])
        self._seed_cache(
            work_dir,
            start_date=cfg["start_date"], end_date=cfg["end_date"],
            source_name="Kongsberg", events=_events_to_dicts([_make_event("Current")]),
        )
        cache = ScrapedDataCache(work_dir=work_dir)
        cache.write({
            **cache.read(),
            "sources": {
                "Kongsberg": cache.read()["sources"]["Kongsberg"],
                "Skien": {
                    "name": "Skien",
                    "url": "https://example.com/skien",
                    "scrape_timestamp": datetime.now().isoformat(),
                    "ttl_hours": 6,
                    "event_count": 1,
                    "blocked": False,
                    "events": _events_to_dicts([_make_event("Stale Skien")]),
                },
            },
        })

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
        ) as mock_scraper:
            run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        mock_scraper.assert_not_called()
        cache_after = ScrapedDataCache(work_dir=work_dir).read()
        assert set(cache_after["sources"].keys()) == {"Kongsberg"}

    def test_fresh_z_suffixed_timestamp_skips_scraping(self, tmp_path):
        """Cache entries written with a 'Z'-suffixed (tz-aware) timestamp,
        as the extension's ScraperAgent does, must still be recognized as
        fresh by is_stale()."""
        work_dir = tmp_path / "pipeline"
        state = PipelineState(work_dir)
        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])
        cached_events = _events_to_dicts([_make_event("Cached practice")])
        fresh_z = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        self._seed_cache(
            work_dir,
            start_date=cfg["start_date"], end_date=cfg["end_date"],
            source_name="Kongsberg", events=cached_events,
            scrape_timestamp=fresh_z,
        )

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
        ) as mock_scraper:
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        mock_scraper.assert_not_called()
        assert result["cached"] == ["Kongsberg"]

    def test_stale_cache_triggers_rescrape(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        state = PipelineState(work_dir)
        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])
        # Must exceed cache_manager.DEFAULT_TTL_HOURS (24h) — is_stale() checks the
        # cache instance's TTL, not the per-entry ttl_hours field seeded by _seed_cache.
        old_ts = (datetime.now() - timedelta(hours=30)).isoformat()
        self._seed_cache(
            work_dir,
            start_date=cfg["start_date"], end_date=cfg["end_date"],
            source_name="Kongsberg", events=_events_to_dicts([_make_event("Old event")]),
            scrape_timestamp=old_ts,
        )

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            return_value=([_make_event("Fresh event")], ""),
        ) as mock_scraper:
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        mock_scraper.assert_called_once()
        assert result["cached"] == []
        src = result["sources"][0]
        assert "from_cache" not in src
        assert src["events"][0]["name"] == "Fresh event"

    def test_force_refresh_bypasses_fresh_cache(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        state = PipelineState(work_dir)
        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])
        self._seed_cache(
            work_dir,
            start_date=cfg["start_date"], end_date=cfg["end_date"],
            source_name="Kongsberg", events=_events_to_dicts([_make_event("Cached event")]),
        )

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            return_value=([_make_event("Fresh event")], ""),
        ) as mock_scraper:
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False, force_refresh=True,
            )

        mock_scraper.assert_called_once()
        assert result["cached"] == []
        assert result["sources"][0]["events"][0]["name"] == "Fresh event"

    def test_date_range_change_invalidates_cache(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        state = PipelineState(work_dir)
        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])
        # Cache was built for a different season
        self._seed_cache(
            work_dir,
            start_date="2024-09-01", end_date="2024-12-01",
            source_name="Kongsberg", events=_events_to_dicts([_make_event("Old season")]),
        )

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            return_value=([_make_event("New season")], ""),
        ) as mock_scraper:
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        mock_scraper.assert_called_once()
        assert result["cached"] == []
        assert result["sources"][0]["events"][0]["name"] == "New season"

    def test_fresh_scrape_persisted_to_cache(self, tmp_path):
        work_dir = tmp_path / "pipeline"
        state = PipelineState(work_dir)
        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            return_value=([_make_event("Practice")], ""),
        ):
            run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        cache = ScrapedDataCache(work_dir=work_dir).read()
        assert cache["sources"]["Kongsberg"]["event_count"] == 1
        assert cache["sources"]["Kongsberg"]["events"][0]["name"] == "Practice"

    def test_config_fingerprint_stored_on_fresh_scrape(self, tmp_path):
        """A fresh scrape must write a config_fingerprint into the cache entry."""
        work_dir = tmp_path / "pipeline"
        state = PipelineState(work_dir)
        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            return_value=([_make_event("Practice")], ""),
        ):
            run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        cache = ScrapedDataCache(work_dir=work_dir).read()
        assert "config_fingerprint" in cache["sources"]["Kongsberg"], (
            "config_fingerprint must be stored in the unified cache entry after a scrape"
        )

    def test_fingerprint_mismatch_triggers_rescrape(self, tmp_path):
        """A cached entry whose config_fingerprint does not match current config
        must be discarded and the source re-scraped."""
        from tournament_scheduler.pipeline.cache_manager import _compute_config_fingerprint

        work_dir = tmp_path / "pipeline"
        state = PipelineState(work_dir)
        # Source uses SOURCE_OUTLOOK, url "https://example.com"
        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])
        cached_events = _events_to_dicts([_make_event("Old cached event")])
        # Seed a cache entry with a fingerprint computed for a different URL
        stale_fingerprint = _compute_config_fingerprint(
            "https://old-url.com", SOURCE_OUTLOOK, None
        )
        sdc = ScrapedDataCache(work_dir=work_dir)
        ts = datetime.now().isoformat()
        sdc.write({
            "_meta": {
                "updated_at": ts,
                "ttl_hours": 6,
                "start_date": cfg["start_date"],
                "end_date": cfg["end_date"],
            },
            "sources": {
                "Kongsberg": {
                    "name": "Kongsberg",
                    "url": "https://old-url.com",
                    "scrape_timestamp": ts,
                    "ttl_hours": 6,
                    "event_count": len(cached_events),
                    "blocked": False,
                    "events": cached_events,
                    "config_fingerprint": stale_fingerprint,
                },
            },
            "source_count": 1,
            "total_events": len(cached_events),
        })

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            return_value=([_make_event("Fresh event")], ""),
        ) as mock_scraper:
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        mock_scraper.assert_called_once()
        assert result["cached"] == [], "fingerprint mismatch must not serve from cache"
        assert result["sources"][0]["events"][0]["name"] == "Fresh event"


class TestEventsToDict:
    def test_serialises_correctly(self):
        events = [_make_event("Practice")]
        dicts = _events_to_dicts(events)
        assert len(dicts) == 1
        assert dicts[0]["name"] == "Practice"
        assert dicts[0]["duration_hours"] == 2.0
        assert "datetime" in dicts[0]


class TestCheckpointDateRangeFields:
    """Stage 2 checkpoint must expose start_date, end_date, and per-source fields."""

    def test_checkpoint_has_start_and_end_date(self, tmp_path):
        """start_date and end_date must appear at the top level of the checkpoint."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "HallA", "type": SOURCE_ICAL, "url": "https://example.com/cal"},
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_ical_scraper",
            return_value=[_make_event("Booking")],
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        assert result["start_date"] == "2025-09-01", (
            f"Expected start_date='2025-09-01', got {result.get('start_date')!r}"
        )
        assert result["end_date"] == "2025-12-01", (
            f"Expected end_date='2025-12-01', got {result.get('end_date')!r}"
        )

    def test_checkpoint_sources_have_event_count_and_blocked(self, tmp_path):
        """Each source entry must include event_count and blocked fields."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "HallA", "type": SOURCE_ICAL, "url": "https://example.com/cal"},
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_ical_scraper",
            return_value=[_make_event("Booking"), _make_event("Cup")],
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        src = result["sources"][0]
        assert "event_count" in src, "Source entry must include event_count"
        assert src["event_count"] == 2, f"Expected event_count=2, got {src['event_count']}"
        assert "blocked" in src, "Source entry must include blocked"
        assert src["blocked"] is False, "Non-blocked source must have blocked=False"

    def test_start_end_date_preserved_with_no_sources(self, tmp_path):
        """Date range fields must appear in the checkpoint even when sources list is empty."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = {"start_date": "2026-01-01", "end_date": "2026-06-30", "teams": []}
        result = run(
            cfg, state,
            datetime(2026, 1, 1), datetime(2026, 6, 30),
            strict=False,
        )
        assert result["start_date"] == "2026-01-01"
        assert result["end_date"] == "2026-06-30"

    def test_sparse_source_gets_event_expectation_warning(self, tmp_path):
        """Sources with suspiciously few events get expectation metadata and a warning."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "Ringerike", "type": SOURCE_ICAL, "url": "https://example.com/cal"},
        ])
        cfg["age_groups"] = ["U7", "U8", "U9", "U10"]

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_ical_scraper",
            return_value=[_make_event("Sparse booking")],
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        src = result["sources"][0]
        assert src["event_expectation"]["status"] == "low"
        assert src["event_expectation"]["expected_min_events"] > src["event_count"]
        assert result["event_expectation_warnings"][0]["name"] == "Ringerike"
        assert "forventet minst" in result["event_expectation_warnings"][0]["message"]

    def test_sufficient_source_has_no_event_expectation_warning(self, tmp_path):
        """Sources at or above the expectation threshold do not get sparse warnings."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_ICAL, "url": "https://example.com/cal"},
        ])
        cfg["age_groups"] = ["U7", "U8", "U9", "U10"]
        events = [_make_event(f"Booking {idx}") for idx in range(8)]

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_ical_scraper",
            return_value=events,
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        src = result["sources"][0]
        assert src["event_expectation"]["status"] == "ok"
        assert result["event_expectation_warnings"] == []


class TestStage2ExpectationSummaries:
    """Status/checkpoint summary output for sparse-source expectation warnings."""

    def test_status_text_mentions_sparse_event_expectation_warnings(self, tmp_path):
        from tournament_scheduler.cli.reporting import _build_status_text

        state = PipelineState(tmp_path / "pipeline")
        state.write_stage(
            StageName.SCRAPING,
            {
                "sources": [],
                "blocked": [],
                "cached": [],
                "event_expectation_warnings": [
                    {
                        "name": "Tønsberg",
                        "event_count": 2,
                        "expected_min_events": 6,
                        "message": "Tønsberg: 2 hendelser funnet, forventet minst ca. 6 for perioden.",
                    }
                ],
            },
            status=StageStatus.DONE,
        )

        text = _build_status_text(tmp_path / "pipeline")

        assert "Mistenkelig få kalenderhendelser" in text
        assert "Tønsberg: 2 hendelser funnet" in text

    def test_checkpoint_printer_summarizes_sparse_event_expectation_warnings(self, tmp_path, capsys):
        from tournament_scheduler.cli.checkpoint_printer import print_checkpoint

        state = PipelineState(tmp_path / "pipeline")
        state.write_stage(
            StageName.SCRAPING,
            {
                "sources": [],
                "blocked": [],
                "cached": [],
                "event_expectation_warnings": [
                    {"name": "Skien", "event_count": 4, "expected_min_events": 6}
                ],
            },
            status=StageStatus.DONE,
        )

        print_checkpoint("stage2_scraping", tmp_path / "pipeline")
        output = capsys.readouterr().out

        assert "event_expectation_warnings" in output
        assert "Skien" in output


class TestCheckpointPreservationOnResume:
    """Regression tests for the RUNNING marker overwriting existing checkpoint data."""

    def test_interrupted_run_preserves_existing_data_on_resume(self, tmp_path):
        """When a prior completed checkpoint exists and a new run is interrupted
        mid-scrape, the checkpoint must still contain the existing data — not an
        empty dict — and must not be left with status=running after a successful
        re-run from cache."""
        work_dir = tmp_path / "pipeline"
        state = PipelineState(work_dir)

        # Simulate a prior completed scrape checkpoint
        existing_data = {
            "sources": [{"name": "Kongsberg", "events": [], "event_count": 0, "blocked": False}],
            "events_by_club": {},
            "blocked": [],
            "cached": [],
            "start_date": "2025-09-01",
            "end_date": "2025-12-01",
        }
        from tournament_scheduler.pipeline.state import StageStatus
        state.write_stage(StageName.SCRAPING, existing_data, status=StageStatus.DONE)

        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_ICAL, "url": "https://example.com/cal"},
        ])

        # Seed fresh cache so the run completes without scraping
        from tournament_scheduler.pipeline.cache_manager import ScrapedDataCache
        cache = ScrapedDataCache(work_dir=work_dir)
        cached_events = _events_to_dicts([_make_event("Practice")])
        cache.write({
            "_meta": {
                "updated_at": datetime.now().isoformat(),
                "ttl_hours": 6,
                "start_date": cfg["start_date"],
                "end_date": cfg["end_date"],
            },
            "sources": {
                "Kongsberg": {
                    "name": "Kongsberg",
                    "url": "https://example.com/cal",
                    "scrape_timestamp": datetime.now().isoformat(),
                    "ttl_hours": 6,
                    "event_count": 1,
                    "blocked": False,
                    "events": cached_events,
                },
            },
            "source_count": 1,
            "total_events": 1,
        })

        run(
            cfg, state,
            datetime(2025, 9, 1), datetime(2025, 12, 1),
            strict=False,
        )

        # After a successful run the checkpoint must not be left as running
        assert state.is_done(StageName.SCRAPING), "Checkpoint must be DONE after successful run"
        envelope = state.read_envelope(StageName.SCRAPING)
        assert envelope["data"] != {}, "Checkpoint data must not be empty after resume"
        assert "sources" in envelope["data"], "Checkpoint data must contain 'sources'"

    def test_fresh_run_with_no_prior_checkpoint_works_correctly(self, tmp_path):
        """When no prior checkpoint exists, _set_status creates a minimal RUNNING
        envelope and the final write_stage overwrites it with full data and DONE status."""
        work_dir = tmp_path / "pipeline"
        state = PipelineState(work_dir)

        # Verify no checkpoint exists before the run
        assert not state.checkpoint_path(StageName.SCRAPING).exists()

        cfg = _make_config_with_sources([
            {"name": "Skien", "type": SOURCE_ICAL, "url": "https://skien.example.com/ical"},
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_ical_scraper",
            return_value=[_make_event("Ice time")],
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
            )

        # Checkpoint file must exist, be DONE, and have full data
        assert state.checkpoint_path(StageName.SCRAPING).exists()
        assert state.is_done(StageName.SCRAPING), "Fresh run must leave checkpoint as DONE"
        assert result["sources"][0]["event_count"] == 1
        envelope = state.read_envelope(StageName.SCRAPING)
        assert envelope["data"] != {}, "Fresh run checkpoint data must not be empty"
        assert envelope["data"]["sources"][0]["name"] == "Skien"


class TestHarnessGate:
    """_check_stage2_checkpoint must proceed without calling any LLM."""

    def test_returns_true_when_one_source_has_events(self, tmp_path):
        """Gate must return True when at least one source has events."""
        from unittest.mock import MagicMock
        from rich.console import Console
        from tournament_scheduler.cli.pipeline_orchestrator import _check_stage2_checkpoint

        checkpoint = {
            "sources": [
                {"name": "HallA", "event_count": 5, "blocked": False},
                {"name": "HallB", "event_count": 0, "blocked": True},
            ],
            "blocked": ["HallB"],
        }
        console = Console(file=open("/dev/null", "w"))
        log_fn = MagicMock()

        result = _check_stage2_checkpoint(checkpoint, True, console, log_fn, harness_active=True)

        assert result is True

    def test_does_not_call_lm_studio_client(self, tmp_path):
        """Gate must proceed deterministically without importing or calling any LLM client."""
        from unittest.mock import MagicMock, patch
        from rich.console import Console
        from tournament_scheduler.cli.pipeline_orchestrator import _check_stage2_checkpoint

        checkpoint = {
            "sources": [{"name": "HallA", "event_count": 3, "blocked": False}],
            "blocked": [],
        }
        console = Console(file=open("/dev/null", "w"))
        log_fn = MagicMock()

        # Patch the LMStudio client to raise if it is ever instantiated
        with patch(
            "tournament_scheduler.llm_judge.get_judge_if_headless",
            side_effect=AssertionError("LLM client must not be called in gate"),
        ):
            # The gate function itself does not call get_judge_if_headless
            result = _check_stage2_checkpoint(checkpoint, True, console, log_fn, harness_active=True)

        assert result is True


class TestStrategyBasedDispatch:
    """_scrape_source dispatches to the correct scraper via get_deterministic_scraper_type."""

    def test_bookup_spa_strategy_routes_to_bookup_scraper(self, tmp_path):
        """A source whose strategy has CalendarEngine.BOOKUP_SPA calls _run_bookup_scraper."""
        state = PipelineState(tmp_path / "pipeline")
        # "Tønsberg" is registered with CalendarEngine.BOOKUP_SPA in STRATEGIES
        cfg = _make_config_with_sources([
            {
                "name": "Tønsberg",
                "type": SOURCE_OUTLOOK,
                "url": "https://www.bookup.no/utleie/Index/860#___/view:item/id:860/part:/r:8/mod:book",
            },
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_bookup_scraper",
            return_value=([_make_event("Booking")], ""),
        ) as mock_bookup, patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_styledcalendar_scraper",
            side_effect=AssertionError("styledcalendar must not be called for bookup source"),
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        mock_bookup.assert_called_once()
        src = result["sources"][0]
        assert src["event_count"] == 1
        assert src["blocked"] is False

    def test_forumbooking_strategy_routes_to_forumbooking_scraper(self, tmp_path):
        """Jar uses the Forumbooking parser instead of the generic browser fallback."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {
                "name": "Jar",
                "type": SOURCE_OUTLOOK,
                "url": "https://www.forumbooking.no/schema.aspx?obj=2&schema=Jarhallen%20(ishall)&kalender=true&safarifix=true",
            },
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_forumbooking_scraper",
            return_value=([_make_event("Forumbooking")], ""),
        ) as mock_forumbooking, patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            side_effect=AssertionError("generic browser scraper must not be called for forumbooking source"),
        ):
            result = run(
                cfg,
                state,
                datetime(2025, 9, 1),
                datetime(2025, 12, 1),
                strict=False,
            )

        mock_forumbooking.assert_called_once()
        src = result["sources"][0]
        assert src["event_count"] == 1
        assert src["events"][0]["name"] == "Forumbooking"

    def test_forumbooking_parser_uses_element_date_time_and_customer(self):
        events = _parse_forumbooking_schedule(
            """
            <div id="cphHuvud_div_2_20260906_13713" class="bokning"
              onmouseover="Tip('&lt;div&gt;Time: Søndag 06.09.2026 09:30-11:00&lt;br /&gt;Customer: Jar Hockey&lt;br /&gt;Anmerkning: U10&lt;/div&gt;')">
              <span><font>Jar Hockey<br />09:30-11:00</font></span>
            </div>
            """
        )

        assert len(events) == 1
        assert events[0].date == "06.09.2026"
        assert events[0].datetime == datetime(2026, 9, 6, 9, 30)
        assert events[0].duration_hours == 1.5
        assert events[0].name == "Jar Hockey - U10"

    def test_styled_calendar_strategy_routes_to_styledcalendar_scraper(self, tmp_path):
        """A source whose strategy has CalendarEngine.STYLED_CALENDAR calls _run_styledcalendar_scraper."""
        state = PipelineState(tmp_path / "pipeline")
        # "Jutul" is registered with CalendarEngine.STYLED_CALENDAR in STRATEGIES
        cfg = _make_config_with_sources([
            {
                "name": "Jutul",
                "type": SOURCE_OUTLOOK,
                "url": "https://baerumishall.no/kalender/",
            },
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_styledcalendar_scraper",
            return_value=([_make_event("Ishockey")], ""),
        ) as mock_styled, patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_bookup_scraper",
            side_effect=AssertionError("bookup must not be called for styledcalendar source"),
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        mock_styled.assert_called_once()
        src = result["sources"][0]
        assert src["event_count"] == 1
        assert src["blocked"] is False

    def test_sportello_strategy_routes_to_sportello_scraper(self, tmp_path):
        """A source whose strategy has CalendarEngine.SPORTELLO calls _run_sportello_scraper."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {
                "name": "Holmen",
                "type": SOURCE_OUTLOOK,
                "url": "https://kalender.sportello.no/booking/11055",
            },
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_sportello_scraper",
            return_value=([_make_event("Holmen booking")], ""),
        ) as mock_sportello, patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_bookup_scraper",
            side_effect=AssertionError("bookup must not be called for sportello source"),
        ), patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_styledcalendar_scraper",
            side_effect=AssertionError("styledcalendar must not be called for sportello source"),
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        mock_sportello.assert_called_once()
        src = result["sources"][0]
        assert src["event_count"] == 1
        assert src["blocked"] is False

    def test_sportello_empty_calendar_is_not_blocked(self, tmp_path):
        """Holmen's direct Sportello scraper can yield an empty calendar without being treated as blocked."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {
                "name": "Holmen",
                "type": SOURCE_OUTLOOK,
                "url": "https://kalender.sportello.no/booking/11055",
            },
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_sportello_scraper",
            return_value=([], "{}"),
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        src = result["sources"][0]
        assert src["event_count"] == 0
        assert src["blocked"] is False
        assert src.get("empty_calendar") is True
        assert src.get("empty_reason")
        assert result.get("blocked") == []
        assert result.get("empty_sources") == ["Holmen"]


class TestCredentialedFallbackGate:
    """Credentialed fallback is only triggered on a clean zero-event return, never on exception."""

    def test_credentialed_fallback_skipped_when_deterministic_raises(self, tmp_path):
        """When the deterministic iCal scraper raises, _try_credentialed_scrape is NOT called."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "Teamup", "type": SOURCE_ICAL, "url": "https://teamup.com/example"},
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_ical_scraper",
            side_effect=RuntimeError("network timeout"),
        ), patch(
            "tournament_scheduler.pipeline.stage2_scraping._try_credentialed_scrape",
            return_value=([], ""),
        ) as mock_cred:
            run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        mock_cred.assert_not_called()

    def test_credentialed_fallback_called_when_deterministic_returns_empty(self, tmp_path):
        """When the deterministic iCal scraper returns [] (no exception), _try_credentialed_scrape IS called."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "Teamup", "type": SOURCE_ICAL, "url": "https://teamup.com/example"},
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_ical_scraper",
            return_value=[],
        ), patch(
            "tournament_scheduler.pipeline.stage2_scraping._try_credentialed_scrape",
            return_value=([], ""),
        ) as mock_cred:
            run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        mock_cred.assert_called_once()

    def test_credentialed_fallback_proceeds_past_guard_for_registered_source(self, tmp_path, monkeypatch):
        """When the source has credentials registered AND initial_navigation, _run_credentialed_bookup_or_outlook is called.

        The existing tests use 'Teamup' which has no credential_env_vars and therefore
        short-circuits at line 40 inside _try_credentialed_scrape.  This test uses a
        source for which get_strategy returns a strategy that requires credentials and
        has initial_navigation steps, confirming the credentialed path is actually
        exercised (not just the outer dispatch).
        """
        from tournament_scheduler.pipeline.scraper_strategies import CalendarEngine, ScraperStrategy

        fake_strategy = ScraperStrategy(
            engine=CalendarEngine.BOOKUP_SPA,
            url="https://bookup.example.com",
            credential_env_vars=["BOOKUP_EMAIL", "BOOKUP_PASSWORD"],
            initial_navigation=[{"action": "fill", "selector": "#email", "text": "BOOKUP_EMAIL"}],
        )

        monkeypatch.setenv("BOOKUP_EMAIL", "test@example.com")
        monkeypatch.setenv("BOOKUP_PASSWORD", "secret")

        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "Bookup", "type": SOURCE_ICAL, "url": "https://bookup.example.com/ical"},
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_ical_scraper",
            return_value=[],
        ), patch(
            "tournament_scheduler.pipeline.scraper_credentialed.get_strategy",
            return_value=fake_strategy,
        ), patch(
            "tournament_scheduler.pipeline.scraper_credentialed._run_credentialed_bookup_or_outlook",
            return_value=([], ""),
        ) as mock_cred_run:
            run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        mock_cred_run.assert_called_once()

    def test_credentialed_fallback_not_called_for_unknown_source_type(self, tmp_path):
        """When source type is unrecognised, _try_credentialed_scrape is NOT called and scraper_error is set.

        This confirms the guard added in stage2_scraping: setting deterministic_raised=True
        in the unknown-type else branch prevents the fallback from being triggered even
        though the deterministic scraper returned zero events.
        """
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "Unknown", "type": "unknown_type", "url": "https://example.com/cal"},
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._try_credentialed_scrape",
            return_value=([], ""),
        ) as mock_cred:
            run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        mock_cred.assert_not_called()
        checkpoint_data = state.read_stage(StageName.SCRAPING)
        src = checkpoint_data["sources"][0]
        assert "scraper_error" in src
        assert "unknown_type" in src["scraper_error"]


# ---------------------------------------------------------------------------
# Consistent source_result dict shape across all three construction branches
# ---------------------------------------------------------------------------

_COMMON_KEYS = {
    "name", "url", "type", "events", "event_count",
    "blocked", "block_reason", "llm_fallback",
}


class TestSourceResultShape:
    """Assert the canonical source_result dict shape is consistent across all branches."""

    def test_missing_url_branch_has_common_keys_and_skipped_extras(self, tmp_path):
        """A source with an empty URL produces a result with common keys plus skipped=True."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "Disabled", "type": SOURCE_OUTLOOK, "url": ""},
        ])
        result = run(
            cfg, state,
            datetime(2025, 9, 1), datetime(2025, 12, 1),
            strict=False,
        )
        src = result["sources"][0]
        assert _COMMON_KEYS.issubset(src.keys()), f"Missing keys: {_COMMON_KEYS - src.keys()}"
        assert src["skipped"] is True
        assert src["skip_reason"]
        # Branch-specific extras for other branches must not be present
        assert "scraper_error" not in src
        assert "from_cache" not in src

    def test_error_branch_has_common_keys_and_scraper_error(self, tmp_path):
        """A source whose future raises an exception has common keys plus scraper_error."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "BrokenHall", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            side_effect=RuntimeError("network timeout"),
        ), patch(
            "tournament_scheduler.pipeline.stage2_scraping._try_credentialed_scrape",
            side_effect=RuntimeError("network timeout"),
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
                strict=False,
            )

        src = result["sources"][0]
        assert _COMMON_KEYS.issubset(src.keys()), f"Missing keys: {_COMMON_KEYS - src.keys()}"
        assert src["blocked"] is True
        assert "scraper_error" in src
        assert "network timeout" in src["scraper_error"]
        assert "skipped" not in src
        assert "from_cache" not in src

    def test_cache_hit_branch_has_common_keys_and_from_cache(self, tmp_path):
        """A source served from cache has common keys plus from_cache=True."""
        work_dir = tmp_path / "pipeline"
        state = PipelineState(work_dir)
        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])
        cached_events = _events_to_dicts([_make_event("CachedEvent")])
        ts = datetime.now().isoformat()
        cache = ScrapedDataCache(work_dir=work_dir)
        cache.write({
            "_meta": {
                "updated_at": ts,
                "ttl_hours": 6,
                "start_date": cfg["start_date"],
                "end_date": cfg["end_date"],
            },
            "sources": {
                "Kongsberg": {
                    "name": "Kongsberg",
                    "url": "https://example.com",
                    "scrape_timestamp": ts,
                    "ttl_hours": 6,
                    "event_count": len(cached_events),
                    "blocked": False,
                    "events": cached_events,
                },
            },
            "source_count": 1,
            "total_events": len(cached_events),
        })

        result = run(
            cfg, state,
            datetime(2025, 9, 1), datetime(2025, 12, 1),
        )

        assert result["cached"] == ["Kongsberg"]
        src = result["sources"][0]
        assert _COMMON_KEYS.issubset(src.keys()), f"Missing keys: {_COMMON_KEYS - src.keys()}"
        assert src["from_cache"] is True
        assert src["blocked"] is False
        assert "skipped" not in src
        assert "scraper_error" not in src

    def test_normal_scrape_branch_has_common_keys_no_extras(self, tmp_path):
        """A source scraped normally has common keys but no branch-specific extras."""
        state = PipelineState(tmp_path / "pipeline")
        cfg = _make_config_with_sources([
            {"name": "Kongsberg", "type": SOURCE_OUTLOOK, "url": "https://example.com"},
        ])

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            return_value=([_make_event("Practice")], ""),
        ):
            result = run(
                cfg, state,
                datetime(2025, 9, 1), datetime(2025, 12, 1),
            )

        src = result["sources"][0]
        assert _COMMON_KEYS.issubset(src.keys()), f"Missing keys: {_COMMON_KEYS - src.keys()}"
        assert src["blocked"] is False
        assert src["event_count"] == 1
        assert "skipped" not in src
        assert "scraper_error" not in src
        assert "from_cache" not in src


# ---------------------------------------------------------------------------
# Isolated unit tests for _scrape_source with injected CalendarCache
# ---------------------------------------------------------------------------

_SCRAPE_START = datetime(2025, 9, 1)
_SCRAPE_END = datetime(2025, 12, 1)

_COMMON_SOURCE_KEYS = {
    "name", "url", "type", "events", "event_count",
    "blocked", "block_reason", "llm_fallback",
}


class TestScrapeSourceIsolated:
    """Call _scrape_source directly with a mock CalendarCache — no run() required."""

    def _mock_cache(self, tmp_path):
        """Return a CalendarCache instance backed by a temp directory."""
        return CalendarCache(work_dir=str(tmp_path))

    def test_ical_source_returns_expected_shape(self, tmp_path):
        """iCal branch returns a result with all required keys when events are found."""
        source_cfg = {
            "name": "Skien",
            "type": SOURCE_ICAL,
            "url": "https://example.com/feed.ics",
        }
        cache = self._mock_cache(tmp_path)

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_ical_scraper",
            return_value=[_make_event("Trening")],
        ):
            result = _scrape_source(
                source_cfg,
                start_date=_SCRAPE_START,
                end_date=_SCRAPE_END,
                calendar_cache=cache,
            )

        assert _COMMON_SOURCE_KEYS.issubset(result.keys())
        assert result["name"] == "Skien"
        assert result["event_count"] == 1
        assert result["blocked"] is False
        assert result["llm_fallback"] is False

    def test_browser_source_returns_expected_shape(self, tmp_path):
        """Outlook/browser branch returns a result with all required keys when events are found."""
        source_cfg = {
            "name": "Kongsberg",
            "type": SOURCE_OUTLOOK,
            "url": "https://example.com/calendar",
        }
        cache = self._mock_cache(tmp_path)

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            return_value=([_make_event("Booking")], ""),
        ):
            result = _scrape_source(
                source_cfg,
                start_date=_SCRAPE_START,
                end_date=_SCRAPE_END,
                calendar_cache=cache,
            )

        assert _COMMON_SOURCE_KEYS.issubset(result.keys())
        assert result["name"] == "Kongsberg"
        assert result["event_count"] == 1
        assert result["blocked"] is False

    def test_error_branch_returns_scraper_error_key(self, tmp_path):
        """When the scraper raises, the result has scraper_error set and event_count=0."""
        source_cfg = {
            "name": "Feil kilde",
            "type": SOURCE_OUTLOOK,
            "url": "https://example.com/bad",
        }
        cache = self._mock_cache(tmp_path)

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_outlook_scraper",
            side_effect=RuntimeError("connection refused"),
        ):
            result = _scrape_source(
                source_cfg,
                start_date=_SCRAPE_START,
                end_date=_SCRAPE_END,
                calendar_cache=cache,
            )

        assert result["event_count"] == 0
        assert "scraper_error" in result
        assert "connection refused" in result["scraper_error"]

    def test_none_calendar_cache_is_accepted(self, tmp_path):
        """Passing calendar_cache=None does not raise; result shape is intact."""
        source_cfg = {
            "name": "NoCacheSource",
            "type": SOURCE_ICAL,
            "url": "https://example.com/feed.ics",
        }

        with patch(
            "tournament_scheduler.pipeline.stage2_scraping._run_ical_scraper",
            return_value=[],
        ):
            result = _scrape_source(
                source_cfg,
                start_date=_SCRAPE_START,
                end_date=_SCRAPE_END,
                calendar_cache=None,
            )

        assert _COMMON_SOURCE_KEYS.issubset(result.keys())
        assert result["event_count"] == 0
