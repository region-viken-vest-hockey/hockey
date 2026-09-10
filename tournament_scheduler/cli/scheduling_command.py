"""Default CLI mode: find available weekend dates for a new tournament."""

from datetime import datetime

from tournament_scheduler.club_registry import missing_clubs
from tournament_scheduler.conflict_checkers.ball_hall_checker import BallHallConflictChecker
from tournament_scheduler.conflict_checkers.excel_checker import ExcelConflictChecker
from tournament_scheduler.conflict_checkers.holiday_checker import HolidayConflictChecker
from tournament_scheduler.conflict_checkers.team_availability_checker import TeamAvailabilityChecker
from tournament_scheduler.conflict_checkers.tournament_checker import TournamentConflictChecker
from tournament_scheduler.data_sources.ball_hall_calendar import BallHallCalendar
from tournament_scheduler.data_sources.calendar_scraper import CalendarScraper
from tournament_scheduler.data_sources.calendar_source_factory import build_known_calendar_sources
from tournament_scheduler.excel.tournament_reader import ExcelTournamentReader
from tournament_scheduler.scheduler import TournamentScheduler
from tournament_scheduler.utils.calendar_cache import CalendarCache
from tournament_scheduler.utils.date_parser import DateParser
from tournament_scheduler.utils.rich_output import TournamentOutput


class SchedulingCommand:
    """Finds available weekend dates for a new tournament (default mode)."""

    def run(self, args, start_date: datetime, end_date: datetime) -> None:
        team_names = [t.strip() for t in args.teams.split(',') if t.strip()]

        TournamentOutput.print_header("TOURNAMENT SCHEDULER")
        TournamentOutput.print_info(f"Date range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
        if team_names:
            TournamentOutput.print_info(f"Filtering conflicts for teams: {', '.join(team_names)}")
        TournamentOutput.print_info("Scraping calendars (this may take 30-60 seconds)...")

        cache = CalendarCache(work_dir=".pipeline")
        scraper = CalendarScraper(cache)

        # Registry-driven calendar sources for every club with a known,
        # usable calendar (Kongsberg, Skien, Ringerike, ... — adding a club is
        # then just a matter of populating its CLUB_REGISTRY entry).
        club_sources, sources_by_club = build_known_calendar_sources(cache)
        for entry in missing_clubs():
            TournamentOutput.print_warning(
                f"Hopper over {entry.club} — kalenderkilde mangler ennå ({entry.note or 'ingen URL'})"
            )

        ice_hall = sources_by_club.get("Kongsberg")
        ball_hall = BallHallCalendar("https://kongsberghallen.no/webkalender/ballhall-dagtid-og-helg/", scraper)

        all_ice_hall_events = ice_hall.scraper.scrape_calendar(ice_hall.url, "ice hall", start_date, end_date)
        ball_hall_events = ball_hall.fetch_events(start_date, end_date)

        # Pull in tournament events from every other known club's calendar too,
        # so team-availability checking accounts for away tournaments as well.
        other_club_events = []
        for club, source in sources_by_club.items():
            if club == "Kongsberg":
                continue
            other_club_events.extend(source.fetch_events(start_date, end_date))

        all_events = all_ice_hall_events + ball_hall_events + other_club_events

        excel_dates = set()
        if args.excel_file:
            TournamentOutput.print_info(f"Reading Excel file: {args.excel_file}")
            excel_dates = ExcelTournamentReader(args.excel_file, DateParser()).get_all_tournament_dates()

        checkers = [
            HolidayConflictChecker(),
            TournamentConflictChecker(ice_hall),
            BallHallConflictChecker(ball_hall),
            ExcelConflictChecker(set()),  # excel_dates passed via find_available_dates below
        ]
        if team_names:
            checkers.append(TeamAvailabilityChecker(all_events))

        scheduler = TournamentScheduler(
            calendar_sources=club_sources + [ball_hall],
            conflict_checkers=checkers,
            date_parser=DateParser()
        )

        TournamentOutput.print_info("Analyzing conflicts and finding available dates...")
        result = scheduler.find_available_dates(
            start_date=start_date,
            end_date=end_date,
            team_names=team_names,
            excel_dates=excel_dates,
            calendar_events=all_events
        )
        TournamentOutput.print_success("Analysis complete")

        self._print_results(result)

    def _print_results(self, result) -> None:
        TournamentOutput.print_summary(
            total_checked=result.total_weekends_checked,
            available_count=len(result.available_dates),
            blocked_count=len(result.excluded_dates),
            breakdown=result.exclusion_breakdown,
        )

        if result.available_dates:
            print(f"\n{'='*60}")
            print("✓ AVAILABLE DATES:")
            print(f"{'='*60}")
            for d in sorted(result.available_dates):
                day_name = d.strftime('%A')
                print(f"  {d.strftime('%Y-%m-%d')} ({day_name})")
            print()
        else:
            TournamentOutput.print_no_dates_found()
