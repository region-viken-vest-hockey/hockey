"""Arena interval conflict detection for season plans.

The season planner treats tournament occupancy as a full datetime interval,
not just an arena/date pair. These helpers are deliberately independent of
``SeasonPlanner`` so Stage 3 planning, Stage 4 export, and operator publish
can all enforce the same hard scheduling rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence

from tournament_scheduler.models import Tournament
from tournament_scheduler.occupancy import tournament_required_ice_minutes


@dataclass(frozen=True)
class ArenaInterval:
    """One arena occupancy interval for a planned tournament."""

    tournament_id: str
    arena: str
    age_group: str
    date: str
    host_club: str | None
    start: datetime
    end: datetime

    @property
    def interval_label(self) -> str:
        """Return a compact human-readable interval label."""
        return f"{self.start.strftime('%Y-%m-%d %H:%M')}–{self.end.strftime('%Y-%m-%d %H:%M')}"


def tournament_interval(
    tournament: Tournament,
    ice_time_for_age_group: Mapping[str, int],
) -> ArenaInterval | None:
    """Return the full occupied interval for *tournament*, or ``None``.

    Cancelled tournaments, tournaments without a parseable ``start_time``, and
    tournaments without a positive configured occupancy duration are ignored.
    """
    if tournament.cancelled or not tournament.start_time:
        return None
    duration_minutes = tournament_required_ice_minutes(tournament, ice_time_for_age_group)
    if duration_minutes <= 0:
        return None
    try:
        hour, minute = (int(part) for part in tournament.start_time.split(":", 1))
        start = datetime.combine(tournament.date, datetime.min.time()).replace(hour=hour, minute=minute)
    except (TypeError, ValueError):
        return None
    end = start + timedelta(minutes=duration_minutes)
    return ArenaInterval(
        tournament_id=tournament.id,
        arena=tournament.arena,
        age_group=tournament.age_group,
        date=tournament.date.isoformat(),
        host_club=tournament.host_club,
        start=start,
        end=end,
    )


def tournament_intervals(
    tournaments: Iterable[Tournament],
    ice_time_for_age_group: Mapping[str, int],
) -> list[ArenaInterval]:
    """Return all evaluable arena intervals for *tournaments*."""
    intervals: list[ArenaInterval] = []
    for tournament in tournaments:
        interval = tournament_interval(tournament, ice_time_for_age_group)
        if interval is not None:
            intervals.append(interval)
    return intervals


def intervals_overlap(first: ArenaInterval, second: ArenaInterval) -> bool:
    """Return ``True`` when two intervals overlap in the same arena."""
    return first.arena == second.arena and first.start < second.end and second.start < first.end


def arena_interval_collisions(intervals: Sequence[ArenaInterval]) -> list[dict[str, str]]:
    """Return structured same-arena interval collisions.

    Adjacent intervals (``first.end == second.start``) are allowed. Overnight
    intervals naturally collide with any interval that starts before their true
    next-day end time.
    """
    ordered = sorted(intervals, key=lambda item: (item.arena, item.start, item.end, item.tournament_id))
    collisions: list[dict[str, str]] = []
    for idx, current in enumerate(ordered):
        for other in ordered[idx + 1 :]:
            if other.arena != current.arena:
                break
            if other.start >= current.end:
                break
            if intervals_overlap(current, other):
                collisions.append(format_arena_collision(current, other))
    return collisions


def find_arena_interval_collisions(
    tournaments: Iterable[Tournament],
    ice_time_for_age_group: Mapping[str, int],
) -> list[dict[str, str]]:
    """Build intervals for *tournaments* and return arena overlap collisions."""
    return arena_interval_collisions(tournament_intervals(tournaments, ice_time_for_age_group))


def format_arena_collision(first: ArenaInterval, second: ArenaInterval) -> dict[str, str]:
    """Return an actionable collision dictionary for reports/errors."""
    if (second.start, second.tournament_id) < (first.start, first.tournament_id):
        first, second = second, first
    message = (
        f"Arena conflict {first.arena} {first.date}: "
        f"{first.tournament_id} ({first.age_group}) {first.interval_label} overlaps "
        f"{second.tournament_id} ({second.age_group}) {second.interval_label}"
    )
    return {
        "date": first.date,
        "arena": first.arena,
        "tournament_id": first.tournament_id,
        "age_group": first.age_group,
        "interval": first.interval_label,
        "conflicting_tournament_id": second.tournament_id,
        "conflicting_age_group": second.age_group,
        "conflicting_interval": second.interval_label,
        "message": message,
    }
