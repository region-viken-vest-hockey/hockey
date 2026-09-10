"""Outlook Web Calendar iframe scraper for Stage 2.

Provides :func:`_run_outlook_scraper` (Playwright-based Outlook/iframe scraping)
and its two HTML parser helpers (:func:`_parse_outlook_calendar` for aria-label
parsing, :func:`_parse_date_param_calendar` for date-parameter pages).
"""

from __future__ import annotations

import re
from datetime import datetime

from ..models import CalendarEvent
from ..utils.calendar_cache import CalendarCache


def _run_outlook_scraper(
    url: str,
    name: str,
    start_date: datetime,
    end_date: datetime,
    cache: CalendarCache | None = None,
) -> tuple[list[CalendarEvent], str]:
    """Playwright scraper for calendar pages.

    Supports two page layouts:
    1. **Outlook iframe** — loads the URL, finds the calendar iframe, iterates
       through each month via ``Go to next month`` button, and parses
       ``aria-label`` attributes to extract events.
    2. **Date-parameter pages** (e.g. ``?date=2026-09-01``) — when no iframe
       is found, navigates by appending/replacing the ``date`` query parameter
       with each month's start date.

    Returns (events, raw_html) where raw_html is the accumulated page content
    across all months.
    """
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
    from ..data_sources.calendar_scraper import OutlookCalendarScraper

    events: list[CalendarEvent] = []
    raw_html: str = ""
    norwegian_months = OutlookCalendarScraper(cache).norwegian_months

    start_month = start_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_month = end_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    months_to_scrape = (
        (end_month.year - start_month.year) * 12
        + (end_month.month - start_month.month)
        + 1
    )

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=30000)
            page.wait_for_timeout(2000)

            iframe_element = page.query_selector("iframe")
            has_iframe = iframe_element is not None and iframe_element.content_frame() is not None

            if has_iframe:
                # ---- Outlook iframe approach ----
                iframe = iframe_element.content_frame()
                iframe.wait_for_timeout(3000)

                for month_idx in range(months_to_scrape):
                    iframe.wait_for_timeout(1000)
                    page_content = iframe.content()
                    raw_html += page_content

                    month_events = _parse_outlook_calendar(page_content, norwegian_months)
                    events.extend(month_events)

                    if month_idx < months_to_scrape - 1:
                        try:
                            next_btn = iframe.query_selector(
                                'button[aria-label*="next month"]'
                            )
                            if next_btn:
                                next_btn.click()
                                iframe.wait_for_timeout(1500)
                        except Exception:
                            pass
            else:
                # ---- Date-parameter approach (e.g. ?date=YYYY-MM-DD) ----
                parsed = urlparse(url)
                query = parse_qs(parsed.query)

                current_month = start_month
                for month_idx in range(months_to_scrape):
                    # Build URL with date parameter
                    date_str = current_month.strftime("%Y-%m-%d")
                    q = dict(query)
                    q["date"] = [date_str]
                    new_query = urlencode(q, doseq=True)
                    month_url = urlunparse(parsed._replace(query=new_query))

                    try:
                        page.goto(month_url, timeout=30000)
                        page.wait_for_timeout(3000)
                        page_content = page.content()
                        raw_html += page_content

                        month_events = _parse_date_param_calendar(
                            page_content, current_month, norwegian_months
                        )
                        events.extend(month_events)
                    except Exception:
                        pass

                    # Next month
                    if current_month.month == 12:
                        current_month = current_month.replace(year=current_month.year + 1, month=1)
                    else:
                        current_month = current_month.replace(month=current_month.month + 1)

            browser.close()
    except Exception:
        pass

    # Deduplicate
    seen: set[tuple[str, str]] = set()
    unique: list[CalendarEvent] = []
    for ev in events:
        key = (ev.date, ev.name)
        if key not in seen:
            seen.add(key)
            unique.append(ev)

    return unique, raw_html


def _parse_date_param_calendar(
    html: str,
    month_start: datetime,
    norwegian_months: dict[str, int],
) -> list[CalendarEvent]:
    """Parse calendar events from a date-parameter page (brp.exigo.no-style).

    Strips HTML tags and looks for visible event-like text patterns.
    """
    from datetime import datetime as _dt

    events: list[CalendarEvent] = []
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # Look for time ranges (HH:MM-HH:MM) that indicate bookings
    time_pattern = r"(\d{1,2}[\.:]\d{2})\s*-\s*(\d{1,2}[\.:]\d{2})"
    for m in re.finditer(time_pattern, text):
        start_str, end_str = m.groups()
        try:
            sh, sm = (int(x) for x in start_str.replace(".", ":").split(":"))
            eh, em = (int(x) for x in end_str.replace(".", ":").split(":"))
        except ValueError:
            continue
        duration = (eh + em / 60.0) - (sh + sm / 60.0)
        if duration < 0:
            duration += 24
        day = _dt(year=month_start.year, month=month_start.month, day=1)
        events.append(CalendarEvent(
            date=day.strftime("%d.%m.%Y"),
            name=f"Booking {m.group()}",
            datetime=day.replace(hour=sh, minute=sm),
            duration_hours=duration,
        ))
    return events


def _parse_outlook_calendar(
    html: str,
    norwegian_months: dict[str, int],
) -> list[CalendarEvent]:
    """Parse events from a single month's Outlook calendar HTML.

    Uses a regex-based approach on ``aria-label`` attributes, matching the
    pattern used by Outlook Web Calendar::

        "Event name, 10:00 AM to 11:30 AM, Monday, January 15, 2026"

    Returns a list of :class:`CalendarEvent` objects.
    """
    events: list[CalendarEvent] = []
    aria_pattern = r'aria-label="([^"]+)"'
    matches = re.findall(aria_pattern, html)

    for aria_label in matches:
        if "Go to" in aria_label or "Print" in aria_label or "Month" in aria_label:
            continue

        parts = [p.strip() for p in aria_label.split(",")]
        if len(parts) < 4:
            continue

        event_name = parts[0]
        time_part = parts[1] if len(parts) > 1 else ""

        start_time: float | None = None
        duration_hours = 0.0

        # AM/PM format
        time_match = re.search(
            r"(\d{1,2}):(\d{2})\s*(AM|PM)\s+to\s+(\d{1,2}):(\d{2})\s*(AM|PM)",
            time_part,
            re.IGNORECASE,
        )
        if time_match:
            sh, sm, sp, eh, em, ep = time_match.groups()
            sh, sm, eh, em = map(int, [sh, sm, eh, em])
            if sp.upper() == "PM" and sh != 12:
                sh += 12
            if sp.upper() == "AM" and sh == 12:
                sh = 0
            if ep.upper() == "PM" and eh != 12:
                eh += 12
            if ep.upper() == "AM" and eh == 12:
                eh = 0
            start_time = sh + sm / 60.0
            end_time = eh + em / 60.0
            if end_time < start_time:
                end_time += 24
            duration_hours = end_time - start_time
        else:
            # 24-hour format
            time_match = re.search(
                r"(\d{1,2}):(\d{2})\s+to\s+(\d{1,2}):(\d{2})",
                time_part,
            )
            if time_match:
                sh, sm, eh, em = map(int, time_match.groups())
                start_time = sh + sm / 60.0
                end_time = eh + em / 60.0
                if end_time < start_time:
                    end_time += 24
                duration_hours = end_time - start_time

        # Parse date from aria-label parts
        found_date = None
        for i, part in enumerate(parts):
            for month_name, month_num in norwegian_months.items():
                if month_name in part.lower():
                    day_match = re.search(r"\b(\d{1,2})\b", part)
                    year_match = None
                    for j in range(i, min(i + 2, len(parts))):
                        yr = re.search(r"\b(20\d{2})\b", parts[j])
                        if yr:
                            year_match = yr
                            break
                    if day_match and year_match:
                        try:
                            found_date = datetime(
                                int(year_match.group(1)),
                                month_num,
                                int(day_match.group(1)),
                            )
                            break
                        except ValueError:
                            continue
            if found_date:
                break

        if found_date and event_name:
            event_dt = found_date
            if start_time is not None:
                hours = int(start_time)
                minutes = int((start_time - hours) * 60)
                event_dt = found_date.replace(hour=hours, minute=minutes)

            events.append(
                CalendarEvent(
                    date=found_date.strftime("%d.%m.%Y"),
                    name=event_name,
                    datetime=event_dt,
                    duration_hours=duration_hours,
                )
            )

    return events
