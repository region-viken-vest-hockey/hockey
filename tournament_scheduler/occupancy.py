"""Canonical tournament hall-occupancy duration contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Protocol

ROUND_BUFFER_MINUTES = 5


class TournamentLike(Protocol):
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


def required_ice_minutes(ice_time_minutes: int | None, round_count: int, *, round_buffer_minutes: int = ROUND_BUFFER_MINUTES) -> int:
    """Return required occupied minutes: configured base ice plus per-round buffer."""
    if not isinstance(ice_time_minutes, int) or ice_time_minutes <= 0 or round_count <= 0:
        return 0
    return ice_time_minutes + max(0, round_buffer_minutes) * round_count


def occupancy_components(age_group: str, ice_time_by_age_group: Mapping[str, int], round_count: int) -> OccupancyComponents:
    ice_time = ice_time_by_age_group.get(age_group)
    buffer_minutes = ROUND_BUFFER_MINUTES * max(0, round_count)
    duration = required_ice_minutes(ice_time, round_count) if ice_time else None
    return OccupancyComponents(age_group, ice_time, round_count, buffer_minutes, duration)


def tournament_required_ice_minutes(tournament: TournamentLike, ice_time_by_age_group: Mapping[str, int]) -> int:
    return required_ice_minutes(
        ice_time_by_age_group.get(tournament.age_group),
        round_count_for_games(tournament.games),
    )


def tournament_end_time(tournament: TournamentLike, ice_time_by_age_group: Mapping[str, int]) -> str | None:
    if not tournament.start_time:
        return None
    duration = tournament_required_ice_minutes(tournament, ice_time_by_age_group)
    if duration <= 0:
        return None
    try:
        start = datetime.strptime(tournament.start_time, "%H:%M")
    except (TypeError, ValueError):
        return None
    return (start + timedelta(minutes=duration)).strftime("%H:%M")
