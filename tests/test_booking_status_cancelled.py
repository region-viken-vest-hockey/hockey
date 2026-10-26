"""Booking-status projection must keep cancelled tournaments out of active totals.

Regression for #842: `booking_status_report` iterated every tournament, so a
cancelled tournament with no covering calendar event was projected as an
active ambiguous row and counted in `needs_attention`. Cancelled tournaments
occupy no ice, so they must not count as current booking discrepancies. Their
history stays available under `cancelled_tournaments`.
"""

from __future__ import annotations

from tournament_scheduler.application.canonical_season.store_facade import booking_status_report
from tournament_scheduler.booking.calendar_bookings import (
    CLUB_BOOKING_SOURCE_ASSERTIONS_KEY,
    MANUAL_BOOKING_ASSERTIONS_KEY,
    _club_booking_source_projection,
)
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


def test_cancelled_tournament_does_not_create_club_source_overlap():
    """A cancelled tournament's club-stated window must not flag an active neighbour."""

    source_id = "club_booking_source:Holmen:abc"
    decisions = {
        CLUB_BOOKING_SOURCE_ASSERTIONS_KEY: [
            {
                "id": source_id,
                "status": "active",
                "host_club": "Holmen",
                "source_document": "changes_and_confirmations/2026-27/Holmen.xlsx",
                "source_version": "2026-09-27",
                "source_fingerprint": "abc",
                "asserted_at": "2026-09-27T00:00:00+00:00",
            }
        ],
        MANUAL_BOOKING_ASSERTIONS_KEY: [
            {
                "id": "manual_booking:rvv-0004:1",
                "status": "active",
                "booking_status": "booked",
                "source_scope": "club_wide_interpretation",
                "source_assertion_id": source_id,
                "tournament_id": "rvv-0004",
                "asserted_interval": {"date": "2026-10-10", "start_time": "13:00", "end_time": "14:00"},
                "stated_interval": {"date": "2026-10-10", "start": "13:00", "end": "14:50", "duration_minutes": "110"},
            },
            {
                "id": "manual_booking:rvv-0003:1",
                "status": "active",
                "booking_status": "booked",
                "source_scope": "club_wide_interpretation",
                "source_assertion_id": source_id,
                "tournament_id": "rvv-0003",
                "asserted_interval": {"date": "2026-10-10", "start_time": "14:00", "end_time": "15:00"},
                "stated_interval": {"date": "2026-10-10", "start": "14:00", "end": "14:50", "duration_minutes": "50"},
            },
        ],
    }
    plan = {
        "tournaments": [
            {"id": "rvv-0004", "age_group": "U12", "date": "2026-10-10", "start_time": "13:00", "host_club": "Holmen"},
            {
                "id": "rvv-0003",
                "age_group": "U12",
                "date": "2026-10-10",
                "start_time": "14:00",
                "host_club": "Holmen",
                "cancelled": True,
            },
        ]
    }
    problem = {"ice_time_minutes": {"U12": 60}}

    sources = _club_booking_source_projection(decisions=decisions, rows=[], plan=plan, problem=problem)

    entry = sources[0]
    assert [item["tournament_id"] for item in entry["tournaments"]] == ["rvv-0004"]
    assert entry["overlapping_source_intervals"] == []
    assert entry["requires_operator_review"] is False


def test_calendar_audit_totals_match_active_rows_and_exclude_cancelled(tmp_path):
    """The audit contract attached to the projection agrees with its active rows (#842)."""

    active = _tournament("t1", date_str="2026-09-12", host="A")
    cancelled = _tournament("t2", date_str="2026-09-19", host="A")
    cancelled["cancelled"] = True
    cancelled["cancellation_reason"] = "team_retirement"

    root = _promote(tmp_path, [active, cancelled])
    report = booking_status_report(season="2026-2027", root=root, problem=_host_a_problem([]))

    audit = report["calendar_audit"]
    assert audit["active_total"] == len(report["tournaments"]) == 1
    assert sum(count for name, count in audit["counts"].items() if name != "cancelled_historical") == audit["active_total"]
    assert audit["buckets"]["cancelled_historical"] == ["t2"]
    assert audit["counts"]["cancelled_historical"] == len(report["cancelled_tournaments"]) == 1


def test_cancelled_tournament_association_is_not_a_stale_finding():
    """Findings and booking-status must agree that cancelled history is not active (#842)."""

    from tournament_scheduler.booking.calendar_bookings import (
        CALENDAR_BOOKING_ASSOCIATIONS_KEY,
        association_findings,
    )

    association = {
        "status": "active",
        "tournament_facts": {},
        "tournament_interval": {},
    }
    decisions = {
        CALENDAR_BOOKING_ASSOCIATIONS_KEY: [
            {**association, "id": "calendar_booking:gone-a:t-cancelled", "event_fingerprint": "gone-a", "tournament_id": "t-cancelled"},
            {**association, "id": "calendar_booking:gone-b:t-active", "event_fingerprint": "gone-b", "tournament_id": "t-active"},
        ]
    }
    plan = {
        "tournaments": [
            {"id": "t-cancelled", "host_club": "A", "date": "2026-10-10", "start_time": "13:00", "age_group": "U12", "cancelled": True},
            {"id": "t-active", "host_club": "A", "date": "2026-10-10", "start_time": "14:00", "age_group": "U12"},
        ]
    }

    findings = association_findings(problem={"ice_time_minutes": {"U12": 60}}, plan=plan, decisions=decisions)

    stale = [finding for finding in findings if finding["code"] == "stale_calendar_booking_association"]
    assert [finding["tournament_id"] for finding in stale] == ["t-active"]
