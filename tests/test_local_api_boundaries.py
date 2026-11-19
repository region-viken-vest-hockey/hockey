"""Readiness (Git-free + legacy Git), AI tool allowlist, ice-time evidence and architecture edges."""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from tests.local_api_support import SEASON, Client, make_app, make_workspace
from tournament_scheduler.application.ports.readiness import ReadinessReason as R, ReadinessStatus
from tournament_scheduler.local_api.readiness import LegacyGitReadiness, LocalWorkspaceReadiness
from tournament_scheduler.local_api.tools import Tool, ToolRefused, ToolRegistry


def test_local_readiness_is_git_free_and_ready_on_a_plain_directory(tmp_path):
    ws = make_workspace(tmp_path)
    assert not (ws / ".git").exists()
    app, service = make_app(ws)
    report = LocalWorkspaceReadiness(service, SEASON, ws / "season").check()
    assert report.status is ReadinessStatus.READY and report.backend == "local"
    assert report.workflow_version.startswith("app-") and report.canonical_revision


def test_local_readiness_fails_closed(tmp_path, monkeypatch):
    ws = make_workspace(tmp_path)
    _, service = make_app(ws)
    def check(season=SEASON):
        return LocalWorkspaceReadiness(service, season, ws / "season").check()

    assert check("2099-2100").reason_code is R.STORE_UNAVAILABLE
    import fcntl
    import os
    lock = ws / "season" / f".{SEASON}.lock"
    lock.touch()
    fd = os.open(lock, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert check().reason_code is R.WRITER_LOCKED
    finally:
        os.close(fd)
    monkeypatch.setattr(service, "load", lambda s: (_ for _ in ()).throw(ValueError("corrupt")))
    r = check()
    assert r.status is ReadinessStatus.REVIEW_REQUIRED and r.reason_code is R.SCHEMA_INCOMPATIBLE
    assert r.workflow_version is None


def test_session_creation_surfaces_review_required_without_blocking_inspection(tmp_path):
    ws = make_workspace(tmp_path)
    app, service = make_app(ws)
    app.operate.readiness = type("R", (), {"check": lambda self: LocalWorkspaceReadiness(
        service, "2099-2100", ws / "season").check()})()
    c = Client(app).login()
    s = c.result("POST", "/api/v1/operate/sessions", {"season": SEASON})
    assert s["readiness"]["status"] == "REVIEW_REQUIRED"
    assert c.result("GET", f"/api/v1/seasons/{SEASON}")["tournament_count"] == 1


def _done(out, code):
    return subprocess.CompletedProcess([], code, stdout=out, stderr="")


@pytest.mark.parametrize("out,code,status,reason", [
    (json.dumps({"status": "READY", "workflow_version": "abc123", "reason": "ok"}), 0, "READY", R.READY_CURRENT),
    (json.dumps({"status": "REVIEW_REQUIRED", "reason_code": "diverged", "reason": "x"}), 1, "REVIEW_REQUIRED", R.DIVERGED),
    (json.dumps({"status": "READY", "reason": "no sha"}), 0, "REVIEW_REQUIRED", R.INSPECTION_FAILED),
    (json.dumps({"status": "READY", "workflow_version": "abc", "reason": "ok"}), 1, "REVIEW_REQUIRED", R.INSPECTION_FAILED),
    ("not json", 1, "REVIEW_REQUIRED", R.INSPECTION_FAILED),
])
def test_legacy_git_preflight_adapter_fails_closed(out, code, status, reason):
    r = LegacyGitReadiness(Path("."), runner=lambda argv, cwd: _done(out, code)).check()
    assert r.status.value == status and r.reason_code is reason and r.backend == "git"


def test_legacy_git_preflight_adapter_survives_runner_failure():
    def boom(argv, cwd):
        raise subprocess.TimeoutExpired("x", 1)
    assert LegacyGitReadiness(Path("."), runner=boom).check().status is ReadinessStatus.REVIEW_REQUIRED


def test_tool_registry_allows_only_inspect_search_preview_plan():
    ok = ToolRegistry([Tool("season_summary", "inspect", lambda: 1), Tool("plan_options", "plan", lambda: 2)])
    assert ok.invoke("plan_options") == 2
    with pytest.raises(ToolRefused):
        ok.invoke("shell")
    for name, kind in [("apply_move", "preview"), ("publish_season", "inspect"), ("git_push", "search"),
                       ("read_file", "inspect"), ("anything", "apply"), ("approve_tournament", "plan")]:
        with pytest.raises(ToolRefused):
            ToolRegistry([Tool(name, kind, lambda: None)])


def test_default_app_registry_has_no_mutating_capability(tmp_path):
    app, _ = make_app(make_workspace(tmp_path))
    assert app.operate.tools.names() == ["ice_time_sources", "move_preview", "season_summary", "tournament_inspection"]


MANUAL = {"source_id": "skien-manual", "timezone": "Europe/Oslo", "venue_id": "skien",
          "from": "2026-09-01T00:00:00+02:00", "to": "2026-10-01T00:00:00+02:00",
          "windows": [{"start": "2026-09-13T09:00:00+02:00", "end": "2026-09-13T15:00:00+02:00", "kind": "available"}]}


def test_ice_time_snapshot_is_source_neutral_and_edits_invalidate_proposals_not_bookings(tmp_path):
    ws = make_workspace(tmp_path)
    app, service = make_app(ws)
    c = Client(app).login()
    assert c.result("GET", "/api/v1/ice-time/sources")["sources"] == []
    put = c.result("PUT", "/api/v1/ice-time/sources", MANUAL)
    assert put["bookings_changed"] is False
    src = c.result("GET", "/api/v1/ice-time/sources/skien-manual")
    assert src["mode"] == "manual" and src["state"] == "partial" and src["provenance"]["actor"] == "op"
    w = src["windows"][0]
    assert w["utc_start"].startswith("2026-09-13T07:00") and w["local_start"].startswith("2026-09-13T09:00")
    assert "scraper" not in json.dumps(src).lower()
    # a proposal computed against the old evidence revision goes stale after a source edit
    sid = c.result("POST", "/api/v1/operate/sessions", {"season": SEASON})["session_id"]
    rev = c.result("GET", f"/api/v1/seasons/{SEASON}")["revision"]
    from tests.local_api_support import move_intent
    p = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent(revision=rev))["proposal"]
    edited = dict(MANUAL, expected_revision=src["revision"], windows=[])
    c.result("PUT", "/api/v1/ice-time/sources", edited)
    r = c.call("POST", f"/api/v1/operate/sessions/{sid}/proposals/{p['proposal_id']}/apply", {"confirm": p["digest"]})
    assert r.status == 409 and r.payload["error"]["code"] == "stale_revision"
    assert service.load(SEASON).schedule["plan"]["tournaments"][0]["date"] == "2026-09-12"


def test_ice_time_edit_validation_and_revision_binding(tmp_path):
    app, _ = make_app(make_workspace(tmp_path))
    c = Client(app).login()
    assert c.call("PUT", "/api/v1/ice-time/sources", dict(MANUAL, timezone="Mars/Base")).status == 400
    assert c.call("PUT", "/api/v1/ice-time/sources", dict(MANUAL, to="2026-08-01T00:00:00+02:00")).status == 400
    c.result("PUT", "/api/v1/ice-time/sources", MANUAL)
    stale = c.call("PUT", "/api/v1/ice-time/sources", dict(MANUAL, expected_revision="deadbeef"))
    assert stale.status == 409
    unbound = c.call("PUT", "/api/v1/ice-time/sources", MANUAL)  # creating again without the current revision
    assert unbound.status == 409


def test_local_api_stays_an_adapter_and_application_never_imports_it():
    for path in Path("tournament_scheduler").rglob("*.py"):
        if "local_api" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            assert not any(n.startswith("tournament_scheduler.local_api") for n in names), path
    for path in Path("tournament_scheduler/local_api").glob("*.py"):
        assert len(path.read_text().splitlines()) <= 300, path
