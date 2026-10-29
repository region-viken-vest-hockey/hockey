"""Per-tournament consequence of a proposed calendar refresh (issue #869).

A dry-run calendar refresh projects the booking status twice: once against the
current canonical state and once against the proposed fresh calendar evidence.
This module compares those two read-only projections tournament by tournament,
so an operator can see what a refresh would change without raw event JSON.

It is pure: it reads two status reports and returns rows. It never mutates
canonical booking, placement or approval state.
"""

from __future__ import annotations

from typing import Any, Mapping


def _bucket_by_tournament(report: Mapping[str, Any]) -> dict[str, str]:
    audit = report.get("calendar_audit") or {}
    buckets = audit.get("buckets") or {}
    return {
        str(tournament_id): str(bucket)
        for bucket, tournament_ids in buckets.items()
        for tournament_id in tournament_ids or []
    }


def _rows_by_tournament(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row.get("tournament_id") or ""): row
        for row in report.get("tournaments") or []
        if isinstance(row, Mapping)
    }


def refresh_tournament_consequences(
    current: Mapping[str, Any],
    proposed: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return one row per tournament whose booking consequence the refresh changes.

    ``current`` is the booking-status report for the committed canonical state;
    ``proposed`` is the same projection over the fresh calendar evidence. A
    tournament whose bucket, comparison and observation are all unchanged is
    omitted, so an empty list means the refresh changes no booking consequence.
    """

    current_buckets = _bucket_by_tournament(current)
    proposed_buckets = _bucket_by_tournament(proposed)
    current_rows = _rows_by_tournament(current)
    proposed_rows = _rows_by_tournament(proposed)
    actions = (proposed.get("calendar_audit") or {}).get("actions") or {}
    rows: list[dict[str, Any]] = []
    for tournament_id in sorted(set(current_rows) | set(proposed_rows)):
        before = current_rows.get(tournament_id, {})
        after = proposed_rows.get(tournament_id, {})
        before_comparison = before.get("interval_comparison") or {}
        after_comparison = after.get("interval_comparison") or {}
        if (
            current_buckets.get(tournament_id) == proposed_buckets.get(tournament_id)
            and before_comparison == after_comparison
            and before.get("latest_observed_interval") == after.get("latest_observed_interval")
        ):
            continue
        observation = after.get("latest_observed_interval") or {}
        rows.append(
            {
                "tournament_id": tournament_id,
                "effective_canonical": after_comparison.get("effective_canonical"),
                "authority": after.get("authority"),
                "explicit_interval_fields": after.get("explicit_interval_fields"),
                "fresh_observed_interval": after_comparison.get("observed"),
                "observed_event": {
                    "event_fingerprint": observation.get("event_fingerprint"),
                    "title": observation.get("title"),
                    "source_trusted": observation.get("source_trusted"),
                }
                if observation
                else None,
                "current_bucket": current_buckets.get(tournament_id),
                "proposed_bucket": proposed_buckets.get(tournament_id),
                "action": actions.get(tournament_id, "none"),
                "proposed_delta": {
                    "status": after_comparison.get("status"),
                    "same_date": after_comparison.get("same_date"),
                    "start_delta_minutes": after_comparison.get("start_delta_minutes"),
                    "end_delta_minutes": after_comparison.get("end_delta_minutes"),
                },
                "follow_up_reasons": list(after.get("follow_up_reasons") or []),
            }
        )
    return rows
