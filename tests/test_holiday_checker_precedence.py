"""Holiday checker precedence: derived exclusion < explicit exception < explicit ban.

The initial planner's date checker must apply the same effective date policy
as maintenance and final verification, so an accepted holiday-date exception
frees exactly its own date and an operator ban always wins.
"""

from datetime import date, datetime

from tournament_scheduler.conflict_checkers.holiday_checker import HolidayConflictChecker
from tournament_scheduler.models import ConflictContext

CHRISTMAS_EXCEPTION = date(2026, 12, 19)
OTHER_HOLIDAY_WEEKEND = date(2026, 12, 20)
WINDOW = (datetime(2026, 12, 1), datetime(2026, 12, 31))


def _excluded(checker: HolidayConflictChecker, *dates: date) -> set[date]:
    context = ConflictContext(start_date=WINDOW[0], end_date=WINDOW[1])
    return set(checker.check_conflicts(list(dates), context).excluded_dates)


def test_derived_holiday_weekend_is_excluded_without_exception() -> None:
    assert CHRISTMAS_EXCEPTION in _excluded(HolidayConflictChecker(), CHRISTMAS_EXCEPTION)


def test_exception_frees_only_its_own_derived_date() -> None:
    checker = HolidayConflictChecker(date_exceptions=[CHRISTMAS_EXCEPTION])

    excluded = _excluded(checker, CHRISTMAS_EXCEPTION, OTHER_HOLIDAY_WEEKEND)

    assert CHRISTMAS_EXCEPTION not in excluded
    assert OTHER_HOLIDAY_WEEKEND in excluded


def test_ban_overrides_exception_on_same_date() -> None:
    checker = HolidayConflictChecker(
        date_exceptions=[CHRISTMAS_EXCEPTION],
        banned_dates=[CHRISTMAS_EXCEPTION],
    )
    result = checker.check_conflicts(
        [CHRISTMAS_EXCEPTION],
        ConflictContext(start_date=WINDOW[0], end_date=WINDOW[1]),
    )

    assert CHRISTMAS_EXCEPTION in result.excluded_dates
    assert result.reasons[CHRISTMAS_EXCEPTION] == "banned by operator"
