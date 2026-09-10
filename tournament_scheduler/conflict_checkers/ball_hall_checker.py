"""Ball hall conflict checker - WARNING ONLY, does not block."""

from datetime import date
from typing import List
from tournament_scheduler.interfaces import ConflictChecker
from tournament_scheduler.models import ConflictContext, ConflictResult
from tournament_scheduler.data_sources.ball_hall_calendar import BallHallCalendar
from tournament_scheduler.utils.date_parser import DateParser
from tournament_scheduler.utils.rich_output import TournamentOutput


class BallHallConflictChecker(ConflictChecker):
    """Checks for ball hall events - reports warnings only, does not exclude dates."""

    def __init__(self, calendar_source: BallHallCalendar):
        """Initialize ball hall checker.

        Args:
            calendar_source: BallHallCalendar instance
        """
        self.calendar_source = calendar_source
        self.warnings = []

    def check_conflicts(self, dates: List[date], context: ConflictContext) -> ConflictResult:
        """Check for ball hall events and generate warnings.

        Args:
            dates: Dates to check
            context: Context with calendar events

        Returns:
            ConflictResult with NO excluded dates (warnings only)
        """
        self.warnings = []
        reasons = {}

        # Build ball hall event dates from context (already filtered for long events)
        ball_hall_events = {}
        for event in context.calendar_events:
            if event.duration_hours > self.calendar_source.min_duration:
                parsed = DateParser.parse(event.date)
                if parsed:
                    event_date = parsed.date()
                    ball_hall_events[event_date] = (event.name, event.duration_hours)

        # Check each date and generate warnings
        for check_date in dates:
            if check_date in ball_hall_events:
                name, duration = ball_hall_events[check_date]
                warning = f"{check_date.strftime('%Y-%m-%d')}: Ball hall event '{name[:40]}' ({duration:.1f}h) - wardrobe may be unavailable"
                self.warnings.append(warning)
                reasons[check_date] = f"Ball hall: {name[:40]} ({duration:.1f}h)"

        # Print warnings if any
        if self.warnings:
            TournamentOutput.print_warning(
                f"BALLHALL-ADVARSLER ({len(self.warnings)} advarsler - blokkerer IKKE):"
            )
            from rich.console import Console
            console = Console()
            for warning in self.warnings:
                console.print(f"  [yellow]{warning}[/yellow]")

        # Return EMPTY excluded_dates - ball hall does not block scheduling
        return ConflictResult(
            excluded_dates=set(),  # No exclusions!
            reasons=reasons,
            checker_name=self.get_checker_name()
        )

    def get_checker_name(self) -> str:
        """Get checker name.

        Returns:
            'ball_hall_warning'
        """
        return 'ball_hall_warning'
