"""Canonical date-policy inputs for makeup-date suggestions.

Makeup suggestions must apply the same effective date policy as the planner,
maintenance search and final verification. The holiday exceptions and operator
bans that govern a season live in its canonical decisions, so this module
resolves the applicable canonical season and builds the holiday checker from
those inputs.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from ..conflict_checkers.holiday_checker import HolidayConflictChecker
from ..season.baseline_overlay import resolve_canonical_state
from ..season.banned_dates import active_banned_date_records
from ..season.holiday_exceptions import active_exception_dates
from ..utils.date_parser import DateParser
from .stage1_config import load_effective_config
from .state import PipelineState


def makeup_date_policy_inputs(state: PipelineState, plan: Any) -> tuple[list[date], list[date]]:
    """Return ``(holiday_date_exceptions, banned_dates)`` for the plan's season."""
    return canonical_date_policy_inputs(state, plan.start_date, plan.end_date)


def canonical_date_policy_inputs(
    state: PipelineState, start: date, end: date
) -> tuple[list[date], list[date]]:
    """Return ``(holiday_date_exceptions, banned_dates)`` for a planning window.

    Both are empty when no canonical season applies, which is the from-scratch
    path, or when the plan carries no window yet.
    """
    if start is None or end is None:
        return [], []
    canonical = resolve_canonical_state(load_effective_config(state), start, end)
    if not canonical:
        return [], []
    decisions = canonical["decisions"]
    exceptions = [date.fromisoformat(value) for value in active_exception_dates(decisions)]
    bans = [
        date.fromisoformat(str(record["date"]))
        for record in active_banned_date_records(decisions)
        if record.get("date")
    ]
    return exceptions, bans


def make_holiday_scheduler(exceptions: Iterable[date], banned_dates: Iterable[date]) -> Any:
    """Build a lightweight ``TournamentScheduler`` holding the canonical holiday checker."""
    from ..scheduler import TournamentScheduler

    return TournamentScheduler(
        calendar_sources=[],
        conflict_checkers=[
            HolidayConflictChecker(date_exceptions=exceptions, banned_dates=banned_dates)
        ],
        date_parser=DateParser(),
    )
