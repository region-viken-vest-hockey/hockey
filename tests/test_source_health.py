"""Tests for tournament_scheduler.pipeline.source_health (calendar source health capability)."""

from __future__ import annotations

import argparse
import json

import pytest

from tournament_scheduler.pipeline.cache_manager import ScrapedDataCache
from tournament_scheduler.pipeline.source_health import compute_source_health
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus


def _write_scraping_checkpoint(work_dir, sources: list[dict]) -> None:
    PipelineState(work_dir).write_stage(StageName.SCRAPING, {"sources": sources}, status=StageStatus.DONE)


class TestComputeSourceHealth:
    def test_empty_when_no_scraping_checkpoint(self, tmp_path):
        assert compute_source_health(str(tmp_path)) == []

    def test_healthy_source_is_ok(self, tmp_path):
        _write_scraping_checkpoint(
            tmp_path,
            [{"name": "Jar", "event_count": 12, "blocked": False, "event_expectation": {"status": "ok"}}],
        )
        results = compute_source_health(str(tmp_path))
        assert len(results) == 1
        assert results[0].status == "ok"
        assert results[0].capability == "source_health:Jar"
        assert results[0].requires_human is False

    def test_blocked_source_requires_human_and_suggests_recovery(self, tmp_path):
        _write_scraping_checkpoint(
            tmp_path,
            [{
                "name": "Kongsberg ishall",
                "event_count": 0,
                "blocked": True,
                "block_reason": "Timeout",
                "llm_fallback": True,
            }],
        )
        result = compute_source_health(str(tmp_path))[0]
        assert result.status == "blocked"
        assert result.requires_human is True
        assert "Timeout" in result.problems
        assert any("scrape-llm" in action for action in result.suggested_actions)
        assert any("recovery-targets" in action for action in result.suggested_actions)

    def test_blocked_without_llm_fallback_suggests_manual_scrape(self, tmp_path):
        _write_scraping_checkpoint(
            tmp_path,
            [{"name": "Skien", "event_count": 0, "blocked": True, "block_reason": "404", "llm_fallback": False}],
        )
        result = compute_source_health(str(tmp_path))[0]
        assert any("scrape --club" in action for action in result.suggested_actions)
        assert any("recovery-inject" in action for action in result.suggested_actions)

    def test_skipped_source_is_warning(self, tmp_path):
        _write_scraping_checkpoint(
            tmp_path,
            [{"name": "Holmen", "event_count": 0, "blocked": False, "skipped": True, "skip_reason": "Ingen URL konfigurert"}],
        )
        result = compute_source_health(str(tmp_path))[0]
        assert result.status == "warning"
        assert "Ingen URL konfigurert" in result.problems

    def test_low_event_expectation_is_warning(self, tmp_path):
        _write_scraping_checkpoint(
            tmp_path,
            [{
                "name": "Jutul",
                "event_count": 1,
                "blocked": False,
                "event_expectation": {"status": "low", "expected_min_events": 5, "message": "For få hendelser"},
            }],
        )
        result = compute_source_health(str(tmp_path))[0]
        assert result.status == "warning"
        assert "For få hendelser" in result.problems

    def test_sparse_vs_previous_snapshot_is_warning(self, tmp_path):
        cache = ScrapedDataCache(work_dir=str(tmp_path))
        cache.write({"sources": {"Sandefjord": {"name": "Sandefjord", "event_count": 10, "events": []}}})
        # A second build_from_checkpoint call rotates "sources" into "previous_sources".
        cache.build_from_checkpoint(
            {"sources": []},
            {"sources": [{"name": "Sandefjord", "url": "", "type": "ical", "event_count": 0, "blocked": False, "events": []}]},
        )
        _write_scraping_checkpoint(
            tmp_path,
            [{"name": "Sandefjord", "event_count": 0, "blocked": False, "event_expectation": {"status": "not_applicable"}}],
        )
        result = compute_source_health(str(tmp_path))[0]
        assert result.status == "warning"
        assert any("forrige vellykkede skraping" in p for p in result.problems)
        assert "previous_event_count=10" in result.evidence

    def test_duplicate_heavy_source_is_warning(self, tmp_path):
        duplicated_events = [{"date": "2026-09-05", "name": "Kamp A"} for _ in range(10)]
        _write_scraping_checkpoint(
            tmp_path,
            [{
                "name": "Ringerike",
                "event_count": 10,
                "blocked": False,
                "events": duplicated_events,
                "event_expectation": {"status": "ok"},
            }],
        )
        result = compute_source_health(str(tmp_path))[0]
        assert result.status == "warning"
        assert any("duplikater" in p for p in result.problems)

    def test_parallel_rink_bookings_are_not_duplicate_warnings(self, tmp_path):
        events = [
            {
                "date": "05.09.2026",
                "datetime": "2026-09-05T09:00:00",
                "name": "Skien fritidspark KF 09:00-19:00",
                "location": "Ishockey - bane 1",
            },
            {
                "date": "05.09.2026",
                "datetime": "2026-09-05T09:00:00",
                "name": "Skien fritidspark KF 09:00-19:00",
                "location": "Ishockey - bane 2",
            },
        ]
        _write_scraping_checkpoint(
            tmp_path,
            [{
                "name": "Skien",
                "event_count": 2,
                "blocked": False,
                "events": events,
                "event_expectation": {"status": "ok"},
            }],
        )

        result = compute_source_health(str(tmp_path))[0]

        assert result.status == "ok"
        assert not any("duplikater" in p for p in result.problems)

    def test_stale_cache_beyond_ttl_is_warning(self, tmp_path):
        cache = ScrapedDataCache(work_dir=str(tmp_path))
        cache.write({"sources": {"Tonsberg": {"name": "Tonsberg", "event_count": 5, "scrape_timestamp": "2020-01-01T00:00:00", "events": []}}})
        _write_scraping_checkpoint(
            tmp_path,
            [{"name": "Tonsberg", "event_count": 5, "blocked": False, "event_expectation": {"status": "ok"}}],
        )
        result = compute_source_health(str(tmp_path))[0]
        assert result.status == "warning"
        assert any("Cache er" in p for p in result.problems)

    def test_to_dict_round_trips_as_json(self, tmp_path):
        _write_scraping_checkpoint(
            tmp_path,
            [{"name": "Jar", "event_count": 3, "blocked": False, "event_expectation": {"status": "ok"}}],
        )
        results = compute_source_health(str(tmp_path))
        payload = json.dumps([r.to_dict() for r in results])
        parsed = json.loads(payload)
        assert parsed[0]["capability"] == "source_health:Jar"


class TestSourcesStatusCli:
    def test_json_output_matches_compute_source_health(self, tmp_path, capsys):
        from tournament_scheduler.cli.reporting import _cmd_sources_status

        _write_scraping_checkpoint(
            tmp_path,
            [{"name": "Jar", "event_count": 3, "blocked": False, "event_expectation": {"status": "ok"}}],
        )
        args = argparse.Namespace(work_dir=str(tmp_path), json=True)
        rc = _cmd_sources_status(args)
        assert rc == 0
        printed = json.loads(capsys.readouterr().out)
        assert printed[0]["capability"] == "source_health:Jar"

    def test_returns_1_when_any_source_blocked(self, tmp_path):
        from tournament_scheduler.cli.reporting import _cmd_sources_status

        _write_scraping_checkpoint(
            tmp_path,
            [{"name": "Jar", "event_count": 0, "blocked": True, "block_reason": "x", "llm_fallback": False}],
        )
        args = argparse.Namespace(work_dir=str(tmp_path), json=False)
        rc = _cmd_sources_status(args)
        assert rc == 1

    def test_returns_0_when_no_checkpoint(self, tmp_path):
        from tournament_scheduler.cli.reporting import _cmd_sources_status

        args = argparse.Namespace(work_dir=str(tmp_path), json=False)
        assert _cmd_sources_status(args) == 0

    def test_dispatch_routes_status_subcommand(self):
        from tournament_scheduler.cli.rvv_cli import _cmd_sources
        from unittest.mock import patch

        args = argparse.Namespace(sources_command="status")
        with patch("tournament_scheduler.cli.rvv_cli._cmd_sources_status", return_value=0) as mock_status:
            rc = _cmd_sources(args)
        mock_status.assert_called_once_with(args)
        assert rc == 0

    def test_dispatch_without_subcommand_prints_usage(self):
        from tournament_scheduler.cli.rvv_cli import _cmd_sources

        args = argparse.Namespace(sources_command=None)
        assert _cmd_sources(args) == 1


class TestArgParsing:
    def test_sources_status_parses_defaults(self):
        from tournament_scheduler.cli.args import build_parser

        args = build_parser().parse_args(["sources", "status"])
        assert args.command == "sources"
        assert args.sources_command == "status"
        assert args.work_dir == ".pipeline"
        assert args.json is False
