"""Negative tests for the loopback security controls: Host/Origin/CSRF/token/limits/bind."""

from __future__ import annotations

import json
import socket
import threading
import urllib.request

import pytest

from tests.local_api_support import SEASON, Client, make_app, make_workspace
from tournament_scheduler.local_api.instance import InstanceRunningError, WorkspaceInstance
from tournament_scheduler.local_api.security import redact, validate_bind_host
from tournament_scheduler.local_api.server import make_server


@pytest.fixture
def env(tmp_path):
    ws = make_workspace(tmp_path)
    app, service = make_app(ws)
    return ws, app, service, Client(app).login()


def test_remote_bind_is_refused_by_default_and_localhost_resolves_to_loopback():
    assert validate_bind_host("127.0.0.1") == "127.0.0.1"
    assert validate_bind_host("localhost") == "127.0.0.1"
    for bad in ("0.0.0.0", "192.168.1.5", "::", "example.com"):
        with pytest.raises(ValueError):
            validate_bind_host(bad)
    assert validate_bind_host("10.0.0.2", allow_remote=True) == "10.0.0.2"


def test_unauthenticated_and_cookieless_requests_are_rejected(env):
    _, app, _, _ = env
    anon = Client(app)
    assert anon.call("GET", "/api/v1/capabilities", auth=False).status == 401
    assert anon.call("GET", f"/api/v1/seasons/{SEASON}", auth=False).status == 401


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1:1", "localhost", ""])
def test_wrong_host_is_rejected_dns_rebinding(env, host):
    c = env[3]
    r = c.call("GET", "/api/v1/capabilities", host=host)
    assert r.status == 403 and r.payload["error"]["code"] == "forbidden_host"


@pytest.mark.parametrize("origin", ["http://evil.example", "https://127.0.0.1:5555", "http://127.0.0.1:9999", "null"])
def test_foreign_origin_cannot_read_or_mutate(env, origin):
    c = env[3]
    assert c.call("GET", "/api/v1/capabilities", origin=origin).status == 403
    r = c.call("POST", "/api/v1/operate/sessions", {"season": SEASON}, origin=origin)
    assert r.status == 403 and r.payload["error"]["code"] == "forbidden_origin"


def test_state_changing_request_without_origin_or_csrf_is_rejected(env):
    c = env[3]
    assert c.call("POST", "/api/v1/operate/sessions", {"season": SEASON}, origin=None).status == 403
    r = c.call("POST", "/api/v1/operate/sessions", {"season": SEASON}, csrf=False)
    assert r.status == 403 and r.payload["error"]["code"] == "csrf_rejected"
    r = c.call("POST", "/api/v1/operate/sessions", {"season": SEASON}, headers={"X-CSRF-Token": "wrong"})
    assert r.status == 403


def test_bootstrap_token_is_one_time_and_wrong_token_rejected(tmp_path):
    app, _ = make_app(make_workspace(tmp_path))
    c = Client(app)
    assert c.call("POST", "/api/v1/session/bootstrap", {"token": "nope"}, auth=False).status == 401
    c.login()
    again = c.call("POST", "/api/v1/session/bootstrap", {"token": app.security.bootstrap_token() if app.security._bootstrap else "x"}, auth=False)
    assert again.status == 401


def test_session_cookie_is_httponly_samesite_and_no_cors_headers(env):
    _, app, _, _ = env
    fresh, _ = make_app(env[0])
    r = Client(fresh).call("POST", "/api/v1/session/bootstrap", {"token": fresh.security.bootstrap_token()}, auth=False)
    cookie = dict(r.headers)["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert not any(k.lower().startswith("access-control") for k, _ in r.headers)


def test_secrets_in_url_are_rejected_and_log_is_redacted(tmp_path):
    ws = make_workspace(tmp_path)
    app, _ = make_app(ws)
    logged = []
    app.log = logged.append
    c = Client(app).login()
    r = c.call("GET", "/api/v1/capabilities?token=abc")
    assert r.status == 400
    c.call("GET", "/api/v1/capabilities")
    blob = json.dumps(logged)
    assert c.csrf not in blob and c.cookie not in blob and app.security._sessions and "abc" not in blob
    assert redact({"Cookie": "x", "csrf_token": "y", "path": "/p"}) == {"Cookie": "***", "csrf_token": "***", "path": "/p"}


def test_oversized_malformed_and_non_json_bodies(env):
    c = env[3]
    big = b'{"season":"' + b"a" * (1024 * 1024 + 1) + b'"}'
    assert c.call("POST", "/api/v1/operate/sessions", raw=big).status == 413
    assert c.call("POST", "/api/v1/operate/sessions", raw=b"{not json").status == 400
    assert c.call("POST", "/api/v1/operate/sessions", raw=b"[1]").status == 400
    r = c.call("POST", "/api/v1/operate/sessions", raw=b"{}", headers={"Content-Type": "text/plain"})
    assert r.status == 400


def test_path_traversal_ids_are_rejected(env):
    c = env[3]
    assert c.call("GET", "/api/v1/seasons/..%2F..%2Fetc").status in (400, 404)
    assert c.call("GET", "/api/v1/ice-time/sources/..%2Fx").status in (400, 404)
    r = c.call("PUT", "/api/v1/ice-time/sources", {"source_id": "../evil", "timezone": "Europe/Oslo"})
    assert r.status == 400


def test_second_instance_is_refused_and_lock_survives_no_crash_leftover(tmp_path):
    a, b = WorkspaceInstance(tmp_path), WorkspaceInstance(tmp_path)
    a.acquire()
    with pytest.raises(InstanceRunningError):
        b.acquire()
    a.publish(1234, "tok")
    assert oct(a.bootstrap_file.stat().st_mode & 0o777) == "0o600"
    a.release()
    assert not a.bootstrap_file.exists()
    b.acquire()  # lock is free after clean shutdown
    b.release()


def test_real_socket_server_serves_only_loopback_and_rejects_forged_host(tmp_path):
    app, _ = make_app(make_workspace(tmp_path))
    server = make_server(app)
    app.security.set_port(server.server_port)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        assert server.server_address[0] == "127.0.0.1"
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/v1/capabilities")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req)
        assert err.value.code == 401
        s = socket.create_connection(("127.0.0.1", server.server_port))
        s.sendall(b"GET /api/v1/capabilities HTTP/1.1\r\nHost: evil.example\r\nConnection: close\r\n\r\n")
        assert b" 403 " in s.recv(4096).split(b"\r\n")[0]
        s.close()
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/") as ui:
            body = ui.read().decode()
            assert "Content-Security-Policy" in body and ui.headers["Cache-Control"] == "no-store"
    finally:
        server.shutdown()
        server.server_close()
