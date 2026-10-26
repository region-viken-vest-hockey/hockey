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
