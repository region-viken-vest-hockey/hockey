"""Event-to-tournament candidate evidence for the read-only booking assessment.

Each candidate records the evidence and counterevidence for one calendar event
that might be a tournament's booking. Candidates never decide the match; the
assessment in ``calendar_bookings`` classifies them.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Mapping

from tournament_scheduler.booking.ice_resources import (
    COVERAGE_COVERS,
    COVERAGE_DISJOINT,
    COVERAGE_PARTIAL,
    event_resource_ids,
    footprint_coverage,
    tournament_resource_footprint,
)
from tournament_scheduler.booking.interval_facts import (
    _parse_hhmm,
    event_covers_tournament_interval,
    event_fingerprint,
    tournament_occupancy_interval_facts,
)


RELATION_SAME_DATE_OVERLAP = "same_date_overlap"
RELATION_SAME_DATE_TIME_SHIFT = "same_date_time_shift"
RELATION_PROXIMATE_DATE_SHIFT = "proximate_date_shift"


_AGE_TOKEN_RE = re.compile(r"\b(J?U)\s?(\d{1,2})\b", re.IGNORECASE)

# Start and end minutes an event may differ from the canonical interval and
# still count as the same booking. Real calendars drift by a minute or two
# (see #692), so an exact match is too strict to be the only identity signal.
INTERVAL_IDENTITY_TOLERANCE_MINUTES = 15


def _assessment_parse_date(value: Any):
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _assessment_age_tokens(value: Any) -> set[str]:
    return {f"{prefix.upper()}{number}" for prefix, number in _AGE_TOKEN_RE.findall(str(value or ""))}


def _assessment_overlaps(
    tournament: Mapping[str, Any],
    event: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
) -> bool:
    interval = tournament_occupancy_interval_facts(tournament, problem)
    if str(event.get("date") or "") != interval["date"]:
        return False
    tournament_span = _parse_hhmm(interval["start_time"]), _parse_hhmm(interval["end_time"])
    event_span = _parse_hhmm(event.get("start")), _parse_hhmm(event.get("end"))
    if None in tournament_span or None in event_span:
        return False
    return tournament_span[0] < event_span[1] and event_span[0] < tournament_span[1]


def _near_exact_interval(
    tournament: Mapping[str, Any],
    event: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
) -> bool:
    """Return whether the event occupies the canonical interval within tolerance."""

    interval = tournament_occupancy_interval_facts(tournament, problem)
    if str(event.get("date") or "") != interval["date"]:
        return False
    event_start, event_end = _parse_hhmm(event.get("start")), _parse_hhmm(event.get("end"))
    tournament_start = _parse_hhmm(interval["start_time"])
    tournament_end = _parse_hhmm(interval["end_time"])
    if None in (event_start, event_end, tournament_start, tournament_end):
        return False
    tolerance = INTERVAL_IDENTITY_TOLERANCE_MINUTES
    return (
        abs(event_start - tournament_start) <= tolerance
        and abs(event_end - tournament_end) <= tolerance
    )


def assessment_candidate(
    tournament: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    problem: Mapping[str, Any] | None,
    date_window_days: int,
) -> dict[str, Any] | None:
    """Return one deterministic event<->tournament candidate row.

    Date is not a search boundary. A tournament can move from one end of the
    season to the other after it was planned, so every same-host event that is
    resource-compatible stays visible to the harness, however far from the
    planned date. ``date_delta_days`` is evidence for ranking.

    Plausible association identity is separate from mere occupancy. For a club
    with a declared resource footprint (Skien), an event is an identity
    candidate only when it carries an age-group match or occupies the canonical
    interval within tolerance on the same date. Any other same-host booking,
    however near in date, is kept as ``season_context_only``: it is listed for
    the harness but is not actionable and does not contest the tournament.
    Clubs without a declared footprint keep resource-agnostic evidence, so a
    same-host booking inside the observation window stays a changed-slot
    candidate. Nothing here decides the semantic match.
    """

    if str(event.get("club") or "") != str(tournament.get("host_club") or ""):
        return None
    event_arena = str(event.get("arena") or event.get("location") or "").strip()
    tournament_arena = str(tournament.get("arena") or "").strip()
    # An arena mismatch is counterevidence, not a reason to drop the event: a
    # genuinely moved booking may have changed venue as well as date/time, and
    # hiding it would leave a stale canonical row looking unmatched.
    arena_mismatch = bool(
        event_arena and tournament_arena and event_arena.lower() != tournament_arena.lower()
    )
    event_date = _assessment_parse_date(event.get("date"))
    tournament_date = _assessment_parse_date(tournament.get("date"))
    if event_date is None or tournament_date is None:
        return None
    date_delta_days = (event_date - tournament_date).days

    covers = event_covers_tournament_interval(event, tournament, problem)
    overlaps = _assessment_overlaps(tournament, event, problem)
    # The footprint is operator-confirmed (ADR 0007), but it still never discards
    # an observed booking. Partial or disjoint occupancy is listed as
    # non-actionable counterevidence, and the harness weighs it.
    resource_coverage = footprint_coverage(
        tournament_resource_footprint(str(tournament.get("host_club") or "")),
        event_resource_ids(event),
    )
    if date_delta_days == 0:
        relation = RELATION_SAME_DATE_OVERLAP if overlaps else RELATION_SAME_DATE_TIME_SHIFT
    else:
        relation = RELATION_PROXIMATE_DATE_SHIFT

    age_group = str(tournament.get("age_group") or "")
    age_tokens = _assessment_age_tokens(event.get("calendar_event") or event.get("title"))
    age_group_conflict = bool(age_tokens) and bool(age_group) and age_group.upper() not in age_tokens
    age_group_match = bool(age_tokens) and bool(age_group) and not age_group_conflict
    near_exact = _near_exact_interval(tournament, event, problem)
    if tournament_resource_footprint(str(tournament.get("host_club") or "")):
        # On a footprint-aware hall, a generic booking that only shares host and
        # footprint is context, not a competitor, however near it sits. It is
        # listed for the harness, and a matching age group or a near-exact
        # interval supplies the identity.
        identity_match = age_group_match or near_exact
    else:
        # Resource-agnostic clubs keep the observation-window rule: a far generic
        # booking is context only, but one inside the window can propose a
        # changed slot.
        identity_match = age_group_match or abs(date_delta_days) <= date_window_days
    season_context_only = not identity_match

    evidence: list[str] = []
    counterevidence: list[str] = []
    if relation == RELATION_SAME_DATE_OVERLAP:
        evidence.append("same_date_interval_overlap")
    if covers:
        evidence.append("event_covers_canonical_interval")
    if near_exact and not covers:
        evidence.append("event_interval_within_tolerance_of_canonical")
    if age_tokens and age_group and not age_group_conflict:
        evidence.append("event_title_age_group_matches")
    if date_delta_days == 0:
        evidence.append("same_calendar_date")
    else:
        counterevidence.append("event_date_differs_from_canonical")
    if season_context_only:
        counterevidence.append("event_lacks_identity_evidence")
    if not covers:
        counterevidence.append("canonical_slot_not_covered")
    if age_group_conflict:
        counterevidence.append("event_title_age_group_differs")
    if arena_mismatch:
        counterevidence.append("event_arena_differs_from_canonical")
    if resource_coverage == COVERAGE_COVERS:
        evidence.append("event_covers_resource_footprint")
    elif resource_coverage == COVERAGE_PARTIAL:
        counterevidence.append("event_occupies_partial_resource_footprint")
    elif resource_coverage == COVERAGE_DISJOINT:
        counterevidence.append("event_occupies_other_resources_only")

    return {
        "resource_coverage": resource_coverage,
        "event_fingerprint": str(event.get("fingerprint") or event_fingerprint(event)),
        "club": str(event.get("club") or ""),
        "date": str(event.get("date") or ""),
        "start": str(event.get("start") or ""),
        "end": str(event.get("end") or ""),
        "title": str(event.get("calendar_event") or event.get("title") or ""),
        "availability": str(event.get("availability") or ""),
        "relation": relation,
        "date_delta_days": date_delta_days,
        "covers_current_interval": bool(covers),
        "overlaps_current_interval": bool(overlaps),
        "age_group_conflict": age_group_conflict,
        "arena_mismatch": arena_mismatch,
        "season_context_only": season_context_only,
        "evidence": sorted(evidence),
        "counterevidence": sorted(counterevidence),
    }


def _date_window_covered(
    *,
    tournament_date: Any,
    source: Mapping[str, Any],
    date_window_days: int,
) -> bool:
    center = _assessment_parse_date(tournament_date)
    if center is None:
        return False
    window_start = center - timedelta(days=max(0, int(date_window_days)))
    window_end = center + timedelta(days=max(0, int(date_window_days)))
    # The navigated/observed window is the scraper's actual coverage claim.
    # ``event_observed_window`` is merely the first/last event date and would
    # let two sparse events bracket an unobserved gap, so it must never prove
    # that the intervening period was actually observed.
    observed = source.get("observed_window") or {}
    if not isinstance(observed, Mapping):
        return False
    observed_start = _assessment_parse_date(observed.get("start"))
    observed_end = _assessment_parse_date(observed.get("end"))
    if observed_start is None or observed_end is None:
        return False
    return observed_start <= window_start and observed_end >= window_end


def source_supports_negative_absence(
    *,
    tournament: Mapping[str, Any],
    source: Mapping[str, Any] | None,
    date_window_days: int,
) -> tuple[bool, list[str]]:
    if not source:
        return False, ["source_missing"]
    reasons: list[str] = []
    if source.get("source_trust") != "trusted":
        reasons.append("source_not_trusted")
    if source.get("source_integrity") != "complete":
        reasons.append("source_integrity_not_complete")
    if not source.get("coverage_proven"):
        reasons.append("source_coverage_not_proven")
    try:
        event_count = int(source.get("event_count") or 0)
    except (TypeError, ValueError):
        event_count = 0
    if event_count <= 0:
        reasons.append("no_other_events_in_source")
    if not _date_window_covered(
        tournament_date=tournament.get("date"),
        source=source,
        date_window_days=date_window_days,
    ):
        reasons.append("tournament_window_not_covered")
    return not reasons, reasons
