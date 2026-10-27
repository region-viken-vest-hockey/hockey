"""Calendar audit buckets from the real booking-status projection (#842).

These run the production projection over canonical fixtures, so a regression
in evidence handling shows up here rather than only in synthetic rows.
"""

from __future__ import annotations

from tournament_scheduler.application.canonical_season.store_facade import (
    booking_status_report,
    confirm_calendar_booking,
    set_manual_booking_assertion,
)
from tests.test_booking_assessment import (
    _assess,
    _event,
    _problem,
    _promote,
    _tournament,
)


def _bucket_of(report, tournament_id):
    for bucket, ids in report["calendar_audit"]["buckets"].items():
        if tournament_id in ids:
            return bucket
    return None


def test_confirmed_association_keeps_its_bucket_when_unrelated_calendar_drifts(tmp_path):
    """A refresh that only adds an unrelated event must not move a confirmed booking."""

    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12")])
    before = _problem([_event("2026-09-12", "10:00", "12:00")])
    event_fp = _assess(root, before)["events"][0]["event_fingerprint"]
    confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fp,
        tournament_id="t1",
        actor="booker",
        note="matched booking",
        problem=before,
    )
    after = _problem(
        [_event("2026-09-12", "10:00", "12:00"), _event("2026-09-20", "18:00", "19:00", title="Other league")],
        complete=True,
    )

    first = booking_status_report(season="2026-2027", root=root, problem=before)
    second = booking_status_report(season="2026-2027", root=root, problem=after)

    assert _bucket_of(first, "t1") == "confirmed_matching"
    assert _bucket_of(second, "t1") == "confirmed_matching"
    assert second["calendar_audit"]["material_mismatches"] == []


def test_accepted_manual_booking_without_usable_calendar_is_uncheckable(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12")])
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:1",
        problem=_problem([], status="untrusted"),
    )

    report = booking_status_report(season="2026-2027", root=root, problem=_problem([], status="untrusted"))

    assert _bucket_of(report, "t1") == "accepted_uncheckable"
    assert report["calendar_audit"]["material_mismatches"] == []


def test_untrusted_calendar_without_evidence_is_no_coverage_not_absence(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12")])

    report = booking_status_report(season="2026-2027", root=root, problem=_problem([], status="untrusted"))

    assert _bucket_of(report, "t1") == "no_calendar_coverage"


def test_complete_calendar_absence_is_unconfirmed_not_missing_coverage(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12")])

    report = booking_status_report(season="2026-2027", root=root, problem=_problem([], complete=True))

    assert _bucket_of(report, "t1") == "unconfirmed_ambiguous"


def test_moved_booked_interval_is_a_material_mismatch_from_the_projection(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="10:00")])
    moved = _problem([_event("2026-09-12", "11:00", "13:00")], complete=True)
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:2",
        problem=moved,
    )

    report = booking_status_report(season="2026-2027", root=root, problem=moved)

    assert _bucket_of(report, "t1") == "material_mismatch"


def test_accepted_booking_with_no_event_on_checkable_calendar_is_flagged_for_review(tmp_path):
    """Lost identity on a complete calendar is review work; authority is kept (#842)."""

    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="10:00")])
    problem = _problem([_event("2026-09-20", "18:00", "19:00", title="Other league")], complete=True)
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:3",
        problem=problem,
    )

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = next(r for r in report["tournaments"] if r["tournament_id"] == "t1")

    assert _bucket_of(report, "t1") == "accepted_identity_review"
    assert row["operational_state"] == "booked"
    assert row["operational_lock"] is True


def test_accepted_booking_with_event_reassigned_to_other_arena_is_flagged_for_review(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="10:00")])
    problem = _problem([_event("2026-09-12", "10:00", "12:00", arena="Arena B")], complete=True)
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:4",
        problem=problem,
    )

    report = booking_status_report(season="2026-2027", root=root, problem=problem)

    assert _bucket_of(report, "t1") == "accepted_identity_review"


def test_metadata_only_change_keeps_accepted_booking_confirmed(tmp_path):
    """Kongsberg-style: a feed that changes only event metadata never moves an accepted interval (#842)."""

    root = _promote(tmp_path, [_tournament("t1", date_str="2026-10-16", start="10:30")])
    accepted = _problem([_event("2026-10-16", "10:30", "12:30", title="Kongsberg miniputt")], complete=True)
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:kongsberg",
        problem=accepted,
    )
    retitled = _problem([_event("2026-10-16", "10:30", "12:30", title="Miniputt U10 (updated)")], complete=True)

    report = booking_status_report(season="2026-2027", root=root, problem=retitled)
    row = next(r for r in report["tournaments"] if r["tournament_id"] == "t1")

    assert _bucket_of(report, "t1") == "confirmed_matching"
    assert row["operational_state"] == "booked"
    assert row["interval_comparison"]["status"] == "matches"


def test_partial_feed_keeps_accepted_booking_booked_and_uncheckable(tmp_path):
    """Holmen-style: an incomplete feed is not a negative and never demotes an accepted booking (#842)."""

    root = _promote(tmp_path, [_tournament("t1", date_str="2026-10-15", start="13:00")])
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:holmen",
        problem=_problem([], complete=True),
    )
    partial = _problem([], status="untrusted")

    report = booking_status_report(season="2026-2027", root=root, problem=partial)
    row = next(r for r in report["tournaments"] if r["tournament_id"] == "t1")

    assert _bucket_of(report, "t1") == "accepted_uncheckable"
    assert row["operational_state"] == "booked"
    assert row["operational_lock"] is True


def test_readable_but_partial_calendar_is_uncheckable_not_identity_loss(tmp_path):
    """A readable feed with incomplete coverage cannot show that an accepted identity is lost (#842)."""

    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="10:00")])
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:partial",
        problem=_problem([], complete=True),
    )
    partial = _problem([_event("2026-09-20", "18:00", "19:00", title="Other league")], integrity="partial")

    report = booking_status_report(season="2026-2027", root=root, problem=partial)

    assert _bucket_of(report, "t1") == "accepted_uncheckable"


def test_accepted_booking_with_unresolved_plausible_candidates_is_not_identity_loss(tmp_path):
    """Competing plausible events are ambiguity to review, never a claim that no event matches (#842)."""

    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="10:00")])
    problem = _problem(
        [
            _event("2026-09-12", "10:00", "11:00"),
            _event("2026-09-12", "11:00", "12:00"),
        ],
        complete=True,
    )
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:competing",
        problem=problem,
    )

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = next(r for r in report["tournaments"] if r["tournament_id"] == "t1")

    assert _bucket_of(report, "t1") == "unconfirmed_ambiguous"
    assert row["operational_state"] == "booked"
    assert row["operational_lock"] is True


def test_accepted_booking_lost_identity_projects_identity_rebind_action(tmp_path):
    """Real projection: a lost identity on a complete calendar asks for an optional rebind only (#847)."""

    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="10:00")])
    problem = _problem([_event("2026-09-20", "18:00", "19:00", title="Other league")], complete=True)
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:5",
        problem=problem,
    )

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = next(r for r in report["tournaments"] if r["tournament_id"] == "t1")

    assert report["calendar_audit"]["actions"]["t1"] == "optional_identity_rebind"
    assert row["operational_state"] == "booked"
    assert row["operational_lock"] is True
