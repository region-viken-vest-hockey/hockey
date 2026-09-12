"""Unit tests for the portable BookUp session-handoff bridge (issue #304).

Covers the client dispatch/error-handling contract and the request handler's
auth/validation behavior. Does not exercise a real Playwright browser or MFA
flow -- that requires a real macOS host run, which is out of scope for
automated tests.
"""

from __future__ import annotations

import json
from datetime import datetime
from http.client import HTTPConnection
from threading import Thread

import pytest
import requests

from tournament_scheduler.models import CalendarEvent
from tournament_scheduler.pipeline import bookup_host_bridge as bridge
from tournament_scheduler.pipeline.bookup_host_bridge import (
    BookUpHostBridgeError,
    _BridgeRequestHandler,
    _events_to_payload,
    _payload_to_events,
    bridge_configured,
    request_bookup_scrape,
)
from tournament_scheduler.pipeline.scraper_credentialed import _try_credentialed_scrape


def test_events_round_trip_through_payload() -> None:
    events = [
        CalendarEvent(
            date="03.09.2026",
            name="Trening",
            datetime=datetime(2026, 9, 3, 8, 0),
            duration_hours=1.5,
            location="Tønsberg ishall",
        )
    ]
    restored = _payload_to_events(_events_to_payload(events))
    assert restored == events


def test_bridge_configured_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(bridge.BRIDGE_URL_ENV, raising=False)
    assert bridge_configured() is False
    monkeypatch.setenv(bridge.BRIDGE_URL_ENV, "http://host.lima.internal:8765")
    assert bridge_configured() is True


def test_request_bookup_scrape_raises_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(bridge.BRIDGE_URL_ENV, raising=False)
    with pytest.raises(BookUpHostBridgeError):
        request_bookup_scrape("Tønsberg", "https://example.com", datetime(2026, 9, 1), datetime(2026, 9, 30))


def test_request_bookup_scrape_raises_on_unreachable_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(bridge.BRIDGE_URL_ENV, "http://host.lima.internal:8765")

    def _raise(*args, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "post", _raise)
    with pytest.raises(BookUpHostBridgeError):
        request_bookup_scrape("Tønsberg", "https://example.com", datetime(2026, 9, 1), datetime(2026, 9, 30))


def test_request_bookup_scrape_raises_on_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(bridge.BRIDGE_URL_ENV, "http://host.lima.internal:8765")

    class _Resp:
        status_code = 401
        text = ""

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    with pytest.raises(BookUpHostBridgeError):
        request_bookup_scrape("Tønsberg", "https://example.com", datetime(2026, 9, 1), datetime(2026, 9, 30))


def test_request_bookup_scrape_returns_events_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(bridge.BRIDGE_URL_ENV, "http://host.lima.internal:8765")

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "status": "ok",
                "events": _events_to_payload(
                    [CalendarEvent(date="03.09.2026", name="Kamp", datetime=datetime(2026, 9, 3, 10, 0))]
                ),
            }

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    events, error = request_bookup_scrape(
        "Tønsberg", "https://example.com", datetime(2026, 9, 1), datetime(2026, 9, 30)
    )
    assert error == ""
    assert len(events) == 1
    assert events[0].name == "Kamp"


def test_request_bookup_scrape_returns_error_when_bridge_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(bridge.BRIDGE_URL_ENV, "http://host.lima.internal:8765")

    class _Resp:
        status_code = 200

        def json(self):
            return {"status": "error", "error": "MFA timed out"}

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    events, error = request_bookup_scrape(
        "Tønsberg", "https://example.com", datetime(2026, 9, 1), datetime(2026, 9, 30)
    )
    assert events == []
    assert error == "MFA timed out"


def test_non_bookup_source_is_unaffected_by_bridge_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(bridge.BRIDGE_URL_ENV, "http://host.lima.internal:8765")
    called = {"bridge": False}
    monkeypatch.setattr(
        "tournament_scheduler.pipeline.bookup_host_bridge.request_bookup_scrape",
        lambda *a, **k: called.__setitem__("bridge", True) or ([], ""),
    )
    events, error = _try_credentialed_scrape(
        "not-a-real-source", "https://example.com", datetime(2026, 9, 1), datetime(2026, 9, 30)
    )
    assert called["bridge"] is False
    assert events == []
    assert error == ""


class _EphemeralBridgeServer:
    """Runs the real request handler on 127.0.0.1 for handler-level tests."""

    def __init__(self, token: str = "secret-token"):
        handler = type("_TestHandler", (_BridgeRequestHandler,), {"token": token})
        self.server = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer(
            ("127.0.0.1", 0), handler
        )
        self.token = token
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def bridge_server():
    srv = _EphemeralBridgeServer()
    yield srv
    srv.close()


def _post(port: int, body: bytes, token: str | None) -> tuple[int, dict]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    conn.request("POST", "/scrape", body=body, headers=headers)
    resp = conn.getresponse()
    data = json.loads(resp.read())
    status = resp.status
    conn.close()
    return status, data


def test_handler_rejects_missing_or_wrong_token(bridge_server) -> None:
    body = json.dumps(
        {"source": "x", "url": "https://example.com", "start_date": "2026-09-01", "end_date": "2026-09-30"}
    ).encode()
    status, data = _post(bridge_server.port, body, token=None)
    assert status == 401
    status, data = _post(bridge_server.port, body, token="wrong")
    assert status == 401


def test_handler_rejects_malformed_json(bridge_server) -> None:
    status, data = _post(bridge_server.port, b"not json", token=bridge_server.token)
    assert status == 400
    assert data["status"] == "error"


def test_handler_rejects_missing_fields(bridge_server) -> None:
    status, data = _post(bridge_server.port, json.dumps({"source": "x"}).encode(), token=bridge_server.token)
    assert status == 400


def test_handler_rejects_non_credentialed_source(bridge_server) -> None:
    body = json.dumps(
        {
            "source": "not-a-real-bookup-source",
            "url": "https://example.com",
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        }
    ).encode()
    status, data = _post(bridge_server.port, body, token=bridge_server.token)
    assert status == 200
    assert data["status"] == "error"
    assert "not-a-real-bookup-source" in data["error"]
