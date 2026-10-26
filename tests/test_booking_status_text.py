"""Host-club operator text reports the calendar audit from the same projection (#842)."""

from __future__ import annotations

from tournament_scheduler.booking.calendar_audit import calendar_audit_summary
from tournament_scheduler.cli.booking_status_text import format_host_club_booking_status


def _report(rows, cancelled=()):
    return {
        "host_club": "Holmen",
        "season": "2026-2027",
        "canonical_state_revision": "rev",
        "tournaments": rows,
        "counts": {"needs_attention": 0},
        "manual_booking_queue": [],
        "unresolved_calendar_events": [],
        "calendar_audit": calendar_audit_summary(rows, cancelled_history=list(cancelled)),
        "cancelled_tournaments": list(cancelled),
    }


def test_text_reports_audit_counts_and_material_mismatch_detail():
    rows = [
        {
            "tournament_id": "rvv-0029",
            "operational_state": "booked",
            "status": "manually_booked",
            "authority": "manual_club_confirmation",
            "conflict": False,
            "follow_up_reasons": [],
            "interval_comparison": {
                "status": "differs",
                "planned": {"date": "2026-10-16", "start": "10:30", "end": "13:00"},
                "observed": {"date": "2026-10-16", "start": "11:00", "end": "13:30"},
                "same_date": True,
                "start_delta_minutes": 30,
                "end_delta_minutes": 30,
            },
        }
    ]

    text = format_host_club_booking_status(_report(rows, cancelled=[{"tournament_id": "rvv-0027"}]))

    assert "Calendar audit: 1 active" in text
    assert "material mismatch: 1" in text
    assert "cancelled/historical (outside active totals): 1" in text
    assert "rvv-0029" in text.split("Calendar audit", 1)[1]
    assert "plan 2026-10-16 10:30-13:00 | calendar 2026-10-16 11:00-13:30" in text
