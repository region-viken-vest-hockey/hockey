"""Derive the effective Stage 3 planning start date (issue #272).

`SeasonPlanner.build_plan` regenerates tournaments across the whole
`[start_date, end_date]` window it is given, and `TournamentScheduler`
enumerates every Saturday/Sunday in that window with no "today or later"
clamp. If the configured season `start_date` is already in the past by the
time a run happens, Stage 3 can generate a tournament dated before the run
itself. `compute_effective_start_date` derives a safe lower bound so Stage 3
never creates a new tournament before that boundary, while leaving the
configured value untouched for anything that only wants to display it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

OSLO_TZ = ZoneInfo("Europe/Oslo")


@dataclass(frozen=True)
class EffectiveStartDate:
    """Result of resolving a configured start date against "today"."""

    configured_start_date: date
    effective_start_date: date
    # None means the configured date was already a valid future tournament
    # weekend and needed no adjustment.
    start_date_adjustment: str | None


def _next_tournament_weekend_on_or_after(d: date) -> date:
    """First Saturday/Sunday on or after *d*.

    Matches `TournamentScheduler._get_weekend_dates`'s definition of a
    tournament weekend (`weekday() in (5, 6)`).
    """
    candidate = d
    while candidate.weekday() not in (5, 6):
        candidate += timedelta(days=1)
    return candidate


def compute_effective_start_date(
    configured_start_date: date, *, today: date | None = None
) -> EffectiveStartDate:
    """Derive the effective Stage 3 planning start date.

    - Configured date in the future: keep it, aligned to the first usable
      tournament weekend on/after it.
    - Configured date today or in the past: start planning on the first
      *future* tournament weekend. A run on a Saturday must not create a
      tournament later that same day, so the boundary is always tomorrow
      at the earliest, using `Europe/Oslo` for "today".
    """
    if today is None:
        today = datetime.now(OSLO_TZ).date()

    if configured_start_date > today:
        effective = _next_tournament_weekend_on_or_after(configured_start_date)
        reason = (
            None
            if effective == configured_start_date
            else "configured date aligned to next tournament weekend"
        )
    else:
        boundary = today + timedelta(days=1)
        effective = _next_tournament_weekend_on_or_after(boundary)
        reason = (
            "configured start date was in the past"
            if configured_start_date < today
            else "configured start date was today"
        )

    return EffectiveStartDate(configured_start_date, effective, reason)
