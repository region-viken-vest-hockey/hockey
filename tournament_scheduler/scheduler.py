"""Tournament scheduler orchestrator - coordinates all components."""

from datetime import datetime, timedelta, date, time
from typing import Dict, List, Set, Optional, Tuple
from tournament_scheduler.interfaces import CalendarDataSource, ConflictChecker
from tournament_scheduler.models import (
    SchedulingResult,
    ConflictContext,
    TournamentInfo,
    CalendarEvent
)
from tournament_scheduler.club_registry import get_club
from tournament_scheduler.utils.date_parser import DateParser
from tournament_scheduler.utils.slot_finder import find_available_slots, parse_time
from tournament_scheduler.excel.tournament_reader import ExcelTournamentReader

# "Optimal" time-of-day window for tournament start times. Slots starting
# closer to the middle of this window score better than slots starting
# very early or very late.
_OPTIMAL_SLOT_START = "11:00"
_SLOT_SEARCH_EARLIEST = "10:00"
_SLOT_SEARCH_LATEST = "16:00"


class TournamentScheduler:
    """Orchestrates tournament scheduling using injected dependencies."""

    def __init__(
        self,
        calendar_sources: List[CalendarDataSource],
        conflict_checkers: List[ConflictChecker],
        date_parser: DateParser
    ):
        """Initialize scheduler with dependencies.

        Args:
            calendar_sources: List of calendar data sources
            conflict_checkers: List of conflict checkers
            date_parser: DateParser instance
        """
        self.calendar_sources = calendar_sources
        self.conflict_checkers = conflict_checkers
        self.date_parser = date_parser

    def find_available_dates(
        self,
        start_date: datetime,
        end_date: datetime,
        team_names: List[str] = None,
        excel_dates: Set[date] = None,
        calendar_events: List = None
    ) -> SchedulingResult:
        """Find available weekend dates for tournaments.

        Args:
            start_date: Start of date range
            end_date: End of date range
            team_names: Optional list of team names to check for conflicts
            excel_dates: Optional set of dates to exclude from Excel
            calendar_events: Optional pre-fetched calendar events to avoid re-scraping

        Returns:
            SchedulingResult with available and excluded dates
        """
        if team_names is None:
            team_names = []
        if excel_dates is None:
            excel_dates = set()

        # Use provided events or fetch from sources
        if calendar_events is not None:
            all_events = calendar_events
        else:
            # Fetch calendar events from all sources
            all_events = []
            for source in self.calendar_sources:
                events = source.fetch_events(start_date, end_date)
                all_events.extend(events)

        # Build context
        context = ConflictContext(
            start_date=start_date,
            end_date=end_date,
            team_names=team_names,
            calendar_events=all_events,
            excel_dates=excel_dates
        )

        # Get all weekend dates
        weekend_dates = self._get_weekend_dates(start_date, end_date)

        # Run all conflict checkers
        all_excluded = set()
        all_reasons = {}
        exclusion_breakdown = {}

        for checker in self.conflict_checkers:
            result = checker.check_conflicts(weekend_dates, context)
            all_excluded.update(result.excluded_dates)
            all_reasons.update(result.reasons)
            exclusion_breakdown[checker.get_checker_name()] = len(result.excluded_dates)

        # Calculate available dates
        available_dates = [d for d in weekend_dates if d not in all_excluded]
        excluded_dates = [d for d in weekend_dates if d in all_excluded]

        # Build detailed exclusions list
        detailed_exclusions = [(d, all_reasons.get(d, "Unknown")) for d in sorted(excluded_dates)]

        return SchedulingResult(
            available_dates=sorted(available_dates),
            excluded_dates=sorted(excluded_dates),
            exclusion_breakdown=exclusion_breakdown,
            detailed_exclusions=detailed_exclusions,
            total_weekends_checked=len(weekend_dates)
        )

    def find_arena_slot_for_date(
        self,
        check_date: date,
        host_club: str,
        required_minutes: int,
        events_by_club: Dict[str, List[CalendarEvent]],
        preferred_start: str = _OPTIMAL_SLOT_START,
        club_calendar_status: Optional[Dict[str, str]] = None,
    ) -> Optional[Tuple[str, str, str]]:
        """Find a time slot in the host club's own arena on a date.

        The planner only books from the assigned host club's own calendar.
        If the host club has no known calendar source, no matching slot, no
        free gap of sufficient length, or (issue #262 P0) no trustworthy
        calendar evidence for this run, this returns ``None`` so the caller
        can keep the original host/arena and fall back to the default start
        time.

        Args:
            check_date: The candidate tournament date.
            host_club: The assigned host club.
            required_minutes: Total tournament duration required, in minutes.
            events_by_club: Calendar events keyed by club name (e.g. from
                Stage 2's ``events_by_club`` checkpoint output plus any
                in-memory reservations for tournaments already placed in the
                current plan).
            club_calendar_status: Per-club calendar-evidence status
                (``"known"``/``"unknown"``) from Stage 2's
                ``club_calendar_status`` checkpoint output. When
                non-empty, a club missing from this mapping, or explicitly
                marked ``"unknown"``, is treated as unavailable -- an empty
                ``events_by_club`` entry is only "known free" when this
                status says so. An empty/``None`` mapping (the default)
                skips the check entirely, for callers that don't compute
                this status (e.g. legacy tests, ad-hoc scheduling calls).

        Returns:
            ``(host_club_used, start_HH:MM, end_HH:MM)`` for the best slot in
            the host club's own hall, or ``None`` if no sufficient slot exists.
        """
        try:
            host_entry = get_club(host_club)
        except KeyError:
            # host_club isn't in the registry at all (e.g. a joint-club team
            # name like "Jar/Jutul" that has no single physical arena) — the
            # docstring above already promises this falls back to None like
            # any other "no known calendar source" club, not a crash.
            return None
        if not host_entry.is_known:
            return None
        if club_calendar_status and club_calendar_status.get(host_entry.club, "unknown") != "known":
            return None

        club_events = events_by_club.get(host_entry.club, [])
        slots = find_available_slots(
            club_events,
            check_date,
            required_minutes,
            earliest_start=_SLOT_SEARCH_EARLIEST,
            latest_start=_SLOT_SEARCH_LATEST,
        )
        if not slots:
            return None

        try:
            preferred_minutes = _time_to_minutes(parse_time(preferred_start))
        except Exception:
            preferred_minutes = _time_to_minutes(parse_time(_OPTIMAL_SLOT_START))

        start_str, end_str = min(
            slots,
            key=lambda slot: (
                abs(_time_to_minutes(parse_time(slot[0])) - preferred_minutes),
                _time_to_minutes(parse_time(slot[0])),
            ),
        )
        return host_entry.club, start_str, end_str

    def reschedule_tournament(
        self,
        tournament_date: date,
        excel_file: str,
        start_date: datetime,
        end_date: datetime
    ) -> SchedulingResult:
        """Reschedule a tournament to find alternative dates when all teams are available.

        Args:
            tournament_date: Original tournament date
            excel_file: Path to Excel file with tournament schedule
            start_date: Start of date range for alternatives
            end_date: End of date range for alternatives

        Returns:
            SchedulingResult with available alternatives and tournament info
        """
        # Extract tournament teams from Excel (prints debug info automatically)
        reader = ExcelTournamentReader(excel_file, self.date_parser)
        tournament_info = reader.get_tournament_info(tournament_date)

        # Get all tournament dates to exclude (except the one we're rescheduling)
        all_tournament_dates = reader.get_all_tournament_dates()
        excel_dates = all_tournament_dates - {tournament_date}

        # Find available dates when all teams are free
        result = self.find_available_dates(
            start_date=start_date,
            end_date=end_date,
            team_names=tournament_info.teams,
            excel_dates=excel_dates
        )

        # Add tournament info to result
        result.tournament_info = tournament_info

        return result

    def _get_weekend_dates(self, start_date: datetime, end_date: datetime) -> List[date]:
        """Get all weekend dates in range.

        Args:
            start_date: Start of range
            end_date: End of range

        Returns:
            List of weekend dates (Saturday and Sunday)
        """
        weekends = []
        current = start_date
        while current <= end_date:
            if current.weekday() in [5, 6]:  # Saturday or Sunday
                weekends.append(current.date())
            current += timedelta(days=1)
        return weekends


def _time_to_minutes(t: time) -> int:
    """Convert a :class:`datetime.time` to minutes since midnight."""
    return t.hour * 60 + t.minute
