"""Portable BookUp session handoff bridge (issue #304, ADR 0001).

Canonical Stage 2 runs launched inside a Lima VM cannot usefully open a
visible BookUp browser for Vipps/SMS MFA. This module lets Stage 2 hand a
BookUp scrape request to a small trusted HTTP server running on the macOS
host instead, where the operator can complete login/MFA in a real browser.

Only normalized :class:`CalendarEvent` data and non-secret status metadata
cross the bridge. Credentials are read from the host process's own
environment (as they already are for the local credentialed scrape path);
they are never transmitted, logged, or echoed back to the caller.

Client side (used from inside Lima, or any caller with the bridge
configured): :func:`bridge_configured`, :func:`request_bookup_scrape`.

Server side (run on the macOS host): :func:`run_server`, or invoke this
module directly / via ``scripts/rvv-bookup-host``.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import requests

from ..models import CalendarEvent

BRIDGE_URL_ENV = "RVV_BOOKUP_HOST_BRIDGE"
BRIDGE_TOKEN_ENV = "RVV_BOOKUP_HOST_BRIDGE_TOKEN"
BRIDGE_TIMEOUT_ENV = "RVV_BOOKUP_HOST_BRIDGE_TIMEOUT"
DEFAULT_BRIDGE_TIMEOUT_SECONDS = 300
DEFAULT_BRIDGE_PORT = 8765
_MAX_REQUEST_BYTES = 4096


class BookUpHostBridgeError(RuntimeError):
    """Bridge is configured but could not serve a scrape.

    Callers must surface this as an explicit failure rather than silently
    falling back to treating the source as trusted.
    """


def bridge_url() -> str:
    return os.environ.get(BRIDGE_URL_ENV, "").strip()


def bridge_configured() -> bool:
    return bool(bridge_url())


def _bridge_timeout_seconds() -> int:
    raw = os.environ.get(BRIDGE_TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_BRIDGE_TIMEOUT_SECONDS
    try:
        return max(15, int(raw))
    except ValueError:
        return DEFAULT_BRIDGE_TIMEOUT_SECONDS


def _events_to_payload(events: list[CalendarEvent]) -> list[dict[str, Any]]:
    return [
        {
            "date": ev.date,
            "name": ev.name,
            "datetime": ev.datetime.isoformat(),
            "duration_hours": ev.duration_hours,
            "location": ev.location,
        }
        for ev in events
    ]


def _payload_to_events(payload: list[dict[str, Any]]) -> list[CalendarEvent]:
    events: list[CalendarEvent] = []
    for item in payload:
        events.append(
            CalendarEvent(
                date=item["date"],
                name=item["name"],
                datetime=datetime.fromisoformat(item["datetime"]),
                duration_hours=float(item.get("duration_hours", 0.0)),
                location=item.get("location", ""),
            )
        )
    return events


# ---------------------------------------------------------------------------
# Client side -- invoked from Stage 2 (typically running inside Lima).
# ---------------------------------------------------------------------------


def request_bookup_scrape(
    name: str,
    url: str,
    start_date: datetime,
    end_date: datetime,
) -> tuple[list[CalendarEvent], str]:
    """Ask the configured host bridge to scrape *name* and return events.

    Returns ``(events, error)`` on a request the bridge was able to answer
    (``error`` non-empty means the bridge itself reported a scrape failure,
    e.g. MFA timeout). Raises :class:`BookUpHostBridgeError` when the bridge
    is configured but unreachable, unauthorized, or returned a malformed
    response -- this must not be treated as "source trusted, zero events".
    """
    base = bridge_url()
    if not base:
        raise BookUpHostBridgeError("Ingen BookUp-bro er konfigurert (RVV_BOOKUP_HOST_BRIDGE mangler).")

    headers = {"Content-Type": "application/json"}
    token = os.environ.get(BRIDGE_TOKEN_ENV, "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.post(
            f"{base.rstrip('/')}/scrape",
            json={
                "source": name,
                "url": url,
                "start_date": start_date.date().isoformat(),
                "end_date": end_date.date().isoformat(),
            },
            headers=headers,
            timeout=_bridge_timeout_seconds(),
        )
    except requests.RequestException as exc:
        raise BookUpHostBridgeError(f"BookUp-bro '{base}' er utilgjengelig for '{name}': {exc}") from exc

    if response.status_code == 401:
        raise BookUpHostBridgeError(f"BookUp-bro avviste forespørselen for '{name}' (ugyldig token).")
    if response.status_code != 200:
        raise BookUpHostBridgeError(
            f"BookUp-bro returnerte status {response.status_code} for '{name}': {response.text[:200]}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise BookUpHostBridgeError(f"BookUp-bro returnerte ugyldig JSON for '{name}'.") from exc

    if body.get("status") != "ok":
        error = body.get("error") or f"BookUp-bro rapporterte feil for '{name}'."
        return [], error

    return _payload_to_events(body.get("events", [])), ""


# ---------------------------------------------------------------------------
# Server side -- run on the macOS host so login/MFA happens in a real browser.
# ---------------------------------------------------------------------------


class _BridgeRequestHandler(BaseHTTPRequestHandler):
    """Bounded scrape endpoint. Set the ``token`` class attribute before use."""

    token: str = ""
    protocol_version = "HTTP/1.1"

    def _respond(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _unauthorized(self) -> None:
        self._respond(401, {"status": "error", "error": "unauthorized"})

    def _bad_request(self, message: str) -> None:
        self._respond(400, {"status": "error", "error": message})

    def _authorized(self) -> bool:
        expected = f"Bearer {self.token}"
        return bool(self.token) and self.headers.get("Authorization", "") == expected

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler naming)
        if self.path != "/scrape":
            self._respond(404, {"status": "error", "error": "unknown endpoint"})
            return
        if not self._authorized():
            self._unauthorized()
            return

        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self._bad_request("invalid Content-Length")
            return
        if length <= 0 or length > _MAX_REQUEST_BYTES:
            self._bad_request("request body must be non-empty and bounded")
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except ValueError:
            self._bad_request("invalid JSON body")
            return

        source = str(payload.get("source", "")).strip()
        url = str(payload.get("url", "")).strip()
        start_raw = str(payload.get("start_date", "")).strip()
        end_raw = str(payload.get("end_date", "")).strip()
        if not source or not url or not start_raw or not end_raw:
            self._bad_request("source, url, start_date and end_date are required")
            return
        try:
            start_date = datetime.fromisoformat(start_raw)
            end_date = datetime.fromisoformat(end_raw)
        except ValueError:
            self._bad_request("start_date/end_date must be ISO dates (YYYY-MM-DD)")
            return

        from .scraper_credentialed import _try_credentialed_scrape
        from .scraper_strategies import get_strategy, requires_credentials

        strategy = get_strategy(source)
        if not strategy or not requires_credentials(strategy):
            self._respond(200, {"status": "error", "error": f"'{source}' er ikke en credentialed BookUp-kilde."})
            return

        print(f"[bookup-host] {source}: scrape forespurt for {start_raw}..{end_raw}", flush=True)
        try:
            events, error = _try_credentialed_scrape(
                source, url, start_date, end_date, host_bridge_mode=True
            )
        except Exception as exc:  # keep the bridge alive for the next request
            self._respond(200, {"status": "error", "error": f"Uventet feil under scraping av '{source}': {exc}"})
            return

        if error:
            self._respond(200, {"status": "error", "error": error})
            return
        self._respond(200, {"status": "ok", "events": _events_to_payload(events)})

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        print(f"[bookup-host] {self.address_string()} - {fmt % args}", flush=True)


def run_server(host: str = "127.0.0.1", port: int = DEFAULT_BRIDGE_PORT, token: str | None = None) -> None:
    """Serve the BookUp host bridge until interrupted (Ctrl+C).

    Never binds without a bearer token: one is generated when neither
    *token* nor ``RVV_BOOKUP_HOST_BRIDGE_TOKEN`` is supplied.
    """
    resolved_token = token if token is not None else os.environ.get(BRIDGE_TOKEN_ENV, "")
    if not resolved_token:
        resolved_token = secrets.token_urlsafe(24)
        print(f"[bookup-host] {BRIDGE_TOKEN_ENV} was not set; generated an ephemeral token.", flush=True)

    handler = type("_ConfiguredBridgeRequestHandler", (_BridgeRequestHandler,), {"token": resolved_token})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"[bookup-host] listening on http://{host}:{port}", flush=True)
    print(
        "[bookup-host] On the Lima side, set:\n"
        f"  export {BRIDGE_URL_ENV}=http://host.lima.internal:{port}\n"
        f"  export {BRIDGE_TOKEN_ENV}={resolved_token}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rvv-bookup-host", description=__doc__)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "Bind address. Defaults to 127.0.0.1 (host-local only, not reachable "
            "from Lima). Pass the host's Lima-reachable interface (or 0.0.0.0) "
            "explicitly once you intend the bridge to serve Lima."
        ),
    )
    parser.add_argument("--port", type=int, default=DEFAULT_BRIDGE_PORT)
    parser.add_argument(
        "--token",
        default=None,
        help="Shared bearer token. Generated if omitted and RVV_BOOKUP_HOST_BRIDGE_TOKEN is unset.",
    )
    args = parser.parse_args(argv)
    run_server(host=args.host, port=args.port, token=args.token)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
