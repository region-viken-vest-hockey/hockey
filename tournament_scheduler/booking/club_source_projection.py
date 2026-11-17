"""Read-only club-source interpretation, overlap review and host scoping."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.booking_facts import _tournaments_by_id
from tournament_scheduler.booking.club_booking_sources import (
    active_club_booking_source_assertions,
    club_booking_source_records,
)
from tournament_scheduler.booking.interval_facts import _parse_hhmm, tournament_occupancy_interval_facts
from tournament_scheduler.booking.manual_assertions import active_manual_assertions, manual_assertion_interval_follow_up


def _club_booking_source_projection(
    *,
    decisions: Mapping[str, Any] | None,
    rows: list[dict[str, Any]],
    plan: Mapping[str, Any] | None,
    problem: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Group per-tournament booking authority under its club-wide source.

    The projection is read-only: it reports which club-provided booking list
    each interpretation came from, the per-ID canonical interval and booking
    state, and any interval follow-up (for example a duration discrepancy the
    club stated but the canonical occupancy deliberately did not adopt). It also
    flags two linked IDs whose club-stated (or canonical) intervals overlap, so an
    ambiguous club list is review-required instead of silently treated as two
    bookings.
    """

    tournaments = _tournaments_by_id(plan)
    rows_by_id = {str(row.get("tournament_id") or ""): row for row in rows}
    active = active_manual_assertions(decisions)
    all_sources = {
        str(record.get("id") or ""): record
        for record in club_booking_source_records(decisions)
    }
    active_source_ids = {
        str(record.get("id") or "")
        for record in active_club_booking_source_assertions(decisions)
    }
    referenced_ids = {
        str(assertion.get("source_assertion_id") or "")
        for assertion in active
        if str(assertion.get("source_assertion_id") or "")
    }
    # Report every active source plus any superseded source still referenced by
    # an active assertion, so replacing a source version never silently drops
    # the provenance (and the review signal) for already-linked IDs.
    report_ids = sorted(active_source_ids) + sorted(referenced_ids - active_source_ids)
    sources: list[dict[str, Any]] = []
    for source_id in report_ids:
        record = all_sources.get(source_id) or {}
        is_active = source_id in active_source_ids
        items: list[dict[str, Any]] = []
        for assertion in sorted(active, key=lambda item: str(item.get("tournament_id") or "")):
            if str(assertion.get("source_assertion_id") or "") != source_id:
                continue
            tid = str(assertion.get("tournament_id") or "")
            tournament = tournaments.get(tid)
            if tournament is not None and tournament.get("cancelled"):
                # A cancelled tournament has no booked interval, so its stated
                # window must not feed overlap review against an active neighbour (#842).
                continue
            row = rows_by_id.get(tid) or {}
            interval = (
                tournament_occupancy_interval_facts(tournament, problem)
                if tournament is not None
                else {}
            )
            stated = assertion.get("stated_interval") or {}
            # The club's own window is the review subject when it stated one; a
            # club-wide list commonly overlaps even though the canonical plan
            # was shortened to fit. Fall back to the canonical interval when the
            # source did not state a window.
            effective_date = str(stated.get("date") or interval.get("date") or "")
            effective_start = str(stated.get("start") or interval.get("start_time") or "")
            effective_end = str(stated.get("end") or interval.get("end_time") or "")
            items.append(
                {
                    "tournament_id": tid,
                    "age_group": str((tournament or {}).get("age_group") or ""),
                    "date": str((tournament or {}).get("date") or ""),
                    "canonical_interval": interval,
                    "stated_interval": dict(stated) if isinstance(stated, Mapping) else {},
                    "effective_source_interval": {
                        "date": effective_date,
                        "start_time": effective_start,
                        "end_time": effective_end,
                    },
                    "booking_status": row.get("status"),
                    "operational_state": row.get("operational_state"),
                    "authority": row.get("authority"),
                    "stale_reasons": list(row.get("stale_reasons") or []),
                    "interval_follow_up": manual_assertion_interval_follow_up(assertion)
                    + list(assertion.get("booking_feasibility_warnings") or []),
                }
            )
        overlaps: list[list[str]] = []
        for left_index, left in enumerate(items):
            for right in items[left_index + 1 :]:
                same_date = (
                    left["effective_source_interval"]["date"]
                    and left["effective_source_interval"]["date"]
                    == right["effective_source_interval"]["date"]
                )
                if not same_date:
                    continue
                left_start = _parse_hhmm(left["effective_source_interval"]["start_time"])
                left_end = _parse_hhmm(left["effective_source_interval"]["end_time"])
                right_start = _parse_hhmm(right["effective_source_interval"]["start_time"])
                right_end = _parse_hhmm(right["effective_source_interval"]["end_time"])
                if None in (left_start, left_end, right_start, right_end):
                    continue
                if left_start < right_end and right_start < left_end:
                    overlaps.append([left["tournament_id"], right["tournament_id"]])
        stale_link = bool(items) and not is_active
        sources.append(
            {
                "id": source_id,
                "host_club": str(record.get("host_club") or ""),
                "source_document": str(record.get("source_document") or ""),
                "source_version": str(record.get("source_version") or ""),
                "source_fingerprint": str(record.get("source_fingerprint") or ""),
                "reference": str(record.get("reference") or ""),
                "asserted_at": str(record.get("asserted_at") or ""),
                "asserted_by": str(record.get("asserted_by") or ""),
                "status": str(record.get("status") or ""),
                "is_active": is_active,
                "superseded_by": str(record.get("superseded_by") or ""),
                "stale_link": stale_link,
                "tournament_ids": [item["tournament_id"] for item in items],
                "tournaments": items,
                "overlapping_source_intervals": overlaps,
                "requires_operator_review": bool(overlaps) or stale_link,
            }
        )
    # A club-wide interpretation recorded before source documents existed (or
    # whose source was not linked) stays visible as unprovenanced so it is not
    # mistaken for a direct per-tournament confirmation with a known source.
    unlinked = sorted(
        str(assertion.get("tournament_id") or "")
        for assertion in active
        if str(assertion.get("source_scope") or "") == "club_wide_interpretation"
        and not str(assertion.get("source_assertion_id") or "")
    )
    if unlinked:
        sources.append(
            {
                "id": "club_booking_source:unlinked",
                "host_club": "",
                "source_document": "",
                "source_version": "",
                "source_fingerprint": "",
                "reference": "",
                "asserted_at": "",
                "asserted_by": "",
                "tournament_ids": unlinked,
                "tournaments": [],
                "overlapping_source_intervals": [],
                "requires_operator_review": True,
                "unprovenanced": True,
            }
        )
    return sources


def _scope_club_sources(
    sources: list[dict[str, Any]], tournament_ids: set[str]
) -> list[dict[str, Any]]:
    """Restrict club source interpretations to tournaments in scope.

    A source keeps only its in-scope tournaments and the overlap pairs between
    them; a source with no in-scope tournament is dropped. The unprovenanced
    bucket is narrowed the same way so another club's interpretation never
    leaks into a host-scoped view.
    """

    scoped: list[dict[str, Any]] = []
    for source in sources:
        if source.get("unprovenanced"):
            ids = [tid for tid in source.get("tournament_ids") or [] if tid in tournament_ids]
            if ids:
                scoped.append({**source, "tournament_ids": ids})
            continue
        items = [item for item in source.get("tournaments") or [] if item.get("tournament_id") in tournament_ids]
        if not items:
            continue
        overlaps = [
            pair for pair in source.get("overlapping_source_intervals") or []
            if all(tid in tournament_ids for tid in pair)
        ]
        scoped.append(
            {
                **source,
                "tournament_ids": [item["tournament_id"] for item in items],
                "tournaments": items,
                "overlapping_source_intervals": overlaps,
                "requires_operator_review": bool(overlaps) or bool(source.get("stale_link")),
            }
        )
    return scoped
