"""An accepted booking keeps its authority when its observed event differs (#859 review).

An exact same-day event that covers the slot can be the observation while the
accepted booking's end time differs. The difference is reported as a material
mismatch with an explicit approval action. Authority, state and the accepted
interval are not rewritten by observation selection.
"""

from __future__ import annotations

from tournament_scheduler.booking.calendar_audit import AUDIT_ACTION_CHANGE_APPROVAL, AUDIT_MATERIAL_MISMATCH
from tournament_scheduler.booking.calendar_bookings import (
    MANUAL_BOOKING_ASSERTIONS_KEY,
    OPERATIONAL_BOOKED,
    booking_status_report,
    tournament_booking_facts,
)

_CLUB = "Jar"
_TOURNAMENT = {
    "id": "rvv-0053",
    "age_group": "U12",
    "date": "2026-11-15",
    "start_time": "15:00",
    "host_club": _CLUB,
    "arena": "Askerhallen",
}


def _event(date, start, end, fingerprint):
    return {
        "club": _CLUB,
        "date": date,
        "start": start,
        "end": end,
        "availability": "fixed_busy",
        "calendar_event": "U12 Serierunde",
        "arena": "Askerhallen",
        "fingerprint": fingerprint,
    }


def _report():
    assertion = {
        "id": "manual_booking:rvv-0053:1",
        "status": "active",
        "booking_status": "booked",
        "source_scope": "tournament",
        "tournament_id": "rvv-0053",
        "tournament_facts": tournament_booking_facts(_TOURNAMENT),
        "asserted_interval": {"date": "2026-11-15", "start_time": "15:00", "duration_minutes": "100", "end_time": "16:40"},
        "stated_interval": {"date": "2026-11-15", "start": "15:00", "end": "16:40", "duration_minutes": "100"},
        "asserted_at": "2026-11-01T00:00:00+00:00",
    }
    events = [_event("2026-11-15", "15:00", "17:00", "exact")]
    events += [_event(f"2026-11-{day:02d}", "15:00", "16:40", f"far-{day}") for day in (22, 29, 6)]
    problem = {
        "ice_time_minutes": {"U12": 100},
        "club_calendar_status": {_CLUB: "known"},
        "club_busy_intervals": {_CLUB: events},
    }
    return booking_status_report(
        problem=problem,
        plan={"tournaments": [dict(_TOURNAMENT)]},
        decisions={MANUAL_BOOKING_ASSERTIONS_KEY: [assertion]},
    )


def test_exact_end_time_difference_keeps_accepted_authority_and_is_a_visible_mismatch():
    report = _report()
    row = next(item for item in report["tournaments"] if item["tournament_id"] == "rvv-0053")

    # Authority and state are the accepted booking's, unchanged by observation selection.
    assert row["authority"] == "manual_club_confirmation"
    assert row["operational_state"] == OPERATIONAL_BOOKED
    assert row["accepted_evidence"]["accepted_interval"] == {"date": "2026-11-15", "start": "15:00", "end": "16:40"}
    # The observed event differs by its end time: reported, not silently accepted.
    comparison = row["interval_comparison"]
    assert comparison["status"] == "differs"
    assert comparison["end_delta_minutes"] == 20

    audit = report["calendar_audit"]
    assert audit["buckets"][AUDIT_MATERIAL_MISMATCH] == ["rvv-0053"]
    assert audit["actions"]["rvv-0053"] == AUDIT_ACTION_CHANGE_APPROVAL
