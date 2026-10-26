"""Active-season calendar audit contract (#842).

Every active booking-status row falls into exactly one bucket. The buckets keep
three different questions apart:

* is an accepted booking contradicted by the observed calendar interval;
* is an accepted booking merely uncheckable, because the independent evidence
  is stale, partial or absent;
* is a tournament without a booking, where absence of evidence is not proof.

Cancelled tournaments occupy no ice, so their history is reported in its own
bucket and never counts toward the active total or a mismatch. The classifier is
pure and reads only the already-projected row fields, so the audit, the
booking-status rows and the operator output cannot disagree about a row.

Freshness drift (a changed source fingerprint or stale-scrape reason) does not
move a booked interval. Only a comparison that shows a different date, start or
end, or an explicit manual/calendar conflict, is a material mismatch.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from tournament_scheduler.booking.calendar_bookings import (
    BOOKING_CONFIRMED_NOT_BOOKED,
    BOOKING_MANUALLY_NOT_BOOKED,
    OPERATIONAL_BOOKED,
    OPERATIONAL_UNKNOWN,
)

AUDIT_CONFIRMED_MATCHING = "confirmed_matching"
AUDIT_ACCEPTED_UNCHECKABLE = "accepted_uncheckable"
AUDIT_MATERIAL_MISMATCH = "material_mismatch"
AUDIT_UNCONFIRMED_AMBIGUOUS = "unconfirmed_ambiguous"
AUDIT_NO_CALENDAR_COVERAGE = "no_calendar_coverage"
AUDIT_EXPLICIT_NOT_BOOKED = "explicit_not_booked"
AUDIT_CANCELLED_HISTORICAL = "cancelled_historical"

AUDIT_BUCKETS = (
    AUDIT_CONFIRMED_MATCHING,
    AUDIT_ACCEPTED_UNCHECKABLE,
    AUDIT_MATERIAL_MISMATCH,
    AUDIT_UNCONFIRMED_AMBIGUOUS,
    AUDIT_NO_CALENDAR_COVERAGE,
    AUDIT_EXPLICIT_NOT_BOOKED,
    AUDIT_CANCELLED_HISTORICAL,
)

# Severity says how the bucket should be read by an operator. Only a material
# mismatch or an unconfirmed candidate asks for action; the rest is evidence.
AUDIT_SEVERITY = {
    AUDIT_CONFIRMED_MATCHING: "none",
    AUDIT_ACCEPTED_UNCHECKABLE: "advisory",
    AUDIT_MATERIAL_MISMATCH: "action_required",
    AUDIT_UNCONFIRMED_AMBIGUOUS: "review_required",
    AUDIT_NO_CALENDAR_COVERAGE: "advisory",
    AUDIT_EXPLICIT_NOT_BOOKED: "none",
    AUDIT_CANCELLED_HISTORICAL: "historical",
}

_EXPLICIT_NOT_BOOKED_STATUSES = (BOOKING_CONFIRMED_NOT_BOOKED, BOOKING_MANUALLY_NOT_BOOKED)


def _classify(row: Mapping[str, Any]) -> str:
    comparison = row.get("interval_comparison")
    comparison_status = str(comparison.get("status") or "") if isinstance(comparison, Mapping) else ""
    if row.get("operational_state") == OPERATIONAL_BOOKED:
        if row.get("conflict") or comparison_status == "differs":
            return AUDIT_MATERIAL_MISMATCH
        if comparison_status == "matches":
            return AUDIT_CONFIRMED_MATCHING
        return AUDIT_ACCEPTED_UNCHECKABLE
    if row.get("conflict"):
        return AUDIT_MATERIAL_MISMATCH
    if str(row.get("status") or "") in _EXPLICIT_NOT_BOOKED_STATUSES:
        return AUDIT_EXPLICIT_NOT_BOOKED
    if row.get("operational_state") == OPERATIONAL_UNKNOWN:
        return AUDIT_NO_CALENDAR_COVERAGE
    return AUDIT_UNCONFIRMED_AMBIGUOUS


def _mismatch_detail(row: Mapping[str, Any]) -> dict[str, Any]:
    comparison = row.get("interval_comparison") if isinstance(row.get("interval_comparison"), Mapping) else {}
    return {
        "tournament_id": str(row.get("tournament_id") or ""),
        "planned": dict(comparison.get("planned") or {}),
        "observed": dict(comparison["observed"]) if isinstance(comparison.get("observed"), Mapping) else None,
        "same_date": comparison.get("same_date"),
        "start_delta_minutes": comparison.get("start_delta_minutes"),
        "end_delta_minutes": comparison.get("end_delta_minutes"),
        "conflict": bool(row.get("conflict")),
        "follow_up_reasons": list(row.get("follow_up_reasons") or []),
    }


def calendar_audit_summary(
    rows: Sequence[Mapping[str, Any]],
    *,
    cancelled_history: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Bucket the active rows and the cancelled history into the audit contract."""

    buckets: dict[str, list[str]] = {bucket: [] for bucket in AUDIT_BUCKETS}
    material: list[dict[str, Any]] = []
    for row in rows:
        bucket = _classify(row)
        buckets[bucket].append(str(row.get("tournament_id") or ""))
        if bucket == AUDIT_MATERIAL_MISMATCH:
            material.append(_mismatch_detail(row))
    buckets[AUDIT_CANCELLED_HISTORICAL] = [str(item.get("tournament_id") or "") for item in cancelled_history]
    return {
        "active_total": len(rows),
        "counts": {bucket: len(ids) for bucket, ids in buckets.items()},
        "buckets": buckets,
        "severity": dict(AUDIT_SEVERITY),
        "material_mismatches": material,
    }
