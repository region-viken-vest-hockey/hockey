"""Live source-versus-scraper QA for high-risk arena calendars.

Marked ``live`` and excluded from hermetic CI. These tests fetch public sources
and compare independent feed/source evidence with the records produced by the
Stage 2 scrapers. They are intentionally bounded to the configured season window
and never mutate canonical season state.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from hashlib import sha256
from pathlib import Path
import re
from typing import Any
import warnings

import pytest
import requests
from icalendar import Calendar as ICalendar
import recurring_ical_events

from tournament_scheduler.pipeline.input_workbook import load_workbook_config
from tournament_scheduler.pipeline.scraper_ical import _run_ical_scraper
from tournament_scheduler.pipeline.scraper_outlook import _run_outlook_scraper
from tournament_scheduler.pipeline.source_integrity import evaluate_source_integrity
from tournament_scheduler.utils.calendar_cache import CalendarCache

pytestmark = pytest.mark.live

_USER_AGENT = "Mozilla/5.0 (compatible; rvv-miniputt-live-source-qa)"


class _FrozenResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.status_code = 200


def _configured_sources() -> dict[str, dict[str, Any]]:
    raw = load_workbook_config("input.xlsx")
    return {str(source["name"]): source for source in raw.get("sources", [])}


def _season_window() -> tuple[datetime, datetime]:
    raw = load_workbook_config("input.xlsx")
    start = _as_date(raw["start_date"])
    end = _as_date(raw["end_date"])
    return datetime.combine(start, time.min), datetime.combine(end, time.max.replace(microsecond=0))


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _raw_ical_display_identity(event: Any) -> tuple[str, str, str, str, bool]:
    start = event.get("DTSTART").dt
    end = event.get("DTEND").dt if event.get("DTEND") else None
    all_day = not hasattr(start, "hour")
    start_iso = start.isoformat()
    end_iso = "" if all_day else (end.isoformat() if end is not None else "")
    return (
        start_iso,
        end_iso,
        str(event.get("SUMMARY", "")),
        str(event.get("LOCATION", "")),
        all_day,
    )


def _raw_ical_occurrence_identity(event: Any) -> tuple[str, str, str, str, str, str, bool]:
    display = _raw_ical_display_identity(event)
    return (
        str(event.get("UID", "")),
        str(event.get("RECURRENCE-ID", "")),
        *display,
    )


def _scraped_ical_identity(event: Any) -> tuple[str, str, str, str, bool]:
    start = event.datetime
    all_day = bool(getattr(event, "all_day", False))
    if all_day:
        start_iso = start.date().isoformat()
        end_iso = ""
    else:
        end_iso = (start + timedelta(hours=event.duration_hours)).isoformat() if event.duration_hours else ""
        start_iso = start.isoformat()
    return (start_iso, end_iso, event.name, event.location or "", all_day)


def _calendar_event_identity(event: Any, *, arena: str) -> tuple[str, str, str, str, str]:
    start = event.datetime
    end = start + timedelta(hours=event.duration_hours) if event.duration_hours else start
    return (
        start.date().isoformat(),
        start.strftime("%H:%M"),
        end.strftime("%H:%M"),
        event.name.strip(),
        (event.location or arena).strip(),
    )


def _month_count(start: datetime, end: datetime) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


_MONTH_NAMES = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "januar": 1,
    "februar": 2,
    "mars": 3,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "oktober": 10,
    "desember": 12,
}


def _shift_month(month_start: date, offset: int) -> date:
    month_index = (month_start.month - 1) + offset
    return date(month_start.year + month_index // 12, (month_index % 12) + 1, 1)


def _month_in_label(label: str | None) -> date | None:
    """Parse a month/year a browser control label refers to, if present."""
    if not label:
        return None
    lowered = label.lower()
    for month_name, month in _MONTH_NAMES.items():
        if month_name in lowered:
            year_match = re.search(r"\b(20\d{2})\b", label)
            if year_match:
                return date(int(year_match.group(1)), month, 1)
    return None


def _iframe_actual_month(iframe: Any) -> date | None:
    """Read the calendar's actually displayed month from a stable control.

    The ``Go to next month <Month> <Year>`` control names the following month,
    so the displayed month is the one before it. Reading the control instead of
    the requested/expected month means a calendar that opens on the wrong month
    or fails to advance cannot silently satisfy the expected-month sequence.
    """
    next_btn = iframe.query_selector('button[aria-label*="next month"]')
    if next_btn is None:
        return None
    next_month = _month_in_label(next_btn.get_attribute("aria-label"))
    if next_month is None:
        return None
    return _shift_month(next_month, -1)


def _month_in_rendered_text(text: str, expected: date) -> bool:
    lowered = text.lower()
    for month_name, month in _MONTH_NAMES.items():
        if month == expected.month and month_name in lowered and str(expected.year) in text:
            return True
    return False


def _parse_visible_outlook_labels(labels: list[str], *, arena: str) -> list[tuple[str, str, str, str, str]]:
    identities: list[tuple[str, str, str, str, str]] = []
    for label in labels:
        if any(token in label.lower() for token in ("go to", "print", "month")):
            continue
        parts = [part.strip() for part in label.split(",")]
        if len(parts) < 4:
            continue
        time_match = re.search(
            r"(\d{1,2}):(\d{2})\s*(AM|PM)?\s+to\s+(\d{1,2}):(\d{2})\s*(AM|PM)?",
            parts[1],
            re.IGNORECASE,
        )
        start_str: str | None = None
        end_str: str | None = None
        if time_match:
            sh, sm, sp, eh, em, ep = time_match.groups()
            start_hour = int(sh)
            end_hour = int(eh)
            if sp and sp.upper() == "PM" and start_hour != 12:
                start_hour += 12
            if sp and sp.upper() == "AM" and start_hour == 12:
                start_hour = 0
            if ep and ep.upper() == "PM" and end_hour != 12:
                end_hour += 12
            if ep and ep.upper() == "AM" and end_hour == 12:
                end_hour = 0
            start_str = f"{start_hour:02d}:{int(sm):02d}"
            if end_hour < start_hour:
                end_hour += 24
            end_str = f"{end_hour % 24:02d}:{int(em):02d}"
        found_date: date | None = None
        for part in parts[2:]:
            lowered = part.lower()
            for month_name, month in _MONTH_NAMES.items():
                if month_name in lowered:
                    day_match = re.search(r"\b(\d{1,2})\b", part)
                    year_match = re.search(r"\b(20\d{2})\b", label)
                    if day_match and year_match:
                        found_date = date(int(year_match.group(1)), month, int(day_match.group(1)))
                    break
            if found_date:
                break
        if found_date:
            identities.append(
                (
                    found_date.isoformat(),
                    start_str or "00:00",
                    end_str or "00:00",
                    parts[0].strip(),
                    arena,
                )
            )
    return identities


def _parse_visible_date_param_text(text: str, *, month_start: datetime, arena: str) -> list[tuple[str, str, str, str, str]]:
    identities: list[tuple[str, str, str, str, str]] = []
    for match in re.finditer(r"(\d{1,2})[\.:](\d{2})\s*-\s*(\d{1,2})[\.:](\d{2})", text):
        sh, sm, eh, em = (int(group) for group in match.groups())
        identities.append(
            (
                month_start.date().isoformat(),
                f"{sh:02d}:{sm:02d}",
                f"{eh:02d}:{em:02d}",
                f"Booking {match.group()}",
                arena,
            )
        )
    return identities


def _browser_observed_outlook_identities(
    url: str,
    *,
    start: datetime,
    end: datetime,
    arena: str,
) -> tuple[list[tuple[str, str, str, str, str]], list[str], str]:
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

    from playwright.sync_api import sync_playwright

    identities: list[tuple[str, str, str, str, str]] = []
    observed_months: list[str] = []
    raw_evidence = ""
    months = _month_count(start, end)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=30000)
        page.wait_for_timeout(2000)
        iframe_element = page.query_selector("iframe")
        iframe = iframe_element.content_frame() if iframe_element else None
        start_month = start.replace(day=1)
        if iframe is not None:
            iframe.wait_for_timeout(3000)
            displayed = _iframe_actual_month(iframe)
            assert displayed is not None, (
                "browser-observed Outlook calendar did not expose a readable displayed month"
            )
            guard = 0
            while displayed != start_month.date() and guard < 36:
                guard += 1
                selector = (
                    'button[aria-label*="next month"]'
                    if displayed < start_month.date()
                    else 'button[aria-label*="previous month"]'
                )
                nav_btn = iframe.query_selector(selector)
                assert nav_btn is not None, (
                    f"browser-observed Outlook could not reach requested start month {start_month:%Y-%m} "
                    f"from {displayed:%Y-%m}"
                )
                before = iframe.content()
                nav_btn.click()
                iframe.wait_for_timeout(1500)
                assert iframe.content() != before, (
                    f"browser-observed Outlook month did not change while aligning from {displayed:%Y-%m}"
                )
                displayed = _iframe_actual_month(iframe)
                assert displayed is not None, (
                    "browser-observed Outlook displayed month became unreadable while aligning"
                )
            assert displayed == start_month.date(), (
                f"browser-observed Outlook aligned to {displayed:%Y-%m}, not requested {start_month:%Y-%m}"
            )
            for month_idx in range(months):
                expected = _shift_month(start_month.date(), month_idx)
                actual = _iframe_actual_month(iframe)
                assert actual is not None, (
                    f"browser-observed Outlook displayed month unreadable at {expected:%Y-%m}"
                )
                assert actual == expected, (
                    "browser-observed Outlook displayed month does not match the requested month: "
                    f"displayed={actual:%Y-%m} expected={expected:%Y-%m}"
                )
                labels = iframe.locator("[aria-label]").evaluate_all(
                    "els => els.map(e => e.getAttribute('aria-label') || '')"
                )
                content = iframe.content()
                raw_evidence += content
                observed_months.append(actual.strftime("%Y-%m"))
                identities.extend(_parse_visible_outlook_labels(labels, arena=arena))
                if month_idx < months - 1:
                    next_btn = iframe.query_selector('button[aria-label*="next month"]')
                    assert next_btn is not None, f"browser-observed Outlook navigation stopped before {expected:%Y-%m}"
                    before = content
                    next_btn.click()
                    iframe.wait_for_timeout(1500)
                    assert iframe.content() != before, f"browser-observed Outlook month did not advance after {expected:%Y-%m}"
        else:
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            for month_idx in range(months):
                month_start = _shift_month(start_month.date(), month_idx)
                q = dict(query)
                q["date"] = [month_start.strftime("%Y-%m-%d")]
                month_url = urlunparse(parsed._replace(query=urlencode(q, doseq=True)))
                page.goto(month_url, timeout=30000)
                page.wait_for_timeout(3000)
                text = page.locator("body").inner_text(timeout=10000)
                assert _month_in_rendered_text(text, month_start), (
                    f"date-parameter page for {month_start:%Y-%m} did not render that month; "
                    "requested-URL month is not independent evidence"
                )
                raw_evidence += text
                observed_months.append(month_start.strftime("%Y-%m"))
                identities.extend(_parse_visible_date_param_text(text, month_start=month_start, arena=arena))
        browser.close()
    return identities, observed_months, sha256(raw_evidence.encode("utf-8")).hexdigest()


def test_ringerike_teamup_feed_matches_ical_scraper_expansion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sources = _configured_sources()
    source = sources["Ringerike"]
    start, end = _season_window()

    response = requests.get(source["url"], timeout=30, headers={"User-Agent": _USER_AGENT})
    assert response.status_code == 200
    feed_bytes = response.content
    feed_fingerprint = sha256(feed_bytes).hexdigest()
    assert b"BEGIN:VCALENDAR" in feed_bytes
    assert feed_bytes.count(b"BEGIN:VEVENT") > 0

    calendar = ICalendar.from_ical(feed_bytes)
    raw_expanded = recurring_ical_events.of(calendar).between(start, end + timedelta(days=1))
    raw_occurrences = [_raw_ical_occurrence_identity(event) for event in raw_expanded]
    raw_identities = [_raw_ical_display_identity(event) for event in raw_expanded]
    assert len(raw_occurrences) >= 1, f"no Teamup occurrences in feed {feed_fingerprint}"
    assert len(raw_occurrences) == len(set(raw_occurrences)), (
        "expanded Teamup feed repeated an identical occurrence identity "
        f"(UID, recurrence and display tuple); feed fingerprint {feed_fingerprint}"
    )
    display_collisions = {ident: count for ident, count in Counter(raw_identities).items() if count > 1}
    if display_collisions:
        warnings.warn(
            "Teamup feed has distinct occurrences that share a display identity "
            f"(legitimate collisions, not automatically erroneous): {len(display_collisions)} "
            f"feed fingerprint {feed_fingerprint}",
            stacklevel=1,
        )

    def frozen_get(url: str, *args: Any, **kwargs: Any) -> _FrozenResponse:
        assert url == source["url"]
        return _FrozenResponse(feed_bytes)

    monkeypatch.setattr(requests, "get", frozen_get)
    scraped = _run_ical_scraper(
        source["url"],
        source["name"],
        start,
        end,
        source["type"],
        cache=CalendarCache(cache_dir=str(tmp_path / "ical-cache")),
    )
    scraped_identities = [_scraped_ical_identity(event) for event in scraped]

    assert Counter(scraped_identities) == Counter(raw_identities), (
        "Teamup scraper did not preserve occurrence display multiplicities "
        f"from immutable feed {feed_fingerprint}"
    )


def _in_season_window(identity: tuple[str, str, str, str, str], start: datetime, end: datetime) -> bool:
    return start.date().isoformat() <= identity[0] <= end.date().isoformat()


def test_kongsberg_outlook_browser_visible_events_match_scraper_output() -> None:
    pytest.importorskip("playwright.sync_api")
    sources = _configured_sources()
    source = sources["Kongsberg"]
    start, end = _season_window()
    arena = source["name"]

    observed, observed_months, evidence_fingerprint = _browser_observed_outlook_identities(
        source["url"], start=start, end=end, arena=arena
    )
    assert observed_months[0] == start.strftime("%Y-%m")
    assert observed_months[-1] == end.strftime("%Y-%m"), (
        "browser-observed Kongsberg navigation did not reach requested end month; "
        f"observed={observed_months}, evidence={evidence_fingerprint}"
    )
    observed = [identity for identity in observed if _in_season_window(identity, start, end)]
    assert observed, f"browser-observed Kongsberg source exposed no events; evidence={evidence_fingerprint}"

    events, raw_html = _run_outlook_scraper(source["url"], source["name"], start, end)
    scraped = [_calendar_event_identity(event, arena=arena) for event in events]
    scraped = [identity for identity in scraped if _in_season_window(identity, start, end)]

    assert raw_html, f"Kongsberg scraper returned no raw HTML; browser evidence={evidence_fingerprint}"

    # A scraper that silently drops a distinct booking (for example because two
    # same-day rentals share a title) must fail this lane even when its integrity
    # verdict is only `partial`. Compare the independently observed distinct
    # occurrence set against the returned records in the dangerous direction.
    missed = sorted(set(observed) - set(scraped))
    assert not missed, (
        "Kongsberg scraper missed browser-observed bookings "
        f"(date/start/end/title/arena): {missed}; evidence={evidence_fingerprint}"
    )

    # The browser exposes each booking through several DOM fragments, so it can
    # report the same occurrence more than once while the scraper collapses the
    # fragments. Surface that (and any extra scraper-only identity) as evidence
    # rather than treating legitimate DOM duplication as a scraper defect.
    browser_counts = Counter(observed)
    scraped_counts = Counter(scraped)
    collapsed = {ident: (scraped_counts[ident], browser_counts[ident]) for ident in set(scraped) if scraped_counts[ident] < browser_counts[ident]}
    extra = sorted(set(scraped) - set(observed))
    if collapsed or extra:
        warnings.warn(
            "Kongsberg live comparison evidence: "
            f"fragments_collapsed={len(collapsed)} scraper_only={extra[:5]} evidence={evidence_fingerprint}",
            stacklevel=1,
        )


def test_kongsberg_outlook_partial_coverage_is_not_negative_booking_evidence() -> None:
    sources = _configured_sources()
    source = sources["Kongsberg"]
    start, end = _season_window()
    source_result = {
        "name": source["name"],
        "type": source["type"],
        "events": [],
        "event_count": 0,
        "coverage": {
            "status": "partial",
            "navigation_complete": False,
            "requested_start": start.date().isoformat(),
            "requested_end": end.date().isoformat(),
            "observed_start": start.date().isoformat(),
            "observed_end": (end.date() - timedelta(days=31)).isoformat(),
            "exceptions": ["known partial fixture: missing requested final month"],
        },
    }

    integrity = evaluate_source_integrity(
        source_result,
        requested_start=start.date().isoformat(),
        requested_end=end.date().isoformat(),
    )

    assert integrity["status"] == "partial"
    assert integrity["coverage_proven"] is False
    assert integrity["trusted_for_negative_claim"] is False
    assert integrity["observed_end"] != integrity["requested_end"]
