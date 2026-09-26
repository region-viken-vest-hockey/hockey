"""Canonical tournament hall-occupancy duration contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Protocol

ROUND_BUFFER_MINUTES = 5
NIHF_SERIES_ROUND_MINIMUM_MINUTES = 120
NIHF_SERIES_ROUND_MINIMUM_AGE_GROUPS = frozenset({"U7", "JU7", "U8", "JU8", "U9", "U10", "JU10", "U11"})


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
    # Populated only when a nominal round count was supplied to
    # `occupancy_components` -- distinguishes the age group's nominal format
    # from this instance's feasible-round-adapted requested occupancy
    # (issue #473). `None` when no round-config was supplied, in which case
    # `required_duration_minutes` is the unreduced flat value (legacy shape).
    nominal_round_count: int | None = None
    effective_requested_minutes: int | None = None

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "age_group": self.age_group,
            "configured_ice_time_minutes": self.configured_ice_time_minutes,
            "round_count": self.round_count,
            "round_buffer_minutes": self.round_buffer_minutes,
            "required_duration_minutes": self.required_duration_minutes,
            "nominal_round_count": self.nominal_round_count,
            "effective_requested_minutes": self.effective_requested_minutes,
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


def occupancy_components(
    age_group: str,
    ice_time_by_age_group: Mapping[str, int],
    round_count: int,
    *,
    rounds_per_tournament: Mapping[str, int] | None = None,
    round_length_minutes: Mapping[str, int] | None = None,
) -> OccupancyComponents:
    ice_time = ice_time_by_age_group.get(age_group)
    buffer_minutes = ROUND_BUFFER_MINUTES * max(0, round_count)
    duration = required_ice_minutes(ice_time, round_count) if ice_time else None
    nominal_round_count = None
    effective_requested_minutes = None
    if rounds_per_tournament is not None or round_length_minutes is not None:
        nominal_round_count = (rounds_per_tournament or {}).get(age_group)
        adapted = effective_required_ice_minutes(
            age_group,
            ice_time,
            (round_length_minutes or {}).get(age_group),
            round_count,
            nominal_round_count=nominal_round_count,
        )
        effective_requested_minutes = adapted.effective_requested_minutes
        duration = adapted.booked_minutes if ice_time else None
    return OccupancyComponents(
        age_group,
        ice_time,
        round_count,
        buffer_minutes,
        duration,
        nominal_round_count=nominal_round_count,
        effective_requested_minutes=effective_requested_minutes,
    )


@dataclass(frozen=True)
class EffectiveOccupancy:
    """Distinguishes the four occupancy facts issue #473 requires.

    ``nominal_format_minutes`` is the unreduced age-group configured value.
    ``feasible_round_count`` is the tournament instance's own actual round
    count (already shaped by registered-pool scarcity upstream -- see
    ``effective_tournament_shape.py``). ``effective_requested_minutes`` is
    what this instance should actually book. ``externally_booked_minutes``
    is set only by a caller that knows a confirmed/manual booking fixes a
    (possibly longer) interval that must never be silently shortened.
    """

    age_group: str
    nominal_format_minutes: int | None
    nominal_round_count: int | None
    feasible_round_count: int
    effective_requested_minutes: int
    externally_booked_minutes: int | None = None

    @property
    def booked_minutes(self) -> int:
        """The minutes to actually use for placement/export/conflicts."""
        if self.externally_booked_minutes is not None and self.externally_booked_minutes > self.effective_requested_minutes:
            return self.externally_booked_minutes
        return self.effective_requested_minutes

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "age_group": self.age_group,
            "nominal_format_minutes": self.nominal_format_minutes,
            "nominal_round_count": self.nominal_round_count,
            "feasible_round_count": self.feasible_round_count,
            "effective_requested_minutes": self.effective_requested_minutes,
            "externally_booked_minutes": self.externally_booked_minutes,
            "booked_minutes": self.booked_minutes,
        }


def effective_required_ice_minutes(
    age_group: str,
    configured_ice_time_minutes: int | None,
    round_length_minutes: int | None,
    feasible_round_count: int,
    *,
    nominal_round_count: int | None = None,
    externally_booked_minutes: int | None = None,
    round_buffer_minutes: int = ROUND_BUFFER_MINUTES,
) -> EffectiveOccupancy:
    """Return the deterministic per-tournament effective occupancy.

    When the tournament's own feasible round count is below the age group's
    configured nominal round count, the booked window is reduced by exactly
    the ice time the missing rounds would have consumed (never by a team-
    count ratio, and never below the minimum-feasibility/governing floors).
    Full participation (``feasible_round_count >= nominal_round_count``, or
    no configured nominal round count to compare against) returns the
    configured value unchanged. A caller-supplied confirmed/manual booking
    interval (``externally_booked_minutes``) is never shortened by this
    reduction -- see :attr:`EffectiveOccupancy.booked_minutes`.
    """
    base = required_ice_minutes(configured_ice_time_minutes, feasible_round_count, round_buffer_minutes=round_buffer_minutes)
    if base <= 0 or not nominal_round_count or nominal_round_count <= feasible_round_count:
        return EffectiveOccupancy(
            age_group, configured_ice_time_minutes, nominal_round_count, feasible_round_count, base, externally_booked_minutes
        )

    removed_rounds = nominal_round_count - feasible_round_count
    per_round_allowance = (
        round_length_minutes + max(0, round_buffer_minutes)
        if isinstance(round_length_minutes, int) and round_length_minutes > 0
        else 0
    )
    reduced = base - removed_rounds * per_round_allowance
    floor = max(
        minimum_playing_requirement_minutes(round_length_minutes, feasible_round_count, round_buffer_minutes=round_buffer_minutes),
        governing_minimum_ice_time_minutes(age_group) or 0,
    )
    effective = max(reduced, floor)
    return EffectiveOccupancy(
        age_group, configured_ice_time_minutes, nominal_round_count, feasible_round_count, effective, externally_booked_minutes
    )


def tournament_required_ice_minutes(
    tournament: TournamentLike,
    ice_time_by_age_group: Mapping[str, int],
    *,
    rounds_per_tournament: Mapping[str, int] | None = None,
    round_length_minutes: Mapping[str, int] | None = None,
) -> int:
    """Return the tournament's booked occupancy minutes.

    With no round-config mappings supplied, this preserves the historical
    flat behavior (the configured age-group value, gated by whether any
    round exists). When ``rounds_per_tournament``/``round_length_minutes``
    are supplied, the returned minutes are adapted to this tournament
    instance's actual feasible round count via
    :func:`effective_required_ice_minutes`.
    """
    round_count = round_count_for_games(tournament.games)
    if rounds_per_tournament is None and round_length_minutes is None:
        return required_ice_minutes(ice_time_by_age_group.get(tournament.age_group), round_count)
    occupancy = effective_required_ice_minutes(
        tournament.age_group,
        ice_time_by_age_group.get(tournament.age_group),
        (round_length_minutes or {}).get(tournament.age_group),
        round_count,
        nominal_round_count=(rounds_per_tournament or {}).get(tournament.age_group),
    )
    return occupancy.booked_minutes


def tournament_end_time(
    tournament: TournamentLike,
    ice_time_by_age_group: Mapping[str, int],
    *,
    rounds_per_tournament: Mapping[str, int] | None = None,
    round_length_minutes: Mapping[str, int] | None = None,
) -> str | None:
    if not tournament.start_time:
        return None
    duration = tournament_required_ice_minutes(
        tournament,
        ice_time_by_age_group,
        rounds_per_tournament=rounds_per_tournament,
        round_length_minutes=round_length_minutes,
    )
    if duration <= 0:
        return None
    try:
        start = datetime.strptime(tournament.start_time, "%H:%M")
    except (TypeError, ValueError):
        return None
    return (start + timedelta(minutes=duration)).strftime("%H:%M")
