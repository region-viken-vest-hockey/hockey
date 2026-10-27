"""End-to-end interval adoption for a club-wide booking confirmation (#860).

A club confirms a tournament was booked but states no window. The planner's
15:00-16:40 is then a proposal. A same-day calendar event shows 15:00-17:00.
The operator accepts that event through the canonical confirm path. The audit
must report the proposal before acceptance and a confirmed match afterwards,
and the planned window must stay in provenance. A repeat audit must not churn.
"""

from __future__ import annotations

from tournament_scheduler.application.canonical_season.store_facade import (
    booking_status_report,
    confirm_calendar_booking,
    load_decisions,
    load_schedule,
    set_manual_booking_assertion,
)
from tournament_scheduler.booking.calendar_bookings import (
    MANUAL_BOOKING_ASSERTIONS_KEY,
    event_fingerprint,
    manual_assertion_stale_reasons,
)
from tests.test_booking_assessment import _event, _problem, _promote, _tournament


def _planned_problem(events, *, complete):
    problem = _problem(events, complete=complete)
    # 100 minutes of ice gives the planned 15:00-16:40 window.
    problem["ice_time_minutes"] = {"U10": 100}
    return problem


def _tournament_row(report, tournament_id):
    return next(row for row in report["tournaments"] if row["tournament_id"] == tournament_id)


def _bucket_of(report, tournament_id):
    for bucket, ids in report["calendar_audit"]["buckets"].items():
        if tournament_id in ids:
            return bucket
    return None


def test_club_confirmation_adopts_longer_calendar_booking_and_then_matches(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="15:00")])
    empty = _planned_problem([], complete=True)
    # Record the club's confirmation against an empty calendar, since a
    # conflicting calendar refuses the write. The stated window stays empty.
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:kongsberg",
        problem=empty,
    )
    event = _event("2026-09-12", "15:00", "17:00", title="TURNERING U10", club="A")
    observed = _planned_problem([event], complete=True)

    before = booking_status_report(season="2026-2027", root=root, problem=observed)
    assert _bucket_of(before, "t1") == "proposed_interval_correction"
    assert before["calendar_audit"]["actions"]["t1"] == "confirm_calendar_interval"
    assert before["calendar_audit"]["material_mismatches"] == []

    result = confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fingerprint({**event, "club": "A"}),
        tournament_id="t1",
        actor="operator",
        note="accepted host calendar booking",
        problem=observed,
    )

    # The planned window is kept as provenance, and the accepted interval is
    # the calendar's 15:00-17:00 (120 minutes).
    alignment = result["interval_alignment"]
    assert alignment["previous_interval"]["end_time"] == "16:40"
    assert alignment["previous_interval"]["duration_minutes"] == "100"
    assert alignment["accepted_calendar_interval"]["end_time"] == "17:00"
    assert alignment["accepted_calendar_interval"]["duration_minutes"] == 120
    overrides = load_decisions("2026-2027", root=root)["ice_time_minutes_overrides"]
    assert overrides[-1]["minutes"] == 120

    after = booking_status_report(season="2026-2027", root=root, problem=observed)
    assert _bucket_of(after, "t1") == "confirmed_matching"
    assert after["calendar_audit"]["actions"]["t1"] == "none"
    assert after["calendar_audit"]["material_mismatches"] == []
    assert _tournament_row(after, "t1")["interval_comparison"]["status"] == "matches"


def test_repeat_audit_after_adoption_is_stable_and_creates_no_new_work(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="15:00")])
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:kongsberg",
        problem=_planned_problem([], complete=True),
    )
    event = _event("2026-09-12", "15:00", "17:00", title="TURNERING U10", club="A")
    observed = _planned_problem([event], complete=True)
    confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fingerprint({**event, "club": "A"}),
        tournament_id="t1",
        actor="operator",
        note="accepted host calendar booking",
        problem=observed,
    )

    first = booking_status_report(season="2026-2027", root=root, problem=observed)
    second = booking_status_report(season="2026-2027", root=root, problem=observed)

    assert first["calendar_audit"]["buckets"] == second["calendar_audit"]["buckets"]
    assert second["calendar_audit"]["counts"]["proposed_interval_correction"] == 0
    assert second["calendar_audit"]["material_mismatches"] == []
    assert second["tournaments"] == first["tournaments"]


def test_explicitly_stated_end_is_not_silently_superseded_by_calendar_acceptance(tmp_path):
    # The club stated 16:40 and the calendar shows 17:00. The operator's accepted
    # calendar booking is recorded, but the stated end stays visible for review.
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="15:00")])
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:kongsberg",
        stated_start="15:00",
        stated_end="16:40",
        problem=_planned_problem([], complete=True),
    )
    event = _event("2026-09-12", "15:00", "17:00", title="TURNERING U10", club="A")
    observed = _planned_problem([event], complete=True)
    confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fingerprint({**event, "club": "A"}),
        tournament_id="t1",
        actor="operator",
        note="accepted host calendar booking",
        problem=observed,
    )

    report = booking_status_report(season="2026-2027", root=root, problem=observed)
    row = _tournament_row(report, "t1")

    assert "tournament_end_time_changed" in row["stale_reasons"]
    assert _bucket_of(report, "t1") != "confirmed_matching"


def test_later_canonical_change_not_covered_by_acceptance_stays_stale(tmp_path):
    # Once the calendar acceptance is in place, a further canonical start change
    # that no accepted booking covers keeps the club's confirmation stale.
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="15:00")])
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:kongsberg",
        problem=_planned_problem([], complete=True),
    )
    event = _event("2026-09-12", "15:00", "17:00", title="TURNERING U10", club="A")
    observed = _planned_problem([event], complete=True)
    confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fingerprint({**event, "club": "A"}),
        tournament_id="t1",
        actor="operator",
        note="accepted host calendar booking",
        problem=observed,
    )
    decisions = load_decisions("2026-2027", root=root)
    assertion = next(a for a in decisions[MANUAL_BOOKING_ASSERTIONS_KEY] if a["tournament_id"] == "t1")
    tournament = dict(next(t for t in load_schedule("2026-2027", root=root)["plan"]["tournaments"] if t["id"] == "t1"))
    tournament["start_time"] = "15:30"

    reasons = manual_assertion_stale_reasons(assertion, problem=observed, tournament=tournament)

    assert "tournament_start_time_changed" in reasons
