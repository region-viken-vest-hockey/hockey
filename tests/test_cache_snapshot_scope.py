"""Snapshot scope of the unified scrape cache (#853).

A scoped refresh (one club) writes a cache whose ``sources`` hold only that club.
The cache must say so explicitly, so readers never treat a single-club snapshot
as a complete refresh of every configured source.
"""

from __future__ import annotations

import argparse

from tournament_scheduler.pipeline.cache_manager import ScrapedDataCache
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus


def _config(*names: str) -> dict:
    return {"sources": [{"name": n, "url": f"https://{n.lower()}.example", "type": "ical"} for n in names]}


def _result(*names: str) -> dict:
    return {
        "sources": [
            {"name": n, "url": f"https://{n.lower()}.example", "type": "ical", "blocked": False, "events": []}
            for n in names
        ]
    }


class TestSnapshotScopeRecordedByCache:
    def test_full_refresh_is_recorded_as_complete(self, tmp_path):
        cache = ScrapedDataCache(work_dir=str(tmp_path))
        data = cache.build_from_checkpoint(_config("Jar", "Tønsberg"), _result("Jar", "Tønsberg"))

        assert data["_meta"]["snapshot_scope"] == {
            "complete": True,
            "refreshed_sources": ["Jar", "Tønsberg"],
            "missing_sources": [],
        }

    def test_single_club_refresh_is_recorded_as_partial_with_missing_sources(self, tmp_path):
        cache = ScrapedDataCache(work_dir=str(tmp_path))
        data = cache.build_from_checkpoint(_config("Jar", "Tønsberg"), _result("Tønsberg"))

        assert data["_meta"]["snapshot_scope"] == {
            "complete": False,
            "refreshed_sources": ["Tønsberg"],
            "missing_sources": ["Jar"],
        }


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


class TestSourcesStatusLabelsPartialScope:
    def test_partial_scope_is_stated_not_reported_as_complete(self, tmp_path, capsys):
        from tournament_scheduler.cli.sources_status import _cmd_sources_status

        ScrapedDataCache(work_dir=str(tmp_path)).build_from_checkpoint(
            _config("Jar", "Tønsberg"), _result("Tønsberg")
        )
        PipelineState(str(tmp_path)).write_stage(
            StageName.SCRAPING, _result("Tønsberg"), status=StageStatus.DONE
        )

        _cmd_sources_status(argparse.Namespace(work_dir=str(tmp_path), json=False))
        out = capsys.readouterr().out

        assert "delvis" in out
        assert "Jar" in out
        assert "Ikke oppdatert i denne kjøringen: Jar" in out
