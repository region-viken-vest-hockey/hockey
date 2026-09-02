"""Forumbooking schedule scraper for Stage 2.

Forumbooking's public ``schema.aspx`` view renders one week of bookings as
plain HTML. The generic browser fallback only sees time ranges and therefore
collapses them onto the first day of each scraped month. This module parses the
Forumbooking-specific booking element ids and tooltip text so Jarhallen events
keep their real dates, times, and customer labels.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta
from typing import Any

from bs4 import BeautifulSoup

from ..models import CalendarEvent

_DATE_IN_ID_RE = re.compile(r"_(20\d{6})_\d+(?:\D|$)")
_TIME_RANGE_RE = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
_CUSTOMER_RE = re.compile(r"Customer:\s*([^<]+)", re.IGNORECASE)
_NOTE_RE = re.compile(r"Anmerkning:\s*([^<]+)", re.IGNORECASE)
_SCHEDULE_RANGE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})")


def _run_forumbooking_scraper(
    url: str,
    name: str,
    start_date: datetime,
    end_date: datetime,
) -> tuple[list[CalendarEvent], str]:
    """Scrape a Forumbooking weekly schedule by clicking next period.

    Returns ``(events, raw_html)``. Failures are intentionally non-throwing so
    Stage 2 can mark the source empty/blocked using its normal policy.
    """
    from playwright.sync_api import sync_playwright

    events: list[CalendarEvent] = []
    raw_html = ""
    start_ref = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_ref = end_date.replace(hour=23, minute=59, second=59, microsecond=0)
    max_weeks = max(1, ((end_ref.date() - start_ref.date()).days // 7) + 4)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 375, "height": 900})
            page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            page.wait_for_timeout(2_000)

            _forumbooking_navigate_to_week(page, start_ref)

            for _ in range(max_weeks):
                page.wait_for_timeout(800)
                page_content = page.content()
                raw_html += page_content

                week_start, _week_end = _parse_visible_week_range(page_content)
                for event in _parse_forumbooking_schedule(page_content):
                    if start_ref <= event.datetime <= end_ref:
                        events.append(event)

                if week_start and week_start.date() > end_ref.date():
                    break
                if not _click_next_period(page):
                    break

            browser.close()
    except Exception:
        pass

    seen: set[tuple[str, str, str]] = set()
    unique: list[CalendarEvent] = []
    for event in events:
        key = (event.date, event.datetime.strftime("%H:%M"), event.name)
        if key not in seen:
            seen.add(key)
            unique.append(event)
    return unique, raw_html


def _forumbooking_navigate_to_week(page: Any, target: datetime) -> None:
    """Click prev/next until the visible Forumbooking week contains target."""
    for _ in range(80):
        week_start, week_end = _parse_visible_week_range(page.content())
        if not week_start or not week_end:
            return
        target_day = target.date()
        if week_start.date() <= target_day <= week_end.date():
            return
        clicked = _click_previous_period(page) if target_day < week_start.date() else _click_next_period(page)
        if not clicked:
            return


def _click_next_period(page: Any) -> bool:
    return _click_period_button(page, "#lbnNext")


def _click_previous_period(page: Any) -> bool:
    return _click_period_button(page, "#lbnPrev")


def _click_period_button(page: Any, selector: str) -> bool:
    try:
        button = page.locator(selector).first
        if button.count() == 0:
            return False
        button.click(force=True)
        page.wait_for_timeout(1_500)
        return True
    except Exception:
        return False


def _parse_visible_week_range(page_content: str) -> tuple[datetime | None, datetime | None]:
    soup = BeautifulSoup(page_content, "html.parser")
    heading = soup.find(id="cphHuvud_lblRubrik")
    text = heading.get_text(" ", strip=True) if heading else soup.get_text(" ", strip=True)[:200]
    match = _SCHEDULE_RANGE_RE.search(text)
    if not match:
        return None, None
    try:
        return (
            datetime.strptime(match.group(1), "%d.%m.%Y"),
            datetime.strptime(match.group(2), "%d.%m.%Y"),
        )
    except ValueError:
        return None, None


def _parse_forumbooking_schedule(page_content: str) -> list[CalendarEvent]:
    """Parse bookings from one Forumbooking schedule HTML page."""
    soup = BeautifulSoup(page_content, "html.parser")
    events: list[CalendarEvent] = []

    for div in soup.find_all("div", class_=lambda value: value and "bokning" in value.split()):
        element_id = str(div.get("id", ""))
        date_match = _DATE_IN_ID_RE.search(element_id)
        if not date_match:
            continue

        tooltip = " ".join(
            str(div.get(attr, "")) for attr in ("onmouseover", "onfocus") if div.get(attr)
        )
        tooltip = html.unescape(html.unescape(tooltip))
        visible_text = div.get_text(" ", strip=True)
        search_text = f"{tooltip} {visible_text}"

        time_match = _TIME_RANGE_RE.search(search_text)
        if not time_match:
            continue

        try:
            day = datetime.strptime(date_match.group(1), "%Y%m%d")
            start_hour, start_minute = (int(part) for part in time_match.group(1).split(":"))
            end_hour, end_minute = (int(part) for part in time_match.group(2).split(":"))
        except ValueError:
            continue

        start_decimal = start_hour + start_minute / 60.0
        end_decimal = end_hour + end_minute / 60.0
        duration = end_decimal - start_decimal
        if duration < 0:
            duration += 24

        customer_match = _CUSTOMER_RE.search(search_text)
        note_match = _NOTE_RE.search(search_text)
        customer = customer_match.group(1).strip() if customer_match else "Booking"
        note = note_match.group(1).strip() if note_match else ""
        event_name = f"{customer} - {note}" if note else customer

        event_dt = day.replace(hour=start_hour, minute=start_minute)
        events.append(
            CalendarEvent(
                date=day.strftime("%d.%m.%Y"),
                name=event_name,
                datetime=event_dt,
                duration_hours=duration,
            )
        )

    return events
