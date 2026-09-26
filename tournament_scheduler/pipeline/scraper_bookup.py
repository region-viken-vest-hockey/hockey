"""BookUp SPA scraper for Stage 2.

Provides BookUp FullCalendar timeGrid scraping for Tønsberg and Sandefjord
Penguins calendar sources.  All three helpers (:func:`_run_bookup_scraper`,
:func:`_bookup_navigate_to_date`, :func:`_parse_bookup_timegrid`) share the
Playwright ``frame`` context and FullCalendar DOM semantics.
"""

from __future__ import annotations

import json as _json
from datetime import date, datetime, timedelta
from typing import Any

from ..models import CalendarEvent
from .source_integrity import INTEGRITY_COMPLETE, INTEGRITY_PARTIAL, with_coverage


def _playwright_call_with_timeout(callable_obj: Any, *args: Any, timeout: int, **kwargs: Any) -> Any:
    """Call a Playwright method with a timeout, tolerating simple test doubles."""
    try:
        return callable_obj(*args, timeout=timeout, **kwargs)
    except TypeError:
        return callable_obj(*args, **kwargs)


def _run_bookup_scraper(
    url: str,
    name: str,
    start_date: datetime,
    end_date: datetime,
) -> tuple[list[CalendarEvent], str]:
    """Scrape a BookUp SPA calendar (Tønsberg, Sandefjord Penguins).

    BookUp shows an iframe with a FullCalendar timeGrid view after clicking
    "Se tilgjengelighet".  Events are rendered as ``.fc-time-grid-event``
    blocks inside a ``.fc-time-grid`` table, under a "Tilgjengelighetskalender"
    heading — a more reliable readiness signal than the toggle button itself,
    which can already be hidden by the time we check for it.

    The scraper:
      1. Navigates to the BookUp index page.
      2. Finds the ``app.html`` iframe.
      3. Clicks "Se tilgjengelighet" to reveal the calendar, if still visible.
      4. Waits for the "Tilgjengelighetskalender" section to render.
      5. Iterates week-by-week via the "next" button.
      6. Extracts ``.fc-time-grid-event`` elements with date / time / title.
    """
    from playwright.sync_api import sync_playwright

    events: list[CalendarEvent] = []
    raw_html: str = ""

    # Coverage evidence: a swallowed navigation/timeout must never let Stage 2
    # read a partial BookUp calendar as fully known. Start unproven and only
    # claim complete once the inspected week range actually covers the requested
    # start and end.
    inspected_dates: list[date] = []
    coverage_exceptions: list[str] = []

    start_date_ref = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date_ref = end_date.replace(hour=0, minute=0, second=0, microsecond=0)
    total_days = (end_date_ref - start_date_ref).days
    max_weeks = (total_days // 7) + 3  # pad a bit

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=30_000)
            page.wait_for_timeout(3_000)

            # Find the BookUp app iframe
            frame = page.frame(url=lambda u: "app.html" in u)
            if not frame:
                browser.close()
                return with_coverage(
                    [], status=INTEGRITY_PARTIAL, navigation_complete=False,
                    exceptions=["BookUp app.html iframe not found"],
                ), raw_html

            # Click "Se tilgjengelighet" if it's still there to reveal the
            # calendar (best-effort — it can already be visible/hidden).
            btn = frame.locator("text=Se tilgjengelighet")
            if btn.count() > 0:
                try:
                    btn.first.click(timeout=5_000)
                except Exception:
                    pass

            try:
                frame.locator("text=Tilgjengelighetskalender").first.wait_for(timeout=15_000)
            except Exception as exc:
                browser.close()
                return with_coverage(
                    [], status=INTEGRITY_PARTIAL, navigation_complete=False,
                    exceptions=[f"BookUp calendar did not render: {exc}"],
                ), raw_html

            # Navigate to start month if possible
            _bookup_navigate_to_date(frame, start_date_ref)
            inspected_dates.extend(_bookup_visible_dates(frame))

            # Scrape week by week
            for week_idx in range(max_weeks):
                frame.wait_for_timeout(1_500)
                page_content = frame.content()
                raw_html += page_content
                inspected_dates.extend(_bookup_visible_dates(frame))

                week_events = _parse_bookup_timegrid(frame, club_name=name, read_details=False)
                # Filter to date range
                for ev in week_events:
                    if start_date_ref <= ev.datetime <= end_date_ref + timedelta(days=1):
                        events.append(ev)

                # Click next week
                next_btn = frame.locator(".fc-next-button, button[aria-label*='next'], .fc-next")
                if next_btn.count() > 0:
                    try:
                        next_btn.first.click(timeout=5_000)
                        frame.wait_for_timeout(1_500)
                    except Exception as exc:
                        coverage_exceptions.append(f"BookUp week navigation stopped early: {exc}")
                        break
                else:
                    break

            browser.close()
    except Exception as exc:
        coverage_exceptions.append(f"BookUp scrape raised: {exc}")

    return with_coverage(
        _deduplicate_bookup_events(events),
        **_bookup_coverage_record(inspected_dates, start_date_ref, end_date_ref, coverage_exceptions),
    ), raw_html


def _bookup_visible_dates(frame: Any) -> list[date]:
    """Return the ISO dates currently rendered in the FullCalendar DOM."""

    try:
        raw = frame.evaluate(
            "JSON.stringify(Array.from(document.querySelectorAll('.fc-day-header[data-date]')).map(e => e.getAttribute('data-date')))"
        )
        values = _json.loads(raw) if isinstance(raw, str) else []
    except Exception:
        return []
    parsed: list[date] = []
    for value in values or []:
        try:
            parsed.append(datetime.strptime(str(value), "%Y-%m-%d").date())
        except ValueError:
            continue
    return parsed


def _bookup_coverage_record(
    inspected_dates: list[date],
    start_date: datetime,
    end_date: datetime,
    exceptions: list[str],
) -> dict[str, Any]:
    """Turn the inspected week/date set into a BookUp coverage record.

    Complete only when **every** requested date was actually displayed; the
    inspected set must cover the requested start, every interior day and the
    requested end. Taking only ``min``/``max`` would let a run that skipped an
    interior week still look complete, so events in the gap could be read as
    free ice. A calendar that silently stopped early, skipped a week, or whose
    initial navigation missed the start stays ``partial``.
    """

    if exceptions:
        return {
            "status": INTEGRITY_PARTIAL,
            "navigation_complete": False,
            "exceptions": list(exceptions),
        }
    if not inspected_dates:
        return {
            "status": INTEGRITY_PARTIAL,
            "navigation_complete": False,
            "exceptions": ["BookUp-kalenderen viste ingen ukeoverskrifter."],
        }
    missing_ranges = _missing_date_ranges(set(inspected_dates), start_date.date(), end_date.date())
    if missing_ranges:
        summary = ", ".join(
            f"{start.isoformat()}" if start == end else f"{start.isoformat()}..{end.isoformat()}"
            for start, end in missing_ranges[:5]
        )
        more = "" if len(missing_ranges) <= 5 else f" (+{len(missing_ranges) - 5} flere)"
        return {
            "status": INTEGRITY_PARTIAL,
            "navigation_complete": False,
            "exceptions": [f"BookUp-dekningen manglet datoer: {summary}{more}."],
        }
    return {"status": INTEGRITY_COMPLETE, "navigation_complete": True, "exceptions": []}


def _missing_date_ranges(
    inspected: set[date],
    start: date,
    end: date,
) -> list[tuple[date, date]]:
    """Return the contiguous ``[start, end]`` date ranges in *start*..*end* not inspected."""

    missing: list[tuple[date, date]] = []
    run_start: date | None = None
    current = start
    while current <= end:
        if current not in inspected:
            if run_start is None:
                run_start = current
        elif run_start is not None:
            missing.append((run_start, current - timedelta(days=1)))
            run_start = None
        current += timedelta(days=1)
    if run_start is not None:
        missing.append((run_start, end))
    return missing


def _deduplicate_bookup_events(events: list[CalendarEvent]) -> list[CalendarEvent]:
    """Preserve distinct same-day intervals while dropping exact duplicates."""
    seen: set[tuple[str, str, str, float]] = set()
    unique: list[CalendarEvent] = []
    for ev in events:
        key = (
            ev.date,
            ev.datetime.strftime("%H:%M"),
            ev.name,
            round(float(ev.duration_hours or 0.0), 4),
        )
        if key not in seen:
            seen.add(key)
            unique.append(ev)
    return unique


def _bookup_navigate_to_date(frame: Any, target: datetime) -> None:
    """Navigate the BookUp FullCalendar to the target date via prev/next.

    Reads the current visible week from the first ``fc-day-header`` element
    and clicks next/prev until the target date is in view.
    """
    for _ in range(52):  # safety limit
        raw = frame.evaluate(
            "JSON.stringify(Array.from(document.querySelectorAll('[data-date]')).map(e => e.getAttribute('data-date')))"
        )
        dates: list[str] = _json.loads(raw) if isinstance(raw, str) else []
        if not dates:
            break
        try:
            first_date = datetime.strptime(dates[0], "%Y-%m-%d")
            last_date = datetime.strptime(dates[-1], "%Y-%m-%d")
        except (ValueError, IndexError):
            break
        if first_date <= target <= last_date:
            break
        if target < first_date:
            btn = frame.locator(".fc-prev-button")
        else:
            btn = frame.locator(".fc-next-button")
        if btn.count() > 0:
            try:
                btn.first.click(timeout=5_000)
                frame.wait_for_timeout(1_000)
            except Exception:
                break
        else:
            break


def _is_own_club_youth_booking(title: str, club_name: str) -> bool:
    """Whether *title* is the host club's own regular youth-team ice time.

    BookUp lists a club's own recurring "Leietaker: <club> / Formål: U-lag"
    booking the same way it lists a firm external rental, but the host club
    controls that slot itself, so it isn't a hard commitment blocking
    tournament use there — treat it as available ice, not occupied.
    """
    lowered = title.lower()
    if "leietaker" not in lowered or "formål" not in lowered:
        return False
    leietaker_part, _, formal_part = lowered.partition("formål")
    club_tokens = [tok for tok in club_name.lower().split() if len(tok) > 2]
    if not club_tokens or not all(tok in leietaker_part for tok in club_tokens):
        return False
    return "u-lag" in formal_part


def _parse_bookup_timegrid(
    frame: Any,
    club_name: str = "",
    *,
    read_details: bool = True,
) -> list[CalendarEvent]:
    """Extract events from a BookUp FullCalendar timeGrid week view.

    Reads the real per-hour booking blocks (``.fc-time-grid-event``, which
    carry an exact ``data-full`` time range) rather than the generic
    ``.fc-bgevent`` shading. Each block is clicked to read its
    "Leietaker"/"Formål" contract detail from the ``#viewModal`` panel BookUp
    reveals when ``read_details`` is enabled — no login required, confirmed
    against Tønsberg's public calendar. Skips the host club's own youth-team
    bookings (see :func:`_is_own_club_youth_booking`) when *club_name* is given
    and details were read. Live Stage 2 disables per-event detail clicks to keep
    refresh bounded; returned ``Booket`` events are interval evidence only and
    cannot distinguish external rentals from the host club's own movable
    youth-team ice.
    """
    events: list[CalendarEvent] = []

    try:
        header_raw = frame.evaluate("""
            (() => JSON.stringify(Array.from(
                document.querySelectorAll('.fc-day-header[data-date]')
            ).map(th => th.getAttribute('data-date'))))()
        """)
        dates: list[str] = _json.loads(header_raw) if isinstance(header_raw, str) else []
        if not dates:
            return events

        # Scope events per day column the same way the (working) bgevent
        # extraction does, rather than inferring a column index per element —
        # that inference mismatched the header order and put every event on
        # the same date.
        col_locator = frame.locator(".fc-time-grid .fc-content-skeleton .fc-content-col")
        col_count = col_locator.count()

        for col_idx in range(min(col_count, len(dates))):
            date_str = dates[col_idx]
            try:
                dt_base = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue

            col_events = col_locator.nth(col_idx).locator(".fc-time-grid-event")
            for j in range(col_events.count()):
                el = col_events.nth(j)
                try:
                    time_meta_raw = el.evaluate("""
                        (el) => {
                            const timeEl = el.querySelector('.fc-time');
                            return JSON.stringify({
                                dataStart: timeEl ? timeEl.getAttribute('data-start') : '',
                                dataFull: timeEl ? timeEl.getAttribute('data-full') : '',
                            });
                        }
                    """)
                    time_meta = _json.loads(time_meta_raw) if isinstance(time_meta_raw, str) else {}
                except Exception:
                    time_meta = {}

                leietaker = ""
                formal = ""
                if read_details:
                    try:
                        el.click(force=True, timeout=1_000)
                        frame.wait_for_timeout(600)
                        title_loc = frame.locator("#viewModal .title")
                        if title_loc.count() > 0:
                            leietaker = _playwright_call_with_timeout(
                                title_loc.first.inner_text,
                                timeout=1_000,
                            ).strip()
                        sub_loc = frame.locator("#viewModal .sub-title")
                        if sub_loc.count() > 0:
                            formal = _playwright_call_with_timeout(
                                sub_loc.first.inner_text,
                                timeout=1_000,
                            ).strip()
                        close_btn = frame.locator(".view-contract-close")
                        if close_btn.count() > 0:
                            _playwright_call_with_timeout(
                                close_btn.first.click,
                                force=True,
                                timeout=1_000,
                            )
                            frame.wait_for_timeout(300)
                    except Exception:
                        pass

                if club_name and leietaker and formal:
                    combined_title = f"Leietaker:{leietaker} Formål:{formal}"
                    if _is_own_club_youth_booking(combined_title, club_name):
                        continue

                duration_hours = 1.0
                data_full = time_meta.get("dataFull", "") or ""
                if "-" in data_full:
                    try:
                        start_s, end_s = (p.strip() for p in data_full.split("-", 1))
                        sh, sm = (int(x) for x in start_s.split(":"))
                        eh, em = (int(x) for x in end_s.split(":"))
                        duration_hours = ((eh * 60 + em) - (sh * 60 + sm)) / 60.0
                    except ValueError:
                        duration_hours = 1.0

                data_start = time_meta.get("dataStart", "") or ""
                hour, minute = 0, 0
                if ":" in data_start:
                    try:
                        hh, mm = data_start.split(":")
                        hour, minute = int(hh), int(mm)
                    except ValueError:
                        hour, minute = 0, 0
                dt = dt_base.replace(hour=hour, minute=minute)

                title = f"{leietaker} ({formal})" if leietaker else "Booket"

                events.append(
                    CalendarEvent(
                        date=dt.strftime("%d.%m.%Y"),
                        name=title,
                        datetime=dt,
                        duration_hours=duration_hours if duration_hours > 0 else 1.0,
                    )
                )

    except Exception:
        pass

    return events
