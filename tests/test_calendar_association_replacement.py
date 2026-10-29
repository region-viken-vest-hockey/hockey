"""Confirming an edited calendar event replaces its established association (#869).

A confirmed calendar event that is edited gets a new fingerprint. Confirming the
new fingerprint for the same tournament booking must retire the old association
as superseded history, leave exactly one active association, and repoint the
accepted evidence at the replacement. A genuinely different event must refuse.
"""

from __future__ import annotations

import pytest

from tournament_scheduler.application.canonical_season.store_facade import (
    SeasonStateError,
    booking_status_report,
    load_decisions,
)
from tournament_scheduler.booking.calendar_bookings import (
    ACTIVE,
    CALENDAR_BOOKING_ASSOCIATIONS_KEY,
)
from tests.test_accepted_short_booking import _confirm, _event, _problem, _promote, _teams, _tournament

START = "10:00"
DATE = "2026-09-12"


def _setup(tmp_path):
    teams = _teams("U10", ("A", "B", "C", "D"))
    tournament = _tournament("asker-u10", age_group="U10", start=START, date_str=DATE, teams=teams)
    root = _promote(tmp_path, [tournament])
    return root, teams


def _active_for(decisions, tournament_id):
    return [
        record
        for record in decisions.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or []
        if record.get("tournament_id") == tournament_id and record.get("status", ACTIVE) == ACTIVE
    ]


def test_edited_event_confirmation_retires_established_association(tmp_path):
    root, teams = _setup(tmp_path)
    original = _event(date=DATE, start=START, end="12:00", title="TURNERING U10")
    _confirm(root, _problem(age_group="U10", ice=120, teams=teams, events=[original], parallel_games=2), original, "asker-u10")
    edited = _event(date=DATE, start=START, end="13:00", title="TURNERING U10")

    _confirm(root, _problem(age_group="U10", ice=180, teams=teams, events=[edited], parallel_games=2), edited, "asker-u10")

    decisions = load_decisions("2026-2027", root=root)
    active = _active_for(decisions, "asker-u10")
    retired = [
        record
        for record in decisions[CALENDAR_BOOKING_ASSOCIATIONS_KEY]
        if record.get("tournament_id") == "asker-u10" and record.get("status") != ACTIVE
    ]
    assert len(active) == 1
    assert active[0]["end"] == "13:00"
    assert [record["status"] for record in retired] == ["superseded"]
    assert retired[0]["superseded_by"] == active[0]["id"]

    row = next(
        r
        for r in booking_status_report(season="2026-2027", root=root, problem=_problem(age_group="U10", ice=180, teams=teams, events=[edited], parallel_games=2))["tournaments"]
        if r["tournament_id"] == "asker-u10"
    )
    assert row["accepted_evidence"]["association_id"] == active[0]["id"]
    assert row["interval_comparison"]["status"] == "matches"


def test_repeating_the_replacement_is_idempotent(tmp_path):
    root, teams = _setup(tmp_path)
    original = _event(date=DATE, start=START, end="12:00", title="TURNERING U10")
    _confirm(root, _problem(age_group="U10", ice=120, teams=teams, events=[original], parallel_games=2), original, "asker-u10")
    edited = _event(date=DATE, start=START, end="13:00", title="TURNERING U10")
    problem = _problem(age_group="U10", ice=180, teams=teams, events=[edited], parallel_games=2)
    _confirm(root, problem, edited, "asker-u10")
    before = load_decisions("2026-2027", root=root)

    _confirm(root, problem, edited, "asker-u10")

    after = load_decisions("2026-2027", root=root)
    assert _active_for(after, "asker-u10") == _active_for(before, "asker-u10")
    assert len(after[CALENDAR_BOOKING_ASSOCIATIONS_KEY]) == len(before[CALENDAR_BOOKING_ASSOCIATIONS_KEY])


def test_genuinely_different_event_refuses_automatic_replacement(tmp_path):
    root, teams = _setup(tmp_path)
    established = _event(date=DATE, start=START, end="12:00", title="TURNERING U10")
    _confirm(root, _problem(age_group="U10", ice=120, teams=teams, events=[established], parallel_games=2), established, "asker-u10")
    different = _event(date=DATE, start="14:00", end="15:00", title="Other club booking")
    problem = _problem(age_group="U10", ice=60, teams=teams, events=[established, different], parallel_games=2)

    with pytest.raises(SeasonStateError, match="active"):
        _confirm(root, problem, different, "asker-u10")

    assert len(_active_for(load_decisions("2026-2027", root=root), "asker-u10")) == 1
