"""Holiday conflict checker.

The exclusion rule itself lives in
:mod:`tournament_scheduler.date_policy` so the initial scheduler and every
later verification/repair path share one implementation. This checker is the
thin scheduler adapter over that canonical policy.
"""

from datetime import date, datetime
from typing import List

from tournament_scheduler.date_policy import holiday_exclusions
from tournament_scheduler.interfaces import ConflictChecker
from tournament_scheduler.models import ConflictContext, ConflictResult


class HolidayConflictChecker(ConflictChecker):
    """Checks for conflicts with Norwegian public holidays."""

    def __init__(self, country: str = "NO"):
        """Initialize holiday checker.

        Args:
            country: Country code for holidays
        """
        self.country = country

    def check_conflicts(self, dates: List[date], context: ConflictContext) -> ConflictResult:
        """Check for holiday week conflicts and weekends before holidays.

        Args:
            dates: Dates to check
            context: Context with date range

        Returns:
            ConflictResult with excluded dates
        """
        start_date = _as_date(context.start_date)
        end_date = _as_date(context.end_date)
        exclusions = holiday_exclusions(start_date, end_date, country=self.country)

        excluded_dates = set()
        reasons = {}
        for check_date in dates:
            reason = exclusions.get(check_date)
            if reason is None:
                continue
            excluded_dates.add(check_date)
            reasons[check_date] = reason

        return ConflictResult(
            excluded_dates=excluded_dates,
            reasons=reasons,
            checker_name=self.get_checker_name(),
        )

    def get_checker_name(self) -> str:
        """Get checker name.

        Returns:
            'holiday_week'
        """
        return "holiday_week"


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value
