"""--reschedule CLI mode: find alternative dates for an existing tournament."""

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


class RescheduleCommand:
    """Finds alternative dates for a tournament that needs to move (--reschedule)."""

    def run(self, args, reschedule_date: datetime, start_date: datetime, end_date: datetime) -> None:
        print("=" * 60)
        print("TOURNAMENT RESCHEDULER")
        print("=" * 60)
        print(f"Original tournament date: {reschedule_date.strftime('%Y-%m-%d')}")
        print(f"Search range for alternatives: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
        print("\nExtracting tournament details from Excel file...")
        print("(Will scrape calendars after team extraction)\n")

        print("Analyzing Excel file...")
        excel_reader = ExcelTournamentReader(args.excel_file, DateParser())
        tournament_info = excel_reader.get_tournament_info(reschedule_date.date())

        print("\nNow scraping calendars to check team availability (30-60 seconds)...\n")

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

        # Fetch ALL ice hall events for timeslot checking (not just tournaments)
        print("Fetching all ice hall events...")
        all_ice_hall_events = ice_hall.scraper.scrape_calendar(ice_hall.url, "ice hall", start_date, end_date)

        # Fetch ball hall events (already filtered for >2 hours)
        ball_hall_events = ball_hall.fetch_events(start_date, end_date)

        # Pull in tournament events from every other known club's calendar too,
        # so team-availability checking accounts for away tournaments as well.
        other_club_events = []
        for club, source in sources_by_club.items():
            if club == "Kongsberg":
                continue
            other_club_events.extend(source.fetch_events(start_date, end_date))

        # Combine all events for team checker
        all_events_for_teams = all_ice_hall_events + ball_hall_events + other_club_events
        print(f"  Total events for team checking: {len(all_events_for_teams)} "
              f"({len(all_ice_hall_events)} ice hall + {len(ball_hall_events)} ball hall + "
              f"{len(other_club_events)} other clubs)\n")

        checkers = [
            HolidayConflictChecker(),
            TournamentConflictChecker(ice_hall),
            BallHallConflictChecker(ball_hall),
            TeamAvailabilityChecker(all_events_for_teams),
            ExcelConflictChecker(set()),  # excel_dates passed via find_available_dates below
        ]

        scheduler = TournamentScheduler(
            calendar_sources=club_sources + [ball_hall],
            conflict_checkers=checkers,
            date_parser=DateParser()
        )

        all_tournament_dates = excel_reader.get_all_tournament_dates()
        excel_dates = all_tournament_dates - {reschedule_date.date()}

        result = scheduler.find_available_dates(
            start_date=start_date,
            end_date=end_date,
            team_names=tournament_info.teams,
            excel_dates=excel_dates,
            calendar_events=all_events_for_teams  # Pass all events (not just tournaments)
        )
        result.tournament_info = tournament_info

        self._print_results(result, tournament_info)

    def _print_results(self, result, tournament_info) -> None:
        print("\n" + "=" * 60)
        print("RESCHEDULING RESULTS")
        print("=" * 60)

        print(f"\nSearched: {result.total_weekends_checked} weekend dates")
        print(f"Available: {len(result.available_dates)} dates when ALL teams are free")
        print(f"Blocked: {len(result.excluded_dates)} dates with conflicts")

        if result.exclusion_breakdown:
            print("\nReasons for blocked dates:")
            for checker_name, count in sorted(result.exclusion_breakdown.items()):
                if checker_name != 'ball_hall_warning' and count > 0:
                    print(f"  • {checker_name.replace('_', ' ').title()}: {count} dates")

        if result.available_dates:
            print(f"\n{'='*60}")
            print(f"✓ AVAILABLE DATES (all {len(tournament_info.teams)} teams free):")
            print(f"{'='*60}")
            for d in sorted(result.available_dates):
                day_name = d.strftime('%A')
                print(f"  {d.strftime('%Y-%m-%d')} ({day_name})")
        else:
            print(f"\n{'='*60}")
            print("✗ NO AVAILABLE DATES FOUND")
            print(f"{'='*60}")
            print("  All dates have conflicts. Try:")
            print("  • Expanding the date range (--start-date / --end-date)")
            print("  • Checking if team schedules can be adjusted")

        print("\n" + "=" * 60 + "\n")
