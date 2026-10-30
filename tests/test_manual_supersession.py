"""Refusal guards for operator-attested supersession of manual booking assertions."""

from __future__ import annotations

import pytest

from tournament_scheduler.application.canonical_season.calendars.manual_supersession import (
    record_calendar_association,
)
from tournament_scheduler.booking.calendar_bookings import MANUAL_BOOKING_ASSERTIONS_KEY
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError

_TOURNAMENT = {"id": "t1"}
_ASSOCIATION = {"id": "calendar_booking:fp:t1", "note": "same match moved"}


def _manual(record_id, *, status="active", booking_status="booked", scope="tournament", tournament_id="t1"):
    return {
        "id": record_id,
        "tournament_id": tournament_id,
        "status": status,
        "booking_status": booking_status,
        "source_scope": scope,
    }


def _record(decisions, supersede_id):
    return record_calendar_association(
        decisions,
        records=[],
        association=_ASSOCIATION,
        tournament=_TOURNAMENT,
        problem=None,
        now="2026-10-21T00:00:00+00:00",
        supersede_manual_assertion_id=supersede_id,
    )


def test_conflicting_negative_assertion_blocks_supersession():
    decisions = {MANUAL_BOOKING_ASSERTIONS_KEY: [_manual("m1"), _manual("m2", booking_status="not-booked")]}

    with pytest.raises(SeasonStateError, match="non-positive manual assertions"):
        _record(decisions, "m1")


def test_multiple_active_positives_block_supersession():
    decisions = {MANUAL_BOOKING_ASSERTIONS_KEY: [_manual("m1"), _manual("m2")]}

    with pytest.raises(SeasonStateError, match="multiple active manual booking assertions"):
        _record(decisions, "m1")


def test_source_level_club_wide_assertion_is_not_superseded():
    decisions = {MANUAL_BOOKING_ASSERTIONS_KEY: [_manual("m1", scope="club_wide")]}

    with pytest.raises(SeasonStateError, match="not tournament-scoped"):
        _record(decisions, "m1")


def test_club_wide_interpretation_negative_blocks_supersession():
    decisions = {
        MANUAL_BOOKING_ASSERTIONS_KEY: [
            _manual("m1", scope="club_wide_interpretation"),
            _manual("m2", scope="club_wide_interpretation", booking_status="not-booked"),
        ]
    }

    with pytest.raises(SeasonStateError, match="non-positive manual assertions"):
        _record(decisions, "m1")


def test_several_club_wide_interpretation_positives_block_supersession():
    decisions = {
        MANUAL_BOOKING_ASSERTIONS_KEY: [
            _manual("m1", scope="club_wide_interpretation"),
            _manual("m2", scope="club_wide_interpretation"),
        ]
    }

    with pytest.raises(SeasonStateError, match="multiple active manual booking assertions"):
        _record(decisions, "m1")


def test_assertion_for_another_tournament_is_not_active_here():
    decisions = {MANUAL_BOOKING_ASSERTIONS_KEY: [_manual("m1", tournament_id="t2")]}

    with pytest.raises(SeasonStateError, match="is not active for tournament t1"):
        _record(decisions, "m1")


def test_no_supersede_id_leaves_manual_assertions_untouched():
    decisions = {MANUAL_BOOKING_ASSERTIONS_KEY: [_manual("m1"), _manual("m2")]}

    updated, superseded = _record(decisions, None)

    assert superseded == []
    assert all(record["status"] == "active" for record in updated[MANUAL_BOOKING_ASSERTIONS_KEY])
