"""Choose the calendar event that is a tournament's primary observation (#853).

A single credible candidate is the observation. Where several candidates are
actionable only by age group or proximity, one exact same-day event that covers
the canonical interval is still the observation, provided no other same-day
candidate overlaps that day. A shared event or an unresolved identity never
yields one.

Only overlapping same-day claims compete for a slot. A claim on a neighbouring date,
or a same-day claim that does not overlap, is a changed-slot guess.
"""

from __future__ import annotations

from typing import Any, Collection, Mapping, Sequence

from tournament_scheduler.booking.assessment_candidates import RELATION_SAME_DATE_OVERLAP
from tournament_scheduler.booking.ice_resources import COVERAGE_COVERS

_AGE_EVIDENCE = "event_title_age_group_matches"
_NEAR_EXACT_EVIDENCE = "event_interval_within_tolerance_of_canonical"
_COVERS_EVIDENCE = "event_covers_canonical_interval"


def _eligible(candidate: Mapping[str, Any], shared: Collection[str], association_unresolved: bool) -> bool:
    if candidate["event_fingerprint"] in shared:
        return False
    evidence = candidate.get("evidence", [])
    identity = any(item in evidence for item in (_AGE_EVIDENCE, _NEAR_EXACT_EVIDENCE, _COVERS_EVIDENCE))
    if candidate.get("resource_coverage") == COVERAGE_COVERS and not identity:
        # A generic overlap on a full footprint is a venue contest, never the
        # tournament's observed booking (#859 review).
        return False
    return not association_unresolved or _AGE_EVIDENCE in candidate.get("evidence", [])


def select_primary_observation(
    actionable: Sequence[Mapping[str, Any]],
    *,
    shared_event_fingerprints: Collection[str],
    association_unresolved: bool,
) -> dict[str, Any] | None:
    """Return the primary observation among *actionable* candidates, or ``None``."""

    if len(actionable) == 1:
        only = actionable[0]
        return only if _eligible(only, shared_event_fingerprints, association_unresolved) else None
    same_day = [candidate for candidate in actionable if candidate.get("relation") == RELATION_SAME_DATE_OVERLAP]
    exact = [candidate for candidate in same_day if candidate.get("covers_current_interval")]
    if len(exact) != 1 or len(same_day) != 1:
        return None
    return exact[0] if _eligible(exact[0], shared_event_fingerprints, association_unresolved) else None


def shared_event_fingerprints(candidates_by_event: Mapping[str, Sequence[tuple[str, Mapping[str, Any]]]]) -> set[str]:
    """Return events that compete for one slot: overlapping same-day claims by two or more tournaments.

    A claim on another date, or a same-day claim that does not overlap the event,
    is only a changed-slot guess. It does not occupy the event's slot, so it is
    not a competitor.
    """

    return {
        event_fp
        for event_fp, entries in candidates_by_event.items()
        if len(
            {
                tournament_id
                for tournament_id, candidate in entries
                if candidate.get("actionable") and candidate.get("relation") == RELATION_SAME_DATE_OVERLAP
            }
        )
        > 1
    }
