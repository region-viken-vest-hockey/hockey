"""Manual booking assertions record a calendar baseline and may be pinned (#860).

A manual assertion is a temporary bridge until the calendar catches up. At the
time it is made it records the one trusted calendar event that overlaps the
tournament (``calendar_baseline``), or ``None`` when there is none or several.
The baseline is inert evidence: it never changes what the assertion means. A
``--pin`` with a reason marks the rare assertion meant to outlive a differing
calendar. Hermetic fixtures only; the live season is never touched.
"""

from __future__ import annotations

import pytest

from tournament_scheduler.application.canonical_season.store_facade import (
    SeasonStateError,
    set_manual_booking_assertion,
)
from tournament_scheduler.booking.calendar_bookings import event_fingerprint
from tests.test_approval_lifecycle import _host_a_problem, _promote, _tournament


def _event(start="10:00", end="12:00", date="2026-09-12", title="Miniputt U10"):
    return {
        "date": date,
        "start": start,
        "end": end,
        "availability": "fixed_busy",
        "calendar_event": title,
    }


def _assert(root, problem, **overrides):
    kwargs = {
        "season": "2026-2027",
        "root": root,
        "tournament_id": "t1",
        "booking_status": "booked",
        "reference": "email:club-2026-09-27",
        "actor": "tester",
        "problem": problem,
    }
    kwargs.update(overrides)
    return set_manual_booking_assertion(**kwargs)["assertion"]


def test_baseline_records_the_single_overlapping_event(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    event = _event()
    assertion = _assert(root, _host_a_problem([event]))

    baseline = assertion["calendar_baseline"]
    assert baseline["date"] == "2026-09-12"
    assert (baseline["start"], baseline["end"]) == ("10:00", "12:00")
    assert baseline["event_fingerprint"] == event_fingerprint({**event, "club": "A"})
    assert baseline["resources"] == []
    assert baseline["source_calendar_status"] == "known"
    assert baseline["calendar_fingerprint"]
    assert "pinned" not in assertion


@pytest.mark.parametrize(
    "events",
    [
        [],
        [_event(date="2026-09-13")],
        [_event(start="10:00", end="11:00"), _event(start="11:00", end="12:00", title="Second")],
    ],
    ids=["no_event", "other_date", "two_overlapping_events"],
)
def test_baseline_is_none_without_exactly_one_overlapping_event(tmp_path, events):
    root = _promote(tmp_path, [_tournament("t1")])
    assertion = _assert(root, _host_a_problem(events))

    assert assertion["calendar_baseline"] is None


def test_baseline_does_not_change_the_assertion_meaning(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    with_event = _assert(root, _host_a_problem([_event()]), dry_run=True)
    without_event = _assert(root, _host_a_problem([]), dry_run=True)

    for key in ("booking_status", "authority", "asserted_interval", "stated_interval", "tournament_facts"):
        assert with_event[key] == without_event[key]


def test_pin_requires_a_reason_and_a_reason_requires_pin(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([_event()])

    with pytest.raises(SeasonStateError, match="requires --pin-reason"):
        _assert(root, problem, pin=True)
    with pytest.raises(SeasonStateError, match="requires --pin"):
        _assert(root, problem, pin_reason="club insists")


def test_pin_with_reason_is_recorded(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    assertion = _assert(root, _host_a_problem([_event()]), pin=True, pin_reason="  Club keeps this slot  ")

    assert assertion["pinned"] is True
    assert assertion["pin_reason"] == "Club keeps this slot"


def test_repeat_is_idempotent_even_if_the_calendar_changed_but_pin_change_is_not(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    first = _assert(root, _host_a_problem([_event()]))

    repeat = set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        reference="email:club-2026-09-27",
        actor="tester",
        problem=_host_a_problem([_event(start="10:00", end="12:30")]),
    )
    assert repeat["idempotent"] is True
    assert repeat["assertion"]["id"] == first["id"]
    assert repeat["assertion"]["calendar_baseline"] == first["calendar_baseline"]

    with pytest.raises(SeasonStateError, match="already has an active manual booking assertion"):
        _assert(root, _host_a_problem([_event()]), pin=True, pin_reason="now outlive the calendar")
