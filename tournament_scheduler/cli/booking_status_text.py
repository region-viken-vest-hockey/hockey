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
    unmatched = [event for event in report.get("unmatched_calendar_events") or [] if isinstance(event, Mapping)]
    lines.append(f"Calendar events with no matching tournament: {len(unmatched)}")
    for event in unmatched:
        lines.append(
            f"  {event.get('date')} {event.get('start')}-{event.get('end')} "
            f"{event.get('title') or '(untitled)'} ({event.get('availability')})"
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
