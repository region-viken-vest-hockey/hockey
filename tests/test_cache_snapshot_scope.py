"""Snapshot scope of the unified scrape cache (#853).

A scoped refresh (one club) writes a cache whose ``sources`` hold only that club.
The cache must say so explicitly, so readers never treat a single-club snapshot
as a complete refresh of every configured source. ``complete`` is source
membership only; freshness is recorded separately.
"""

from __future__ import annotations

import argparse
import json

from tournament_scheduler.pipeline.cache_manager import ScrapedDataCache
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus


def _config(*names: str) -> dict:
    return {"sources": [{"name": n, "url": f"https://{n.lower()}.example", "type": "ical"} for n in names]}


def _result(*names: str, from_cache: tuple[str, ...] = ()) -> dict:
    return {
        "sources": [
            {
                "name": n,
                "url": f"https://{n.lower()}.example",
                "type": "ical",
                "blocked": False,
                "events": [],
                "from_cache": n in from_cache,
            }
            for n in names
        ]
    }


class TestSnapshotScopeRecordedByCache:
    def test_full_refresh_is_recorded_as_complete_and_fresh(self, tmp_path):
        cache = ScrapedDataCache(work_dir=str(tmp_path))
        data = cache.build_from_checkpoint(_config("Jar", "Tønsberg"), _result("Jar", "Tønsberg"))

        assert data["_meta"]["snapshot_scope"] == {
            "complete": True,
            "included_sources": ["Jar", "Tønsberg"],
            "freshly_scraped_sources": ["Jar", "Tønsberg"],
            "reused_cached_sources": [],
            "missing_sources": [],
        }

    def test_single_club_refresh_is_recorded_as_partial_with_missing_sources(self, tmp_path):
        cache = ScrapedDataCache(work_dir=str(tmp_path))
        data = cache.build_from_checkpoint(_config("Jar", "Tønsberg"), _result("Tønsberg"))

        scope = data["_meta"]["snapshot_scope"]
        assert scope["complete"] is False
        assert scope["included_sources"] == ["Tønsberg"]
        assert scope["missing_sources"] == ["Jar"]

    def test_cached_source_is_included_but_not_counted_as_freshly_scraped(self, tmp_path):
        cache = ScrapedDataCache(work_dir=str(tmp_path))
        cache.build_from_checkpoint(_config("Jar", "Tønsberg"), _result("Jar", "Tønsberg"))
        data = cache.build_from_checkpoint(
            _config("Jar", "Tønsberg"),
            _result("Jar", "Tønsberg", from_cache=("Jar",)),
        )

        scope = data["_meta"]["snapshot_scope"]
        assert scope["complete"] is True
        assert scope["included_sources"] == ["Jar", "Tønsberg"]
        assert scope["reused_cached_sources"] == ["Jar"]
        assert scope["freshly_scraped_sources"] == ["Tønsberg"]


class TestSnapshotScopeReadBack:
    def test_snapshot_scope_reads_partial_scope_from_cache(self, tmp_path):
        from tournament_scheduler.pipeline.source_health import snapshot_scope

        ScrapedDataCache(work_dir=str(tmp_path)).build_from_checkpoint(
            _config("Jar", "Tønsberg"), _result("Tønsberg")
        )
        scope = snapshot_scope(str(tmp_path))

        assert scope is not None
        assert scope["complete"] is False
        assert scope["missing_sources"] == ["Jar"]

    def test_snapshot_scope_is_none_without_cache(self, tmp_path):
        from tournament_scheduler.pipeline.source_health import snapshot_scope

        assert snapshot_scope(str(tmp_path)) is None


def _write_partial_snapshot(tmp_path) -> None:
    ScrapedDataCache(work_dir=str(tmp_path)).build_from_checkpoint(
        _config("Jar", "Tønsberg"), _result("Tønsberg")
    )
    PipelineState(str(tmp_path)).write_stage(
        StageName.SCRAPING, _result("Tønsberg"), status=StageStatus.DONE
    )


class TestSourcesStatusLabelsPartialScope:
    def test_partial_scope_is_stated_not_reported_as_complete(self, tmp_path, capsys):
        from tournament_scheduler.cli.sources_status import _cmd_sources_status

        _write_partial_snapshot(tmp_path)
        _cmd_sources_status(argparse.Namespace(work_dir=str(tmp_path), json=False))
        out = capsys.readouterr().out

        assert "delvis" in out
        assert "Ikke med i denne kjøringen: Jar" in out

    def test_json_output_exposes_partial_scope_to_automation(self, tmp_path, capsys):
        from tournament_scheduler.cli.sources_status import _cmd_sources_status

        _write_partial_snapshot(tmp_path)
        _cmd_sources_status(argparse.Namespace(work_dir=str(tmp_path), json=True))
        printed = json.loads(capsys.readouterr().out)

        assert printed[0]["capability"] == "source_health:Tønsberg"
        assert "snapshot_scope=partial missing=Jar" in printed[0]["evidence"]

    def test_complete_snapshot_adds_no_scope_evidence(self, tmp_path, capsys):
        from tournament_scheduler.cli.sources_status import _cmd_sources_status

        ScrapedDataCache(work_dir=str(tmp_path)).build_from_checkpoint(
            _config("Tønsberg"), _result("Tønsberg")
        )
        PipelineState(str(tmp_path)).write_stage(
            StageName.SCRAPING, _result("Tønsberg"), status=StageStatus.DONE
        )
        _cmd_sources_status(argparse.Namespace(work_dir=str(tmp_path), json=True))
        printed = json.loads(capsys.readouterr().out)

        assert not any(item.startswith("snapshot_scope=") for item in printed[0]["evidence"])

    def test_legacy_cache_without_scope_is_reported_as_unknown(self, tmp_path, capsys):
        from tournament_scheduler.cli.sources_status import _cmd_sources_status

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "scraped_data.json").write_text(json.dumps({"sources": {}}), encoding="utf-8")
        PipelineState(str(tmp_path)).write_stage(
            StageName.SCRAPING, _result("Tønsberg"), status=StageStatus.DONE
        )
        _cmd_sources_status(argparse.Namespace(work_dir=str(tmp_path), json=True))
        printed = json.loads(capsys.readouterr().out)

        assert "snapshot_scope=unknown (cache predates scope recording)" in printed[0]["evidence"]
