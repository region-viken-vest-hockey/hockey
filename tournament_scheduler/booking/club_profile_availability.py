"""Club-profile-driven availability: evidence-backed internal allocation authority.

This module exposes the ``club_controlled_available`` classification which is
distinct from both scraper-proven ``free`` time and existing ``movable_busy``
bookings. It is granted by a validated club profile, not inferred from a
calendar scrape.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from tournament_scheduler.booking.calendar_availability import CalendarAvailability
from tournament_scheduler.clubs.profiles import get_club_profile


def club_controlled_available_policy(
    club: str,
    on_date: date,
    start_time: str,
    duration_minutes: int,
) -> dict[str, str] | None:
    """Return configured internal-allocation authority covering an interval.

    This is intentionally separate from ``FREE`` and ``MOVABLE_BUSY``: the
    interval is not a negative inference from a scrape and no existing event is
    claimed to be displaced. The evidence-backed profile itself grants planning
    authority.
    """

    if duration_minutes <= 0:
        return None
    if not isinstance(on_date, date):
        return None
    try:
        start = datetime.strptime(start_time, "%H:%M")
    except (TypeError, ValueError):
        return None
    end_time = (start + timedelta(minutes=duration_minutes)).strftime("%H:%M")
    profile = get_club_profile(club)
    policy = profile.availability_for_interval(on_date, start_time, end_time) if profile else None
    if policy is None:
        return None
    return {
        "availability": CalendarAvailability.CLUB_CONTROLLED_AVAILABLE.value,
        "authority": policy.authority,
        "planning_semantics": policy.planning_semantics,
        "policy_id": policy.id,
        "evidence_kind": policy.evidence.kind,
        "evidence_reference": policy.evidence.reference,
        "evidence_summary": policy.evidence.summary,
    }


def is_club_controlled_available(club: str, on_date: date, start_time: str, duration_minutes: int) -> bool:
    """Convenience boolean check for profile-authorized internal allocation."""
    return club_controlled_available_policy(club, on_date, start_time, duration_minutes) is not None