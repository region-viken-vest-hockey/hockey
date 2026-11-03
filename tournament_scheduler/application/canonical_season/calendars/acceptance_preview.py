"""Read-only construction of the reviewed club-booking acceptance preview."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from tournament_scheduler.booking.calendar_bookings import (
    MANUAL_ASSERTION_SUPERSEDED,
    TOURNAMENT_CANCELLED_RELEASE_REASON,
    active_manual_assertions,
    booking_status_report,
    club_booking_source_by_id,
    manual_assertion_records,
    manual_assertion_stale_reasons,
)
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.season.state import canonical_state_revision

from ..shared import _resolve_plan_problem


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _content_digest(preview: Mapping[str, Any]) -> str:
    """Digest of what was reviewed, independent of the canonical revision.

    Every canonical write advances the revision (including the acceptance audit
    entry itself), so recognising an already-accepted set needs a revision-free
    identity of the reviewed content.
    """

    return _digest(
        {
            "season": preview["season"],
            "source_id": preview["source_id"],
            "source_fingerprint": preview["source_fingerprint"],
            "interpretations": preview["interpretations"],
            "conflicts": preview["conflicts"],
        }
    )


def _reviewed_token(preview: Mapping[str, Any]) -> str:
    return _digest(
        {"content": _content_digest(preview), "canonical_state_revision": preview["canonical_state_revision"]}
    )


def _preview_from_snapshot(
    snapshot,
    *,
    season: str,
    source_id: str,
    expected_revision: str | None,
) -> dict[str, Any]:
    schedule, decisions = snapshot.schedule, snapshot.decisions
    revision = canonical_state_revision(schedule, decisions)
    if expected_revision and expected_revision != revision:
        raise SeasonStateError(f"Stale canonical revision: expected {expected_revision}, current is {revision}")

    source = club_booking_source_by_id(decisions, source_id)
    if source is None:
        raise SeasonStateError(
            f"Unknown active club booking source assertion: {source_id}. Superseded sources cannot be accepted."
        )

    plan = schedule.get("plan") or {}
    tournaments = {
        str(tournament.get("id") or ""): tournament
        for tournament in plan.get("tournaments") or []
        if isinstance(tournament, Mapping)
    }
    problem = _resolve_plan_problem(schedule, None, decisions)
    club = str(source.get("host_club") or "")
    interpretations: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    linked = sorted(
        (
            assertion
            for assertion in active_manual_assertions(decisions)
            if str(assertion.get("source_assertion_id") or "") == source_id
            and str(assertion.get("source_scope") or "") == "club_wide_interpretation"
        ),
        key=lambda assertion: str(assertion.get("tournament_id") or ""),
    )
    if not linked:
        conflicts.append(
            {
                "kind": "no_linked_interpretations",
                "message": "The source has no active per-tournament interpretations to accept.",
            }
        )

    for assertion in linked:
        tournament_id = str(assertion.get("tournament_id") or "")
        tournament = tournaments.get(tournament_id)
        stale_reasons = manual_assertion_stale_reasons(
            assertion,
            problem=problem,
            tournament=tournament,
        )
        item_conflicts = list(stale_reasons)
        if str(assertion.get("host_club") or "") != club:
            item_conflicts.append("source_host_club_mismatch")
        if tournament is not None and tournament.get("cancelled"):
            item_conflicts.append("tournament_cancelled")
        item_conflicts = sorted(set(item_conflicts))
        if item_conflicts:
            conflicts.append(
                {
                    "kind": "interpretation_not_current",
                    "tournament_id": tournament_id,
                    "reasons": item_conflicts,
                    "message": f"Tournament {tournament_id} must be reviewed before source acceptance.",
                }
            )

        interpretations.append(
            {
                "tournament_id": tournament_id,
                "assertion_id": str(assertion.get("id") or ""),
                "booking_status": str(assertion.get("booking_status") or ""),
                "before_interval": assertion.get("calendar_baseline"),
                "accepted_interval": assertion.get("asserted_interval"),
                "stated_interval": assertion.get("stated_interval"),
                "source_revision": str(assertion.get("source_revision") or ""),
                "conflicts": item_conflicts,
            }
        )

    # A tournament cancelled after it was linked has its interpretation released
    # (superseded) by the cancellation owner, so it no longer appears among the
    # active rows; it must still block acceptance of the source as a whole.
    active_ids = {str(a.get("tournament_id") or "") for a in linked}
    for record in sorted(manual_assertion_records(decisions), key=lambda r: str(r.get("tournament_id") or "")):
        tournament_id = str(record.get("tournament_id") or "")
        if (
            str(record.get("source_assertion_id") or "") == source_id
            and str(record.get("status") or "") == MANUAL_ASSERTION_SUPERSEDED
            and str(record.get("supersede_reason") or "") == TOURNAMENT_CANCELLED_RELEASE_REASON
            and tournament_id not in active_ids
            and (tournaments.get(tournament_id) or {}).get("cancelled")
            and not any(c.get("tournament_id") == tournament_id for c in conflicts)
        ):
            conflicts.append(
                {
                    "kind": "interpretation_tournament_cancelled",
                    "tournament_id": tournament_id,
                    "message": (
                        f"Tournament {tournament_id} was cancelled after being linked to this source; "
                        "record the current reviewed source version before acceptance."
                    ),
                }
            )

    # The booking-source report is the owner of overlap review for one club list.
    report = booking_status_report(problem=problem, plan=plan, decisions=decisions)
    source_report = next((r for r in report.get("club_booking_sources") or [] if r.get("id") == source_id), {})
    if source_report.get("overlapping_source_intervals"):
        conflicts.append(
            {
                "kind": "overlapping_source_intervals",
                "tournament_pairs": source_report["overlapping_source_intervals"],
                "message": "The club-stated windows of linked interpretations overlap; the club must map them first.",
            }
        )

    preview: dict[str, Any] = {
        "season": season,
        "source_id": source_id,
        "club": club,
        "source_document": str(source.get("source_document") or ""),
        "source_version": str(source.get("source_version") or ""),
        "source_fingerprint": str(source.get("source_fingerprint") or ""),
        "canonical_state_revision": revision,
        "interpretations": interpretations,
        # Compatibility for callers introduced by the original PR.
        "proposed_assertions": interpretations,
        "count": len(interpretations),
        "conflicts": conflicts,
    }
    preview["content_digest"] = _content_digest(preview)
    preview["reviewed_token"] = _reviewed_token(preview)
    return preview
