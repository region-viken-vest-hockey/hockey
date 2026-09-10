"""Time slot availability checker."""

from datetime import date, time, timedelta
from typing import List, Tuple
from tournament_scheduler.interfaces import ConflictChecker
from tournament_scheduler.models import ConflictContext, ConflictResult
from tournament_scheduler.utils.date_parser import DateParser
from tournament_scheduler.utils.rich_output import TournamentOutput
from tournament_scheduler.utils.slot_finder import find_available_slots


class TimeSlotChecker(ConflictChecker):
    """Checks if dates have available time slots for tournaments."""

    def __init__(
        self,
        calendar_events: List,
        min_duration_hours: float = 2.5,
        earliest_start: str = "10:00",
        latest_start: str = "15:30"
    ):
        """Initialize time slot checker.

        Args:
            calendar_events: All calendar events to check against
            min_duration_hours: Minimum hours needed for tournament
            earliest_start: Earliest acceptable start time (HH:MM)
            latest_start: Latest acceptable start time (HH:MM)
        """
        self.calendar_events = calendar_events
        self.min_duration_hours = min_duration_hours
        self.earliest_start = self._parse_time(earliest_start)
        self.latest_start = self._parse_time(latest_start)

    def check_conflicts(self, dates: List[date], context: ConflictContext) -> ConflictResult:
        """Check for time slot conflicts.

        Args:
            dates: Dates to check
            context: Context with calendar events

        Returns:
            ConflictResult with dates that don't have suitable time slots
        """
        excluded_dates = set()
        reasons = {}
        self.available_slots = {}  # Store available time slots for dates (make it instance variable)

        TournamentOutput.print_info(
            f"Sjekker ledige tidslukker (trenger {self.min_duration_hours}t, "
            f"start mellom {self._format_time(self.earliest_start)}-{self._format_time(self.latest_start)})..."
        )

        conflicts_list = []
        for check_date in dates:
            slots = self._find_available_slots(check_date)
            if not slots:
                excluded_dates.add(check_date)
                booked_times = self._get_booked_times(check_date)
                reason = f"Opptatt: {booked_times}" if booked_times else f"Ingen {self.min_duration_hours}t luke"
                reasons[check_date] = reason
                conflicts_list.append((check_date, reason))
            else:
                # Store the available slots for this date
                self.available_slots[check_date] = slots

        if conflicts_list:
            TournamentOutput.print_conflict_table(
                "TIDSLUKE-KONFLIKTER",
                conflicts_list
            )
        else:
            TournamentOutput.print_success(f"Alle {len(dates)} datoer har ledige tidslukker")

        return ConflictResult(
            excluded_dates=excluded_dates,
            reasons=reasons,
            checker_name=self.get_checker_name()
        )

    def get_suggested_slot(self, check_date: date) -> str:
        """Get suggested (earliest) time slot for a date.

        Args:
            check_date: Date to get suggested slot for

        Returns:
            Formatted time slot string (e.g., "11:00-13:30") or empty string
        """
        slots = self.available_slots.get(check_date, [])
        if slots:
            # Return the earliest slot (first one)
            return f"{slots[0][0]}-{slots[0][1]}"
        return ""

    def get_checker_name(self) -> str:
        """Get checker name."""
        return 'timeslot'

    def _find_available_slots(self, check_date: date) -> List[Tuple[str, str]]:
        """Find available time slots on a date.

        Args:
            check_date: Date to check

        Returns:
            List of (start_time, end_time) tuples in HH:MM format
        """
        return find_available_slots(
            self.calendar_events,
            check_date,
            int(self.min_duration_hours * 60),
            earliest_start=self._format_time(self.earliest_start),
            latest_start=self._format_time(self.latest_start),
        )

    def _get_booked_times(self, check_date: date) -> str:
        """Get formatted string of booked times on a date.

        Args:
            check_date: Date to check

        Returns:
            Formatted string like "14:30-17:00, 19:00-21:00"
        """
        events_today = []
        for event in self.calendar_events:
            parsed = DateParser.parse(event.date)
            if parsed and parsed.date() == check_date:
                if hasattr(event.datetime, 'hour') and event.duration_hours > 0:
                    events_today.append(event)

        if not events_today:
            return ""

        # Format times
        time_ranges = []
        for event in sorted(events_today, key=lambda e: e.datetime):
            start_str = event.datetime.strftime('%H:%M')
            end_time = event.datetime + timedelta(hours=event.duration_hours)
            end_str = end_time.strftime('%H:%M')
            time_ranges.append(f"{start_str}-{end_str}")

        return ", ".join(time_ranges[:3])

    def _parse_time(self, time_str: str) -> time:
        """Parse time string to time object.

        Args:
            time_str: Time in HH:MM format

        Returns:
            time object
        """
        hour, minute = map(int, time_str.split(':'))
        return time(hour, minute)

    def _format_time(self, t: time) -> str:
        """Format time object to string.

        Args:
            t: time object

        Returns:
            String in HH:MM format
        """
        return f"{t.hour:02d}:{t.minute:02d}"

    def _minutes_to_time(self, minutes: int) -> str:
        """Convert minutes since midnight to HH:MM string.

        Args:
            minutes: Minutes since midnight

        Returns:
            String in HH:MM format
        """
        hour = minutes // 60
        minute = minutes % 60
        return f"{hour:02d}:{minute:02d}"
