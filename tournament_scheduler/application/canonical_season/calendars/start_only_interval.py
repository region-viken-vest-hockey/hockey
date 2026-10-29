"""Alignment interval for a source that states only a booking start (#869).

A club may state the start of a booking and leave the end unstated. The start
is then the source's explicit authority, while the end stays proposal-derived:
the proposed duration is kept. The derived end is used only to align the
canonical tournament. It is never written back into the manual source record,
which keeps exactly what the source stated.
"""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.calendar_bookings import tournament_occupancy_interval_facts
from tournament_scheduler.booking.interval_facts import _parse_hhmm
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError

_MINUTES_PER_DAY = 24 * 60


def alignment_interval(
    stated_interval: Mapping[str, Any],
    tournament: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
    interval_date: str,
) -> dict[str, str]:
    """Return the date/start/end interval that aligns the canonical tournament."""

    start = str(stated_interval.get("start") or "")
    end = str(stated_interval.get("end") or "")
    if end:
        return {"date": interval_date, "start": start, "end": end}
    start_minutes = _parse_hhmm(start)
    duration = int(tournament_occupancy_interval_facts(tournament, problem).get("duration_minutes") or 0)
    if start_minutes is None or duration <= 0:
        raise SeasonStateError(
            f"Stated start {start} cannot be aligned: the proposed duration of tournament "
            f"{tournament.get('id')} is unknown"
        )
    end_minutes = start_minutes + duration
    if end_minutes >= _MINUTES_PER_DAY:
        raise SeasonStateError("Overnight booking intervals are not supported")
    return {
        "date": interval_date,
        "start": start,
        "end": f"{end_minutes // 60:02d}:{end_minutes % 60:02d}",
    }
