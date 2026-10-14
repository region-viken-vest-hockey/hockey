"""Club-profile availability classification for candidate weekends.

This module bridges the club-profile-driven ``club_controlled_available``
classification into the candidate-weekend evidence slice, so the suggestion
ranking treats profile-authorized internal allocation on par with
host-controlled ``movable_busy`` ice.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from tournament_scheduler.booking.calendar_availability import (
    CLASSIFICATION_SOURCE_INFERRED,
    classify_club_event_detailed,
)
from tournament_scheduler.booking.club_profile_availability import (
    CLUB_CONTROLLED_AVAILABLE,
    club_controlled_available_policy,
)


# Availability labels surfaced to the operator.
AVAILABILITY_FREE = "free"
AVAILABILITY_CLUB_CONTROLLED = CLUB_CONTROLLED_AVAILABLE
AVAILABILITY_MOVABLE = "movable_busy"
AVAILABILITY_UNKNOWN = "unknown"


def classify_candidate_availability(
    calendar_known: bool,
    movable: dict[str, Any] | None,
    host_club: str,
    candidate_date: date,
    start_time: str,
    duration_minutes: int,
) -> tuple[str, bool, str, str]:
    """Classify one date's ice for the host at a concrete interval.

    Returns ``(availability, requires_host_confirmation, calendar_event,
    classification_source)``. ``movable`` is the raw overlapping interval, if
    any. An untrusted calendar is reported as explicit ``unknown`` capacity
    that needs host confirmation rather than silently looking free.

    Profile-authorized ``club_controlled_available`` is treated as usable
    capacity without host confirmation (the profile itself grants authority).
    """
    if movable is not None:
        event_title = str(movable.get("calendar_event") or "")
        source = str(movable.get("classification_source") or "")
        if source == CLASSIFICATION_SOURCE_INFERRED:
            return AVAILABILITY_MOVABLE, True, event_title, source
        _, _, derived = classify_club_event_detailed(host_club, event_title)
        return AVAILABILITY_MOVABLE, True, event_title, source or derived

    controlled = club_controlled_available_policy(
        host_club, candidate_date, start_time, duration_minutes
    )
    if controlled is not None:
        return (
            CLUB_CONTROLLED_AVAILABLE,
            False,
            "",
            f"club_profile:{controlled['policy_id']}",
        )
    if not calendar_known:
        return AVAILABILITY_UNKNOWN, True, "", "unknown"
    return AVAILABILITY_FREE, False, "", "trusted_calendar"