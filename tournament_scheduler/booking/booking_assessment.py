"""Read-only revision-bound tournament-to-event assessment orchestration."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from tournament_scheduler.booking.assessment_candidates import (
    ASSESSMENT_PRESUMED_UNSCHEDULED,
    assessment_candidate,
    assessment_candidate_rank,
)
from tournament_scheduler.booking.assessment_events import assessment_events
from tournament_scheduler.booking.assessment_sources import assessment_sources
from tournament_scheduler.booking.assessment_tournament import _assessment_tournament_row
from tournament_scheduler.booking.booking_facts import _tournaments_by_id
from tournament_scheduler.booking.booking_vocabulary import (
    _ASSESSMENT_UNRESOLVED,
    ASSESSMENT_AMBIGUOUS,
    ASSESSMENT_ASSOCIATED,
    ASSESSMENT_COMPETING_CANDIDATES,
    ASSESSMENT_MANUALLY_ASSERTED,
    ASSESSMENT_NOT_CHECKABLE,
    ASSESSMENT_PROPOSED_CHANGED_SLOT,
    ASSESSMENT_PROPOSED_UNCHANGED,
    ASSESSMENT_SCHEMA_VERSION,
    ASSESSMENT_UNMATCHED,
    DEFAULT_ASSESSMENT_DATE_WINDOW_DAYS,
)
from tournament_scheduler.booking.calendar_associations import association_findings, valid_active_associations
from tournament_scheduler.booking.calendar_evidence import iter_events
from tournament_scheduler.booking.ice_resources import (
    COVERAGE_DISJOINT,
    COVERAGE_PARTIAL,
    tournament_resource_footprint,
)
from tournament_scheduler.booking.interval_facts import event_fingerprint
from tournament_scheduler.booking.primary_observation import shared_event_fingerprints as shared_event_fingerprints_for
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256


def booking_assessment(
    *,
    problem: Mapping[str, Any] | None,
    plan: Mapping[str, Any] | None,
    decisions: Mapping[str, Any] | None,
    canonical_state_revision: str,
    season: str = "",
    clubs: Iterable[str] | None = None,
    date_window_days: int = DEFAULT_ASSESSMENT_DATE_WINDOW_DAYS,
) -> dict[str, Any]:
    """Build a deterministic, read-only tournament<->event booking crosswalk.

    The result is bound to the exact canonical revision and to each club's
    calendar fingerprint, so a second agent rerunning it against the same frozen
    inputs reproduces the same classifications.  It never writes canonical
    state, never confirms a booking and never claims that calendar absence proves
    a tournament is unbooked -- suspicious/untrusted sources fail closed to
    ``not_checkable`` and unresolved semantics stay visible.
    """

    tournaments = list(_tournaments_by_id(plan).values())
    # A cancelled tournament occupies no ice and has no current booking to
    # confirm against a calendar event, so it must not appear as an active
    # assessment row or be counted among unresolved/ambiguous bookings.
    tournaments = [
        tournament
        for tournament in tournaments
        if not tournament.get("cancelled")
    ]
    events = list(iter_events(problem))
    if clubs is not None:
        wanted = {str(club) for club in clubs}
        tournaments = [t for t in tournaments if str(t.get("host_club") or "") in wanted]
        events = [e for e in events if str(e.get("club") or "") in wanted]

    sources, trusted_clubs, positive_evidence_usable_clubs = assessment_sources(
        problem=problem, tournaments=tournaments, events=events,
    )
    all_clubs = sorted(sources)

    events_by_club: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        events_by_club.setdefault(str(event.get("club") or ""), []).append(event)

    valid_associations = valid_active_associations(decisions, problem=problem, plan=plan)
    association_by_tournament = {
        str(record.get("tournament_id") or ""): str(record.get("event_fingerprint") or "")
        for record in valid_associations
        if str(record.get("tournament_id") or "")
    }
    event_by_fingerprint = {
        str(event.get("fingerprint") or event_fingerprint(event)): event for event in events
    }

    tournament_by_id: dict[str, Mapping[str, Any]] = {
        str(tournament.get("id") or ""): tournament for tournament in tournaments
    }
    candidates_by_tournament: dict[str, list[dict[str, Any]]] = {}
    candidates_by_event: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for tournament in tournaments:
        tournament_id = str(tournament.get("id") or "")
        club = str(tournament.get("host_club") or "")
        candidates: list[dict[str, Any]] = []
        for event in events_by_club.get(club, []):
            candidate = assessment_candidate(
                tournament,
                event,
                problem=problem,
                date_window_days=date_window_days,
            )
            if candidate is not None:
                # Full-window trust is required for negative claims, but a
                # partially covered source may still contribute authentic
                # observed events to positive reconciliation. Contradicting
                # title/arena evidence still blocks actionability.
                candidate["source_trusted"] = club in trusted_clubs
                candidate["source_positive_evidence_usable"] = club in positive_evidence_usable_clubs
                candidate["actionable"] = bool(
                    candidate["source_positive_evidence_usable"]
                    and not candidate["season_context_only"]
                    and not candidate["age_group_conflict"]
                    and not candidate["arena_mismatch"]
                    and candidate["resource_coverage"] not in {COVERAGE_PARTIAL, COVERAGE_DISJOINT}
                )
                candidate["footprint_aware"] = bool(tournament_resource_footprint(club))
                candidates.append(candidate)
                candidates_by_event.setdefault(candidate["event_fingerprint"], []).append(
                    (tournament_id, candidate)
                )
        candidates.sort(key=assessment_candidate_rank)
        candidates_by_tournament[tournament_id] = candidates

    # An actionable event that plausibly belongs to more than one tournament is
    # one-to-many (or a group booking); every affected tournament competes for
    # it instead of the assessment silently binding it to whichever row sorts
    # first. Non-actionable observations do not create competition.
    shared_event_fingerprints = shared_event_fingerprints_for(candidates_by_event)

    tournament_rows: list[dict[str, Any]] = []
    for tournament_id, tournament in tournament_by_id.items():
        club = str(tournament.get("host_club") or "")
        associated_fp = association_by_tournament.get(tournament_id)
        associated_event = event_by_fingerprint.get(associated_fp) if associated_fp else None
        # A confirmed association whose event is missing or retitled keeps its
        # authority. No other event may stand in for it, so the row shows an
        # unresolved identity rather than a match (#847).
        association_unresolved = bool(associated_fp) and associated_event is None
        tournament_rows.append(
            _assessment_tournament_row(
                tournament,
                candidates=candidates_by_tournament.get(tournament_id, []),
                problem=problem,
                decisions=decisions,
                associated_event=associated_event,
                source_trusted=club in trusted_clubs,
                source_positive_evidence_usable=club in positive_evidence_usable_clubs,
                source=sources.get(club),
                date_window_days=date_window_days,
                shared_event_fingerprints=shared_event_fingerprints,
                association_unresolved=association_unresolved,
            )
        )
    tournament_rows.sort(key=lambda row: row["tournament_id"])

    events_rows = assessment_events(
        events=events,
        candidates_by_event=candidates_by_event,
        tournament_by_id=tournament_by_id,
    )

    counts: dict[str, int] = {
        ASSESSMENT_ASSOCIATED: 0,
        ASSESSMENT_MANUALLY_ASSERTED: 0,
        ASSESSMENT_PROPOSED_UNCHANGED: 0,
        ASSESSMENT_PROPOSED_CHANGED_SLOT: 0,
        ASSESSMENT_COMPETING_CANDIDATES: 0,
        ASSESSMENT_AMBIGUOUS: 0,
        ASSESSMENT_UNMATCHED: 0,
        ASSESSMENT_PRESUMED_UNSCHEDULED: 0,
        ASSESSMENT_NOT_CHECKABLE: 0,
    }
    for row in tournament_rows:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    counts["unresolved_tournaments"] = sum(
        1 for row in tournament_rows if row["classification"] in _ASSESSMENT_UNRESOLVED
    )
    counts["unresolved_events"] = sum(
        1 for row in events_rows if row["unmatched"] or row["one_to_many"]
    )
    findings = association_findings(problem=problem, plan=plan, decisions=decisions)
    counts["stale_associations"] = sum(
        1 for finding in findings if finding.get("code") == "stale_calendar_booking_association"
    )

    unresolved = {
        "tournament_ids": sorted(
            row["tournament_id"] for row in tournament_rows if row["classification"] in _ASSESSMENT_UNRESOLVED
        ),
        "event_fingerprints": sorted(
            row["event_fingerprint"] for row in events_rows if row["unmatched"] or row["one_to_many"]
        ),
    }

    assessment: dict[str, Any] = {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "season": season,
        "canonical_state_revision": str(canonical_state_revision),
        "date_window_days": int(date_window_days),
        "clubs": all_clubs,
        "sources": sources,
        "tournaments": tournament_rows,
        "events": events_rows,
        "counts": counts,
        "unresolved": unresolved,
        "findings": findings,
        "limitations": [
            "calendar_absence_is_non_binding_negative_evidence_only_when_source_window_is_complete",
            "presumed_unscheduled_never_cancels_or_deletes_a_tournament",
            "proposals_are_advisory_until_an_explicit_operator_confirmation",
            "source_coverage_proof_is_not_established_by_this_assessment",
        ],
    }
    assessment["assessment_fingerprint"] = stable_payload_sha256(assessment)
    return assessment
