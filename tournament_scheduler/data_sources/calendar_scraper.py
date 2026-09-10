"""Calendar scraping using Playwright - extracted from monolithic code."""

import re
from datetime import datetime
from typing import List, Optional
from playwright.sync_api import sync_playwright
from tournament_scheduler.models import CalendarEvent
from tournament_scheduler.interfaces import CalendarScraper as BaseCalendarScraper
from tournament_scheduler.utils.calendar_cache import CalendarCache
from rich.console import Console

console = Console()


class OutlookCalendarScraper(BaseCalendarScraper):
    """Handles scraping of Outlook calendars using Playwright."""

    def __init__(self, cache: Optional[CalendarCache] = None):
        """Initialize calendar scraper.

        Args:
            cache: Optional CalendarCache instance for caching scraped events
        """
        self.cache = cache or CalendarCache()
        self.norwegian_months = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12,
            'januar': 1, 'februar': 2, 'mars': 3, 'april': 4,
            'mai': 5, 'juni': 6, 'juli': 7, 'august': 8,
            'september': 9, 'oktober': 10, 'november': 11, 'desember': 12
        }

    def scrape_calendar(self, url: str, calendar_type: str,
                       start_date: datetime, end_date: datetime) -> List[CalendarEvent]:
        """Scrape Outlook calendar using Playwright.

        Args:
            url: Calendar URL
            calendar_type: Type of calendar (for logging)
            start_date: Start of date range
            end_date: End of date range

        Returns:
            List of CalendarEvent objects
        """
        # Check cache first
        cached_events = self.cache.get(url, calendar_type, start_date, end_date)
        if cached_events is not None:
            console.print(f"  [green]✓[/green] Bruker cachet {calendar_type} kalenderdata ([cyan]{len(cached_events)}[/cyan] hendelser)")
            return cached_events

        events = []

        current_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start_month = start_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_month = end_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        months_to_start = ((start_month.year - current_month.year) * 12 +
                          (start_month.month - current_month.month))
        months_to_scrape = ((end_month.year - start_month.year) * 12 +
                           (end_month.month - start_month.month)) + 1

        try:
            with sync_playwright() as p:
                console.print(f"  [dim]Starter nettleser for {calendar_type}...[/dim]")
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()

                console.print(f"  [dim]Laster {calendar_type} kalenderside...[/dim]")
                page.goto(url, timeout=30000)
                page.wait_for_timeout(2000)

                iframe_element = page.query_selector('iframe')
                if not iframe_element:
                    console.print(f"  [yellow]⚠[/yellow] Kunne ikke finne {calendar_type} iframe", style="yellow")
                    browser.close()
                    return events

                iframe = iframe_element.content_frame()
                if not iframe:
                    console.print(f"  [yellow]⚠[/yellow] Kunne ikke laste {calendar_type} iframe innhold", style="yellow")
                    browser.close()
                    return events

                console.print("  [dim]Henter kalender iframe...[/dim]")
                iframe.wait_for_timeout(3000)

                # Navigate to start month
                if months_to_start > 0:
                    console.print(f"  [dim]Navigerer til {start_month.strftime('%B %Y')}...[/dim]")
                    for _ in range(months_to_start):
                        try:
                            next_button = iframe.query_selector('button[aria-label*="Go to next month"]')
                            if next_button:
                                next_button.click()
                                iframe.wait_for_timeout(1000)
                        except Exception as e:
                            console.print(f"  [yellow]⚠[/yellow] Kunne ikke navigere til startmåned: {e}", style="yellow")
                            break
                elif months_to_start < 0:
                    console.print(f"  [dim]Navigerer til {start_month.strftime('%B %Y')}...[/dim]")
                    for _ in range(abs(months_to_start)):
                        try:
                            prev_button = iframe.query_selector('button[aria-label*="Go to previous month"]')
                            if prev_button:
                                prev_button.click()
                                iframe.wait_for_timeout(1000)
                        except Exception as e:
                            console.print(f"  [yellow]⚠[/yellow] Kunne ikke navigere til startmåned: {e}", style="yellow")
                            break

                # Scrape events
                console.print(f"  [dim]Skraper {months_to_scrape} måneder ({start_month.strftime('%b %Y')} til {end_month.strftime('%b %Y')})...[/dim]")
                for month_offset in range(months_to_scrape):
                    iframe.wait_for_timeout(1000)
                    if month_offset > 0:
                        console.print(f"    [dim]Måned {month_offset + 1}/{months_to_scrape}...[/dim]")

                    page_content = iframe.content()
                    month_events = self._parse_outlook_calendar(page_content)
                    events.extend(month_events)

                    if month_offset < months_to_scrape - 1:
                        try:
                            next_button = iframe.query_selector('button[aria-label*="Go to next month"]')
                            if next_button:
                                next_button.click()
                                iframe.wait_for_timeout(1500)
                            else:
                                console.print(f"  [yellow]⚠[/yellow] Kunne ikke finne neste måned-knapp i {calendar_type}", style="yellow")
                                break
                        except Exception as e:
                            console.print(f"  [yellow]⚠[/yellow] Kunne ikke navigere mellom måneder: {e}", style="yellow")
                            break

                browser.close()

            # Deduplicate
            unique_events = []
            seen = set()
            for event in events:
                key = (event.date, event.name)
                if key not in seen:
                    seen.add(key)
                    unique_events.append(event)

            console.print(f"  [green]✓[/green] Skrapte [cyan]{len(unique_events)}[/cyan] hendelser fra {calendar_type} kalender")

            # Cache the results
            self.cache.set(url, calendar_type, start_date, end_date, unique_events)

            return unique_events

        except Exception as e:
            console.print(f"  [red]✗[/red] Kunne ikke skrape {calendar_type} kalender: {e}", style="red")
            return []

    def _parse_outlook_calendar(self, text: str) -> List[CalendarEvent]:
        """Parse events from Outlook calendar HTML.

        Args:
            text: HTML content from calendar

        Returns:
            List of CalendarEvent objects
        """
        events = []
        aria_pattern = r'aria-label="([^"]+)"'
        matches = re.findall(aria_pattern, text)

        for aria_label in matches:
            if 'Go to' in aria_label or 'Print' in aria_label or 'Month' in aria_label:
                continue

            parts = [p.strip() for p in aria_label.split(',')]
            if len(parts) < 4:
                continue

            event_name = parts[0]
            time_part = parts[1] if len(parts) > 1 else ''

            # Parse time and duration
            start_time = None
            end_time = None
            duration_hours = 0

            # AM/PM format
            time_match = re.search(r'(\d{1,2}):(\d{2})\s*(AM|PM)\s+to\s+(\d{1,2}):(\d{2})\s*(AM|PM)',
                                  time_part, re.IGNORECASE)
            if time_match:
                start_hour, start_min, start_period, end_hour, end_min, end_period = time_match.groups()
                start_hour, start_min, end_hour, end_min = map(int, [start_hour, start_min, end_hour, end_min])

                if start_period.upper() == 'PM' and start_hour != 12:
                    start_hour += 12
                elif start_period.upper() == 'AM' and start_hour == 12:
                    start_hour = 0

                if end_period.upper() == 'PM' and end_hour != 12:
                    end_hour += 12
                elif end_period.upper() == 'AM' and end_hour == 12:
                    end_hour = 0

                start_time = start_hour + start_min / 60.0
                end_time = end_hour + end_min / 60.0
                if end_time < start_time:
                    end_time += 24
                duration_hours = end_time - start_time
            else:
                # 24-hour format
                time_match = re.search(r'(\d{1,2}):(\d{2})\s+to\s+(\d{1,2}):(\d{2})', time_part)
                if time_match:
                    start_hour, start_min, end_hour, end_min = map(int, time_match.groups())
                    start_time = start_hour + start_min / 60.0
                    end_time = end_hour + end_min / 60.0
                    if end_time < start_time:
                        end_time += 24
                    duration_hours = end_time - start_time

            # Parse date
            found_date = None
            for i, part in enumerate(parts):
                for month_name, month_num in self.norwegian_months.items():
                    if month_name in part.lower():
                        day_match = re.search(r'\b(\d{1,2})\b', part)
                        year_match = None
                        for j in range(i, min(i+2, len(parts))):
                            year_match = re.search(r'\b(20\d{2})\b', parts[j])
                            if year_match:
                                break
                        if day_match and year_match:
                            try:
                                found_date = datetime(
                                    int(year_match.group(1)),
                                    month_num,
                                    int(day_match.group(1))
                                )
                                break
                            except ValueError:
                                continue
                if found_date:
                    break

            if found_date and event_name:
                # Apply start_time to datetime if parsed
                event_datetime = found_date
                if start_time is not None:
                    hours = int(start_time)
                    minutes = int((start_time - hours) * 60)
                    event_datetime = found_date.replace(hour=hours, minute=minutes)

                events.append(CalendarEvent(
                    date=found_date.strftime('%d.%m.%Y'),
                    name=event_name,
                    datetime=event_datetime,
                    duration_hours=duration_hours
                ))

        # Deduplicate
        unique_events = []
        seen = set()
        for event in events:
            key = (event.date, event.name)
            if key not in seen:
                seen.add(key)
                unique_events.append(event)

        return unique_events


# Backward compatibility alias
CalendarScraper = OutlookCalendarScraper
