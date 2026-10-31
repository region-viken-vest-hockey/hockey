"""Accepting a calendar block that equals a manual-derived duration relabels the override (#860).

A manual assertion that stated an interval writes a ``manual_club_confirmation_interval``
duration override. When the calendar block for the same tournament is accepted and
supersedes that assertion, the minutes may already be equal, so no new override was
written and the manual-derived label stayed beside the calendar association. That is
not a clean calendar-authority transition: the active override must carry
``calendar_event_association``. Hermetic fixtures only.
"""

from __future__ import annotations

import pytest

from tournament_scheduler.application.canonical_season.store_facade import (
    confirm_calendar_booking,
    load_decisions,
    set_manual_booking_assertion,
)
from tournament_scheduler.booking.calendar_bookings import event_fingerprint
from tournament_scheduler.season.ice_time_overrides import relabels_to_calendar_authority
from tests.test_approval_lifecycle import _host_a_problem, _promote, _tournament

EVENT = {
    "date": "2026-09-12",
    "start": "10:00",
    "end": "12:00",
    "availability": "fixed_busy",
    "calendar_event": "Miniputt U10",
}


def _active_overrides(root, tournament_id="t1"):
    return [
        row
        for row in load_decisions("2026-2027", root=root).get("ice_time_minutes_overrides", [])
        if row.get("tournament_id") == tournament_id and row.get("status") == "active"
    ]


def _manual_with_equal_minutes(root, problem):
    return set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        reference="email:club-2026-09-27",
        stated_start="10:00",
        stated_end="12:00",
        actor="tester",
        problem=problem,
    )["assertion"]


def _confirm(root, problem, manual_id, **overrides):
    kwargs = {
        "season": "2026-2027",
        "root": root,
        "event_fingerprint": event_fingerprint({**EVENT, "club": "A"}),
        "tournament_id": "t1",
        "actor": "tester",
        "note": "Calendar is the authority; the manual interval was not stated by the club",
        "supersede_manual_assertion_id": manual_id,
        "problem": problem,
    }
    kwargs.update(overrides)
    return confirm_calendar_booking(**kwargs)


def test_superseding_with_equal_minutes_relabels_the_manual_override(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([EVENT])
    manual = _manual_with_equal_minutes(root, problem)
    before = _active_overrides(root)
    assert [row.get("authority") for row in before] == ["manual_club_confirmation_interval"]

    result = _confirm(root, problem, manual["id"])

    assert result["superseded_manual_assertions"] == [manual["id"]]
    after = _active_overrides(root)
    assert [row.get("authority") for row in after] == ["calendar_event_association"]
    assert after[0]["minutes"] == before[0]["minutes"] == 120
    assert result["ice_time_override"]["id"] == after[0]["id"]


def test_dry_run_reports_the_relabel_without_writing(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([EVENT])
    manual = _manual_with_equal_minutes(root, problem)
    before = _active_overrides(root)

    preview = _confirm(root, problem, manual["id"], dry_run=True)

    assert preview["ice_time_override"]["authority"] == "calendar_event_association"
    assert _active_overrides(root) == before


def test_repeat_confirmation_does_not_write_another_override(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([EVENT])
    manual = _manual_with_equal_minutes(root, problem)
    _confirm(root, problem, manual["id"])
    once = _active_overrides(root)

    _confirm(root, problem, manual["id"])

    assert _active_overrides(root) == once


def test_an_operator_override_without_authority_is_left_alone(tmp_path):
    """Only a manual-derived label is relabelled; plain operator overrides keep their meaning."""

    from tournament_scheduler.application.canonical_season.store_facade import set_ice_time_minutes

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([EVENT])
    set_ice_time_minutes(
        season="2026-2027", root=root, tournament_id="t1", minutes=120, request_id="host-confirm-1",
        actor="tester", note="host confirmed",
    )
    before = _active_overrides(root)
    assert [row.get("authority") for row in before] == [None]

    result = _confirm(root, problem, None, note="Calendar block matches the booked slot")

    assert result["ice_time_override"] is None
    assert _active_overrides(root) == before


def test_preview_and_apply_agree_and_the_manual_override_is_released(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([EVENT])
    manual = _manual_with_equal_minutes(root, problem)
    old = _active_overrides(root)[0]

    preview = _confirm(root, problem, manual["id"], dry_run=True)
    applied = _confirm(root, problem, manual["id"])

    keys = ("tournament_id", "minutes", "authority", "status", "type", "request_id")
    assert {k: preview["ice_time_override"][k] for k in keys} == {k: applied["ice_time_override"][k] for k in keys}
    rows = {
        row["id"]: row
        for row in load_decisions("2026-2027", root=root).get("ice_time_minutes_overrides", [])
        if row.get("tournament_id") == "t1"
    }
    assert rows[old["id"]]["status"] != "active"
    assert rows[applied["ice_time_override"]["id"]]["status"] == "active"


@pytest.mark.parametrize(
    ("existing_authority", "incoming", "expected"),
    [
        ("manual_club_confirmation_interval", "calendar_event_association", True),
        (None, "calendar_event_association", False),
        ("", "calendar_event_association", False),
        ("calendar_event_association", "calendar_event_association", False),
        ("host_confirmed", "calendar_event_association", False),
        ("manual_club_confirmation_interval", "manual_club_confirmation_interval", False),
        ("manual_club_confirmation_interval", "", False),
    ],
)
def test_only_a_manual_derived_override_is_replaced_by_calendar_authority(existing_authority, incoming, expected):
    existing = None if existing_authority is None else {"authority": existing_authority}

    assert relabels_to_calendar_authority(existing, incoming) is expected
    assert relabels_to_calendar_authority(None, incoming) is False
