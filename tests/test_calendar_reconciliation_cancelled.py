"""Calendar-booking reconciliation must skip cancelled tournaments.

Regression for a bug surfaced while retiring a team: `reconcile-calendar-
bookings --club <club>` iterated every tournament hosted by the club with no
`cancelled` check, so a tournament already cancelled (e.g. because its only
roster team retired) was still classified against calendar evidence and
could get a meaningless "needs confirmation" booking-evidence record
persisted for ice that no longer has anything scheduled on it.
"""

from __future__ import annotations

from tournament_scheduler.season_state import reconcile_calendar_bookings
from tests.test_approval_lifecycle import _host_a_problem, _promote, _tournament


def test_cancelled_tournament_excluded_from_calendar_reconciliation(tmp_path):
    active = _tournament("t1", date_str="2026-09-12", host="A")
    cancelled = _tournament("t2", date_str="2026-09-19", host="A")
    cancelled["cancelled"] = True
    cancelled["cancellation_reason"] = "team_retirement"

    root = _promote(tmp_path, [active, cancelled])

    # A busy calendar event overlapping the cancelled tournament's old slot --
    # if the cancelled tournament were still classified, this would produce a
    # single-overlap "needs confirmation" row for it.
    events = [
        {
            "date": "2026-09-19",
            "start_time": "10:00",
            "end_time": "12:00",
            "source": "test",
        }
    ]
    problem = _host_a_problem(events)

    result = reconcile_calendar_bookings(
        season="2026-2027", root=root, club="A", problem=problem, dry_run=True
    )
    classified_ids = {row["tournament_id"] for row in result["classified"]}
    assert "t2" not in classified_ids, result["classified"]
    assert "t1" in classified_ids
