"""Shared fixtures for local operate API tests: a copied promoted-season workspace + HTTP-ish client."""

from __future__ import annotations

import json
from http.cookies import SimpleCookie
from pathlib import Path

from tests.test_season_state import _stage_plan
from tournament_scheduler.application.canonical_season.store_facade import promote_from_stage3
from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.local_api.launcher import build_app
from tournament_scheduler.pipeline.state import PipelineState

SEASON = "2026-2027"
TID = "u10-a-20260912"
HOST = "127.0.0.1:5555"
ORIGIN = f"http://{HOST}"


def make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    state = PipelineState(tmp_path / ".pipeline")
    _stage_plan(state)
    promote_from_stage3(work_dir=tmp_path / ".pipeline", root=workspace / "season", actor="tester")
    return workspace


def make_app(workspace: Path, router=None, wait_s: float = 5.0):
    service = CanonicalSeasonService(root=workspace / "season", write_gate=lambda: None)
    app = build_app(workspace, SEASON, service=service, router=router, operator="op", wait_s=wait_s)
    app.security.set_port(5555)
    return app, service


class Client:
    def __init__(self, app) -> None:
        self.app, self.cookie, self.csrf = app, None, None

    def call(self, method, path, body=None, *, headers=None, auth=True, origin=ORIGIN, host=HOST, csrf=True,
             raw=None):
        h = {"Host": host}
        if origin is not None:
            h["Origin"] = origin
        if auth and self.cookie:
            h["Cookie"] = self.cookie
        if auth and csrf and self.csrf:
            h["X-CSRF-Token"] = self.csrf
        payload = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())
        if payload:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        resp = self.app.handle(method, path, h, payload)
        return resp

    def login(self):
        token = self.app.security.bootstrap_token()
        r = self.call("POST", "/api/v1/session/bootstrap", {"token": token}, auth=False)
        assert r.status == 200, r.payload
        self.cookie = SimpleCookie(dict(r.headers)["Set-Cookie"].split(";")[0]).output(header="").strip()
        self.csrf = r.payload["result"]["csrf_token"]
        return self

    def result(self, method, path, body=None, **kw):
        r = self.call(method, path, body, **kw)
        assert r.status == 200, (r.status, r.payload)
        return r.payload["result"]


def move_intent(request_id="req-00000001", *, revision, date="2026-09-13", **extra):
    body = {"request_id": request_id, "gesture": "calendar_drag", "season": SEASON, "tournament_id": TID,
            "dimension": "date", "value": date, "timezone": "Europe/Oslo", "expected_revision": revision}
    body.update(extra)
    return body
