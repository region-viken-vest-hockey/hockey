"""Canonical tournament hall-occupancy duration contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Protocol

ROUND_BUFFER_MINUTES = 5
NIHF_SERIES_ROUND_MINIMUM_MINUTES = 120
NIHF_SERIES_ROUND_MINIMUM_AGE_GROUPS = frozenset({"U7", "JU7", "U8", "JU8", "U9", "U10", "JU10", "U11"})


class TournamentLike(Protocol):
    id: str
    age_group: str
    games: list
    start_time: str | None


@dataclass(frozen=True)
class OccupancyComponents:
    age_group: str
    configured_ice_time_minutes: int | None
    round_count: int
    round_buffer_minutes: int
    required_duration_minutes: int | None

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "age_group": self.age_group,
            "configured_ice_time_minutes": self.configured_ice_time_minutes,
            "round_count": self.round_count,
            "round_buffer_minutes": self.round_buffer_minutes,
            "required_duration_minutes": self.required_duration_minutes,
        }


def round_count_for_games(games: list) -> int:
    """Return the highest round number in *games*, or 0 for no games."""
    if not games:
        return 0
    return max(int(getattr(game, "round_number", 0) or 0) for game in games)


def minimum_playing_requirement_minutes(
    round_length_minutes: int | None,
    round_count: int,
    *,
    round_buffer_minutes: int = ROUND_BUFFER_MINUTES,
) -> int:
    """Return the minimum minutes needed for rounds plus changeovers."""
    if not isinstance(round_length_minutes, int) or round_length_minutes <= 0 or round_count <= 0:
        return 0
    return round_count * (round_length_minutes + max(0, round_buffer_minutes))


def governing_minimum_ice_time_minutes(age_group: str) -> int | None:
    """Return the governing per-series-round booking floor for *age_group*."""
    return NIHF_SERIES_ROUND_MINIMUM_MINUTES if age_group in NIHF_SERIES_ROUND_MINIMUM_AGE_GROUPS else None


def required_ice_minutes(ice_time_minutes: int | None, round_count: int, *, round_buffer_minutes: int = ROUND_BUFFER_MINUTES) -> int:
    """Return tournament hall occupancy minutes.

    ``ice_time_minutes`` is the complete booked/occupied window for the
    tournament. The per-round buffer remains part of format validation, but it
    is not added on top of the configured booking duration.
    """
    if not isinstance(ice_time_minutes, int) or ice_time_minutes <= 0 or round_count <= 0:
        return 0
    return ice_time_minutes


def occupancy_components(age_group: str, ice_time_by_age_group: Mapping[str, int], round_count: int) -> OccupancyComponents:
    ice_time = ice_time_by_age_group.get(age_group)
    buffer_minutes = ROUND_BUFFER_MINUTES * max(0, round_count)
    duration = required_ice_minutes(ice_time, round_count) if ice_time else None
    return OccupancyComponents(age_group, ice_time, round_count, buffer_minutes, duration)


def effective_ice_time_minutes(
    tournament: TournamentLike,
    ice_time_by_age_group: Mapping[str, int],
    overrides_by_tournament: Mapping[str, int] | None = None,
) -> int | None:
    """Return the effective configured occupancy for one tournament.

    A host-confirmed per-tournament override always wins over the flat
    age-group default; ``None`` means no positive duration is configured.
    Keeping this resolution in one place is what makes arena-conflict
    verification, calendar-booking coverage, exports and slot search describe
    the same interval.
    """

    tournament_id = str(getattr(tournament, "id", "") or "")
    explicit = getattr(tournament, "ice_time_minutes_override", None)
    if isinstance(explicit, int) and explicit > 0:
        return explicit
    override = (overrides_by_tournament or {}).get(tournament_id)
    if isinstance(override, int) and override > 0:
        return override
    return ice_time_by_age_group.get(tournament.age_group)


def tournament_required_ice_minutes(
    tournament: TournamentLike,
    ice_time_by_age_group: Mapping[str, int],
    *,
    overrides_by_tournament: Mapping[str, int] | None = None,
) -> int:
    return required_ice_minutes(
        effective_ice_time_minutes(tournament, ice_time_by_age_group, overrides_by_tournament),
        round_count_for_games(tournament.games),
    )


def tournament_end_time(
    tournament: TournamentLike,
    ice_time_by_age_group: Mapping[str, int],
    *,
    overrides_by_tournament: Mapping[str, int] | None = None,
) -> str | None:
    if not tournament.start_time:
        return None
    duration = tournament_required_ice_minutes(
        tournament,
        ice_time_by_age_group,
        overrides_by_tournament=overrides_by_tournament,
    )
    if duration <= 0:
        return None
    try:
        start = datetime.strptime(tournament.start_time, "%H:%M")
    except (TypeError, ValueError):
        return None
    return (start + timedelta(minutes=duration)).strftime("%H:%M")
