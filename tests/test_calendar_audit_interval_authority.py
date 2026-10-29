"""Field-level interval authority in the calendar audit (#860).

A club-wide confirmation ("we booked these tournaments") does not confirm the
planner's exact duration or end time. When the source stated no window, a
differing calendar interval is an unreconciled proposed correction, not a
contradiction of an explicit fact. A field the source did state stays
authoritative, so a differing stated field is still a material mismatch.
"""

from __future__ import annotations

from tournament_scheduler.booking.calendar_audit import (
    AUDIT_ACTION_CHANGE_APPROVAL,
    AUDIT_ACTION_CONFIRM_INTERVAL,
    AUDIT_MATERIAL_MISMATCH,
    AUDIT_PROPOSED_INTERVAL_CORRECTION,
    calendar_audit_summary,
)
from tournament_scheduler.booking.calendar_bookings import (
    BOOKING_AUTHORITY_CALENDAR,
    BOOKING_AUTHORITY_MANUAL,
    BOOKING_AUTHORITY_MANUAL_INTERPRETATION,
    OPERATIONAL_BOOKED,
)


def _planned(*, end="16:40"):
    return {"date": "2026-10-16", "start": "15:00", "end": end}


def _comparison(*, start_delta=0, end_delta=0, observed_end="17:00", observed_date="2026-10-16"):
    status = "matches" if start_delta == 0 and end_delta == 0 and observed_date == "2026-10-16" else "differs"
    planned_start = 15 * 60
    observed_start = planned_start + start_delta
    return {
        "status": status,
        "effective_canonical": _planned(),
        "observed": {
            "date": observed_date,
            "start": f"{observed_start // 60:02d}:{observed_start % 60:02d}",
            "end": observed_end,
        },
        "same_date": observed_date == "2026-10-16",
        "start_delta_minutes": start_delta,
        "end_delta_minutes": end_delta,
    }


def _manual_row(tid, *, comparison, explicit_fields, conflict=False):
    row = {
        "tournament_id": tid,
        "status": "manually_booked",
        "operational_state": OPERATIONAL_BOOKED,
        "authority": BOOKING_AUTHORITY_MANUAL,
        "conflict": conflict,
        "needs_attention": conflict,
        "interval_comparison": comparison,
        "calendar_stale_reasons": [],
        "follow_up_reasons": [],
    }
    if explicit_fields is not None:
        row["explicit_interval_fields"] = list(explicit_fields)
    return row


def test_unstated_end_with_longer_calendar_booking_is_a_proposed_correction_not_a_mismatch():
    # Kongsberg rvv-0053 shape: plan 15:00-16:40, calendar 15:00-17:00, and the
    # club never stated an end time.
    row = _manual_row("rvv-0053", comparison=_comparison(end_delta=20), explicit_fields=[])

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_PROPOSED_INTERVAL_CORRECTION] == ["rvv-0053"]
    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == []
    assert summary["material_mismatches"] == []
    assert summary["actions"]["rvv-0053"] == AUDIT_ACTION_CONFIRM_INTERVAL
    assert summary["severity"][AUDIT_PROPOSED_INTERVAL_CORRECTION] == "review_required"


def test_stated_end_that_differs_from_calendar_stays_a_material_mismatch():
    # The club explicitly stated 16:40; the calendar says 17:00. Both are
    # authoritative facts in disagreement, so the audit flags it and does not
    # adopt 17:00.
    row = _manual_row("rvv-0054", comparison=_comparison(end_delta=20), explicit_fields=["end"])

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == ["rvv-0054"]
    assert summary["buckets"][AUDIT_PROPOSED_INTERVAL_CORRECTION] == []
    assert summary["actions"]["rvv-0054"] == AUDIT_ACTION_CHANGE_APPROVAL


def test_unstated_start_differing_while_stated_end_matches_is_a_proposed_correction():
    # Only the unstated start moved; the stated end agrees with the calendar.
    row = _manual_row(
        "rvv-0055",
        comparison=_comparison(start_delta=-30, end_delta=0, observed_end="16:40"),
        explicit_fields=["end"],
    )

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_PROPOSED_INTERVAL_CORRECTION] == ["rvv-0055"]
    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == []


def test_differing_unstated_field_next_to_a_differing_stated_field_is_a_material_mismatch():
    # The stated end disagrees, so the row is a real contradiction even though
    # the start was never stated.
    row = _manual_row(
        "rvv-0056",
        comparison=_comparison(start_delta=-30, end_delta=20, observed_end="17:00"),
        explicit_fields=["end"],
    )

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == ["rvv-0056"]
    assert summary["buckets"][AUDIT_PROPOSED_INTERVAL_CORRECTION] == []


def test_manual_conflict_is_a_material_mismatch_even_without_stated_fields():
    row = _manual_row("rvv-0057", comparison=_comparison(end_delta=20), explicit_fields=[], conflict=True)

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == ["rvv-0057"]
    assert summary["actions"]["rvv-0057"] == AUDIT_ACTION_CHANGE_APPROVAL


def test_calendar_association_authority_stays_a_material_mismatch_without_explicit_fields():
    # An accepted calendar association is itself the authoritative interval, so
    # its differing interval is a real change, never a proposed correction.
    row = _manual_row("rvv-0058", comparison=_comparison(end_delta=20), explicit_fields=[])
    row["authority"] = BOOKING_AUTHORITY_CALENDAR

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == ["rvv-0058"]
    assert summary["buckets"][AUDIT_PROPOSED_INTERVAL_CORRECTION] == []


def test_row_without_explicit_field_projection_keeps_the_conservative_material_mismatch():
    # Absence of the projection means the authority is unknown, so the audit
    # never downgrades a difference to a proposal.
    row = _manual_row("rvv-0059", comparison=_comparison(end_delta=20), explicit_fields=None)

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == ["rvv-0059"]


def test_unstated_date_change_with_stated_times_is_a_proposed_correction():
    # The club stated only the times; the calendar moved the booking to another day.
    row = _manual_row(
        "rvv-0060",
        comparison=_comparison(observed_date="2026-10-17", observed_end="16:40"),
        explicit_fields=["start", "end"],
    )

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_PROPOSED_INTERVAL_CORRECTION] == ["rvv-0060"]
    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == []


def test_changed_date_that_contradicts_a_stated_date_is_a_material_mismatch():
    row = _manual_row(
        "rvv-0061",
        comparison=_comparison(observed_date="2026-10-17", observed_end="16:40"),
        explicit_fields=["date", "start", "end"],
    )

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == ["rvv-0061"]
    assert summary["buckets"][AUDIT_PROPOSED_INTERVAL_CORRECTION] == []


def test_club_interpretation_confirmation_with_unstated_end_is_a_proposed_correction():
    # Kongsberg rvv-0053 on the live season: a club-wide confirmation recorded as
    # an interpretation, with no stated end. It is the same proposal as a plain
    # club confirmation and must not be a material mismatch.
    row = _manual_row("rvv-0053", comparison=_comparison(end_delta=20), explicit_fields=[])
    row["authority"] = BOOKING_AUTHORITY_MANUAL_INTERPRETATION

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_PROPOSED_INTERVAL_CORRECTION] == ["rvv-0053"]
    assert summary["material_mismatches"] == []


def test_interpretation_authority_with_stated_end_that_differs_stays_a_material_mismatch():
    row = _manual_row("rvv-0062", comparison=_comparison(end_delta=20), explicit_fields=["end"])
    row["authority"] = BOOKING_AUTHORITY_MANUAL_INTERPRETATION

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == ["rvv-0062"]
    assert summary["buckets"][AUDIT_PROPOSED_INTERVAL_CORRECTION] == []


def test_interpretation_authority_with_explicit_conflict_stays_a_material_mismatch():
    row = _manual_row("rvv-0063", comparison=_comparison(end_delta=20), explicit_fields=[], conflict=True)
    row["authority"] = BOOKING_AUTHORITY_MANUAL_INTERPRETATION

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == ["rvv-0063"]


def test_interpretation_authority_without_field_projection_is_not_auto_proposed():
    row = _manual_row("rvv-0064", comparison=_comparison(end_delta=20), explicit_fields=None)
    row["authority"] = BOOKING_AUTHORITY_MANUAL_INTERPRETATION

    summary = calendar_audit_summary([row], cancelled_history=[])

    assert summary["buckets"][AUDIT_MATERIAL_MISMATCH] == ["rvv-0064"]
