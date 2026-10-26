"""Booking-status projection must keep cancelled tournaments out of active totals.

Regression for #842: `booking_status_report` iterated every tournament, so a
cancelled tournament with no covering calendar event was projected as an
active ambiguous row and counted in `needs_attention`. Cancelled tournaments
occupy no ice, so they must not count as current booking discrepancies. Their
history stays available under `cancelled_tournaments`.
"""

from __future__ import annotations

from tournament_scheduler.application.canonical_season.store_facade import booking_status_report
from tests.test_approval_lifecycle import _host_a_problem, _promote, _tournament


def test_cancelled_tournament_not_counted_as_active_booking_discrepancy(tmp_path):
    active = _tournament("t1", date_str="2026-09-12", host="A")
    cancelled = _tournament("t2", date_str="2026-09-19", host="A")
    cancelled["cancelled"] = True
    cancelled["cancellation_reason"] = "team_retirement"

    root = _promote(tmp_path, [active, cancelled])
    problem = _host_a_problem([])

    report = booking_status_report(season="2026-2027", root=root, problem=problem)

    assert [row["tournament_id"] for row in report["tournaments"]] == ["t1"]
    assert report["counts"]["needs_attention"] == 0
    assert all(item["tournament_id"] != "t2" for item in report["manual_booking_queue"])
    assert [row["tournament_id"] for row in report["cancelled_tournaments"]] == ["t2"]
    assert report["cancelled_tournaments"][0]["cancellation_reason"] == "team_retirement"
