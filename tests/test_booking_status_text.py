"""Host-club operator text reports the calendar audit from the same projection (#842)."""

from __future__ import annotations

from tournament_scheduler.booking.calendar_audit import calendar_audit_summary
from tournament_scheduler.cli.booking_status_text import format_host_club_booking_status


def _report(rows, cancelled=()):
    return {
        "host_club": "Holmen",
        "season": "2026-2027",
        "canonical_state_revision": "rev",
        "tournaments": rows,
        "counts": {"needs_attention": 0},
        "manual_booking_queue": [],
        "unresolved_calendar_events": [],
        "calendar_audit": calendar_audit_summary(rows, cancelled_history=list(cancelled)),
        "cancelled_tournaments": list(cancelled),
    }


def test_text_reports_audit_counts_and_material_mismatch_detail():
    rows = [
        {
            "tournament_id": "rvv-0029",
            "operational_state": "booked",
            "status": "manually_booked",
            "authority": "manual_club_confirmation",
            "conflict": False,
            "follow_up_reasons": [],
            "interval_comparison": {
                "status": "differs",
                "planned": {"date": "2026-10-16", "start": "10:30", "end": "13:00"},
                "observed": {"date": "2026-10-16", "start": "11:00", "end": "13:30"},
                "same_date": True,
                "start_delta_minutes": 30,
                "end_delta_minutes": 30,
            },
        }
    ]

    text = format_host_club_booking_status(_report(rows, cancelled=[{"tournament_id": "rvv-0027"}]))

    assert "Calendar audit: 1 active" in text
    assert "material mismatch: 1" in text
    assert "cancelled/historical (outside active totals): 1" in text
    assert "rvv-0029" in text.split("Calendar audit", 1)[1]
    assert "plan 2026-10-16 10:30-13:00 | calendar 2026-10-16 11:00-13:30" in text


def test_explicit_conflict_is_rendered_without_a_fabricated_interval():
    rows = [
        {
            "tournament_id": "rvv-0010",
            "operational_state": "booked",
            "status": "manually_booked",
            "authority": "manual_club_confirmation",
            "conflict": True,
            "follow_up_reasons": ["calendar_negative_conflicts_with_manual_booking"],
            "interval_comparison": None,
        }
    ]

    text = format_host_club_booking_status(_report(rows))

    assert "rvv-0010: explicit conflict between accepted booking and calendar evidence" in text
    assert "differs" not in text.split("Calendar audit", 1)[1]


def test_identity_review_bucket_is_rendered_with_its_count():
    rows = [
        {
            "tournament_id": "rvv-0029",
            "operational_state": "booked",
            "status": "manually_booked",
            "authority": "manual_club_confirmation",
            "conflict": False,
            "follow_up_reasons": [],
            "interval_comparison": {"status": "no_observed_interval"},
            "calendar_covers_window": True,
        }
    ]

    text = format_host_club_booking_status(_report(rows))

    assert "accepted, no matching event on checkable calendar (review; authority kept): 1" in text


def test_unresolved_identity_label_does_not_read_as_lost_confirmation():
    rows = [
        {
            "tournament_id": "rvv-0030",
            "operational_state": "booked",
            "status": "manually_booked",
            "authority": "manual_club_confirmation",
            "conflict": False,
            "follow_up_reasons": [],
            "interval_comparison": {"status": "no_observed_interval"},
            "booking_assessment_classification": "proposed_unchanged",
            "actionable_candidate_count": 2,
            "calendar_covers_window": True,
        }
    ]

    text = format_host_club_booking_status(_report(rows))

    assert "needs review: unresolved calendar identity (accepted bookings keep authority): 1" in text
    assert "unconfirmed, needs review" not in text


def test_accepted_identity_review_row_keeps_booked_state_and_names_no_debooking():
    # Issue #847: a lost calendar identity is review work. The operator text
    # must keep the accepted booking as booked and never describe it as unbooked.
    rows = [
        {
            "tournament_id": "rvv-0029",
            "operational_state": "booked",
            "status": "manually_booked",
            "authority": "manual_club_confirmation",
            "operational_lock": True,
            "conflict": False,
            "follow_up_reasons": [],
            "interval_comparison": {"status": "no_observed_interval", "planned": {"date": "2026-10-16", "start": "10:30", "end": "13:00"}},
            "actionable_candidate_count": 0,
            "calendar_covers_window": True,
        }
    ]

    text = format_host_club_booking_status(_report(rows))
    row_text = text.split("Calendar audit", 1)[0]

    assert "state: booked" in row_text
    assert "operator action: optional identity rebind; booking stays booked" in row_text
    assert "not booked" not in row_text.lower()
    assert "unbooked" not in row_text.lower()


def test_material_mismatch_text_shows_both_intervals_and_end_difference_without_auto_change():
    rows = [
        {
            "tournament_id": "rvv-0004",
            "operational_state": "booked",
            "status": "manually_booked",
            "authority": "manual_club_confirmation",
            "conflict": False,
            "follow_up_reasons": [],
            "interval_comparison": {
                "status": "differs",
                "planned": {"date": "2026-10-16", "start": "10:30", "end": "13:00"},
                "observed": {"date": "2026-10-16", "start": "10:30", "end": "13:10"},
                "same_date": True,
                "start_delta_minutes": 0,
                "end_delta_minutes": 10,
            },
        }
    ]

    text = format_host_club_booking_status(_report(rows))

    assert "plan 2026-10-16 10:30-13:00 | calendar 2026-10-16 10:30-13:10 | differs: start +0 min, end +10 min" in text
    assert "operator action: booking change needs explicit approval; no change made" in text


def test_accepted_booking_row_shows_who_when_and_why_it_was_accepted():
    # Issue #847: the operator sees the accepted fact next to the current evidence.
    rows = [
        {
            "tournament_id": "rvv-0029",
            "operational_state": "booked",
            "status": "manually_booked",
            "authority": "manual_club_confirmation",
            "conflict": False,
            "follow_up_reasons": [],
            "accepted_evidence": {
                "accepted_at": "2026-09-22T10:00:00+00:00",
                "accepted_by": "booker",
                "note": "club confirmed by email",
                "accepted_interval": {"date": "2026-10-16", "start": "10:30", "end": "13:00"},
            },
        }
    ]

    text = format_host_club_booking_status(_report(rows))
    row_text = text.split("Calendar audit", 1)[0]

    assert "accepted: 2026-10-16 10:30-13:00 by booker on 2026-09-22 (club confirmed by email)" in row_text


def test_accepted_evidence_with_malformed_interval_does_not_break_rendering():
    # Review of #851: malformed projection data must not crash the operator text.
    rows = [
        {
            "tournament_id": "rvv-0029",
            "operational_state": "booked",
            "status": "manually_booked",
            "authority": "manual_club_confirmation",
            "conflict": False,
            "follow_up_reasons": [],
            "accepted_evidence": {"accepted_interval": "10:30-13:00", "accepted_by": "", "accepted_at": None},
        }
    ]

    text = format_host_club_booking_status(_report(rows))

    assert "accepted: unknown interval by unknown on  (no note)" in text


def test_superseded_acceptance_is_shown_under_the_current_one():
    # Issue #847: an earlier acceptance that was replaced stays visible to the operator.
    rows = [
        {
            "tournament_id": "rvv-0029",
            "operational_state": "booked",
            "status": "confirmed_booked",
            "authority": "calendar_event_association",
            "conflict": False,
            "follow_up_reasons": [],
            "accepted_evidence": {
                "accepted_at": "2026-09-22T10:00:00+00:00",
                "accepted_by": "booker",
                "note": "same booking, now on the calendar",
                "accepted_interval": {"date": "2026-10-16", "start": "10:30", "end": "13:00"},
                "history": [
                    {
                        "kind": "manual",
                        "status": "superseded",
                        "accepted_at": "2026-09-01T09:00:00+00:00",
                        "accepted_by": "club-admin",
                        "reference": "email:1",
                        "ended_at": "2026-09-22T10:00:00+00:00",
                        "ended_by": "calendar_booking:fp:rvv-0029",
                        "ended_reason": "same match moved",
                    }
                ],
            },
        }
    ]

    text = format_host_club_booking_status(_report(rows))
    row_text = text.split("Calendar audit", 1)[0]

    assert "  earlier: manual by club-admin on 2026-09-01 (email:1), superseded 2026-09-22" in row_text


def test_released_acceptance_is_not_described_as_replaced():
    # Review of #852: a release ends an acceptance without a replacement.
    rows = [
        {
            "tournament_id": "rvv-0029",
            "operational_state": "not_booked",
            "status": "not_booked",
            "authority": None,
            "conflict": False,
            "follow_up_reasons": [],
            "accepted_evidence": {
                "accepted_by": "booker",
                "accepted_at": "2026-09-22T10:00:00+00:00",
                "accepted_interval": {"date": "2026-10-16", "start": "10:30", "end": "13:00"},
                "history": [
                    {
                        "kind": "calendar",
                        "status": "released",
                        "accepted_at": "2026-09-01T09:00:00+00:00",
                        "accepted_by": "booker",
                        "reference": "",
                        "note": "matched",
                        "ended_at": "2026-09-30T08:00:00+00:00",
                    }
                ],
            },
        }
    ]

    row_text = format_host_club_booking_status(_report(rows)).split("Calendar audit", 1)[0]

    assert "  earlier: calendar by booker on 2026-09-01 (matched), released 2026-09-30" in row_text
    assert "replaced" not in row_text
