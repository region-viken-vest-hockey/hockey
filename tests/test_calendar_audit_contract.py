"""Active-season calendar audit contract (#842).

The audit buckets every active booking-status row into one of a fixed set of
categories. Cancelled tournaments never enter an active bucket: their history is
reported separately. Freshness drift alone never turns an accepted booking into
a moved or mismatched one.
"""

from __future__ import annotations

from tournament_scheduler.booking.calendar_audit import (
    AUDIT_ACCEPTED_UNCHECKABLE,
    AUDIT_CANCELLED_HISTORICAL,
    AUDIT_CONFIRMED_MATCHING,
    AUDIT_EXPLICIT_NOT_BOOKED,
    AUDIT_MATERIAL_MISMATCH,
    AUDIT_NO_CALENDAR_COVERAGE,
    AUDIT_UNCONFIRMED_AMBIGUOUS,
    calendar_audit_summary,
)
from tournament_scheduler.booking.calendar_bookings import (
    BOOKING_AUTHORITY_MANUAL,
    BOOKING_CONFIRMED_NOT_BOOKED,
    OPERATIONAL_ACTION_REQUIRED,
    OPERATIONAL_BOOKED,
    OPERATIONAL_CHANGED_SLOT_REVIEW,
    OPERATIONAL_NOT_BOOKED,
    OPERATIONAL_UNKNOWN,
)


def _interval(*, date="2026-10-16", start="10:30", end="13:00"):
    return {"date": date, "start": start, "end": end}


def _comparison(status, *, planned=None, observed=None):
    planned = planned or _interval()
    return {
        "status": status,
        "planned": {"date": planned["date"], "start": planned["start"], "end": planned["end"]},
        "observed": observed,
        "same_date": observed is None or observed["date"] == planned["date"],
        "start_delta_minutes": None,
        "end_delta_minutes": None,
    }


def _booked_row(tid, *, comparison, conflict=False, calendar_stale_reasons=None):
    return {
        "tournament_id": tid,
        "status": "manually_booked",
        "operational_state": OPERATIONAL_BOOKED,
        "authority": BOOKING_AUTHORITY_MANUAL,
        "conflict": conflict,
        "needs_attention": conflict,
        "interval_comparison": comparison,
        "calendar_stale_reasons": list(calendar_stale_reasons or []),
        "follow_up_reasons": [],
    }


def test_booked_row_with_matching_interval_is_confirmed_even_when_fingerprint_drifted():
    # Freshness drift (a changed source fingerprint) with an unchanged booked
    # interval is not a moved booking.
    row = _booked_row(
        "rvv-0029",
        comparison=_comparison("matches", observed=_interval()),
        calendar_stale_reasons=["calendar_source_fingerprint_changed"],
    )

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_CONFIRMED_MATCHING] == ["rvv-0029"]
    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == []
    assert summary["material_mismatches"] == []


def test_booked_row_with_changed_interval_is_a_material_mismatch():
    observed = _interval(start="11:00", end="13:30")
    row = _booked_row("rvv-0029", comparison=_comparison("differs", observed=observed))

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == ["rvv-0029"]
    detail = summary["material_mismatches"][0]
    assert detail["tournament_id"] == "rvv-0029"
    assert detail["planned"] == {"date": "2026-10-16", "start": "10:30", "end": "13:00"}
    assert detail["observed"] == observed


def test_booked_row_with_conflicting_manual_and_calendar_is_a_material_mismatch():
    row = _booked_row("rvv-0010", comparison=None, conflict=True)

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == ["rvv-0010"]


def test_booked_row_without_comparable_observation_is_uncheckable_not_a_mismatch():
    # The accepted booking stays protected; missing or stale independent
    # calendar evidence is shown as uncheckable, never as proven disagreement.
    row = _booked_row(
        "rvv-0044",
        comparison=_comparison("no_observed_interval"),
        calendar_stale_reasons=["calendar_source_stale"],
    )

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_ACCEPTED_UNCHECKABLE] == ["rvv-0044"]
    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == []
    assert summary["material_mismatches"] == []


def test_unbooked_row_without_calendar_coverage_is_not_treated_as_unbooked_proof():
    row = {
        "tournament_id": "rvv-0050",
        "status": "unknown",
        "operational_state": OPERATIONAL_UNKNOWN,
        "authority": None,
        "conflict": False,
        "needs_attention": False,
        "interval_comparison": None,
        "calendar_stale_reasons": [],
        "follow_up_reasons": [],
    }

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_NO_CALENDAR_COVERAGE] == ["rvv-0050"]


def test_unbooked_changed_slot_candidate_needs_review_not_mismatch():
    row = {
        "tournament_id": "rvv-0051",
        "status": "ambiguous",
        "operational_state": OPERATIONAL_CHANGED_SLOT_REVIEW,
        "authority": None,
        "conflict": False,
        "needs_attention": True,
        "interval_comparison": _comparison("differs", observed=_interval(date="2026-10-17")),
        "calendar_stale_reasons": [],
        "follow_up_reasons": [],
    }

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_UNCONFIRMED_AMBIGUOUS] == ["rvv-0051"]
    assert summary["material_mismatches"] == []


def test_explicit_rejection_is_separate_from_ambiguity():
    row = {
        "tournament_id": "rvv-0052",
        "status": BOOKING_CONFIRMED_NOT_BOOKED,
        "operational_state": OPERATIONAL_NOT_BOOKED,
        "authority": None,
        "conflict": False,
        "needs_attention": False,
        "interval_comparison": None,
        "calendar_stale_reasons": [],
        "follow_up_reasons": [],
    }

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_EXPLICIT_NOT_BOOKED] == ["rvv-0052"]


def test_action_required_without_confirmation_is_unconfirmed():
    row = {
        "tournament_id": "rvv-0053",
        "status": "stale",
        "operational_state": OPERATIONAL_ACTION_REQUIRED,
        "authority": None,
        "conflict": False,
        "needs_attention": True,
        "interval_comparison": None,
        "calendar_stale_reasons": [],
        "follow_up_reasons": [],
    }

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_UNCONFIRMED_AMBIGUOUS] == ["rvv-0053"]


def test_cancelled_history_is_reported_outside_active_totals():
    active = _booked_row("rvv-0001", comparison=_comparison("matches", observed=_interval()))
    cancelled = [{"tournament_id": "rvv-0027"}, {"tournament_id": "rvv-0044"}]

    summary = calendar_audit_summary([active], cancelled_history=cancelled)

    assert summary["buckets"][AUDIT_CANCELLED_HISTORICAL] == ["rvv-0027", "rvv-0044"]
    assert summary["counts"][AUDIT_CANCELLED_HISTORICAL] == 2
    assert summary["active_total"] == 1
    assert sum(summary["counts"][name] for name in summary["counts"] if name != AUDIT_CANCELLED_HISTORICAL) == 1
