"""Assessment classification and primary observation for one tournament."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.assessment_candidates import (
    ASSESSMENT_PRESUMED_UNSCHEDULED,
    RELATION_SAME_DATE_OVERLAP,
    source_supports_negative_absence,
)
from tournament_scheduler.booking.booking_vocabulary import (
    ASSESSMENT_AMBIGUOUS,
    ASSESSMENT_ASSOCIATED,
    ASSESSMENT_COMPETING_CANDIDATES,
    ASSESSMENT_MANUALLY_ASSERTED,
    ASSESSMENT_NOT_CHECKABLE,
    ASSESSMENT_PROPOSED_CHANGED_SLOT,
    ASSESSMENT_PROPOSED_UNCHANGED,
    ASSESSMENT_UNMATCHED,
    BOOKING_AUTHORITY_CALENDAR,
    BOOKING_AUTHORITY_MANUAL,
    BOOKING_AUTHORITY_MANUAL_INTERPRETATION,
    DEFAULT_ASSESSMENT_DATE_WINDOW_DAYS,
)
from tournament_scheduler.booking.ice_resources import COVERAGE_PARTIAL
from tournament_scheduler.booking.interval_facts import event_fingerprint, tournament_occupancy_interval_facts
from tournament_scheduler.booking.manual_assertions import (
    manual_assertion_for_tournament,
    manual_assertion_projection_status,
    manual_assertion_stale_reasons,
)
from tournament_scheduler.booking.primary_observation import select_primary_observation


def _assessment_tournament_row(
    tournament: Mapping[str, Any],
    *,
    candidates: list[dict[str, Any]],
    problem: Mapping[str, Any] | None,
    decisions: Mapping[str, Any] | None,
    associated_event: Mapping[str, Any] | None,
    source_trusted: bool,
    source_positive_evidence_usable: bool,
    source: Mapping[str, Any] | None = None,
    date_window_days: int = DEFAULT_ASSESSMENT_DATE_WINDOW_DAYS,
    shared_event_fingerprints: set[str] | None = None,
    association_unresolved: bool = False,
) -> dict[str, Any]:
    tournament_id = str(tournament.get("id") or "")
    shared_event_fingerprints = shared_event_fingerprints or set()
    manual = manual_assertion_for_tournament(decisions, tournament_id)
    manual_stale_reasons = (
        manual_assertion_stale_reasons(manual, problem=problem, tournament=tournament)
        if manual is not None
        else []
    )
    has_manual_authority = manual is not None and not manual_stale_reasons
    source_supports_absence, absence_blockers = source_supports_negative_absence(
        tournament=tournament,
        source=source,
        date_window_days=date_window_days,
    )
    # Observations with contradicting title/arena evidence stay in the report
    # but are not credible competing matches; only actionable candidates may
    # contest or resolve a proposal.
    actionable = [candidate for candidate in candidates if candidate.get("actionable")]
    # Season-context-only rows lack identity evidence. They are listed for the
    # harness but do not contest the row.
    contesting = [candidate for candidate in candidates if not candidate.get("season_context_only")]
    if has_manual_authority:
        classification = ASSESSMENT_MANUALLY_ASSERTED
    elif associated_event is not None:
        classification = ASSESSMENT_ASSOCIATED
    elif not source_positive_evidence_usable:
        classification = ASSESSMENT_NOT_CHECKABLE
    elif not contesting:
        classification = ASSESSMENT_PRESUMED_UNSCHEDULED if source_supports_absence else ASSESSMENT_UNMATCHED
    elif len(actionable) > 1:
        # Several credible events map to the same tournament; the assessment
        # refuses to pick one and leaves the competition visible.
        classification = ASSESSMENT_COMPETING_CANDIDATES
    elif len(actionable) == 1:
        candidate = actionable[0]
        if candidate["event_fingerprint"] in shared_event_fingerprints:
            # One credible event plausibly belongs to more than one tournament
            # (one-to-many or group booking); do not silently bind it here.
            classification = ASSESSMENT_COMPETING_CANDIDATES
        elif candidate["covers_current_interval"] and candidate["relation"] == RELATION_SAME_DATE_OVERLAP:
            classification = ASSESSMENT_PROPOSED_UNCHANGED
        else:
            classification = ASSESSMENT_PROPOSED_CHANGED_SLOT
    else:
        # Only conflicting/observation candidates remain.  If the source is
        # complete for this tournament's window, non-actionable observations at
        # the wrong arena/age group do not block a negative finding for the
        # canonical booking; otherwise keep the row ambiguous. Partial use of
        # the required resource footprint during the interval is different: the
        # hall is genuinely occupied, so it is never silently read as absence.
        partial_resource_overlap = any(
            candidate.get("resource_coverage") == COVERAGE_PARTIAL
            and candidate.get("overlaps_current_interval")
            for candidate in candidates
        )
        classification = (
            ASSESSMENT_PRESUMED_UNSCHEDULED
            if source_supports_absence and not partial_resource_overlap
            else ASSESSMENT_AMBIGUOUS
        )

    if has_manual_authority:
        authority: str | None = (
            BOOKING_AUTHORITY_MANUAL_INTERPRETATION
            if str(manual.get("source_scope") or "") == "club_wide_interpretation"
            else BOOKING_AUTHORITY_MANUAL
        )
    elif associated_event is not None:
        authority = BOOKING_AUTHORITY_CALENDAR
    else:
        authority = None

    interval = tournament_occupancy_interval_facts(tournament, problem)
    row: dict[str, Any] = {
        "tournament_id": tournament_id,
        "classification": classification,
        "host_club": str(tournament.get("host_club") or ""),
        "age_group": str(tournament.get("age_group") or ""),
        "arena": str(tournament.get("arena") or ""),
        "date": interval["date"],
        "start_time": interval["start_time"],
        "duration_minutes": interval["duration_minutes"],
        "end_time": interval["end_time"],
        "canonical_interval": interval,
        # Authority (a deliberate association/assertion) is reported separately
        # from whether the current calendar source is checkable.  An explicit
        # authority can stand even when the scrape is untrusted; the source
        # review flag never silently demotes it.
        "authority": authority,
        "source_trusted": source_trusted,
        "calendar_source_checkable": source_trusted,
        # Full coverage: trusted, complete, proven window and events present.
        # Only this, not mere readability, may show that an identity is lost.
        "calendar_covers_window": bool(source_supports_absence),
        "source_positive_evidence_usable": source_positive_evidence_usable,
        "proposal_is_binding": False,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    if classification == ASSESSMENT_PRESUMED_UNSCHEDULED:
        row["negative_evidence"] = {
            "reason": "complete_trusted_calendar_window_without_plausible_match",
            "absence_window_days": int(date_window_days),
            "source_fingerprint": (source or {}).get("calendar_fingerprint"),
            "source_event_count": (source or {}).get("event_count"),
            # The verified navigated window that passed `_date_window_covered`,
            # never the first/last-event extent it deliberately ignores.
            "observed_window": (source or {}).get("observed_window"),
        }
    elif absence_blockers:
        row["absence_evidence_blockers"] = absence_blockers
    if has_manual_authority:
        row["evidence_reason"] = "accepted_manual_authority"
    elif contesting and source_positive_evidence_usable and not source_trusted:
        row["evidence_reason"] = "event_evidence_usable"
    elif not source_trusted:
        row["evidence_reason"] = "source_coverage_unproven"
    if classification in {ASSESSMENT_AMBIGUOUS, ASSESSMENT_COMPETING_CANDIDATES}:
        row.setdefault("evidence_reasons", []).append("association_ambiguous")
    if association_unresolved:
        row["association_identity_unresolved"] = True
    if associated_event is not None:
        row["associated_event_fingerprint"] = str(
            associated_event.get("fingerprint") or event_fingerprint(associated_event)
        )
    # Expose one canonical selection for downstream projections. A shared or
    # competing event deliberately has no primary candidate: choosing one would
    # turn unresolved evidence into an implied observed booking.
    primary_candidate = None
    if associated_event is not None:
        associated_fingerprint = row["associated_event_fingerprint"]
        primary_candidate = next(
            (
                candidate
                for candidate in actionable
                if candidate["event_fingerprint"] == associated_fingerprint
            ),
            None,
        )
    else:
        # With an unresolved association, proximity alone is not the same booking (#847).
        primary_candidate = select_primary_observation(
            actionable,
            shared_event_fingerprints=shared_event_fingerprints,
            association_unresolved=association_unresolved,
        )
    if primary_candidate is not None:
        row["primary_candidate_event_fingerprint"] = primary_candidate["event_fingerprint"]
    if manual is not None:
        row["manual_authority"] = str(manual.get("authority") or "")
        row["manual_booking_status"] = manual_assertion_projection_status(manual)
        if manual_stale_reasons:
            row["manual_assertion_stale_reasons"] = manual_stale_reasons
    return row
