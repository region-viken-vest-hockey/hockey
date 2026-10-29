"""A club can state only a start time without inventing an end (#869 item 4).

A club-wide or manual source may explicitly state the booking start while
leaving the end unstated. The start is then the source's explicit authority,
and the end stays proposal-derived. The manual record keeps exactly what was
stated, so no end is fabricated. The derived end is never persisted as
accepted source evidence, so it keeps following the proposal. A start-only
statement cannot create a planning-floor exception: a short booking needs an
explicit stated window.
"""

from __future__ import annotations

from tournament_scheduler.application.canonical_season.store_facade import (
    booking_status_report,
    load_decisions,
    load_schedule,
    set_manual_booking_assertion,
)
from tournament_scheduler.booking.calendar_bookings import MANUAL_BOOKING_ASSERTIONS_KEY
from tests.test_calendar_interval_adoption_e2e import _planned_problem, _tournament_row
from tests.test_booking_assessment import _promote, _tournament


def _proposal(minutes: int):
    problem = _planned_problem([], complete=True)
    problem["ice_time_minutes"] = {"U10": minutes}
    return problem


def _state_start(root, problem):
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:kongsberg",
        stated_date="2026-09-12",
        stated_start="13:00",
        problem=problem,
    )


def test_start_only_manual_assertion_is_recorded_without_fabricating_an_end(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="15:00")])

    _state_start(root, _proposal(120))

    record = next(
        r for r in load_decisions("2026-2027", root=root)[MANUAL_BOOKING_ASSERTIONS_KEY] if r["tournament_id"] == "t1"
    )
    assert record["stated_interval"] == {"date": "2026-09-12", "start": "13:00"}


def test_start_only_statement_moves_the_effective_start_and_keeps_the_proposed_duration(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="15:00")])
    problem = _proposal(120)

    _state_start(root, problem)

    row = _tournament_row(booking_status_report(season="2026-2027", root=root, problem=problem), "t1")
    assert row["canonical_interval"]["start_time"] == "13:00"
    assert row["canonical_interval"]["end_time"] == "15:00"
    assert row["explicit_interval_fields"] == ["date", "start"]
    assert load_schedule("2026-2027", root=root)["plan"]["tournaments"][0]["start_time"] == "13:00"


def test_derived_end_does_not_pin_the_effective_interval_after_the_proposal_changes(tmp_path):
    """The derived end is a proposal assumption: a changed duration must reach the canonical interval."""
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="15:00")])
    _state_start(root, _proposal(120))

    row = _tournament_row(booking_status_report(season="2026-2027", root=root, problem=_proposal(150)), "t1")

    assert row["canonical_interval"]["start_time"] == "13:00"
    assert row["canonical_interval"]["end_time"] == "15:30"
    record = load_decisions("2026-2027", root=root)[MANUAL_BOOKING_ASSERTIONS_KEY][0]
    assert "end" not in record["stated_interval"]
