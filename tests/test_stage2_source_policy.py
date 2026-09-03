from datetime import datetime
from unittest.mock import patch

import pytest

from tournament_scheduler.pipeline.scraper_credentialed import (
    _bookup_storage_state_path,
    _try_credentialed_scrape,
)
from tournament_scheduler.pipeline.source_readiness import classify_unresolved_sources
from tournament_scheduler.pipeline.stage2_scraping import SOURCE_OUTLOOK, Stage2Error, run
from tournament_scheduler.pipeline.state import PipelineState, StageName
from tournament_scheduler.utils.calendar_cache import CalendarCache


START = datetime(2025, 9, 1)
END = datetime(2025, 12, 1)


def _config(name: str, source_type: str = SOURCE_OUTLOOK) -> dict:
    return {
        "start_date": "2025-09-01",
        "end_date": "2025-12-01",
        "teams": [{"club": "Kongsberg", "label": "Kongsberg U10", "age_group": "U10"}],
        "sources": [{"name": name, "type": source_type, "url": "https://example.com/calendar"}],
    }


def test_temporary_bookup_sources_are_unresolved_but_not_blocking():
    classified = classify_unresolved_sources([
        {"name": "Tønsberg"},
        {"name": "Sandefjord Penguins"},
    ])

    assert classified["planning_ready"] is True
    assert [s["name"] for s in classified["temporary"]] == ["Tønsberg", "Sandefjord Penguins"]
    assert classified["blocking"] == []


def test_unexpected_unresolved_source_remains_blocking():
    classified = classify_unresolved_sources([
        {"name": "Tønsberg"},
        {"name": "Jutul"},
    ])

    assert classified["planning_ready"] is False
    assert [s["name"] for s in classified["blocking"]] == ["Jutul"]


def test_sandefjord_failure_is_done_but_still_recorded_as_unresolved(tmp_path):
    state = PipelineState(tmp_path / "pipeline")
    cfg = _config("Sandefjord Penguins")

    with patch(
        "tournament_scheduler.pipeline.stage2_scraping._try_credentialed_scrape",
        return_value=([], "BookUp auth unavailable"),
    ):
        result = run(cfg, state, START, END, strict=True)

    assert state.is_done(StageName.SCRAPING)
    assert result["planning_ready"] is True
    assert result["blocked"] == ["Sandefjord Penguins"]
    assert result["blocking_sources"] == []
    assert result["temporarily_unresolved_sources"] == ["Sandefjord Penguins"]
    assert result["sources"][0]["blocked"] is True
    assert "ikke-blokkerende" in result["warning"]


def test_unexpected_scrape_failure_still_raises_in_strict_mode(tmp_path):
    state = PipelineState(tmp_path / "pipeline")
    cfg = _config("UnexpectedHall", source_type="unknown_type")

    with pytest.raises(Stage2Error) as exc_info:
        run(cfg, state, START, END, strict=True)

    assert [s["name"] for s in exc_info.value.blocked] == ["UnexpectedHall"]
    checkpoint = state.read_stage(StageName.SCRAPING)
    assert checkpoint["planning_ready"] is False
    assert checkpoint["blocking_sources"] == ["UnexpectedHall"]


def test_explicit_allow_missing_is_the_broad_override(tmp_path):
    state = PipelineState(tmp_path / "pipeline")
    cfg = _config("UnexpectedHall", source_type="unknown_type")

    result = run(cfg, state, START, END, strict=True, allow_missing_sources=True)

    assert result["planning_ready"] is True
    assert result["blocking_sources"] == []
    assert result["operator_allowed_missing_sources"] == ["UnexpectedHall"]


def test_bookup_storage_state_lives_under_pipeline_auth(tmp_path):
    cache = CalendarCache(work_dir=tmp_path / "pipeline")

    assert _bookup_storage_state_path(cache) == (
        tmp_path / "pipeline" / "auth" / "bookup-storage-state.json"
    )


def test_saved_bookup_state_allows_headless_retry_without_env_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("BOOKUP_EMAIL", raising=False)
    monkeypatch.delenv("BOOKUP_PASSWORD", raising=False)
    cache = CalendarCache(work_dir=tmp_path / "pipeline")
    state_path = _bookup_storage_state_path(cache)
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")

    with patch(
        "tournament_scheduler.pipeline.scraper_credentialed._run_credentialed_bookup_or_outlook",
        return_value=([], "expired"),
    ) as runner:
        events, error = _try_credentialed_scrape(
            "Tønsberg",
            "https://www.bookup.no/utleie/Index/860",
            START,
            END,
            cache,
        )

    assert events == []
    assert error == "expired"
    runner.assert_called_once()
    assert runner.call_args.kwargs["storage_state_path"] == state_path


def test_missing_bookup_state_without_credentials_reports_host_recovery(tmp_path, monkeypatch):
    monkeypatch.delenv("BOOKUP_EMAIL", raising=False)
    monkeypatch.delenv("BOOKUP_PASSWORD", raising=False)
    cache = CalendarCache(work_dir=tmp_path / "pipeline")

    events, error = _try_credentialed_scrape(
        "Tønsberg",
        "https://www.bookup.no/utleie/Index/860",
        START,
        END,
        cache,
    )

    assert events == []
    assert "macOS-verten" in error
    assert "bookup-storage-state.json" in error
