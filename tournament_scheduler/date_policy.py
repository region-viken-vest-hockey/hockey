"""Canonical deterministic date-admissibility policy.

The repository's holiday/date exclusions have exactly one implementation
here. The initial scheduler's :class:`~tournament_scheduler.conflict_checkers.holiday_checker.HolidayConflictChecker`,
every repair/optimizer date move and the independent planning-contract
verifier all derive their forbidden dates from this module, so a date the
initial scheduler would never have chosen can never be re-introduced by a
later search path or slip past final verification.

The policy is the long-standing RVV holiday policy:

* the entire Monday-Sunday week containing a Norwegian public holiday is
  excluded, and
* the weekend immediately before a public holiday is excluded.

Keeping this list in one place also lets the application surface the active
excluded dates as rules/audit evidence instead of hiding the policy inside a
single planner.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Mapping, Optional, Set

import holidays as _holidays

DEFAULT_COUNTRY = "NO"

#: Canonical source tag for holiday-policy exclusions carried in a
#: ``planning_problem`` (distinct from operator ``banned_dates``).
HOLIDAY_POLICY_SOURCE = "holiday_policy"


def _parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _holiday_calendar(country: str) -> Any:
    try:
        return _holidays.country_holidays(country)
    except Exception:
        # An unknown country code must not make planning crash; falling back
        # to the canonical Norwegian calendar keeps date admissibility
        # deterministic instead of silently disabling the policy.
        return _holidays.country_holidays(DEFAULT_COUNTRY)


def holiday_exclusions(
    start_date: date,
    end_date: date,
    *,
    country: str = DEFAULT_COUNTRY,
) -> Dict[date, str]:
    """Return ``{excluded_date: reason}`` for the closed window.

    ``start_date``/``end_date`` are inclusive. Only dates inside the window
    are returned. A holiday in the ten days after the window still blocks the
    weekend immediately before it when that weekend falls inside the window,
    matching the initial scheduler's behaviour.
    """
    if start_date is None or end_date is None or end_date < start_date:
        return {}

    calendar = _holiday_calendar(country)

    holiday_weeks: Set[date] = set()
    current = start_date
    while current <= end_date:
        if current in calendar:
            week_start = current - timedelta(days=current.weekday())
            for offset in range(7):
                holiday_weeks.add(week_start + timedelta(days=offset))
        current += timedelta(days=1)

    weekends_before: Dict[date, str] = {}
    extended_end = end_date + timedelta(days=10)
    current = start_date
    while current <= extended_end:
        if current in calendar:
            holiday_name = calendar.get(current)
            days_since_monday = current.weekday()
            saturday = current - timedelta(days=days_since_monday + 2)
            sunday = current - timedelta(days=days_since_monday + 1)
            # Assignment (not setdefault): the later holiday in the window
            # owns the reason, matching the historical checker exactly.
            if saturday.weekday() == 5 and start_date <= saturday <= end_date:
                weekends_before[saturday] = holiday_name
            if sunday.weekday() == 6 and start_date <= sunday <= end_date:
                weekends_before[sunday] = holiday_name
        current += timedelta(days=1)

    excluded: Dict[date, str] = {}
    day = start_date
    while day <= end_date:
        if day in holiday_weeks:
            reason = "Holiday week"
            week_start = day - timedelta(days=day.weekday())
            for offset in range(7):
                candidate = week_start + timedelta(days=offset)
                if candidate in calendar:
                    reason = f"Holiday week: {calendar.get(candidate)}"
                    break
            excluded[day] = reason
        elif day in weekends_before:
            excluded[day] = f"Weekend before: {weekends_before[day]}"
        day += timedelta(days=1)
    return excluded


def holiday_excluded_dates(
    start_date: date,
    end_date: date,
    *,
    country: str = DEFAULT_COUNTRY,
) -> Set[date]:
    """The canonical holiday-policy excluded dates in the closed window."""
    return set(holiday_exclusions(start_date, end_date, country=country))


def problem_window(problem: Optional[Mapping[str, Any]]) -> tuple[Optional[date], Optional[date]]:
    """Return ``(start_date, end_date)`` carried by a ``planning_problem``."""
    if not isinstance(problem, Mapping):
        return None, None
    return _parse_date(problem.get("start_date")), _parse_date(problem.get("end_date"))


def problem_date_exclusions(problem: Optional[Mapping[str, Any]]) -> Dict[date, str]:
    """Canonical excluded dates for a ``planning_problem``.

    Combines the holiday policy derived from the problem's own planning
    window with any explicit ``date_exclusions`` the problem carries (for
    example a season whose policy was frozen at promotion time). This is the
    single reader every automatic date-changing path uses, so a repair cannot
    "fix" one exclusion by forgetting another.
    """
    out: Dict[date, str] = {}
    start, end = problem_window(problem)
    if start is not None and end is not None:
        out.update(holiday_exclusions(start, end))
    if isinstance(problem, Mapping):
        for entry in problem.get("date_exclusions") or []:
            if not isinstance(entry, Mapping):
                continue
            excluded = _parse_date(entry.get("date"))
            if excluded is None:
                continue
            reason = str(entry.get("reason") or "excluded by date policy")
            out.setdefault(excluded, reason)
    return out


def is_excluded_date(problem: Optional[Mapping[str, Any]], on_date: Optional[date]) -> Optional[str]:
    """Return the exclusion reason for *on_date*, or ``None`` when admissible."""
    if on_date is None:
        return None
    return problem_date_exclusions(problem).get(on_date)
