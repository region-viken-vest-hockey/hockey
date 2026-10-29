"""An explicit manual interval transitions cleanly to newer calendar reality (#869 item 4).

A club states an explicit window (13:00-15:35). A later accepted calendar event
shows the real booking, 13:00-16:00. The calendar becomes the effective
authority and carries the new exact duration. The superseded manual assertion
keeps its stated window unchanged as history. No manual window is synthesized,
and no stale manual-derived override survives.
"""

from __future__ import annotations

from tournament_scheduler.application.canonical_season.store_facade import (
    booking_status_report,
    confirm_calendar_booking,
    load_decisions,
    set_manual_booking_assertion,
)
from tournament_scheduler.booking.calendar_bookings import (
    MANUAL_BOOKING_ASSERTIONS_KEY,
    event_fingerprint,
)
from tournament_scheduler.season.ice_time_overrides import active_overrides
from tests.test_calendar_interval_adoption_e2e import _planned_problem, _tournament_row
from tests.test_booking_assessment import _event, _promote, _tournament


def _manual(decisions, tournament_id="t1"):
    return next(r for r in decisions[MANUAL_BOOKING_ASSERTIONS_KEY] if r["tournament_id"] == tournament_id)


def test_explicit_manual_window_transitions_to_calendar_authority_and_keeps_history(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="13:00")])
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:kongsberg",
        stated_date="2026-09-12",
        stated_start="13:00",
        stated_end="15:35",
        problem=_planned_problem([], complete=True),
    )
    manual_id = _manual(load_decisions("2026-2027", root=root))["id"]
    stated_before = dict(_manual(load_decisions("2026-2027", root=root))["stated_interval"])
    event = _event("2026-09-12", "13:00", "16:00", title="TURNERING U10", club="A")
    observed = _planned_problem([event], complete=True)

    confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fingerprint({**event, "club": "A"}),
        tournament_id="t1",
        actor="operator",
        note="calendar shows the real booking",
        problem=observed,
        supersede_manual_assertion_id=manual_id,
    )

    decisions = load_decisions("2026-2027", root=root)
    superseded = _manual(decisions)
    assert superseded["status"] == "superseded"
    assert dict(superseded["stated_interval"]) == stated_before
    assert active_overrides(decisions) == {"t1": 180}
    row = _tournament_row(booking_status_report(season="2026-2027", root=root, problem=observed), "t1")
    assert row["accepted_evidence"]["association_id"].startswith("calendar_booking:")
    assert row["accepted_evidence"]["accepted_interval"] == {"date": "2026-09-12", "start": "13:00", "end": "16:00"}
    assert row["canonical_interval"]["end_time"] == "16:00"
    assert row["interval_comparison"]["status"] == "matches"


def _ice(minutes, events=()):
    problem = _planned_problem(list(events), complete=True)
    problem["ice_time_minutes"] = {"U10": minutes}
    return problem


def test_calendar_event_supplies_the_end_for_a_start_only_record_and_keeps_its_start_provenance(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="15:00")])
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:kongsberg",
        stated_date="2026-09-12",
        stated_start="13:00",
        problem=_ice(120),
    )
    manual_id = _manual(load_decisions("2026-2027", root=root))["id"]
    event = _event("2026-09-12", "13:00", "16:00", title="TURNERING U10", club="A")
    observed = _ice(120, [event])

    confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fingerprint({**event, "club": "A"}),
        tournament_id="t1",
        actor="operator",
        note="calendar supplies the end",
        problem=observed,
        supersede_manual_assertion_id=manual_id,
    )

    decisions = load_decisions("2026-2027", root=root)
    assert _manual(decisions)["status"] == "superseded"
    assert dict(_manual(decisions)["stated_interval"]) == {"date": "2026-09-12", "start": "13:00"}
    assert len(decisions[MANUAL_BOOKING_ASSERTIONS_KEY]) == 1
    assert active_overrides(decisions) == {"t1": 180}
    row = _tournament_row(booking_status_report(season="2026-2027", root=root, problem=observed), "t1")
    assert row["canonical_interval"]["start_time"] == "13:00"
    assert row["canonical_interval"]["end_time"] == "16:00"
    assert row["interval_comparison"]["status"] == "matches"
    assert row["accepted_evidence"]["association_id"].startswith("calendar_booking:")
    assert row["accepted_evidence"]["accepted_interval"] == {"date": "2026-09-12", "start": "13:00", "end": "16:00"}


def test_club_wide_start_only_acceptance_keeps_scope_and_moves_only_the_start(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="15:00")])

    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:club-wide",
        source_scope="club_wide_interpretation",
        stated_date="2026-09-12",
        stated_start="13:00",
        problem=_ice(120),
    )

    decisions = load_decisions("2026-2027", root=root)
    record = _manual(decisions)
    assert record["source_scope"] == "club_wide_interpretation"
    assert record["stated_interval"] == {"date": "2026-09-12", "start": "13:00"}
    row = _tournament_row(booking_status_report(season="2026-2027", root=root, problem=_ice(120)), "t1")
    assert row["canonical_interval"]["start_time"] == "13:00"
    assert row["canonical_interval"]["end_time"] == "15:00"
    assert row["explicit_interval_fields"] == ["date", "start"]
