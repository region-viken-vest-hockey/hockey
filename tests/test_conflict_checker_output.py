"""Conflict checker warnings must not write to stdout.

Conflict checkers run beneath machine-readable commands (``--json``) that share
stdout, so their warning text belongs on the logging channel (stderr) instead.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from types import SimpleNamespace

from tournament_scheduler.conflict_checkers.ball_hall_checker import BallHallConflictChecker
from tournament_scheduler.conflict_checkers.excel_team_checker import ExcelTeamConflictChecker
from tournament_scheduler.models import CalendarEvent, ConflictContext
from tournament_scheduler.utils.date_parser import DateParser


def test_ball_hall_warnings_log_instead_of_writing_stdout(capsys, caplog):
    checker = BallHallConflictChecker(SimpleNamespace(min_duration=2.0))
    event = CalendarEvent(
        date="10.01.2026",
        name="Ballet Gala",
        datetime=datetime(2026, 1, 10, 10, 0),
        duration_hours=4.0,
    )
    context = ConflictContext(
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 2, 1),
        calendar_events=[event],
    )

    with caplog.at_level(logging.WARNING, logger="tournament_scheduler.conflict_checkers.ball_hall_checker"):
        result = checker.check_conflicts([date(2026, 1, 10)], context)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert result.excluded_dates == set()
    messages = [r.getMessage() for r in caplog.records]
    assert any("BALLHALL-ADVARSLER (1 advarsler" in m for m in messages)
    assert any("Ballet Gala" in m for m in messages)


def test_excel_weekend_warnings_log_instead_of_writing_stdout(monkeypatch, capsys, caplog):
    checker = ExcelTeamConflictChecker("unused.xlsx", ["Team A"], DateParser())
    saturday = date(2026, 1, 10)
    sunday = date(2026, 1, 11)
    monkeypatch.setattr(
        checker,
        "_find_team_events_in_excel",
        lambda: {"Team A": {sunday: "Cup"}},
    )
    context = ConflictContext(start_date=datetime(2026, 1, 1), end_date=datetime(2026, 2, 1))

    with caplog.at_level(logging.WARNING, logger="tournament_scheduler.conflict_checkers.excel_team_checker"):
        result = checker.check_conflicts([saturday], context)

    captured = capsys.readouterr()
    assert "HELGE-KONFLIKTER" not in captured.out
    assert "Team A" not in captured.out
    assert result.excluded_dates == set()
    messages = [r.getMessage() for r in caplog.records]
    assert any("HELGE-KONFLIKTER" in m for m in messages)
    assert any("Team A" in m and "Cup" in m for m in messages)
