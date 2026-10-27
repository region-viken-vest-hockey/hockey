"""Full booking-status projection for a club whose calendar omits its bookings (#853).

Sandefjord Penguins does not enter its miniputt tournaments in its external
calendar. An operator-accepted booking with no calendar event must keep its
authority and booked state through the real projection, and the audit must not
ask for a candidate resolution or a fresh approval for it.
"""

from __future__ import annotations

from tournament_scheduler.booking.calendar_audit import (
    AUDIT_ACCEPTED_UNCHECKABLE,
    AUDIT_ACTION_NONE,
)
from tournament_scheduler.booking.calendar_bookings import (
    MANUAL_BOOKING_ASSERTIONS_KEY,
    OPERATIONAL_BOOKED,
    booking_status_report,
    tournament_booking_facts,
)
from tournament_scheduler.booking.calendar_audit import AUDIT_ACCEPTED_IDENTITY_REVIEW, AUDIT_UNCONFIRMED_AMBIGUOUS

_TOURNAMENT = {
    "id": "rvv-0024",
    "age_group": "U10",
    "date": "2026-10-10",
    "start_time": "13:00",
    "host_club": "Sandefjord Penguins",
    "arena": "Bugården ishall",
}


def _projection(*, manual_status="booked", extra_problem=None):
    tournament = dict(_TOURNAMENT)
    assertion = {
        "id": "manual_booking:rvv-0024:1",
        "status": "active",
        "booking_status": manual_status,
        "source_scope": "tournament",
        "tournament_id": "rvv-0024",
        "tournament_facts": tournament_booking_facts(tournament),
        "asserted_interval": {
            "date": "2026-10-10",
            "start_time": "13:00",
            "duration_minutes": "120",
            "end_time": "15:00",
        },
        "stated_interval": {"date": "2026-10-10", "start": "13:00", "end": "15:00", "duration_minutes": "120"},
        "asserted_at": "2026-10-01T00:00:00+00:00",
    }
    problem = {
        "ice_time_minutes": {"U10": 120},
        "club_calendar_status": {"Sandefjord Penguins": "known"},
        "club_busy_intervals": {"Sandefjord Penguins": []},
    }
    problem.update(extra_problem or {})
    return booking_status_report(
        problem=problem,
        plan={"tournaments": [tournament]},
        decisions={MANUAL_BOOKING_ASSERTIONS_KEY: [assertion]},
    )


def _row(report, tournament_id="rvv-0024"):
    return next(row for row in report["tournaments"] if row["tournament_id"] == tournament_id)


def test_accepted_sandefjord_booking_keeps_authority_when_calendar_has_no_event():
    report = _projection()
    row = _row(report)

    assert row["authority"] == "manual_club_confirmation"
    assert row["operational_state"] == OPERATIONAL_BOOKED
    assert row["calendar_publishes_bookings"] is False
    assert row["actionable_candidate_count"] == 0
    assert row["needs_attention"] is False

    # This fixture has no trusted complete calendar window, so the audit reaches
    # the bucket through the uncheckable branch. The classifier branch that
    # reads calendar_publishes_bookings is covered in test_calendar_audit_contract.
    audit = report["calendar_audit"]
    assert audit["buckets"][AUDIT_ACCEPTED_UNCHECKABLE] == ["rvv-0024"]
    assert audit["buckets"][AUDIT_ACCEPTED_IDENTITY_REVIEW] == []
    assert audit["buckets"][AUDIT_UNCONFIRMED_AMBIGUOUS] == []
    assert audit["actions"]["rvv-0024"] == AUDIT_ACTION_NONE


def test_standing_ice_use_block_does_not_contest_accepted_sandefjord_booking():
    # Live shape: a generic standing ice-use block elsewhere in the window. It
    # must stay context, so the accepted booking is not asked to be resolved.
    standing = {
        "club": "Sandefjord Penguins",
        "date": "2026-10-17",
        "start": "08:00",
        "end": "10:00",
        "availability": "fixed_busy",
        "calendar_event": "Sandefjord Penguins fast istid (opptatt utenom tildelt helgevindu)",
        "arena": "Bugården ishall",
        "fingerprint": "standing-block",
    }
    report = _projection(extra_problem={"club_busy_intervals": {"Sandefjord Penguins": [standing]}})

    row = _row(report)
    assert row["authority"] == "manual_club_confirmation"
    assert row["operational_state"] == OPERATIONAL_BOOKED
    assert row["actionable_candidate_count"] == 0
    audit = report["calendar_audit"]
    assert audit["buckets"][AUDIT_ACCEPTED_UNCHECKABLE] == ["rvv-0024"]
    assert audit["actions"]["rvv-0024"] == AUDIT_ACTION_NONE
