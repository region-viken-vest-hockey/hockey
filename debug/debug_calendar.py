#!/usr/bin/env python3
"""Debug tool to inspect calendar bookings."""

import sys
import argparse
from datetime import datetime, timedelta
from tournament_scheduler.data_sources.calendar_scraper import OutlookCalendarScraper
from tournament_scheduler.data_sources.ical_scraper import ICalScraper
from tournament_scheduler.utils.date_parser import DateParser


def format_event(event):
    """Format event for display.

    Args:
        event: CalendarEvent object

    Returns:
        Formatted string
    """
    if hasattr(event.datetime, 'hour') and event.duration_hours > 0:
        start_time = event.datetime.strftime('%H:%M')
        end_time = event.datetime + timedelta(hours=event.duration_hours)
        end_str = end_time.strftime('%H:%M')
        return f"{event.date} {start_time}-{end_str} ({event.duration_hours:.1f}h): {event.name}"
    else:
        return f"{event.date} (all day): {event.name}"


def debug_calendar(calendar_type, start_date, end_date, specific_date=None):
    """Fetch and display calendar bookings.

    Args:
        calendar_type: Type of calendar (kongsberg_ice, kongsberg_ball, skien_ice)
        start_date: Start of date range
        end_date: End of date range
        specific_date: Optional specific date to focus on
    """
    print("=" * 80)
    print("CALENDAR DEBUG TOOL")
    print("=" * 80)

    # Initialize scraper based on type
    if calendar_type == 'kongsberg_ice':
        scraper = OutlookCalendarScraper()
        url = "https://kongsberghallen.no/webkalender/ishall/"
        name = "Kongsberg Ice Hall"
    elif calendar_type == 'kongsberg_ball':
        scraper = OutlookCalendarScraper()
        url = "https://kongsberghallen.no/webkalender/ballhall-dagtid-og-helg/"
        name = "Kongsberg Ball Hall"
    elif calendar_type == 'skien_ice':
        scraper = ICalScraper('istiderskienhockey@gmail.com')
        url = "https://skienishockey.no/kalender-isbooking/"
        name = "Skien Ice Hall"
    else:
        print(f"Error: Unknown calendar type: {calendar_type}")
        print("Valid types: kongsberg_ice, kongsberg_ball, skien_ice")
        sys.exit(1)

    print(f"\nCalendar: {name}")
    print(f"URL: {url}")
    print(f"Date range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    if specific_date:
        print(f"Focusing on: {specific_date.strftime('%Y-%m-%d')}")
    print()

    # Fetch events
    print("Fetching events...")
    events = scraper.scrape_calendar(url, name, start_date, end_date)

    print(f"\n{'='*80}")
    print(f"FOUND {len(events)} EVENTS")
    print(f"{'='*80}\n")

    if not events:
        print("No events found in the specified date range.")
        return

    if specific_date:
        # Filter for specific date
        date_str = specific_date.strftime('%d.%m.%Y')
        filtered_events = [e for e in events if e.date == date_str]

        print(f"Events on {specific_date.strftime('%Y-%m-%d (%A)')}:")
        print("-" * 80)

        if filtered_events:
            # Sort by time
            sorted_events = sorted(filtered_events, key=lambda e: e.datetime if hasattr(e.datetime, 'hour') else datetime.min)

            for event in sorted_events:
                print(f"  {format_event(event)}")

            # Show available gaps
            print(f"\n{'='*80}")
            print("TIME ANALYSIS FOR THIS DATE")
            print(f"{'='*80}\n")

            events_with_time = [e for e in sorted_events if hasattr(e.datetime, 'hour') and e.duration_hours > 0]
            if events_with_time:
                print("Busy periods:")
                for event in events_with_time:
                    start_time = event.datetime.strftime('%H:%M')
                    end_time = event.datetime + timedelta(hours=event.duration_hours)
                    end_str = end_time.strftime('%H:%M')
                    print(f"  {start_time}-{end_str}: {event.name[:60]}")

                # Calculate gaps
                print("\nAvailable gaps (for 2.5h tournament, starting 11:00-14:00):")

                # Sort by start time
                sorted_by_time = sorted(events_with_time, key=lambda e: e.datetime)

                # Check before first event
                first_start = sorted_by_time[0].datetime
                if first_start.hour >= 11 and first_start.hour >= 13:  # At least 2.5h before first event
                    print(f"  11:00-{first_start.strftime('%H:%M')} (before first booking)")

                # Check gaps between events
                for i in range(len(sorted_by_time) - 1):
                    event1 = sorted_by_time[i]
                    event2 = sorted_by_time[i + 1]

                    gap_start = event1.datetime + timedelta(hours=event1.duration_hours)
                    gap_end = event2.datetime
                    gap_hours = (gap_end - gap_start).total_seconds() / 3600

                    if gap_hours >= 2.5 and gap_start.hour <= 14:
                        print(f"  {gap_start.strftime('%H:%M')}-{gap_end.strftime('%H:%M')} ({gap_hours:.1f}h gap)")

                # Check after last event
                last_end = sorted_by_time[-1].datetime + timedelta(hours=sorted_by_time[-1].duration_hours)
                if last_end.hour <= 14:
                    print(f"  {last_end.strftime('%H:%M')}-16:30 (after last booking)")
            else:
                print("No timed events - entire day available")
        else:
            print(f"  No events found on {specific_date.strftime('%Y-%m-%d')}")
            print("  ✓ Full day available (11:00-16:30)")
    else:
        # Show all events grouped by date
        from collections import defaultdict
        events_by_date = defaultdict(list)

        for event in events:
            events_by_date[event.date].append(event)

        # Sort dates
        sorted_dates = sorted(events_by_date.keys(), key=lambda d: DateParser.parse(d))

        print(f"Showing first 20 dates (total: {len(sorted_dates)} dates with events):\n")

        for date_str in sorted_dates[:20]:
            parsed_date = DateParser.parse(date_str)
            if parsed_date:
                date_display = parsed_date.strftime('%Y-%m-%d (%A)')
            else:
                date_display = date_str

            print(f"{date_display}:")

            date_events = sorted(events_by_date[date_str], key=lambda e: e.datetime if hasattr(e.datetime, 'hour') else datetime.min)
            for event in date_events[:10]:  # Max 10 events per date
                print(f"  {format_event(event)}")

            if len(date_events) > 10:
                print(f"  ... and {len(date_events) - 10} more events")
            print()

        if len(sorted_dates) > 20:
            print(f"... and {len(sorted_dates) - 20} more dates with events")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Debug tool to inspect calendar bookings',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Calendar types:
  kongsberg_ice    Kongsberg ice hall
  kongsberg_ball   Kongsberg ball hall
  skien_ice        Skien ice hall

Examples:
  # Check Skien ice hall for March 2026
  %(prog)s skien_ice --start 2026-03-01 --end 2026-03-31

  # Check specific date
  %(prog)s skien_ice --date 2026-03-07

  # Check Kongsberg ice hall for next month
  %(prog)s kongsberg_ice --start 2025-12-01 --end 2025-12-31
        '''
    )

    parser.add_argument('calendar', choices=['kongsberg_ice', 'kongsberg_ball', 'skien_ice'],
                       help='Calendar to inspect')
    parser.add_argument('--start', type=str,
                       help='Start date (YYYY-MM-DD), default: today')
    parser.add_argument('--end', type=str,
                       help='End date (YYYY-MM-DD), default: 30 days from start')
    parser.add_argument('--date', type=str,
                       help='Specific date to focus on (YYYY-MM-DD)')

    args = parser.parse_args()

    # Parse dates
    if args.date:
        # If specific date provided, use it for both start and end
        specific_date = DateParser.parse(args.date)
        if not specific_date:
            print(f"Error: Invalid date format '{args.date}'. Use YYYY-MM-DD format.")
            sys.exit(1)
        start_date = specific_date
        end_date = specific_date
        specific_date_obj = specific_date.date()
    else:
        specific_date_obj = None

        if args.start:
            start_date = DateParser.parse(args.start)
            if not start_date:
                print(f"Error: Invalid start date format '{args.start}'. Use YYYY-MM-DD format.")
                sys.exit(1)
        else:
            start_date = datetime.now()

        if args.end:
            end_date = DateParser.parse(args.end)
            if not end_date:
                print(f"Error: Invalid end date format '{args.end}'. Use YYYY-MM-DD format.")
                sys.exit(1)
        else:
            end_date = start_date + timedelta(days=30)

    # Run debug
    try:
        debug_calendar(args.calendar, start_date, end_date, specific_date_obj)
    except KeyboardInterrupt:
        print("\n\nCancelled by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
