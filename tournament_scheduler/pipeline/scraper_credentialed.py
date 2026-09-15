"""Optional credentialed browser adapter for future non-public sources.

Current RVV BookUp sources are public and are handled by ``scraper_bookup``.
This module deliberately contains no BookUp login, MFA, headed-browser, or
session-handoff behavior. It exists only for a future source strategy that
explicitly declares environment-variable credentials.
"""

from __future__ import annotations

import os
from datetime import datetime
from string import Template
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from ..data_sources.calendar_scraper import OutlookCalendarScraper
from ..models import CalendarEvent
from ..utils.calendar_cache import CalendarCache
from .scraper_outlook import _parse_date_param_calendar, _parse_outlook_calendar
from .scraper_strategies import get_strategy, requires_credentials


def _try_credentialed_scrape(
    name: str,
    url: str,
    start_date: datetime,
    end_date: datetime,
    cache: CalendarCache | None = None,
) -> tuple[list[CalendarEvent], str]:
    """Attempt a source strategy that explicitly declares credentials.

    BookUp never uses this function. The strategy must declare both
    ``credential_env_vars`` and non-interactive ``initial_navigation`` steps.
    """
    strategy = get_strategy(name)
    if not strategy or not requires_credentials(strategy):
        return [], ""

    missing = [var for var in strategy.credential_env_vars if not os.environ.get(var)]
    if missing:
        return [], (
            f"Kilden '{name}' krever innlogging men miljovariablene "
            f"{', '.join(missing)} er ikke satt."
        )
    if not strategy.initial_navigation:
        return [], f"Kilden '{name}' har credentials men ingen initial_navigation."
    if any(step.get("cmd") == "manual_login" for step in strategy.initial_navigation):
        return [], (
            f"Kilden '{name}' bruker en utfaset manuell innloggingsstrategi. "
            "Integrer kilden med en ikke-interaktiv eller offentlig datakilde i stedet."
        )

    creds = {var: os.environ[var] for var in strategy.credential_env_vars}
    try:
        return _run_credentialed_browser(name, url, start_date, end_date, strategy, creds, cache)
    except Exception as exc:
        return [], f"Credentialed scrape feilet for '{name}': {exc}"


def _run_credentialed_browser(
    name: str,
    url: str,
    start_date: datetime,
    end_date: datetime,
    strategy: Any,
    creds: dict[str, str],
    cache: CalendarCache | None = None,
) -> tuple[list[CalendarEvent], str]:
    """Run a non-interactive, headless credentialed browser strategy."""
    from playwright.sync_api import sync_playwright

    events: list[CalendarEvent] = []
    norwegian_months = OutlookCalendarScraper(cache).norwegian_months
    start_month = start_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_month = end_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    months_to_scrape = (
        (end_month.year - start_month.year) * 12
        + (end_month.month - start_month.month)
        + 1
    )

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=30_000)
            page.wait_for_timeout(3_000)

            for step in strategy.initial_navigation:
                cmd = step.get("cmd", "")
                selector = Template(step.get("selector", "")).safe_substitute(creds)
                text = Template(step.get("text", "")).safe_substitute(creds)
                wait_ms = int(step.get("wait_ms", 1_500))

                if cmd == "click" and selector:
                    el = page.locator(selector)
                    if el.count() > 0:
                        el.first.click()
                elif cmd == "type" and selector:
                    el = page.locator(selector)
                    if el.count() > 0:
                        el.first.fill(text)
                elif cmd == "goto" and step.get("url"):
                    page.goto(Template(str(step["url"])).safe_substitute(creds), timeout=30_000)
                elif cmd == "wait":
                    pass
                else:
                    return [], f"Ustøttet initial_navigation-kommando '{cmd}' for '{name}'."
                page.wait_for_timeout(wait_ms)

            _credentialed_scrape_months(
                page,
                events,
                months_to_scrape,
                norwegian_months,
                start_month=start_month,
            )
            browser.close()
    except Exception as exc:
        return [], f"Credentialed Playwright-feil for '{name}': {exc}"

    seen: set[tuple[str, str]] = set()
    unique: list[CalendarEvent] = []
    for event in events:
        key = (event.date, event.name)
        if key not in seen:
            seen.add(key)
            unique.append(event)
    return unique, ""


def _credentialed_scrape_months(
    page: Any,
    events: list[CalendarEvent],
    months_to_scrape: int,
    norwegian_months: dict[str, int],
    *,
    start_month: datetime,
) -> None:
    """Extract Outlook/date-parameter calendar data from an authenticated page."""
    iframe_element = page.query_selector("iframe")
    has_iframe = iframe_element is not None and iframe_element.content_frame() is not None

    if has_iframe:
        iframe = iframe_element.content_frame()
        iframe.wait_for_timeout(3_000)
        for month_idx in range(months_to_scrape):
            iframe.wait_for_timeout(1_000)
            events.extend(_parse_outlook_calendar(iframe.content(), norwegian_months))
            if month_idx < months_to_scrape - 1:
                try:
                    next_btn = iframe.query_selector('button[aria-label*="next month"]')
                    if next_btn:
                        next_btn.click()
                        iframe.wait_for_timeout(1_500)
                except Exception:
                    pass
        return

    parsed = urlparse(page.url)
    query = parse_qs(parsed.query)
    current_month = start_month
    for _ in range(months_to_scrape):
        q = dict(query)
        q["date"] = [current_month.strftime("%Y-%m-%d")]
        month_url = urlunparse(parsed._replace(query=urlencode(q, doseq=True)))
        try:
            page.goto(month_url, timeout=30_000)
            page.wait_for_timeout(3_000)
            events.extend(
                _parse_date_param_calendar(page.content(), current_month, norwegian_months)
            )
        except Exception:
            pass
        if current_month.month == 12:
            current_month = current_month.replace(year=current_month.year + 1, month=1)
        else:
            current_month = current_month.replace(month=current_month.month + 1)
