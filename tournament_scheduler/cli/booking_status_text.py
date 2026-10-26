"""Human-readable host-club view of ``season booking-status --host-club``.

Pure rendering over the booking-status projection: it adds no rule, status or
classification of its own, so the text can never disagree with ``--json``.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Mapping


def _signed(minutes: int) -> str:
    return f"{minutes:+d} min"


def _comparison_line(comparison: Mapping[str, Any]) -> str:
    planned = comparison.get("planned") or {}
    planned_text = f"{planned.get('date')} {planned.get('start')}-{planned.get('end')}"
    status = comparison.get("status")
    if status == "matches":
        return f"plan {planned_text} | calendar matches"
    observed = comparison.get("observed") or {}
    if status == "no_observed_interval" or not observed:
        return f"plan {planned_text} | calendar: no matched observation"
    observed_text = f"{observed.get('date')} {observed.get('start')}-{observed.get('end')}"
    if status == "not_comparable":
        return f"plan {planned_text} | calendar {observed_text} | not comparable (unparseable time)"
    deltas = f"start {_signed(comparison['start_delta_minutes'])}, end {_signed(comparison['end_delta_minutes'])}"
    if not comparison.get("same_date"):
        deltas = f"different date, {deltas}"
    return f"plan {planned_text} | calendar {observed_text} | differs: {deltas}"


_AUDIT_LABELS = (
    ("confirmed_matching", "confirmed, interval matches"),
    ("accepted_uncheckable", "accepted, calendar uncheckable (not a mismatch)"),
    ("accepted_identity_review", "accepted, no matching event on checkable calendar (review; authority kept)"),
    ("material_mismatch", "material mismatch"),
    ("unconfirmed_ambiguous", "needs review: unresolved calendar identity (accepted bookings keep authority)"),
    ("no_calendar_coverage", "no calendar coverage (not proof of absence)"),
    ("explicit_not_booked", "explicitly not booked"),
    ("cancelled_historical", "cancelled/historical (outside active totals)"),
)


def _audit_lines(audit: Mapping[str, Any]) -> list[str]:
    """Render the calendar audit contract from the projection's own buckets."""

    counts = audit.get("counts") or {}
    lines = [f"Calendar audit: {audit.get('active_total', 0)} active"]
    for key, label in _AUDIT_LABELS:
        lines.append(f"  {label}: {counts.get(key, 0)}")
    for detail in audit.get("material_mismatches") or []:
        if not isinstance(detail, Mapping):
            continue
        if detail.get("kind") == "interval_change":
            lines.append(f"  {detail.get('tournament_id')}: {_comparison_line({**detail, 'status': 'differs'})}")
        else:
            lines.append(f"  {detail.get('tournament_id')}: explicit conflict between accepted booking and calendar evidence")
    return lines


def _row_lines(row: Mapping[str, Any]) -> list[str]:
    lines = [
        f"{row.get('tournament_id')}  {row.get('age_group')}  {row.get('date')} {row.get('start_time')}  {row.get('arena')}",
        f"  state: {row.get('operational_state')} (status {row.get('status')}, authority {row.get('authority') or 'none'})",
    ]
    comparison = row.get("interval_comparison")
    if isinstance(comparison, Mapping):
        lines.append(f"  interval: {_comparison_line(comparison)}")
    classification = row.get("booking_assessment_classification")
    if classification:
        lines.append(f"  assessment: {classification}")
    follow_up = [str(reason) for reason in row.get("follow_up_reasons") or []]
    if follow_up:
        lines.append(f"  follow-up: {', '.join(follow_up)}")
    if row.get("rejected_booking_evidence"):
        lines.append(f"  rejected evidence: {len(row['rejected_booking_evidence'])} record(s) need resolution")
    return lines


def format_host_club_booking_status(report: Mapping[str, Any]) -> str:
    """Render one host club's booking status and planned-vs-observed intervals."""

    rows = [row for row in report.get("tournaments") or [] if isinstance(row, Mapping)]
    lines = [
        f"Booking status for host club {report.get('host_club')} ({report.get('season')})",
        f"Canonical revision: {report.get('canonical_state_revision')}",
        "",
    ]
    if not rows:
        lines.append("No tournaments are hosted by this club.")
    for row in rows:
        lines.extend(_row_lines(row))
        lines.append("")
    audit = report.get("calendar_audit")
    if isinstance(audit, Mapping):
        lines.extend(_audit_lines(audit))
        lines.append("")
    unresolved = [event for event in report.get("unresolved_calendar_events") or [] if isinstance(event, Mapping)]
    lines.append(f"Calendar events without one clear tournament match: {len(unresolved)}")
    for event in unresolved:
        lines.append(
            f"  {event.get('date')} {event.get('start')}-{event.get('end')} "
            f"{event.get('title') or '(untitled)'} ({event.get('availability')}; {event.get('reason')})"
        )
    lines.append("")
    counts = report.get("counts") or {}
    lines.append(
        f"Tournaments: {len(rows)} | needs attention: {counts.get('needs_attention', 0)} "
        f"| manual queue: {len(report.get('manual_booking_queue') or [])}"
    )
    return "\n".join(lines)


def print_booking_status(args: argparse.Namespace) -> None:
    """Print the booking-status projection: host-club text, otherwise JSON.

    ``--host-club`` without ``--json`` gives the human-readable view; every other
    invocation keeps the structured JSON it always printed.
    """

    from ..application.canonical_season.store_facade import booking_status_report

    report = booking_status_report(season=args.season, root=args.root, host_club=args.host_club)
    if args.host_club and not args.json:
        print(format_host_club_booking_status(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
