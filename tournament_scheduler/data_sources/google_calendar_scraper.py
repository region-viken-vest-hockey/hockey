"""Google Calendar scraper for embedded Google Calendars."""

import re
from datetime import datetime
from typing import List
from playwright.sync_api import sync_playwright
from tournament_scheduler.models import CalendarEvent
from tournament_scheduler.interfaces import CalendarScraper
from rich.console import Console

console = Console()


class GoogleCalendarScraper(CalendarScraper):
    """Handles scraping of embedded Google Calendars using Playwright."""

    def __init__(self):
        """Initialize Google Calendar scraper."""
        self.norwegian_months = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12,
            'januar': 1, 'februar': 2, 'mars': 3, 'april': 4,
            'mai': 5, 'juni': 6, 'juli': 7, 'august': 8,
            'september': 9, 'oktober': 10, 'november': 11, 'desember': 12
        }

    def scrape_calendar(self, url: str, calendar_name: str,
                       start_date: datetime, end_date: datetime) -> List[CalendarEvent]:
        """Scrape Google Calendar using Playwright.

        Args:
            url: Calendar URL (page containing Google Calendar embed)
            calendar_name: Name of calendar (for logging)
            start_date: Start of date range
            end_date: End of date range

        Returns:
            List of CalendarEvent objects
        """
        events = []

        try:
            with sync_playwright() as p:
                console.print(f"  [dim]Starter nettleser for {calendar_name}...[/dim]")
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()

                console.print(f"  [dim]Laster {calendar_name} side...[/dim]")
                page.goto(url, timeout=30000)
                page.wait_for_timeout(3000)

                # Find Google Calendar iframe
                console.print("  [dim]Ser etter Google Calendar iframe...[/dim]")
                iframe_element = page.query_selector('iframe[src*="calendar.google.com"]')
                if not iframe_element:
                    console.print(f"  [yellow]⚠[/yellow] Kunne ikke finne Google Calendar iframe for {calendar_name}", style="yellow")
                    browser.close()
                    return events

                iframe = iframe_element.content_frame()
                if not iframe:
                    console.print(f"  [yellow]⚠[/yellow] Kunne ikke laste Google Calendar iframe innhold for {calendar_name}", style="yellow")
                    browser.close()
                    return events

                console.print("  [dim]Åpner Google Calendar iframe...[/dim]")
                iframe.wait_for_timeout(2000)

                # Google Calendar shows events in list or agenda view
                # Try to switch to agenda view if available
                try:
                    # Look for view switcher and click on agenda/list view
                    agenda_button = iframe.query_selector('button[aria-label*="Agenda"], [data-view="agenda"]')
                    if agenda_button:
                        agenda_button.click()
                        iframe.wait_for_timeout(1000)
                except Exception as e:
                    console.print(f"  [dim]Notat: Kunne ikke bytte til agenda-visning: {e}[/dim]")

                # Calculate months to scrape
                current_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                start_month = start_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                end_month = end_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

                months_to_scrape = ((end_month.year - start_month.year) * 12 +
                                   (end_month.month - start_month.month)) + 1

                console.print(f"  [dim]Skraper {months_to_scrape} måneder ({start_month.strftime('%b %Y')} til {end_month.strftime('%b %Y')})...[/dim]")

                # Navigate and scrape
                current_scrape_month = start_month
                for month_offset in range(months_to_scrape):
                    if month_offset > 0:
                        console.print(f"    [dim]Måned {month_offset + 1}/{months_to_scrape}...[/dim]")

                    iframe.wait_for_timeout(1500)

                    # Get page content and parse events
                    page_content = iframe.content()
                    month_events = self._parse_google_calendar(page_content, current_scrape_month)
                    events.extend(month_events)

                    # Navigate to next month if not the last month
                    if month_offset < months_to_scrape - 1:
                        try:
                            # Google Calendar next button
                            next_button = iframe.query_selector('button[aria-label*="Next"]')
                            if next_button:
                                next_button.click()
                                iframe.wait_for_timeout(1500)
                            else:
                                console.print(f"  [yellow]⚠[/yellow] Kunne ikke finne neste måned-knapp i {calendar_name}", style="yellow")
                                break
                        except Exception as e:
                            console.print(f"  [yellow]⚠[/yellow] Kunne ikke navigere mellom måneder: {e}", style="yellow")
                            break

                    current_scrape_month = self._next_month(current_scrape_month)

                browser.close()

            # Deduplicate
            unique_events = []
            seen = set()
            for event in events:
                key = (event.date, event.name)
                if key not in seen:
                    seen.add(key)
                    unique_events.append(event)

            console.print(f"  [green]✓[/green] Skrapte [cyan]{len(unique_events)}[/cyan] hendelser fra {calendar_name}")
            return unique_events

        except Exception as e:
            console.print(f"  [red]✗[/red] Kunne ikke skrape {calendar_name}: {e}", style="red")
            import traceback
            traceback.print_exc()
            return []

    def _parse_google_calendar(self, html: str, current_month: datetime) -> List[CalendarEvent]:
        """Parse events from Google Calendar HTML.

        Args:
            html: HTML content from calendar
            current_month: Current month being scraped

        Returns:
            List of CalendarEvent objects
        """
        events = []

        # Google Calendar uses various patterns - try multiple approaches
        # Look for event divs, spans with data attributes, etc.

        # Pattern 1: Event divs with data-* attributes
        event_pattern = r'data-eventid="([^"]*)"[^>]*>([^<]+)</div>'
        matches = re.findall(event_pattern, html, re.DOTALL)

        # Pattern 2: Look for aria-label patterns similar to Outlook
        aria_pattern = r'aria-label="([^"]+)"'
        aria_matches = re.findall(aria_pattern, html)

        for aria_label in aria_matches:
            # Skip navigation elements
            if any(skip in aria_label.lower() for skip in ['next', 'previous', 'today', 'month', 'week', 'day', 'agenda']):
                continue

            # Try to parse event from aria-label
            # Google format often: "Event Name, Date, Time"
            parts = [p.strip() for p in aria_label.split(',')]
            if len(parts) >= 2:
                event_name = parts[0]

                # Try to find date and time info
                date_found = None
                duration_hours = 0

                for part in parts[1:]:
                    # Look for date patterns
                    for month_name, month_num in self.norwegian_months.items():
                        if month_name in part.lower():
                            day_match = re.search(r'\b(\d{1,2})\b', part)
                            if day_match:
                                try:
                                    date_found = datetime(
                                        current_month.year,
                                        month_num,
                                        int(day_match.group(1))
                                    )
                                except ValueError:
                                    continue
                            break

                    # Look for time patterns
                    time_match = re.search(r'(\d{1,2}):(\d{2})\s*[-–to]+\s*(\d{1,2}):(\d{2})', part)
                    if time_match:
                        start_hour, start_min, end_hour, end_min = map(int, time_match.groups())
                        start_time = start_hour + start_min / 60.0
                        end_time = end_hour + end_min / 60.0
                        if end_time < start_time:
                            end_time += 24
                        duration_hours = end_time - start_time

                if date_found and event_name:
                    events.append(CalendarEvent(
                        date=date_found.strftime('%d.%m.%Y'),
                        name=event_name,
                        datetime=date_found,
                        duration_hours=duration_hours
                    ))

        return events

    def _next_month(self, current: datetime) -> datetime:
        """Get next month datetime.

        Args:
            current: Current month

        Returns:
            Next month datetime
        """
        if current.month == 12:
            return current.replace(year=current.year + 1, month=1)
        else:
            return current.replace(month=current.month + 1)
