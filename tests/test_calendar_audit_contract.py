"""Active-season calendar audit contract (#842).

The audit buckets every active booking-status row into one of a fixed set of
categories. Cancelled tournaments never enter an active bucket: their history is
reported separately. Freshness drift alone never turns an accepted booking into
a moved or mismatched one.
"""

from __future__ import annotations

from tournament_scheduler.booking.calendar_audit import (
    AUDIT_ACTION_CHANGE_APPROVAL,
    AUDIT_ACTION_RESOLVE_CANDIDATE,
    AUDIT_ACCEPTED_UNCHECKABLE,
    AUDIT_CANCELLED_HISTORICAL,
    AUDIT_CONFIRMED_MATCHING,
    AUDIT_EXPLICIT_NOT_BOOKED,
    AUDIT_MATERIAL_MISMATCH,
    AUDIT_NO_CALENDAR_COVERAGE,
    AUDIT_UNCONFIRMED_AMBIGUOUS,
    AUDIT_ACTIONS,
    AUDIT_ACTION_NONE,
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


def _comparison(status, *, effective=None, observed=None):
    effective = effective or _interval()
    return {
        "status": status,
        "effective_canonical": {"date": effective["date"], "start": effective["start"], "end": effective["end"]},
        "observed": observed,
        "same_date": observed is None or observed["date"] == effective["date"],
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
    assert detail["effective_canonical"] == {"date": "2026-10-16", "start": "10:30", "end": "13:00"}
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
        "booking_assessment_classification": "not_checkable",
        "calendar_stale_reasons": [],
        "follow_up_reasons": [],
    }

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_NO_CALENDAR_COVERAGE] == ["rvv-0050"]


def test_unmatched_event_on_readable_calendar_is_ambiguity_not_missing_coverage():
    # The calendar was read, but no event could be tied to the tournament. That is
    # inconclusive evidence, so it must not be reported as missing coverage.
    row = {
        "tournament_id": "rvv-0054",
        "status": "unknown",
        "operational_state": OPERATIONAL_UNKNOWN,
        "authority": None,
        "conflict": False,
        "needs_attention": False,
        "interval_comparison": None,
        "booking_assessment_classification": "unmatched",
        "calendar_status": "unknown",
        "calendar_stale_reasons": [],
        "follow_up_reasons": [],
    }

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_UNCONFIRMED_AMBIGUOUS] == ["rvv-0054"]
    assert summary["buckets"][AUDIT_NO_CALENDAR_COVERAGE] == []


def test_explicit_conflict_without_comparison_is_not_reported_as_interval_change():
    row = _booked_row("rvv-0010", comparison=None, conflict=True)

    summary = calendar_audit_summary([row], cancelled_history=[])

    detail = summary["material_mismatches"][0]
    assert detail["kind"] == "explicit_conflict"
    assert detail["observed"] is None


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


def test_each_active_row_has_one_operator_action_from_its_bucket():
    # Issue #847: the operator action is a projection of the bucket, so the
    # text and --json cannot disagree about what, if anything, to do.
    matching = _booked_row("rvv-0001", comparison=_comparison("matches", observed=_interval()))
    lost_identity = _booked_row("rvv-0002", comparison=_comparison("no_observed_interval"))
    lost_identity["operational_state"] = OPERATIONAL_BOOKED
    lost_identity["actionable_candidate_count"] = 0
    lost_identity["calendar_covers_window"] = True
    moved = _booked_row("rvv-0003", comparison=_comparison("differs", observed=_interval(end="13:10")))

    summary = calendar_audit_summary([matching, lost_identity, moved], cancelled_history=[])

    actions = summary.get("actions", {})
    assert actions.get("rvv-0001") == "none"
    assert actions.get("rvv-0002") == "optional_identity_rebind"
    assert actions.get("rvv-0003") == "explicit_booking_change_approval"


def test_end_time_only_difference_on_accepted_booking_is_a_material_mismatch_not_identity_loss():
    # Issue #847: seven Jar bookings differ by +10 minutes at the end only. That
    # is a real interval contradiction on an accepted booking, not a missing event.
    observed = _interval(end="13:10")
    row = _booked_row("rvv-0004", comparison=_comparison("differs", observed=observed))
    row["interval_comparison"]["end_delta_minutes"] = 10
    row["interval_comparison"]["start_delta_minutes"] = 0

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == ["rvv-0004"]
    assert summary["buckets"]["accepted_identity_review"] == []
    assert summary["material_mismatches"][0]["end_delta_minutes"] == 10
    assert summary.get("actions", {}).get("rvv-0004") == "explicit_booking_change_approval"


def test_every_non_none_action_has_operator_wording_in_text():
    # JSON and text must not diverge: a new action without a text label would be
    # silently dropped from the operator view.
    from tournament_scheduler.cli.booking_status_text import _ACTION_LABELS

    for bucket, action in AUDIT_ACTIONS.items():
        if action != AUDIT_ACTION_NONE:
            assert action in _ACTION_LABELS, f"{bucket} -> {action} has no operator wording"


def _presence_row(tid, *, comparison, candidates=1, conflict=False):
    # A Sandefjord-style booking: the host calendar does not publish tournament
    # bookings, so an absent interval proves nothing and must not demand action.
    row = _booked_row(tid, comparison=comparison, conflict=conflict)
    row.update(
        {
            "calendar_publishes_bookings": False,
            "calendar_covers_window": True,
            "actionable_candidate_count": candidates,
        }
    )
    return row


def test_accepted_booking_without_calendar_presence_is_not_a_resolve_candidate_demand():
    # Without the presence capability the same complete calendar with a
    # contested candidate asks for identity resolution (the #853 operator report).
    row = _booked_row("rvv-0024", comparison=_comparison("no_observed_interval"))
    row.update({"calendar_covers_window": True, "actionable_candidate_count": 1})

    summary = calendar_audit_summary([row], cancelled_history=[])
    assert summary["buckets"][AUDIT_UNCONFIRMED_AMBIGUOUS] == ["rvv-0024"]
    assert summary["actions"]["rvv-0024"] == AUDIT_ACTION_RESOLVE_CANDIDATE

    presence = _presence_row("rvv-0024", comparison=_comparison("no_observed_interval"), candidates=0)
    summary = calendar_audit_summary([presence], cancelled_history=[])

    assert summary["buckets"][AUDIT_ACCEPTED_UNCHECKABLE] == ["rvv-0024"]
    assert summary["buckets"][AUDIT_UNCONFIRMED_AMBIGUOUS] == []
    assert summary["actions"]["rvv-0024"] == AUDIT_ACTION_NONE
    assert summary["material_mismatches"] == []


def test_accepted_booking_keeps_its_authority_when_calendar_presence_is_unpublished():
    row = _presence_row("rvv-0037", comparison=_comparison("no_observed_interval"), candidates=0)

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_CONFIRMED_MATCHING] == []
    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == []
    assert row["status"] == "manually_booked"
    assert row["operational_state"] == OPERATIONAL_BOOKED


def test_demonstrated_interval_conflict_stays_material_when_presence_is_unpublished():
    row = _presence_row(
        "rvv-0060",
        comparison=_comparison(
            "differs",
            observed={"date": "2026-10-16", "start": "12:00", "end": "13:00"},
        ),
    )

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == ["rvv-0060"]
    assert summary["actions"]["rvv-0060"] == AUDIT_ACTION_CHANGE_APPROVAL


def test_explicit_conflict_stays_material_when_presence_is_unpublished():
    row = _presence_row("rvv-0102", comparison=_comparison("no_observed_interval"), conflict=True)

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == ["rvv-0102"]


def test_assessment_provenance_carries_calendar_presence_limitation_without_authority_change():
    from tournament_scheduler.booking.status_crosswalk import assessment_provenance_fields

    fields = assessment_provenance_fields(
        {"classification": "unmatched", "host_club": "Sandefjord Penguins", "calendar_covers_window": True}
    )
    assert assessment_provenance_fields({"host_club": "Skien"}).get("calendar_publishes_bookings") is None

    assert fields["calendar_publishes_bookings"] is False
    assert "status" not in fields
    assert "authority" not in fields


def test_contested_candidate_on_unpublished_calendar_still_requires_identity_resolution():
    # An actionable candidate is evidence that may be a different booking identity.
    # Absence of the primary interval must not suppress that review (#853 review).
    row = _presence_row("rvv-0024", comparison=_comparison("no_observed_interval"), candidates=1)

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_UNCONFIRMED_AMBIGUOUS] == ["rvv-0024"]
    assert summary["buckets"][AUDIT_ACCEPTED_UNCHECKABLE] == []
    assert summary["actions"]["rvv-0024"] == AUDIT_ACTION_RESOLVE_CANDIDATE
    assert row["operational_state"] == OPERATIONAL_BOOKED
