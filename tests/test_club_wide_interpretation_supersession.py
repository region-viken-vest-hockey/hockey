"""A calendar confirmation supersedes one tournament's club-wide interpretation (#567).

A club-wide source statement covers several tournaments, and each tournament keeps its
own interpretation record. Confirming a calendar event for one tournament may supersede
only that tournament's interpretation, and only when the operator names it. Every other
interpretation under the same statement stays active and unchanged. An attested
supersession also lets the same booking's calendar block move its start or end, since the
attestation is what says it is the same booking; without it, a moved start is a different
booking (#869).
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest

from tournament_scheduler.application.canonical_season.store_facade import (
    booking_status_report,
    confirm_calendar_booking,
    load_decisions,
    set_manual_booking_assertion,
)
from tournament_scheduler.booking.calendar_bookings import (
    CALENDAR_BOOKING_ASSOCIATIONS_KEY,
    MANUAL_BOOKING_ASSERTIONS_KEY,
    event_fingerprint,
)
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tests.test_booking_assessment import _event, _promote, _tournament
from tests.test_calendar_interval_adoption_e2e import _planned_problem, _tournament_row

ROOT_SEASON = Path(__file__).resolve().parents[1] / "season"


def _ice(minutes, events=()):
    problem = _planned_problem(list(events), complete=True)
    problem["ice_time_minutes"] = {"U10": minutes}
    return problem


def _club_statement(root, tournament_id, date_str, stated_start):
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id=tournament_id,
        booking_status="booked",
        actor="booker",
        reference="Miniserierunder Frisk Asker.xlsx",
        source_scope="club_wide_interpretation",
        stated_date=date_str,
        stated_start=stated_start,
        problem=_ice(120),
    )


def _positive_for(decisions, tournament_id):
    return [
        record
        for record in decisions[MANUAL_BOOKING_ASSERTIONS_KEY]
        if record["tournament_id"] == tournament_id and record["status"] == "active"
    ]


def _active_associations(decisions, tournament_id):
    return [
        record
        for record in decisions.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or []
        if record.get("tournament_id") == tournament_id and record.get("status", "active") == "active"
    ]


def _confirm(root, event, problem, tournament_id, *, supersede=None, note="calendar shows the real booking", dry_run=False):
    return confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fingerprint({**event, "club": "A"}),
        tournament_id=tournament_id,
        actor="operator",
        note=note,
        problem=problem,
        supersede_manual_assertion_id=supersede,
        dry_run=dry_run,
    )


def test_one_named_tournament_supersedes_and_its_siblings_stay_unchanged(tmp_path):
    root = _promote(
        tmp_path,
        [
            _tournament("t1", date_str="2026-09-12", start="15:00"),
            _tournament("t2", date_str="2026-09-19", start="15:00"),
            _tournament("t3", date_str="2026-09-26", start="15:00"),
        ],
    )
    _club_statement(root, "t1", "2026-09-12", "13:30")
    _club_statement(root, "t2", "2026-09-19", "13:30")
    _club_statement(root, "t3", "2026-09-26", "13:30")
    before = load_decisions("2026-2027", root=root)
    manual_before = copy.deepcopy(before[MANUAL_BOOKING_ASSERTIONS_KEY])
    named = _positive_for(before, "t1")[0]["id"]
    event = _event("2026-09-12", "13:00", "16:00", title="TURNERING U10", club="A")

    _confirm(root, event, _ice(180, [event]), "t1", supersede=named)

    after = load_decisions("2026-2027", root=root)
    by_id = {record["id"]: record for record in after[MANUAL_BOOKING_ASSERTIONS_KEY]}
    assert by_id[named]["status"] == "superseded"
    assert by_id[named]["superseded_by"].startswith("calendar_booking:")
    for record in manual_before:
        if record["id"] != named:
            assert by_id[record["id"]] == record
    assert [r["id"] for r in _active_associations(after, "t1")] == [by_id[named]["superseded_by"]]


def test_attested_supersession_retires_the_old_association_for_a_moved_start(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="13:30")])
    _club_statement(root, "t1", "2026-09-12", "13:30")
    first = _event("2026-09-12", "13:30", "14:50", title="U10 Serierunde", club="A")
    _confirm(root, first, _ice(80, [first]), "t1", note="accept the calendar block")
    manual_id = _positive_for(load_decisions("2026-2027", root=root), "t1")[0]["id"]
    moved = _event("2026-09-12", "13:00", "14:20", title="U10 Serierunde", club="A")

    _confirm(root, moved, _ice(80, [moved]), "t1", supersede=manual_id, note="same booking, retimed by the host")

    decisions = load_decisions("2026-2027", root=root)
    active = _active_associations(decisions, "t1")
    assert [(r["start"], r["end"]) for r in active] == [("13:00", "14:20")]
    retired = [r for r in decisions[CALENDAR_BOOKING_ASSOCIATIONS_KEY] if r["tournament_id"] == "t1" and r["status"] != "active"]
    assert [r["status"] for r in retired] == ["superseded"]
    assert retired[0]["superseded_by"] == active[0]["id"]
    row = _tournament_row(
        booking_status_report(season="2026-2027", root=root, problem=_ice(80, [moved])), "t1"
    )
    assert row["accepted_evidence"]["association_id"] == active[0]["id"]
    assert row["interval_comparison"]["status"] == "matches"


def test_a_moved_start_without_attestation_is_still_a_different_booking(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="13:30")])
    first = _event("2026-09-12", "13:30", "14:50", title="U10 Serierunde", club="A")
    _confirm(root, first, _ice(80, [first]), "t1", note="accept the calendar block")
    moved = _event("2026-09-12", "13:00", "14:20", title="U10 Serierunde", club="A")

    with pytest.raises(SeasonStateError, match="different"):
        _confirm(root, moved, _ice(80, [moved]), "t1", note="no attestation")

    assert [r["status"] for r in _active_associations(load_decisions("2026-2027", root=root), "t1")] == ["active"]


def test_attested_supersession_still_refuses_a_different_title(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="13:30")])
    _club_statement(root, "t1", "2026-09-12", "13:30")
    first = _event("2026-09-12", "13:30", "14:50", title="U10 Serierunde", club="A")
    _confirm(root, first, _ice(80, [first]), "t1", note="accept the calendar block")
    manual_id = _positive_for(load_decisions("2026-2027", root=root), "t1")[0]["id"]
    other = _event("2026-09-12", "13:00", "14:20", title="U11 Serierunde", club="A")

    with pytest.raises(SeasonStateError, match="different"):
        _confirm(root, other, _ice(80, [other]), "t1", supersede=manual_id, note="attested")

    decisions = load_decisions("2026-2027", root=root)
    assert _positive_for(decisions, "t1")[0]["status"] == "active"
    assert [r["status"] for r in _active_associations(decisions, "t1")] == ["active"]


def test_dry_run_reports_the_same_supersession_that_apply_writes(tmp_path):
    root = _promote(
        tmp_path,
        [
            _tournament("t1", date_str="2026-09-12", start="15:00"),
            _tournament("t2", date_str="2026-09-19", start="15:00"),
        ],
    )
    _club_statement(root, "t1", "2026-09-12", "13:30")
    _club_statement(root, "t2", "2026-09-19", "13:30")
    named = _positive_for(load_decisions("2026-2027", root=root), "t1")[0]["id"]
    event = _event("2026-09-12", "13:00", "16:00", title="TURNERING U10", club="A")
    before = load_decisions("2026-2027", root=root)

    preview = _confirm(root, event, _ice(180, [event]), "t1", supersede=named, dry_run=True)

    assert preview["superseded_manual_assertions"] == [named]
    assert load_decisions("2026-2027", root=root) == before
    applied = _confirm(root, event, _ice(180, [event]), "t1", supersede=named)
    assert applied["superseded_manual_assertions"] == preview["superseded_manual_assertions"]
    assert preview["association"]["start"] == applied["association"]["start"]


def test_a_moved_start_with_an_ineligible_assertion_id_is_refused_before_any_write(tmp_path):
    """A bare assertion id does not attest the same booking: the request must validate first (#567)."""
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="13:30")])
    first = _event("2026-09-12", "13:30", "14:50", title="U10 Serierunde", club="A")
    _confirm(root, first, _ice(80, [first]), "t1", note="accept the calendar block")
    before = load_decisions("2026-2027", root=root)
    moved = _event("2026-09-12", "13:00", "14:20", title="U10 Serierunde", club="A")

    with pytest.raises(SeasonStateError, match="is not active for tournament t1"):
        _confirm(root, moved, _ice(80, [moved]), "t1", supersede="manual_booking:t1:not-a-record", note="attested")

    assert load_decisions("2026-2027", root=root) == before


def test_moved_start_replacement_previews_and_applies_identically(tmp_path):
    """Dry-run and apply agree on a replacement of an existing association, not just on a first confirmation."""
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="13:30")])
    _club_statement(root, "t1", "2026-09-12", "13:30")
    first = _event("2026-09-12", "13:30", "14:50", title="U10 Serierunde", club="A")
    _confirm(root, first, _ice(80, [first]), "t1", note="accept the calendar block")
    manual_id = _positive_for(load_decisions("2026-2027", root=root), "t1")[0]["id"]
    before = load_decisions("2026-2027", root=root)
    moved = _event("2026-09-12", "13:00", "14:20", title="U10 Serierunde", club="A")

    preview = _confirm(root, moved, _ice(80, [moved]), "t1", supersede=manual_id, note="same booking, retimed", dry_run=True)

    assert load_decisions("2026-2027", root=root) == before
    applied = _confirm(root, moved, _ice(80, [moved]), "t1", supersede=manual_id, note="same booking, retimed")
    assert preview["superseded_manual_assertions"] == applied["superseded_manual_assertions"] == [manual_id]
    assert preview["association"]["id"] == applied["association"]["id"]
    assert (preview["association"]["start"], preview["association"]["end"]) == (applied["association"]["start"], applied["association"]["end"]) == ("13:00", "14:20")
    decisions = load_decisions("2026-2027", root=root)
    active = _active_associations(decisions, "t1")
    assert [r["id"] for r in active] == [applied["association"]["id"]]
    retired = [r for r in decisions[CALENDAR_BOOKING_ASSOCIATIONS_KEY] if r["tournament_id"] == "t1" and r["status"] != "active"]
    assert [r["superseded_by"] for r in retired] == [applied["association"]["id"]]


# The ten Frisk Asker tournaments blocked on club-wide interpretations (rvv-0065 ... rvv-0191).
# Each block is the live Frisk Asker calendar event for that tournament, from the 30 Oct feed.
FRISK_BLOCKED = [
    ("rvv-0065", "U11 Serierunde", "14:30", "16:50"),
    ("rvv-0070", "U11 Serierunde", "14:00", "16:20"),
    ("rvv-0087", "U8 Serierunde", "15:00", "17:20"),
    ("rvv-0100", "U11 Serierunde", "15:30", "17:50"),
    ("rvv-0127", "U11 Serierunde", "15:00", "17:20"),
    ("rvv-0134", "U7 Serierunde", "13:00", "14:50"),
    ("rvv-0135", "U10 Serierunde", "16:30", "18:50"),
    ("rvv-0179", "JU8 Serierunde", "13:00", "14:20"),
    ("rvv-0181", "U11 Serierunde", "14:30", "16:50"),
    ("rvv-0191", "U9 Serierunde", "13:30", "15:50"),
]


@pytest.mark.skipif(not (ROOT_SEASON / "2026-2027" / "schedule.json").exists(), reason="canonical season not present")
def test_canonical_season_has_the_ten_frisk_asker_blocks_adopted(tmp_path):
    """Read-only end-state check on a copy of the canonical season (#567, #860).

    The supersession workflow itself is covered by the hermetic tests above. This pins the
    outcome the maintainer approved: each of the ten Frisk Asker Serierunde blocks is the single
    active calendar association for its tournament, matches its canonical interval, has no active
    manual positive left (its manual assertion is kept as superseded history).
    """
    root = tmp_path / "season"
    shutil.copytree(ROOT_SEASON / "2026-2027", root / "2026-2027")
    from tournament_scheduler.application.canonical_season.store_facade import load_schedule

    schedule = load_schedule("2026-2027", root=root)
    problem = schedule["verification_context"]["problem"]
    decisions = load_decisions("2026-2027", root=root)
    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    for tid, title, start, end in FRISK_BLOCKED:
        active = _active_associations(decisions, tid)
        assert len(active) == 1, tid
        assert (active[0]["start"], active[0]["end"]) == (start, end), tid
        assert _tournament_row(report, tid)["interval_comparison"]["status"] == "matches", tid
        assert _positive_for(decisions, tid) == [], tid
        assert any(
            r["tournament_id"] == tid and r["status"] == "superseded" for r in decisions[MANUAL_BOOKING_ASSERTIONS_KEY]
        ), tid
