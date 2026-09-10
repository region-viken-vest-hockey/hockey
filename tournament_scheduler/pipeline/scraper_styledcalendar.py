"""StyledCalendar/FullCalendar scraper for Stage 2 (Bærum ishall / Jutul).

Provides :func:`_run_styledcalendar_scraper` which opens the StyledCalendar
embed URL, switches to month view, iterates through each target month, and
extracts events from rendered ``.fc-daygrid-event`` elements.
"""

from __future__ import annotations

import json as _json
from datetime import datetime

from ..models import CalendarEvent


def _run_styledcalendar_scraper(
    name: str,
    start_date: datetime,
    end_date: datetime,
) -> tuple[list[CalendarEvent], str]:
    """Scrape StyledCalendar/FullCalendar widget (Bærum ishall/Jutul).

    Opens the StyledCalendar embed URL directly, switches to month view,
    iterates through each target month via the next-button, and extracts
    events from the rendered ``.fc-daygrid-event`` elements.
    """
    from playwright.sync_api import sync_playwright

    events: list[CalendarEvent] = []
    raw_html = ""
    embed_url = "https://embed.styledcalendar.com/#rYk5U1FtYNByMIMz2AoR"

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
            page.goto(embed_url, timeout=30_000)
            page.wait_for_timeout(8_000)  # Wait for JS render

            # Switch to month view
            month_btn = page.query_selector("button.fc-dayGridMonth-button")
            if month_btn:
                is_active = page.evaluate(
                    "document.querySelector('button.fc-dayGridMonth-button')?.classList.contains('fc-button-active')"
                )
                if not is_active:
                    month_btn.click()
                    page.wait_for_timeout(1_000)

            # Navigate to start month
            for _ in range(24):  # Max 2 years of clicking
                title = page.evaluate(
                    "document.querySelector('.fc-toolbar-title')?.innerText || ''"
                )
                if not title:
                    break
                try:
                    parts = title.lower().split()
                    month_names = [
                        "", "januar", "februar", "mars", "april", "mai", "juni",
                        "juli", "august", "september", "oktober", "november", "desember",
                    ]
                    cur_month = month_names.index(parts[0]) if parts[0] in month_names else 0
                    cur_year = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                except (ValueError, IndexError):
                    break

                if cur_year > start_month.year or (cur_year == start_month.year and cur_month >= start_month.month):
                    break

                next_btn = page.query_selector(".fc-next-button")
                if next_btn:
                    next_btn.click()
                    page.wait_for_timeout(500)
                else:
                    break

            # Extract each month
            for month_idx in range(months_to_scrape):
                page.wait_for_timeout(1_000)

                # Extract events from current month view
                raw = page.evaluate("""
                    JSON.stringify(Array.from(document.querySelectorAll('.fc-daygrid-event')).map(e => {
                        const day = e.closest('[data-date]');
                        const date = day ? day.getAttribute('data-date') || '' : '';
                        const title = (e.querySelector('.fc-event-title') || e).innerText.trim();
                        return { date, title };
                    }))
                """)
                raw_events = _json.loads(raw) if isinstance(raw, str) else []
                if not isinstance(raw_events, list):
                    raw_events = []

                for item in raw_events:
                    date_str = item.get("date", "")
                    title = item.get("title", "")
                    if not date_str or not title:
                        continue
                    try:
                        dt = datetime.strptime(date_str, "%Y-%m-%d")
                    except ValueError:
                        continue
                    events.append(CalendarEvent(
                        date=dt.strftime("%d.%m.%Y"),
                        name=title,
                        datetime=dt,
                        duration_hours=1.0,
                    ))

                # Navigate to next month
                if month_idx < months_to_scrape - 1:
                    next_btn = page.query_selector(".fc-next-button")
                    if next_btn:
                        next_btn.click()
                        page.wait_for_timeout(500)
                    else:
                        break

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
