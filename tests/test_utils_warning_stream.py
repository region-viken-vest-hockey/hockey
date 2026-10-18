"""Cache and history write/read failures must not write to stdout.

Machine-readable commands (``--json``) share stdout with these helpers, so a
Rich console warning here would corrupt the payload. Warnings belong on the
logging channel (stderr) instead.
"""

from __future__ import annotations

import logging
from datetime import datetime

from tournament_scheduler.models import CalendarEvent
from tournament_scheduler.utils.calendar_cache import CalendarCache
from tournament_scheduler.utils.search_history import SearchHistory


def _event() -> CalendarEvent:
    return CalendarEvent(
        date="10.01.2026",
        name="Cup",
        datetime=datetime(2026, 1, 10, 10, 0),
        duration_hours=2.0,
    )


def test_calendar_cache_write_failure_logs_warning_not_stdout(tmp_path, capsys, caplog):
    cache = CalendarCache(work_dir=str(tmp_path))
    # A directory at the cache-file path makes the write fail.
    (cache.cache_dir / "unwritable.json").mkdir()
    cache._get_cache_key = lambda *_args, **_kwargs: "unwritable"

    with caplog.at_level(logging.WARNING, logger="tournament_scheduler.utils.calendar_cache"):
        cache.set(
            url="https://example.com/feed.ics",
            calendar_name="Test Arena",
            start_date=datetime(2026, 1, 1),
            end_date=datetime(2026, 2, 1),
            events=[_event()],
        )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert any("Kunne ikke cache kalenderdata" in r.getMessage() for r in caplog.records)


def test_search_history_save_failure_logs_warning_not_stdout(tmp_path, capsys, caplog):
    history = SearchHistory(history_file=str(tmp_path / "history.json"))
    history.history_file.mkdir()  # makes the write fail

    with caplog.at_level(logging.WARNING, logger="tournament_scheduler.utils.search_history"):
        history.save_search({"club": "Ringerike"})

    captured = capsys.readouterr()
    assert captured.out == ""
    assert any("Kunne ikke lagre søkehistorikk" in r.getMessage() for r in caplog.records)


def test_search_history_load_failure_logs_warning_not_stdout(tmp_path, capsys, caplog):
    path = tmp_path / "history.json"
    path.write_text("{not json", encoding="utf-8")
    history = SearchHistory(history_file=str(path))

    with caplog.at_level(logging.WARNING, logger="tournament_scheduler.utils.search_history"):
        assert history.load_history() == []

    captured = capsys.readouterr()
    assert captured.out == ""
    assert any("Kunne ikke laste søkehistorikk" in r.getMessage() for r in caplog.records)
