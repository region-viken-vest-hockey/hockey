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


def assessment_candidate(
    tournament: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    problem: Mapping[str, Any] | None,
    date_window_days: int,
) -> dict[str, Any] | None:
    """Return one deterministic, bounded event<->tournament candidate row.

    Date is not a search boundary. A tournament can move from one end of the
    season to the other after it was planned, so an event far from the planned
    date stays a candidate when it carries an age-group match for the same host.
    Without that identity evidence, only events within ``date_window_days`` are
    kept. ``date_delta_days`` is evidence for ranking either way. It does **not**
    decide the semantic match: every row carries the evidence and
    counterevidence a harness/operator needs to adjudicate it.
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
    # A tournament that needs a resource footprint can only be booked by an
    # event that occupies all of it. A booking on other resources only is not
    # this tournament's booking; one that touches only part of the footprint is
    # kept visible as partial occupancy, never as a credible match.
    resource_coverage = footprint_coverage(
        tournament_resource_footprint(str(tournament.get("host_club") or "")),
        event_resource_ids(event),
    )
    if resource_coverage == COVERAGE_DISJOINT:
        return None
    if date_delta_days == 0:
        relation = RELATION_SAME_DATE_OVERLAP if overlaps else RELATION_SAME_DATE_TIME_SHIFT
    else:
        relation = RELATION_PROXIMATE_DATE_SHIFT

    age_group = str(tournament.get("age_group") or "")
    age_tokens = _assessment_age_tokens(event.get("calendar_event") or event.get("title"))
    age_group_conflict = bool(age_tokens) and bool(age_group) and age_group.upper() not in age_tokens
    age_group_match = bool(age_tokens) and bool(age_group) and not age_group_conflict
    # Only identity evidence (an age group that matches) may keep a far-off
    # event; a generic same-host booking far from the planned date is noise.
    if abs(date_delta_days) > date_window_days and not age_group_match:
        return None

    evidence: list[str] = []
    counterevidence: list[str] = []
    if relation == RELATION_SAME_DATE_OVERLAP:
        evidence.append("same_date_interval_overlap")
    if covers:
        evidence.append("event_covers_canonical_interval")
    if age_tokens and age_group and not age_group_conflict:
        evidence.append("event_title_age_group_matches")
    if date_delta_days == 0:
        evidence.append("same_calendar_date")
    else:
        counterevidence.append("event_date_differs_from_canonical")
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
