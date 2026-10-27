"""Read-only crosswalk from the calendar assessment to booking-status rows.

The booking-status projection keeps its accepted authority and status decisions
in ``calendar_bookings``. This module only attaches advisory provenance to a
row: the assessment classification, the canonical and observed intervals, and
the exact planned-versus-observed difference (issue #719).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from tournament_scheduler.booking.assessment_candidates import ASSESSMENT_PRESUMED_UNSCHEDULED
from tournament_scheduler.booking.interval_facts import _parse_hhmm
from tournament_scheduler.booking.calendar_presence import calendar_publishes_tournament_bookings

_MINUTES_PER_DAY = 24 * 60


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def assessment_provenance_fields(assessment_row: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the non-binding assessment provenance for one booking-status row.

    The crosswalk is advisory: attaching these fields never changes an accepted
    booking status or authority. It carries the classification and canonical
    interval and, only when assessment selected one defensible identity match,
    that primary calendar observation. A negative or competing finding
    deliberately carries no observed interval, because unrelated or unresolved
    evidence must never be presented as this tournament's observed booking.
    """

    if not assessment_row:
        return {}
    fields: dict[str, Any] = {}
    classification = str(assessment_row.get("classification") or "")
    if classification:
        fields["booking_assessment_classification"] = classification
    canonical_interval = assessment_row.get("canonical_interval")
    if isinstance(canonical_interval, Mapping):
        fields["canonical_interval"] = dict(canonical_interval)
    candidates = [
        candidate
        for candidate in assessment_row.get("candidates") or []
        if isinstance(candidate, Mapping)
    ]
    primary_fingerprint = str(assessment_row.get("primary_candidate_event_fingerprint") or "")
    primary = next(
        (
            candidate
            for candidate in candidates
            if candidate.get("actionable")
            and str(candidate.get("event_fingerprint") or "") == primary_fingerprint
        ),
        None,
    )
    if primary is not None:
        fields["latest_observed_interval"] = {
            "date": str(primary.get("date") or ""),
            "start": str(primary.get("start") or ""),
            "end": str(primary.get("end") or ""),
            "event_fingerprint": str(primary.get("event_fingerprint") or ""),
            "title": str(primary.get("title") or ""),
            "source_trusted": bool(primary.get("source_trusted")),
            "actionable": True,
        }
    if classification == ASSESSMENT_PRESUMED_UNSCHEDULED:
        fields["negative_evidence"] = assessment_row.get("negative_evidence")
    # Advisory only: how many plausible calendar events the assessment still
    # contests for this tournament. Zero is what makes a lost identity provable.
    fields["actionable_candidate_count"] = sum(1 for candidate in candidates if candidate.get("actionable"))
    if assessment_row.get("association_identity_unresolved"):
        # A confirmed booking whose recorded event was retitled or lost: an interval
        # observation can show a move, but cannot prove the same event (#847).
        fields["association_identity_unresolved"] = True
    if "calendar_covers_window" in assessment_row:
        # Advisory only: whether the calendar fully covers this tournament's
        # window. It separates "no event on a complete calendar" from "readable
        # but partial or unreadable", without changing any booking authority.
        fields["calendar_covers_window"] = bool(assessment_row.get("calendar_covers_window"))
    # Documented source limitation from the host club's profile (#853). It informs
    # the audit only; it never changes booking authority or status.
    publishes = calendar_publishes_tournament_bookings(str(assessment_row.get("host_club") or ""))
    if publishes is not None:
        fields["calendar_publishes_bookings"] = publishes
    return fields


def interval_comparison(canonical: Any, observed: Any) -> dict[str, Any] | None:
    """Exact planned-vs-observed difference for one booking-status row.

    ``canonical`` is the planned interval (``date``/``start_time``/``end_time``)
    and ``observed`` the single actionable calendar observation
    (``date``/``start``/``end``) chosen by the assessment. Minute deltas are
    observed minus planned, so a positive start delta means the calendar event
    starts later than the plan. Nothing is inferred: without an observation the
    status is ``no_observed_interval``, never a match.
    """
    if not isinstance(canonical, Mapping):
        return None
    planned = {
        "date": str(canonical.get("date") or ""),
        "start": str(canonical.get("start_time") or ""),
        "end": str(canonical.get("end_time") or ""),
    }
    if not isinstance(observed, Mapping):
        return {
            "status": "no_observed_interval",
            "planned": planned,
            "observed": None,
            "same_date": None,
            "start_delta_minutes": None,
            "end_delta_minutes": None,
        }
    observed_interval = {
        "date": str(observed.get("date") or ""),
        "start": str(observed.get("start") or ""),
        "end": str(observed.get("end") or ""),
    }
    same_date = observed_interval["date"] == planned["date"]
    planned_day = _parse_date(planned["date"])
    observed_day = _parse_date(observed_interval["date"])
    planned_start, observed_start = _parse_hhmm(planned["start"]), _parse_hhmm(observed_interval["start"])
    planned_end, observed_end = _parse_hhmm(planned["end"]), _parse_hhmm(observed_interval["end"])
    if None in (planned_day, observed_day, planned_start, observed_start, planned_end, observed_end):
        start_delta = end_delta = None
        status = "not_comparable"
    else:
        # Deltas are taken from full date+time values, so a shifted date is
        # reported as its real distance rather than as a clock-time difference.
        day_offset = (observed_day - planned_day).days * _MINUTES_PER_DAY
        start_delta = day_offset + observed_start - planned_start
        end_delta = day_offset + observed_end - planned_end
        status = "matches" if same_date and start_delta == 0 and end_delta == 0 else "differs"
    return {
        "status": status,
        "planned": planned,
        "observed": observed_interval,
        "same_date": same_date,
        "start_delta_minutes": start_delta,
        "end_delta_minutes": end_delta,
    }


def explicit_interval_fields(assertion: Mapping[str, Any]) -> list[str] | None:
    """Return the interval fields (date, start, end) the source explicitly stated.

    A club-wide confirmation can state a window or only confirm that a booking
    exists. Fields it did not state keep the planner's value as a proposal. A
    record without the ``stated_interval`` key predates that record, so its
    authority is unknown and ``None`` is returned.
    """

    if "stated_interval" not in assertion:
        return None
    stated = assertion.get("stated_interval") or {}
    return [field for field in ("date", "start", "end") if str(stated.get(field) or "")]


def attach_interval_comparison(row: dict[str, Any]) -> dict[str, Any]:
    """Attach ``interval_comparison`` to a row that carries a canonical interval."""

    comparison = interval_comparison(row.get("canonical_interval"), row.get("latest_observed_interval"))
    if comparison is not None:
        row["interval_comparison"] = comparison
    return row
