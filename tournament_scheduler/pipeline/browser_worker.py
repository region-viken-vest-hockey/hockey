"""
Browser worker — long-lived Playwright process commanded via stdin/stdout JSON.

The extension launches this as a child process and sends one JSON command per
line on stdin. Each command is executed against a persistent Chromium page (headless by default;
headed when `RVV_BOOKUP_MANUAL_LOGIN=1`), and the result is written as one JSON line on stdout.

Commands
-------
  {"cmd":"goto",   "url":"...", "wait_ms":3000}
      Navigate to URL, wait for network idle + wait_ms, return page snapshot.

  {"cmd":"click",  "selector":"...", "wait_ms":1500}
      Click the first element matching the CSS/text selector, wait, return snapshot.

  {"cmd":"extract","strategy":"outlook"|"date_param", "month_start":"2026-09-01"}
      Parse events from the current page using the given strategy.

  {"cmd":"type",   "selector":"#username", "text":"user@example.com", "wait_ms":1000}
      Fill an input field with the given text (form field), wait, return snapshot.

  {"cmd":"screenshot"}
      Return a base64-encoded PNG of the current viewport.

  {"cmd":"snapshot"}
      Return the current sanitized page snapshot without taking an action.

  {"cmd":"exit"}
      Clean shutdown.

Response format
--------------
  {"ok":true,  "html":"...", "url":"...", "title":"...", "events":[...],
   "interactive":[{"tag":"a","text":"Ishall","selector":"a:text('Ishall')"},...],
   "screenshot":"base64...", "error":""}

  {"ok":false, "error":"..."}

The worker traps SIGTERM/SIGINT and exits cleanly.
"""

from __future__ import annotations

import base64
import json
import os
import re
import signal
import sys
import time
from datetime import datetime
from typing import Any

# Navigation timeout constants
GOTO_TIMEOUT_MS = 30_000   # initial page.goto — networkidle wait
GOTO_RETRY_TIMEOUT_MS = 60_000  # retry goto — longer to recover from slow loads


def _now_iso() -> str:
    return datetime.now().isoformat()


# ---------------------------------------------------------------------------
# Credential-leak mitigation (defense-in-depth, layer 1: Python/DOM)
# ---------------------------------------------------------------------------
#
# When BookUp credentials (BOOKUP_EMAIL/BOOKUP_PASSWORD) are entered
# on-demand during the run flow, page snapshots returned by this worker
# (html, iframe_html, interactive element labels) are forwarded to the TS
# extension layer and ultimately into LLM prompts (see
# `.pi/lib/scraper-agent.ts::userMessage`).
# To avoid the entered credential values ever reaching the LLM:
#   1. (primary, this module) `_sanitize_html()` blanks `value="..."`
#      attributes on password/email/username input fields before HTML is
#      returned from `_snapshot()`.
#   2. (this module, secondary) `_redact_credentials()` scrubs literal
#      BOOKUP_EMAIL/BOOKUP_PASSWORD substrings from interactive-element
#      label/placeholder text in `_interactive_elements()`.
#   3. (TS layer, fallback) `redactCredentials()` in scraper-agent.ts scrubs
#      the same substrings from `snapshot.html`/`iframe_html`/interactive
#      text again before building the LLM user message, in case a path here
#      is missed.
#
# Longer-term alternative (out of scope for this fix): out-of-band browser
# auth — establish a persistent authenticated browser profile/cookie session
# once, outside the LLM loop (e.g. via `initial_navigation` running headfully
# once to save storage state), so no login UI/credential state is ever
# captured in a snapshot or fed to the LLM at all.

# Matches `value="..."` / `value='...'` attributes on <input> elements whose
# tag also declares a credential-related type or known field id/name, so we
# never leak entered passwords/emails into HTML snapshots forwarded to the
# extension/LLM layers.
_CREDENTIAL_INPUT_RE = re.compile(
    r"""(<input\b[^>]*?\b(?:type=["']?(?:password|email)["']?|
          (?:id|name)=["']?(?:email|password|username|user|login)["']?)
        [^>]*?\bvalue=["'])[^"']*(["'])""",
    re.IGNORECASE | re.VERBOSE,
)


def _sanitize_html(html: str) -> str:
    """Strip credential `value="..."` attributes from input fields in HTML.

    Blanks the `value` attribute of `<input>` elements that are password
    fields, email fields, or commonly-named username/login fields, so that
    raw HTML/iframe snapshots returned from `_snapshot()` never contain
    credential values entered by the user.
    """
    if not html:
        return html
    return _CREDENTIAL_INPUT_RE.sub(r"\1\2", html)


def _redact_credentials(text: str) -> str:
    """Replace any literal BookUp credential values found in `text`.

    Defense-in-depth: even if a credential value somehow ends up in an
    interactive-element label/placeholder echo (e.g. a form pre-filled by
    the page itself), scrub any substring matching the resolved
    `BOOKUP_EMAIL`/`BOOKUP_PASSWORD` env vars before it leaves this process.
    """
    if not text:
        return text
    for env_var in ("BOOKUP_EMAIL", "BOOKUP_PASSWORD"):
        value = os.environ.get(env_var, "")
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


class BrowserWorker:
    """Manages a Chromium page and executes commands against it."""

    def __init__(self) -> None:
        self._page = None
        self._browser = None
        self._playwright = None

    def start(self) -> None:
        """Launch the browser (lazy — first command triggers it)."""
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright()
        p = self._playwright.__enter__()
        headed = os.environ.get("RVV_BOOKUP_MANUAL_LOGIN", "").strip().lower() in {
            "1", "true", "yes", "y", "on"
        }
        self._browser = p.chromium.launch(headless=not headed)
        self._page = self._browser.new_page()
        self._page.set_default_timeout(15_000)

    def stop(self) -> None:
        """Shut down browser and Playwright."""
        try:
            if self._page is not None:
                self._page.close()
        except Exception:
            pass
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                self._playwright.__exit__(None, None, None)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Snapshot helpers
    # ------------------------------------------------------------------

    def _snapshot(self) -> dict[str, Any]:
        """Capture current page state: HTML, URL, title, interactive elements."""
        if self._page is None:
            return {"html": "", "url": "", "title": "", "interactive": []}
        try:
            html = self._page.content()
        except Exception:
            html = ""
        try:
            url = self._page.url
        except Exception:
            url = ""
        try:
            title = self._page.title()
        except Exception:
            title = ""
        # Also capture iframe HTML when present
        iframe_html = ""
        try:
            iframe_el = self._page.query_selector("iframe")
            if iframe_el:
                frame = iframe_el.content_frame()
                if frame:
                    iframe_html = frame.content()
        except Exception:
            pass
        return {
            "html": _sanitize_html(html),
            "iframe_html": _sanitize_html(iframe_html),
            "url": url,
            "title": title,
            "interactive": self._interactive_elements(),
        }

    def _interactive_elements(self) -> list[dict[str, str]]:
        """Return a list of clickable/interactive elements on the page."""
        if self._page is None:
            return []
        elements: list[dict[str, str]] = []

        try:
            buttons = self._page.locator(
                "button, a[href], input[type='submit'], input[type='button'], "
                "[role='button'], [role='link'], "
                "input:not([type='hidden']):not([type='submit']):not([type='button']), "
                "textarea, select"
            )
            count = buttons.count()
            for i in range(min(count, 40)):
                try:
                    tag = buttons.nth(i).evaluate("el => el.tagName.toLowerCase()")
                    text = buttons.nth(i).inner_text().strip()[:80]
                    href = buttons.nth(i).get_attribute("href") or ""
                    placeholder = buttons.nth(i).get_attribute("placeholder") or ""
                    input_type = buttons.nth(i).get_attribute("type") or ""
                    if tag == "input":
                        label_text = f"{input_type} input"
                        if placeholder:
                            label_text += f" (placeholder='{placeholder}')"
                    elif tag == "textarea":
                        label_text = f"textarea{ ' (' + placeholder + ')' if placeholder else '' }"
                    elif tag == "select":
                        label_text = f"select '{text}'"
                    else:
                        label_text = f"{text} ({href})" if href and tag == "a" else text
                    sel = self._build_selector(buttons.nth(i))
                    elements.append({
                        "tag": tag,
                        "text": _redact_credentials(label_text)[:80],
                        "selector": sel,
                    })
                except Exception:
                    continue
        except Exception:
            pass
        return elements

    def _build_selector(self, locator: Any) -> str:
        """Build a Playwright-compatible CSS selector for an element."""
        try:
            test_id = locator.get_attribute("data-testid")
            if test_id:
                return f"[data-testid='{test_id}']"
        except Exception:
            pass
        try:
            elem_id = locator.get_attribute("id")
            if elem_id and len(elem_id) < 20:
                return f"#{elem_id}"
        except Exception:
            pass
        try:
            aria = locator.get_attribute("aria-label")
            if aria:
                escaped = aria.replace(chr(39), chr(92) + chr(39))
                return "[aria-label='" + escaped + "']"
        except Exception:
            pass
        try:
            tag = locator.evaluate("el => el.tagName.toLowerCase()")
            text = locator.inner_text().strip()[:60]
            if text and tag in ("button", "a"):
                safe_text = text.replace(chr(39), chr(92) + chr(39))
                return f"{tag}:text('" + safe_text + "')'"
        except Exception:
            pass
        return "*"

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def cmd_snapshot(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return the current page snapshot without navigating or clicking."""
        self.start()
        snap = self._snapshot()
        return {"ok": True, **snap}

    def cmd_goto(self, params: dict[str, Any]) -> dict[str, Any]:
        self.start()
        url = params.get("url", "")
        wait_ms = int(params.get("wait_ms", 3000))
        try:
            self._page.goto(url, timeout=GOTO_TIMEOUT_MS, wait_until="networkidle")
        except Exception:
            try:
                self._page.goto(url, timeout=GOTO_RETRY_TIMEOUT_MS, wait_until="domcontentloaded")
            except Exception as exc:
                return {"ok": False, "error": f"goto feilet: {exc}"}
        time.sleep(wait_ms / 1000.0)
        snap = self._snapshot()
        return {"ok": True, **snap}

    def _target(self, params: dict[str, Any]):
        """Return the page or iframe context based on params."""
        if params.get("iframe"):
            iframe_el = self._page.query_selector("iframe")
            if iframe_el:
                frame = iframe_el.content_frame()
                if frame:
                    return frame
        return self._page

    def cmd_click(self, params: dict[str, Any]) -> dict[str, Any]:
        self.start()
        selector = params.get("selector", "")
        wait_ms = int(params.get("wait_ms", 1500))
        target = self._target(params)
        if not selector:
            return {"ok": False, "error": "click krever en selector"}
        try:
            element = target.locator(selector)
            if element.count() == 0:
                return {"ok": False, "error": f"Fant ingen elementer med selector '{selector}'"}
            element.first.click()
        except Exception as exc:
            return {"ok": False, "error": f"click feilet: {exc}"}
        time.sleep(wait_ms / 1000.0)
        snap = self._snapshot()
        return {"ok": True, **snap}

    def cmd_extract(self, params: dict[str, Any]) -> dict[str, Any]:
        """Extract calendar events from the current page."""
        self.start()
        strategy = params.get("strategy", "auto")
        month_start_str = params.get("month_start", "")
        use_iframe = params.get("iframe", False)
        norwegian_months = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
            "januar": 1, "februar": 2, "mars": 3, "april": 4,
            "mai": 5, "juni": 6, "juli": 7, "august": 8,
            "september": 9, "oktober": 10, "november": 11, "desember": 12,
        }

        try:
            if use_iframe:
                iframe_el = self._page.query_selector("iframe")
                if iframe_el:
                    frame = iframe_el.content_frame()
                    if frame:
                        html = frame.content()
                    else:
                        html = self._page.content()
                else:
                    html = self._page.content()
            else:
                html = self._page.content()
        except Exception as exc:
            return {"ok": False, "error": f"extract feilet: {exc}"}

        events: list[dict[str, Any]] = []

        if strategy == "outlook" or strategy == "auto":
            events = self._parse_outlook_aria(html, norwegian_months)

        if not events and (strategy == "date_param" or strategy == "auto"):
            month_start = None
            if month_start_str:
                try:
                    month_start = datetime.strptime(month_start_str, "%Y-%m-%d")
                except ValueError:
                    pass
            if month_start is None:
                month_start = datetime.now().replace(day=1)
            events = self._parse_date_param(html, month_start)

        if not events and (strategy == "styledcalendar" or strategy == "auto"):
            events = self._parse_styledcalendar()

        return {"ok": True, "events": events}

    def cmd_eval(self, params: dict[str, Any]) -> dict[str, Any]:
        """Execute JavaScript in the page context and return the result."""
        self.start()
        js = params.get("js", "")
        if not js:
            return {"ok": False, "error": "eval krever en 'js'-parameter"}
        try:
            result = self._page.evaluate(js)
            return {"ok": True, "result": result}
        except Exception as exc:
            return {"ok": False, "error": f"eval feilet: {exc}"}

    def cmd_type(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fill an input field with text, then wait."""
        self.start()
        selector = params.get("selector", "")
        text = params.get("text", "")
        wait_ms = int(params.get("wait_ms", 1000))
        if not selector:
            return {"ok": False, "error": "type krever en selector"}
        try:
            element = self._page.locator(selector)
            if element.count() == 0:
                return {"ok": False, "error": f"Fant ingen elementer med selector '{selector}'"}
            element.first.fill(text)
        except Exception as exc:
            return {"ok": False, "error": f"type feilet: {exc}"}
        time.sleep(wait_ms / 1000.0)
        snap = self._snapshot()
        return {"ok": True, **snap}

    def cmd_screenshot(self, params: dict[str, Any]) -> dict[str, Any]:
        self.start()
        try:
            raw = self._page.screenshot(type="png")
            b64 = base64.b64encode(raw).decode("ascii")
            return {"ok": True, "screenshot": b64}
        except Exception as exc:
            return {"ok": False, "error": f"screenshot feilet: {exc}"}

    # ------------------------------------------------------------------
    # Event parsers
    # ------------------------------------------------------------------

    def _parse_outlook_aria(
        self, html: str, norwegian_months: dict[str, int]
    ) -> list[dict[str, Any]]:
        """Parse Outlook Web Calendar events from aria-labels in iframe HTML."""
        events: list[dict[str, Any]] = []
        aria_matches = re.findall(r'aria-label="([^"]+)"', html)

        for aria_label in aria_matches:
            if "Go to" in aria_label or "Print" in aria_label or "Month" in aria_label:
                continue

            parts = [p.strip() for p in aria_label.split(",")]
            if len(parts) < 4:
                continue

            event_name = parts[0]
            time_part = parts[1] if len(parts) > 1 else ""

            start_time: float | None = None
            duration_hours = 0.0

            t = re.search(
                r"(\d{1,2}):(\d{2})\s*(AM|PM)\s+to\s+(\d{1,2}):(\d{2})\s*(AM|PM)",
                time_part, re.IGNORECASE,
            )
            if t:
                sh, sm, sp, eh, em, ep = t.groups()
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
                t = re.search(r"(\d{1,2}):(\d{2})\s+to\s+(\d{1,2}):(\d{2})", time_part)
                if t:
                    sh, sm, eh, em = map(int, t.groups())
                    start_time = sh + sm / 60.0
                    end_time = eh + em / 60.0
                    if end_time < start_time:
                        end_time += 24
                    duration_hours = end_time - start_time

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
                            except ValueError:
                                pass
                            break
                if found_date:
                    break

            if found_date and event_name:
                event_dt = found_date
                if start_time is not None:
                    h = int(start_time)
                    m = int((start_time - h) * 60)
                    event_dt = found_date.replace(hour=h, minute=m)
                events.append({
                    "date": found_date.strftime("%d.%m.%Y"),
                    "name": event_name,
                    "datetime": event_dt.isoformat(),
                    "duration_hours": duration_hours,
                })

        # Deduplicate
        seen: set[tuple[str, str]] = set()
        unique: list[dict[str, Any]] = []
        for ev in events:
            key = (ev["date"], ev["name"])
            if key not in seen:
                seen.add(key)
                unique.append(ev)
        return unique

    def _parse_date_param(
        self, html: str, month_start: datetime
    ) -> list[dict[str, Any]]:
        """Parse events from date-parameter pages (brp.exigo.no style)."""
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        events: list[dict[str, Any]] = []
        time_pattern = r"(\d{1,2}[\.:]\d{2})\s*[-–]\s*(\d{1,2}[\.:]\d{2})"
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
            day = month_start.replace(day=1)
            events.append({
                "date": day.strftime("%d.%m.%Y"),
                "name": f"Booking {m.group()}",
                "datetime": day.replace(hour=sh, minute=sm).isoformat(),
                "duration_hours": duration,
            })
        return events


# ---------------------------------------------------------------------------
# Main loop
    def _parse_styledcalendar(self) -> list[dict[str, Any]]:
        """Extract FullCalendar events from the current page via JS eval.

        First switches to month view (fc-dayGridMonth) if needed, then
        iterates all .fc-daygrid-event elements and extracts date+title.
        """
        if self._page is None:
            return []
        try:
            import time as _time
            # Switch to month view if not already active
            month_btn = self._page.query_selector("button.fc-dayGridMonth-button")
            if month_btn:
                is_active = self._page.evaluate(
                    "document.querySelector('button.fc-dayGridMonth-button')?.classList.contains('fc-button-active')"
                )
                if not is_active:
                    month_btn.click()
                    _time.sleep(1.0)

            # Extract events from the month grid
            raw = self._page.evaluate("""
                JSON.stringify(Array.from(document.querySelectorAll('.fc-daygrid-event')).map(e => {
                    const day = e.closest('[data-date]');
                    const date = day ? day.getAttribute('data-date') || '' : '';
                    const title = (e.querySelector('.fc-event-title') || e).innerText.trim();
                    return { date, title };
                }))
            """)
            raw_events = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(raw_events, list):
                return []
            events: list[dict[str, Any]] = []
            seen: set[tuple[str, str]] = set()
            for item in raw_events:
                date_str = item.get("date", "")
                title = item.get("title", "")
                if not date_str or not title:
                    continue
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    continue
                key = (date_str, title)
                if key in seen:
                    continue
                seen.add(key)
                events.append({
                    "date": dt.strftime("%d.%m.%Y"),
                    "name": title,
                    "datetime": dt.isoformat(),
                    "duration_hours": 1.0,
                })
            return events
        except Exception:
            return []


# ---------------------------------------------------------------------------

def main() -> None:
    worker = BrowserWorker()

    def _handle_signal(signum: int, _frame: object) -> None:
        resp = {"ok": False, "error": f"worker avbrutt (signal {signum})"}
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        worker.stop()
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            resp = {"ok": False, "error": f"ugyldig JSON: {exc}"}
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        cmd = msg.get("cmd", "")
        params = {k: v for k, v in msg.items() if k != "cmd"}

        try:
            if cmd == "goto":
                resp = worker.cmd_goto(params)
            elif cmd == "click":
                resp = worker.cmd_click(params)
            elif cmd == "extract":
                resp = worker.cmd_extract(params)
            elif cmd == "eval":
                resp = worker.cmd_eval(params)
            elif cmd == "screenshot":
                resp = worker.cmd_screenshot(params)
            elif cmd == "snapshot":
                resp = worker.cmd_snapshot(params)
            elif cmd == "type":
                resp = worker.cmd_type(params)
            elif cmd == "exit":
                resp = {"ok": True, "message": "avslutter"}
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
                worker.stop()
                return
            else:
                resp = {"ok": False, "error": f"ukjent kommando: '{cmd}'"}
        except Exception as exc:
            resp = {"ok": False, "error": f"feil ved {cmd}: {exc}"}

        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    worker.stop()


if __name__ == "__main__":
    main()
