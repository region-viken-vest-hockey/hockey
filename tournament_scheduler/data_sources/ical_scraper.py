"""iCal-based calendar scraper for Google Calendar and other generic iCal feeds."""

import requests
from datetime import datetime, timedelta
from typing import List, Optional
from icalendar import Calendar as iCalendar
import recurring_ical_events
from tournament_scheduler.models import CalendarEvent
from tournament_scheduler.interfaces import CalendarScraper
from tournament_scheduler.utils.calendar_cache import CalendarCache
from rich.console import Console

console = Console()


class ICalScraper(CalendarScraper):
    """Scrapes Google Calendar using public iCal feeds."""

    def __init__(self, calendar_id: str, cache: Optional[CalendarCache] = None):
        """Initialize iCal scraper.

        Args:
            calendar_id: Either a Google Calendar ID (email-style, e.g.
                "name@gmail.com") which gets expanded into the public Google
                Calendar iCal feed URL, or a full feed URL (any
                http(s)://... address — Forumbooking, Teamup, Bærum ishall,
                etc.) which is used as-is.
            cache: Optional CalendarCache instance for caching scraped events
        """
        self.calendar_id = calendar_id
        self.cache = cache or CalendarCache()

    def _feed_url(self) -> str:
        """Resolve the actual iCal feed URL to fetch.

        Email-style Google Calendar IDs are expanded into the public Google
        Calendar iCal feed URL (existing Skien integration pattern). Any value
        that already looks like a full URL (http/https) — Forumbooking,
        Teamup, Bærum ishall, etc. — is used as-is.
        """
        if self.calendar_id.startswith('http://') or self.calendar_id.startswith('https://'):
            return self.calendar_id
        return f'https://calendar.google.com/calendar/ical/{self.calendar_id}/public/basic.ics'

    def scrape_calendar(
        self,
        url: str,
        calendar_name: str,
        start_date: datetime,
        end_date: datetime,
        location_filter: Optional[str] = None,
    ) -> List[CalendarEvent]:
        """Scrape calendar events from iCal feed.

        Args:
            url: Not used (kept for interface compatibility)
            calendar_name: Display name for this calendar
            start_date: Start of date range
            end_date: End of date range
            location_filter: When provided, only include events whose LOCATION
                field contains this string (case-insensitive substring match).
                Events with an empty/missing LOCATION are excluded when a
                filter is active.

        Returns:
            List of CalendarEvent objects
        """
        events = []

        # Check cache first (use iCal URL as the "url" parameter)
        ical_url = self._feed_url()
        cached_events = self.cache.get(ical_url, calendar_name, start_date, end_date, location_filter)
        if cached_events is not None:
            console.print(f"  [green]✓[/green] Bruker cachet {calendar_name} kalenderdata ([cyan]{len(cached_events)}[/cyan] hendelser)")
            return cached_events

        try:
            console.print(f"  [dim]Henter {calendar_name} iCal feed...[/dim]")
            response = requests.get(ical_url, timeout=15)

            if response.status_code != 200:
                console.print(f"  [red]✗[/red] Kunne ikke hente iCal feed: HTTP {response.status_code}", style="red")
                return events

            # Parse iCal data
            cal = iCalendar.from_ical(response.content)

            # Get events in date range
            # Add buffer to ensure we catch all events
            extended_end = end_date + timedelta(days=1)
            ical_events = recurring_ical_events.of(cal).between(start_date, extended_end)

            console.print(f"  [dim]Fant {len(ical_events)} hendelser i datoperioden[/dim]")

            # Convert to CalendarEvent objects
            for event in ical_events:
                start = event.get('DTSTART').dt
                end_dt = event.get('DTEND').dt if event.get('DTEND') else None
                summary = str(event.get('SUMMARY', ''))

                location = str(event.get('LOCATION', ''))

                # Apply location filter if specified
                if location_filter is not None:
                    if location_filter.lower() not in location.lower():
                        continue

                # iCal distinguishes a date-only DTSTART (all-day event) from
                # a real timed event. Keep that semantic distinction explicit
                # instead of making a date-only row look like a literal 00:00
                # booking in the human audit calendar. CalendarEvent is not a
                # slots dataclass, so ``all_day`` is attached as backward-
                # compatible event metadata until the model contract is next
                # versioned.
                all_day = not hasattr(start, 'hour')

                if not all_day:
                    event_datetime = start
                    event_date_str = start.strftime('%d.%m.%Y')

                    # Calculate duration
                    if end_dt and hasattr(end_dt, 'hour'):
                        duration_hours = (end_dt - start).total_seconds() / 3600
                    else:
                        duration_hours = 0
                else:
                    event_datetime = datetime.combine(start, datetime.min.time())
                    event_date_str = start.strftime('%d.%m.%Y')
                    duration_hours = 0

                calendar_event = CalendarEvent(
                    date=event_date_str,
                    name=summary,
                    datetime=event_datetime,
                    duration_hours=duration_hours,
                    location=location,
                )
                calendar_event.all_day = all_day
                events.append(calendar_event)

            # Deduplicate. all-day vs genuinely timed-midnight events are
            # intentionally distinct even when date/name/hour happen to match.
            unique_events = []
            seen = set()
            for event in events:
                key = (
                    event.date,
                    event.name,
                    event.datetime.hour if hasattr(event.datetime, 'hour') else 0,
                    bool(getattr(event, "all_day", False)),
                    event.location,
                )
                if key not in seen:
                    seen.add(key)
                    unique_events.append(event)

            console.print(f"  [green]✓[/green] Skrapte [cyan]{len(unique_events)}[/cyan] hendelser fra {calendar_name}")

            # Cache the results
            if location_filter is None:
                self.cache.set(ical_url, calendar_name, start_date, end_date, unique_events)
            else:
                self.cache.set(
                    ical_url,
                    calendar_name,
                    start_date,
                    end_date,
                    unique_events,
                    location_filter=location_filter,
                )

            return unique_events

        except Exception as e:
            console.print(f"  [red]✗[/red] Kunne ikke skrape {calendar_name}: {e}", style="red")
            import traceback
            traceback.print_exc()
            return []