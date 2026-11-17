"""Complete deterministic projection parity across the booking-owner extraction.

Digests were captured from the pre-extraction facade using these hermetic inputs.
They cover full serialized rows, actions, counts, provenance and fingerprints;
focused booking tests explain the individual domain rules. An intentional output
contract change needs review of the complete projection before updating a digest.
"""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from tournament_scheduler.booking import calendar_bookings as booking
from tests.test_booking_assessment import _event, _plan, _problem, _tournament


EXPECTED = {
    "ambiguous": "4cdf70ccc87c6580dd94827789aa52d55cf4c85f959f9fa63714fc34a43a1f09",
    "associated": "9ffd00a4fb36103f183e66aa0bbb99c42fcb3e8478bd45cb39b75748d102a46a",
    "cancelled": "b504200d75cdb86edd76305bcbe5070d3ee98173e6aaf63df66f238beab871de",
    "changed_interval": "c897494dff811837dc931bc88ab0f1fd6a84ce701c5d6fd03a78c866f430ab2c",
    "complete_empty": "b6ed83ef6ba37b962c043f159ea789236aa38a118d4471b5d2d9e81bfa7f2eb4",
    "identity_drift": "89bd4dae2b58ac4bec5da090482f70b90c73cc6b5ceeeb1c5336f9af7beceaef",
    "manual": "74831b0701cf0cfe01641022270b257c1f1172afa4ec15f1612de66e2b84325f",
    "partial": "880d3ed2cf9623f2b01386503f8024d4fc82f0e986104a74ba5fdec62433981a",
    "unknown": "e9be847eb86dbe2de72ac19aa99c1c073e537b7df5418866a560d68d2047b36b",
}


def _inputs(case):
    tournament = _tournament("active")
    cancelled = {**_tournament("cancelled"), "cancelled": True}
    plan = _plan([tournament, cancelled])
    event = _event("2026-09-12", "10:00", "12:00")
    events = [] if case in ("unknown", "complete_empty") else [event]
    if case == "ambiguous":
        events.append(_event("2026-09-13", "10:00", "12:00"))
    problem = _problem(
        events,
        status="unknown" if case == "unknown" else "known",
        complete=case != "partial",
    )
    decisions = {}
    if case == "manual":
        source = booking.new_club_booking_source_record(
            club="A", source_document="club-email", source_version="1",
            actor="tester", asserted_at="2026-09-01", source_revision="revision",
        )
        manual = booking.new_manual_assertion_record(
            tournament=tournament, booking_status="booked", problem=problem,
            actor="tester", note="club confirmed", reference="email",
            source_scope="club_wide_interpretation",
            stated_interval={"start": "10:00", "end": "12:00"},
            asserted_at="2026-09-01", source_revision="revision",
            source_assertion_id=source["id"],
        )
        decisions[booking.CLUB_BOOKING_SOURCE_ASSERTIONS_KEY] = [source]
        decisions[booking.MANUAL_BOOKING_ASSERTIONS_KEY] = [manual]
    if case in ("associated", "identity_drift", "changed_interval", "cancelled"):
        record = booking.new_association_record(
            event=event, tournament=tournament, actor="tester", note="confirmed",
            source_revision="revision", problem=problem,
        )
        record["created_at"] = "2026-09-01T00:00:00+00:00"
        decisions[booking.CALENDAR_BOOKING_ASSOCIATIONS_KEY] = [record]
    if case == "identity_drift":
        event["calendar_event"] = "Retitled miniputt U10"
    if case == "changed_interval":
        event["end"] = "12:10"
    if case == "cancelled":
        tournament["cancelled"] = True
    return problem, plan, decisions


@pytest.mark.parametrize("case, expected", EXPECTED.items())
def test_complete_booking_projection_preserves_characterized_output(case, expected):
    problem, plan, decisions = _inputs(case)
    original = copy.deepcopy((problem, plan, decisions))
    projection = {
        "assessment": booking.booking_assessment(
            problem=problem, plan=plan, decisions=decisions,
            canonical_state_revision="revision",
        ),
        "status": booking.booking_status_report(
            problem=problem, plan=plan, decisions=decisions,
        ),
        "host_status": booking.booking_status_report(
            problem=problem, plan=plan, decisions=decisions, host_club="A",
        ),
        "association_findings": booking.association_findings(
            problem=problem, plan=plan, decisions=decisions,
        ),
    }
    assert (problem, plan, decisions) == original
    serialized = json.dumps(projection, sort_keys=True)
    assert hashlib.sha256(serialized.encode()).hexdigest() == expected


@pytest.mark.parametrize(
    "start,end,message",
    [
        (None, "12:00", "requires --stated-start"),
        ("bad", None, "Invalid stated start time"),
        ("12:00", "12:00", "end must be after start"),
        ("12:00", "11:00", "end must be after start"),
    ],
)
def test_interval_validation_errors_remain_at_the_existing_facade(start, end, message):
    with pytest.raises(ValueError, match=message):
        booking.validate_stated_interval(start, end)
