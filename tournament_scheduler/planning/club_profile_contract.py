"""Club-profile integration for the planning contract verifier.

This module encapsulates the logic for filtering calendar intervals by a club's
tournament resource footprint and for classifying profile-authorized internal
allocation capacity in the independent verifier.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Tuple

from tournament_scheduler.booking.calendar_availability import CalendarAvailability
from tournament_scheduler.booking.club_profile_availability import (
    club_controlled_available_policy,
)
from tournament_scheduler.booking.ice_resources import event_affects_tournament_footprint
from tournament_scheduler.booking.calendar_bookings import (
    associated_tournament_for_event,
    event_fingerprint,
)


def filter_intervals_by_footprint(
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]],
    club: Optional[str],
    on_date: date,
) -> List[Dict[str, Any]]:
    """Return intervals for *club* on *on_date* that affect the tournament footprint.

    This is the shared filter used by fixed_busy_windows, movable_busy_windows,
    and external_calendar_conflict so planning and reconciliation use identical
    footprint logic.
    """
    if not club_busy_intervals or not club:
        return []
    date_str = on_date.isoformat()
    filtered: List[Dict[str, Any]] = []
    for entry in club_busy_intervals.get(club, []):
        if entry.get("date") != date_str:
            continue
        if not event_affects_tournament_footprint(str(club), entry):
            continue
        filtered.append(entry)
    return filtered


def classify_profile_allocation(
    host_club: str,
    on_date: date,
    start_time: str,
    duration_minutes: int,
) -> Optional[Dict[str, Any]]:
    """Return profile-authorized allocation classification if the interval is covered.

    Returns a dict with availability, authority, planning_semantics, policy_id,
    and evidence fields, or None if no profile policy covers the interval.
    """
    return club_controlled_available_policy(host_club, on_date, start_time, duration_minutes)


def collect_profile_allocations_used(
    tournament_obj: Any,
    ice_time_minutes: Mapping[str, int],
    overrides_by_tournament: Mapping[str, int] | None,
    problem: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Check if a tournament interval falls in club-controlled available profile windows.

    Returns a list of allocation records (typically 0 or 1) for the evidence bundle.
    """
    from tournament_scheduler.booking.arena_conflicts import tournament_interval
    from tournament_scheduler.season.ice_time_overrides import overrides_from_problem

    ice_time_overrides = overrides_from_problem(problem)
    interval = tournament_interval(
        tournament_obj,
        ice_time_minutes,
        overrides_by_tournament=ice_time_overrides,
    )
    if interval is None or not interval.host_club:
        return []

    duration_minutes = int((interval.end - interval.start).total_seconds() // 60)
    controlled = club_controlled_available_policy(
        interval.host_club,
        interval.start.date(),
        interval.start.strftime("%H:%M"),
        duration_minutes,
    )
    if controlled is None:
        return []

    return [{
        "tournament_id": interval.tournament_id,
        "host_club": interval.host_club,
        "age_group": interval.age_group,
        "date": interval.date,
        "interval": interval.interval_label,
        **controlled,
    }]