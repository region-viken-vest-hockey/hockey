"""Conflict-aware manual-placement candidate weekends.

When the bounded responsibility-preserving repair (``responsibility_preserving_repair``
+ ``host_placement_repair``) cannot commit an automatic placement, the operator
still needs to see *which* concrete weekends could work for the responsible host
and why the others did not. This module owns that suggestion slice.

It is deliberately **evidence-only**: it never mutates the candidate, never
transfers hosting responsibility and never claims a full candidate verification.
It reuses the availability facts owned by ``calendar_availability`` /
``planning_contract`` (``fixed_busy``, ``movable_busy``, ``free``, ``unknown``)
and checks the proposed roster for same-team/same-date collisions, so a weekend
is never suggested as usable when one of its teams already plays that date
unless a hard-valid replacement roster is supplied by the caller.

Ownership boundary:

* this module owns the candidate-date availability classification, the
  team/date collision check, deterministic rejection reasons and the ranking
  preference between otherwise-valid alternatives;
* the caller owns the candidate-date set and the (optional) replacement-roster
  generation, because those depend on planner/participant-selection state;
* committing a placement, regenerating games and running the independent
  verifier remain the existing repair engine's job -- this slice only proposes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Hashable, List, Mapping, Optional, Sequence, Set, Tuple

from .calendar_availability import (
    CLASSIFICATION_SOURCE_INFERRED,
    classify_club_event_detailed,
)
from .planning_contract import (
    _parse_date,
    external_calendar_conflict,
    movable_calendar_opportunity,
)

# Availability labels surfaced to the operator. ``unknown`` mirrors the
# ambiguous scraped-event case: it is an honest fact, never assumed free.
AVAILABILITY_FREE = "free"
AVAILABILITY_MOVABLE = "movable_busy"
AVAILABILITY_UNKNOWN = "unknown"

# Ranking preference (lower is better). Mirrors the issue's suggestion order:
# verified free ice + current roster first, then host-controlled movable ice,
# then the same with a hard-valid replacement roster, and ambiguous capacity
# last (because it always needs host confirmation).
_RANK_FREE_CURRENT = 0
_RANK_MOVABLE_CURRENT = 1
_RANK_FREE_ALTERNATE = 2
_RANK_MOVABLE_ALTERNATE = 3
_RANK_UNKNOWN_CURRENT = 4
_RANK_UNKNOWN_ALTERNATE = 5

DEFAULT_MAX_SUGGESTIONS = 5
DEFAULT_MAX_DATES = 24

#: A roster replacement callback: given a candidate date, return
#: ``(team_keys, display_roster)`` for a hard-valid roster that still
#: represents the responsible host, or ``None`` when none exists.
ReplacementRoster = Callable[[date], Optional[Tuple[Set[Hashable], List[Dict[str, str]]]]]


@dataclass(frozen=True)
class CandidateWeekend:
    """One manual-placement candidate weekend for the responsible host."""

    host_club: str
    date: str
    start_time: str
    end_time: str
    availability: str
    requires_host_confirmation: bool
    calendar_event: str
    classification_source: str
    roster_source: str
    roster: Tuple[Dict[str, str], ...]
    team_conflicts: Tuple[str, ...]
    reason: Optional[str]

    @property
    def usable(self) -> bool:
        """True when nothing disqualifies the weekend as an operator option."""
        return self.reason is None

    def rank(self) -> int:
        alternate = self.roster_source == "alternate"
        if self.availability == AVAILABILITY_FREE:
            return _RANK_FREE_ALTERNATE if alternate else _RANK_FREE_CURRENT
        if self.availability == AVAILABILITY_MOVABLE:
            return _RANK_MOVABLE_ALTERNATE if alternate else _RANK_MOVABLE_CURRENT
        return _RANK_UNKNOWN_ALTERNATE if alternate else _RANK_UNKNOWN_CURRENT

    def to_dict(self) -> Dict[str, Any]:
        return {
            "host_club": self.host_club,
            "date": self.date,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "availability": self.availability,
            "requires_host_confirmation": self.requires_host_confirmation,
            "calendar_event": self.calendar_event,
            "classification_source": self.classification_source,
            "roster_source": self.roster_source,
            "roster": [dict(team) for team in self.roster],
            "team_conflicts": list(self.team_conflicts),
            "usable": self.usable,
            "reason": self.reason,
            "rank": self.rank(),
        }


def season_weekend_dates(problem: Mapping[str, Any]) -> List[date]:
    """Every Saturday/Sunday inside the planning window.

    A convenience default for callers that do not already carry the
    scheduler's available-date set. It is intentionally a *superset*: it makes
    no claim about holidays/locked dates, which the caller's date set owns.
    """
    start = _parse_date(problem.get("start_date"))
    end = _parse_date(problem.get("end_date"))
    if start is None or end is None or end < start:
        return []
    out: List[date] = []
    day = start
    while day <= end:
        if day.weekday() >= 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def enumerate_candidate_weekends(
    problem: Mapping[str, Any],
    *,
    host_club: str,
    team_keys: Set[Hashable],
    candidate_dates: Sequence[date],
    duration_minutes: int,
    preferred_start_time: str,
    candidate_start_times: Optional[Sequence[str]] = None,
    occupancy: Optional[Mapping[str, Set[Hashable]]] = None,
    team_labels: Optional[Mapping[str, str]] = None,
    current_roster: Optional[Sequence[Mapping[str, str]]] = None,
    replacement_roster: Optional[ReplacementRoster] = None,
    max_suggestions: int = DEFAULT_MAX_SUGGESTIONS,
    max_dates: int = DEFAULT_MAX_DATES,
) -> Dict[str, Any]:
    """Rank conflict-aware manual candidate weekends for one host.

    Parameters
    ----------
    problem:
        The normalized ``planning_problem`` (availability facts live here).
    host_club:
        The responsible host. Only this club's ice is ever considered.
    team_keys:
        Keys identifying the current roster's teams, in the caller's canonical
        key space (``SeasonPlanner._team_key`` / ``models.team_key``).
    candidate_dates:
        Bounded, ordered set of dates to consider. Evaluation stops after
        ``max_dates``; the order is preserved.
    duration_minutes:
        Required occupied duration, used only to derive the reported end time
        and to detect a fixed-busy overlap.
    preferred_start_time:
        Start time to try first and report when free.
    candidate_start_times:
        Ordered start times to fall back to when ``preferred_start_time``
        overlaps a fixed booking on a date. Defaults to just the preferred
        start, so a date is rejected as ``fixed_busy`` only when no reported
        start fits.
    occupancy:
        ``date_iso -> team keys already playing that date``. Usually derived
        from the candidate's already-built tournaments. Absent/empty means no
        known collision.
    replacement_roster:
        Optional callback that returns ``(team_keys, display_roster)`` for a
        hard-valid replacement roster that keeps the responsible host
        represented, or ``None``. It is only consulted when the current roster
        collides on a date.
    """
    occupancy = occupancy or {}
    team_labels = team_labels or {}
    busy_intervals = problem.get("club_busy_intervals") or {}
    calendar_known = _calendar_known(problem, host_club)

    all_dates = list(candidate_dates)
    bounded_dates = all_dates[:max_dates]
    budget_exhausted = len(all_dates) > len(bounded_dates)

    candidates: List[CandidateWeekend] = []
    rejected: List[Dict[str, Any]] = []

    for candidate_date in bounded_dates:
        date_iso = candidate_date.isoformat()
        # Prefer the current start time, then any other legal same-host start,
        # so a date whose preferred slot is fixed-busy is only rejected when no
        # reported start time fits -- matching the repair engine's same-date
        # start-time search without re-deriving it.
        start_time = next(
            (
                start
                for start in (candidate_start_times or [preferred_start_time])
                if not external_calendar_conflict(
                    busy_intervals, host_club, candidate_date, start, duration_minutes
                )
            ),
            None,
        )
        if start_time is None:
            rejected.append(_rejection(host_club, date_iso, "fixed_busy"))
            continue

        movable = movable_calendar_opportunity(
            busy_intervals, host_club, candidate_date, start_time, duration_minutes
        )
        availability, requires_confirmation, event_title, source = _availability(
            calendar_known, movable, host_club
        )

        conflicts = _conflicts(team_keys, occupancy.get(date_iso))
        if conflicts:
            replacement = replacement_roster(candidate_date) if replacement_roster else None
            if replacement is None:
                rejected.append(
                    _rejection(
                        host_club,
                        date_iso,
                        "team_already_plays",
                        conflicts=conflicts,
                        team_labels=team_labels,
                        availability=availability,
                    )
                )
                continue
            alt_keys, alt_roster = replacement
            alt_conflicts = _conflicts(alt_keys, occupancy.get(date_iso))
            if alt_conflicts:
                rejected.append(
                    _rejection(
                        host_club,
                        date_iso,
                        "replacement_roster_still_conflicts",
                        conflicts=alt_conflicts,
                        team_labels=team_labels,
                        availability=availability,
                    )
                )
                continue
            roster_source = "alternate"
            roster = tuple(dict(team) for team in alt_roster)
        else:
            roster_source = "current"
            roster = tuple(dict(team) for team in (current_roster or []))

        candidates.append(
            CandidateWeekend(
                host_club=host_club,
                date=date_iso,
                start_time=start_time,
                end_time=_end_time(start_time, duration_minutes),
                availability=availability,
                requires_host_confirmation=requires_confirmation,
                calendar_event=event_title,
                classification_source=source,
                roster_source=roster_source,
                roster=roster,
                team_conflicts=tuple(),
                reason=None,
            )
        )

    candidates.sort(key=lambda candidate: (candidate.rank(), candidate.date))
    usable = candidates[:max_suggestions]
    if not usable:
        status = "search_budget_exhausted" if budget_exhausted else "bounded_date_set_exhausted"
    else:
        status = "suggestions"

    return {
        "host_club": host_club,
        "dates_considered": [d.isoformat() for d in bounded_dates],
        "candidate_weekends": [candidate.to_dict() for candidate in usable],
        "rejected_candidate_dates": rejected,
        "status": status,
        "bounded_date_set_exhausted": not budget_exhausted,
        "search_budget_exhausted": budget_exhausted,
    }


def _calendar_known(problem: Mapping[str, Any], host_club: str) -> bool:
    statuses = problem.get("club_calendar_status") or {}
    if not statuses:
        return False
    return statuses.get(host_club) == "known"


def _availability(
    calendar_known: bool,
    movable: Optional[Mapping[str, Any]],
    host_club: str,
) -> Tuple[str, bool, str, str]:
    """Classify one date's ice for the host at a concrete interval.

    Returns ``(availability, requires_host_confirmation, calendar_event,
    classification_source)``. ``movable`` is the raw overlapping interval, if
    any. An untrusted calendar is reported as explicit ``unknown`` capacity
    that needs host confirmation rather than silently looking free.
    """
    if movable is not None:
        event_title = str(movable.get("calendar_event") or "")
        source = str(movable.get("classification_source") or "")
        if source == CLASSIFICATION_SOURCE_INFERRED:
            return AVAILABILITY_MOVABLE, True, event_title, source
        _, _, derived = classify_club_event_detailed(host_club, event_title)
        return AVAILABILITY_MOVABLE, True, event_title, source or derived
    if not calendar_known:
        return AVAILABILITY_UNKNOWN, True, "", "unknown"
    return AVAILABILITY_FREE, False, "", "trusted_calendar"


def _conflicts(team_keys: Set[Hashable], occupied: Optional[Set[Hashable]]) -> List[Hashable]:
    if not team_keys or not occupied:
        return []
    return sorted(team_keys & set(occupied))


def _rejection(
    host_club: str,
    date_iso: str,
    reason: str,
    *,
    conflicts: Optional[Sequence[Hashable]] = None,
    team_labels: Optional[Mapping[str, str]] = None,
    availability: Optional[str] = None,
) -> Dict[str, Any]:
    labels = team_labels or {}
    entry: Dict[str, Any] = {
        "host_club": host_club,
        "date": date_iso,
        "reason": reason,
        "usable": False,
    }
    if conflicts:
        entry["team_conflicts"] = [labels.get(key, key) for key in conflicts]
    if availability:
        entry["availability"] = availability
    return entry


def _end_time(start: str, duration_minutes: int) -> str:
    try:
        parsed = datetime.strptime(start, "%H:%M")
    except (TypeError, ValueError):
        return ""
    return (parsed + timedelta(minutes=max(0, duration_minutes))).strftime("%H:%M")
