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


def test_retitled_confirmed_booking_keeps_authority_without_claiming_a_match(tmp_path):
    """Issue #847: a retitled event keeps the accepted booking booked, but identity is not re-proven.

    The title (and therefore the fingerprint) changed, so the booking is not
    shown as a confirmed match. Authority and the lock stay with the booking,
    and the candidate is an unresolved review item.
    """

    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="10:00")])
    before = _problem([_event("2026-09-12", "10:00", "12:00", title="Miniputt U10")])
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
    renamed = _problem([_event("2026-09-12", "10:00", "12:00", title="Miniputt U10 (updated)")], complete=True)

    report = booking_status_report(season="2026-2027", root=root, problem=renamed)
    row = next(r for r in report["tournaments"] if r["tournament_id"] == "t1")

    assert row["operational_state"] == "booked"
    assert row["authority"] == "calendar_event_association"
    assert row["operational_lock"] is True
    assert _bucket_of(report, "t1") == "unconfirmed_ambiguous"
    assert report["calendar_audit"]["actions"]["t1"] == "resolve_candidate"
    assert report["calendar_audit"]["material_mismatches"] == []


def test_confirmed_booking_keeps_authority_when_its_event_disappears(tmp_path):
    """The original #847 bug: a missing confirmed event must not turn the booking into action_required."""

    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="10:00")])
    before = _problem([_event("2026-09-12", "10:00", "12:00", title="Miniputt U10")])
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

    report = booking_status_report(season="2026-2027", root=root, problem=_problem([], complete=True))
    row = next(r for r in report["tournaments"] if r["tournament_id"] == "t1")

    assert row["operational_state"] == "booked"
    assert row["authority"] == "calendar_event_association"
    assert row["operational_lock"] is True
    assert _bucket_of(report, "t1") != "material_mismatch"
    assert report["calendar_audit"]["material_mismatches"] == []


def test_moved_and_retitled_confirmed_event_is_a_material_mismatch_with_both_intervals(tmp_path):
    """Issue #847: a calendar interval change must not hide behind identity drift.

    The event moved from 10:00-12:00 to 11:00-13:00 and was retitled, so its
    fingerprint changed. The accepted booking stays booked, and the moved
    interval is reported as a material difference with both intervals. Nothing
    is changed automatically.
    """

    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="10:00")])
    before = _problem([_event("2026-09-12", "10:00", "12:00", title="Miniputt U10")])
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
    moved = _problem([_event("2026-09-12", "11:00", "13:00", title="Miniputt U10 (updated)")], complete=True)

    report = booking_status_report(season="2026-2027", root=root, problem=moved)
    row = next(r for r in report["tournaments"] if r["tournament_id"] == "t1")
    detail = report["calendar_audit"]["material_mismatches"][0]

    assert row["operational_state"] == "booked"
    assert row["authority"] == "calendar_event_association"
    assert _bucket_of(report, "t1") == "material_mismatch"
    assert detail["planned"] == {"date": "2026-09-12", "start": "10:00", "end": "12:00"}
    assert detail["observed"] == {"date": "2026-09-12", "start": "11:00", "end": "13:00"}
    assert report["calendar_audit"]["actions"]["t1"] == "explicit_booking_change_approval"


def test_unrelated_same_host_event_is_not_a_material_mismatch_for_a_lost_booking(tmp_path):
    """Issue #847: proximity alone is not the same booking.

    The confirmed event disappeared. The only other event on that host is an
    unrelated league at a different time, with no age-group evidence. It must
    not be presented as the moved accepted booking.
    """

    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="10:00")])
    before = _problem([_event("2026-09-12", "10:00", "12:00", title="Miniputt U10")])
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
    unrelated = _problem([_event("2026-09-12", "13:00", "15:00", title="Senior league")], complete=True)

    report = booking_status_report(season="2026-2027", root=root, problem=unrelated)
    row = next(r for r in report["tournaments"] if r["tournament_id"] == "t1")

    assert row["operational_state"] == "booked"
    assert row["authority"] == "calendar_event_association"
    assert _bucket_of(report, "t1") != "material_mismatch"
    assert report["calendar_audit"]["material_mismatches"] == []


def test_confirmed_booking_reports_how_and_when_it_was_accepted(tmp_path):
    """Issue #847: the operator can see who accepted a booking and from which interval, even after drift."""

    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="10:00")])
    before = _problem([_event("2026-09-12", "10:00", "12:00", title="Miniputt U10")])
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

    report = booking_status_report(season="2026-2027", root=root, problem=_problem([], complete=True))
    row = next(r for r in report["tournaments"] if r["tournament_id"] == "t1")
    provenance = row["accepted_evidence"]

    assert provenance["accepted_by"] == "booker"
    assert provenance["note"] == "matched booking"
    assert provenance["accepted_interval"] == {"date": "2026-09-12", "start": "10:00", "end": "12:00"}
    assert provenance["accepted_at"]
    assert provenance["event_fingerprint"] == event_fp


def test_manual_confirmation_reports_how_and_when_it_was_accepted(tmp_path):
    """Issue #847 review: manual confirmations carry the same provenance as calendar ones."""

    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="10:00")])
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="booker",
        reference="email:9",
        problem=_problem([], complete=True),
    )

    report = booking_status_report(season="2026-2027", root=root, problem=_problem([], complete=True))
    row = next(r for r in report["tournaments"] if r["tournament_id"] == "t1")
    provenance = row["accepted_evidence"]

    assert row["authority"] == "manual_club_confirmation"
    assert provenance["accepted_by"] == "booker"
    assert provenance["reference"] == "email:9"
    assert provenance["accepted_at"]
    assert provenance["assertion_id"].startswith("manual_booking:t1:")


def test_calendar_confirmation_that_supersedes_a_manual_booking_keeps_the_original_acceptance(tmp_path):
    """Issue #847 lifecycle: the manual acceptance it replaced stays recoverable on the row."""

    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="10:00")])
    problem = _problem([_event("2026-09-12", "10:00", "12:00", title="Miniputt U10")], complete=True)
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="club-admin",
        reference="email:1",
        problem=problem,
    )
    manual_id = booking_status_report(season="2026-2027", root=root, problem=problem)["tournaments"][0][
        "accepted_evidence"
    ]["assertion_id"]
    event_fp = _assess(root, problem)["events"][0]["event_fingerprint"]
    confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fp,
        tournament_id="t1",
        actor="booker",
        note="same booking, now on the calendar",
        problem=problem,
        supersede_manual_assertion_id=manual_id,
    )

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = next(r for r in report["tournaments"] if r["tournament_id"] == "t1")
    evidence = row["accepted_evidence"]
    replaced = [entry for entry in evidence["history"] if entry["record_id"] == manual_id]

    assert row["authority"] == "calendar_event_association"
    assert evidence["accepted_by"] == "booker"
    assert len(replaced) == 1
    assert replaced[0]["status"] == "superseded"
    assert replaced[0]["accepted_by"] == "club-admin"
    assert replaced[0]["reference"] == "email:1"
    assert replaced[0]["ended_by"] == evidence["association_id"]


def test_acceptance_history_keeps_only_recognised_end_states_in_order():
    # Review of #852: unknown or missing statuses are not evidence that an acceptance ended.
    from tournament_scheduler.booking.accepted_provenance import acceptance_history

    decisions = {
        "manual_booking_assertions": [
            {"id": "m1", "tournament_id": "t1", "status": "superseded", "asserted_at": "2026-09-01",
             "asserted_by": "a", "superseded_by": "calendar_booking:fp:t1", "supersede_reason": "moved", "superseded_at": "2026-09-05"},
            {"id": "m2", "tournament_id": "t1", "status": "revoked", "asserted_at": "2026-08-01",
             "asserted_by": "b", "revoked_at": "2026-08-20", "revoked_by": "c", "revoke_reason": "wrong date"},
            {"id": "m3", "tournament_id": "t1", "status": "mystery", "asserted_at": "2026-07-01"},
            {"id": "m4", "tournament_id": "t1", "asserted_at": "2026-07-02"},
            {"id": "m5", "tournament_id": "t1", "status": "active", "asserted_at": "2026-09-10"},
            {"id": "m6", "tournament_id": "t2", "status": "superseded", "asserted_at": "2026-09-01"},
        ],
        "calendar_booking_associations": [
            {"id": "c1", "tournament_id": "t1", "status": "released", "created_at": "2026-09-02",
             "created_by": "d", "released_at": "2026-09-03", "released_by": "e", "release_note": "wrong event"},
        ],
    }

    history = acceptance_history(decisions, "t1")

    assert [entry["record_id"] for entry in history] == ["m2", "m1", "c1"]
    assert [entry["status"] for entry in history] == ["revoked", "superseded", "released"]
    assert history[1]["ended_by"] == "calendar_booking:fp:t1"
    assert history[1]["ended_reason"] == "moved"
    assert history[2]["ended_reason"] == "wrong event"
    assert history[2]["ended_at"] == "2026-09-03"
