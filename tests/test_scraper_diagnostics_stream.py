"""Scraper progress and failure messages must not write to stdout.

Scrapers run underneath machine-readable commands (``--json``) that own stdout,
so a Rich warning emitted from a scraper would corrupt the payload. These
diagnostics belong on stderr.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from tournament_scheduler.data_sources import ical_scraper
from tournament_scheduler.utils.calendar_cache import CalendarCache


def test_ical_scraper_http_failure_writes_diagnostic_to_stderr_not_stdout(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        ical_scraper.requests,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=503, content=b""),
    )
    scraper = ical_scraper.ICalScraper(
        calendar_id="https://example.com/feed.ics",
        cache=CalendarCache(work_dir=str(tmp_path)),
    )

    events = scraper.scrape_calendar(
        url="",
        calendar_name="Test Arena",
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 2, 1),
    )

    captured = capsys.readouterr()
    assert events == []
    assert captured.out == ""
    assert "HTTP 503" in captured.err
