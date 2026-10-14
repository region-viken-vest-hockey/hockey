"""Club-profile integration for planning availability and verification."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from tournament_scheduler.booking.club_profile_availability import (
    club_controlled_available_policy,
)
from tournament_scheduler.booking.ice_resources import event_affects_tournament_footprint


def filter_intervals_by_footprint(
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]],
    club: Optional[str],
    on_date: date,
) -> List[Dict[str, Any]]:
    """Return same-date intervals that affect the club's tournament footprint."""

    if not club_busy_intervals or not club:
        return []
    return [
        entry
        for entry in club_busy_intervals.get(club, [])
        if entry.get("date") == on_date.isoformat()
        and event_affects_tournament_footprint(club, entry)
    ]


def profile_allocation_record(interval: Any, duration_minutes: int) -> Dict[str, Any] | None:
    """Project profile-authorized capacity used by one tournament interval."""

    if not interval.host_club:
        return None
    controlled = club_controlled_available_policy(
        interval.host_club,
        interval.start.date(),
        interval.start.strftime("%H:%M"),
        duration_minutes,
    )
    if controlled is None:
        return None
    return {
        "tournament_id": interval.tournament_id,
        "host_club": interval.host_club,
        "age_group": interval.age_group,
        "date": interval.date,
        "interval": interval.interval_label,
        **controlled,
    }
