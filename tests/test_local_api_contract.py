"""End-to-end local API contract: reads, preview, stale/replay apply, BYOAI E2E, CLI-facade parity."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from tests.local_api_support import SEASON, TID, Client, make_app, make_workspace, move_intent
from tournament_scheduler.local_api.contracts import OperatorIntent
from tournament_scheduler.local_api.tools import RouterDecision


def tree_digest(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and not p.name.endswith(".lock")}


@pytest.fixture
def env(tmp_path):
    ws = make_workspace(tmp_path)
    app, service = make_app(ws)
    c = Client(app).login()
    sid = c.result("POST", "/api/v1/operate/sessions", {"season": SEASON})["session_id"]
    return ws, app, service, c, sid


def revision(c):
    return c.result("GET", f"/api/v1/seasons/{SEASON}")["revision"]


def test_start_and_browse_never_mutate_canonical_state(env):
    ws, app, service, c, sid = env
    before = tree_digest(ws / "season")
    c.result("GET", "/api/v1/capabilities")
    c.result("GET", "/api/v1/readiness")
    c.result("GET", f"/api/v1/seasons/{SEASON}")
    c.result("GET", f"/api/v1/seasons/{SEASON}/tournaments/{TID}")
    c.result("GET", "/api/v1/ice-time/sources")
    assert c.call("GET", "/").status == 200
    assert tree_digest(ws / "season") == before


def test_reads_expose_revision_authority_and_capabilities(env):
    _, _, _, c, _ = env
    caps = c.result("GET", "/api/v1/capabilities")
    assert caps["operator"] == "op" and caps["writable"] is True and caps["season_scope"] == "current"
    assert caps["revision"] == revision(c) and caps["operations"] == ["move_tournament"]
    ready = c.result("GET", "/api/v1/readiness")
    assert ready["status"] == "READY" and ready["backend"] == "local" and ready["canonical_revision"]
    assert c.result("GET", f"/api/v1/seasons/{SEASON}/tournaments/{TID}")["tournament"]["id"] == TID
    assert c.call("GET", "/api/v1/seasons/2099-2100").status in (404, 422)


def test_valid_preview_then_separate_confirmed_apply(env):
    ws, _, service, c, sid = env
    rev = revision(c)
    before = tree_digest(ws / "season")
    out = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent(revision=rev))
    assert out["outcome"] == "previewed"
    prop = out["proposal"]
    assert prop["preview"]["change_table"] == [{"field": "date", "from": "2026-09-12", "to": "2026-09-13"}]
    assert tree_digest(ws / "season") == before  # preview wrote nothing
    # apply needs the digest the human reviewed
    bad = c.call("POST", f"/api/v1/operate/sessions/{sid}/proposals/{prop['proposal_id']}/apply", {"confirm": "x"})
    assert bad.status == 400
    applied = c.result("POST", f"/api/v1/operate/sessions/{sid}/proposals/{prop['proposal_id']}/apply",
                       {"confirm": prop["digest"]})
    assert applied["outcome"] == "applied" and applied["result"]["revision_after"] != rev
    moved = service.load(SEASON).schedule["plan"]["tournaments"][0]
    assert moved["date"] == "2026-09-13"
    # replay of the same approval is rejected, not re-applied
    replay = c.call("POST", f"/api/v1/operate/sessions/{sid}/proposals/{prop['proposal_id']}/apply",
                    {"confirm": prop["digest"]})
    assert replay.status == 409 and replay.payload["error"]["code"] == "replayed"


def test_invalid_preview_is_blocked_or_review_required_and_cannot_be_applied(env):
    ws, _, service, c, sid = env
    rev = revision(c)
    before = tree_digest(ws / "season")
    out = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents",
                   move_intent("req-00000002", revision=rev, tournament_id="no-such-tournament"))
    assert out["outcome"] == "review_required" and out["error"]["code"] == "review_required"
    assert tree_digest(ws / "season") == before
    # structurally invalid intents never reach routing
    for bad in (move_intent("req-00000003", revision=rev, date="13/09/2026"),
                move_intent("req-00000004", revision=rev, timezone="Mars/Base"),
                move_intent("req-00000005", revision=rev, dimension="team"),
                {**move_intent("req-00000006", revision=rev), "expected_revision": None},
                move_intent("bad id!", revision=rev)):
        assert c.call("POST", f"/api/v1/operate/sessions/{sid}/intents", bad).status == 400


def test_stale_revision_at_ingress_and_at_apply(env):
    _, _, service, c, sid = env
    rev = revision(c)
    out = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents",
                   move_intent("req-00000007", revision="0" * 16))
    assert out["outcome"] == "stale"
    prop = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents",
                    move_intent("req-00000008", revision=rev))["proposal"]
    # another tab/CLI writes first
    service.move_tournament(season=SEASON, tournament_id=TID, start_time="11:00", actor="other-tab")
    r = c.call("POST", f"/api/v1/operate/sessions/{sid}/proposals/{prop['proposal_id']}/apply",
               {"confirm": prop["digest"]})
    assert r.status == 409 and r.payload["error"]["code"] == "stale_revision"
    tour = service.load(SEASON).schedule["plan"]["tournaments"][0]
    assert tour["date"] == "2026-09-12" and tour["start_time"] == "11:00"  # no silent commit of our move


def test_idempotency_rapid_repeat_and_multi_tab_supersede(env):
    _, _, _, c, sid = env
    rev = revision(c)
    first = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent("req-00000009", revision=rev))
    again = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent("req-00000009", revision=rev))
    assert again == first
    clash = c.call("POST", f"/api/v1/operate/sessions/{sid}/intents",
                   move_intent("req-00000009", revision=rev, date="2026-09-14"))
    assert clash.status == 409 and clash.payload["error"]["code"] == "request_id_reused"
    second = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents",
                      move_intent("req-00000010", revision=rev, date="2026-09-14"))
    old = first["proposal"]
    r = c.call("POST", f"/api/v1/operate/sessions/{sid}/proposals/{old['proposal_id']}/apply", {"confirm": old["digest"]})
    assert r.status == 409  # superseded by the newer preview, never silently applied
    assert second["outcome"] == "previewed"


def test_api_preview_verdict_matches_cli_facade_on_frozen_copy(tmp_path):
    ws = make_workspace(tmp_path)
    frozen = tmp_path / "frozen"
    shutil.copytree(ws, frozen)
    app, _ = make_app(ws)
    c = Client(app).login()
    sid = c.result("POST", "/api/v1/operate/sessions", {"season": SEASON})["session_id"]
    out = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent(revision=revision(c)))
    from tournament_scheduler.application.canonical_season.store_facade import move_tournament
    direct = move_tournament(season=SEASON, tournament_id=TID, root=frozen / "season", date="2026-09-13", dry_run=True)
    mp = direct["move_preview"]
    assert out["proposal"]["preview"]["after_fingerprint"] == mp["after_fingerprint"]
    assert out["proposal"]["preview"]["change_table"][0]["to"] == mp["new_placement"]["date"]
    assert out["proposal"]["preview"]["revision"] == mp["before_canonical_revision"]


class FakeRouter:
    """Stands in for the BYOAI provider/router (#965/#966): reads via tools, returns a typed intent."""

    def route(self, intent, tools, emit):
        summary = tools.invoke("season_summary", season=intent.season)
        emit("finding", {"source": "canonical_schedule", "text": f"{summary['tournament_count']} tournament(s)"})
        return RouterDecision(
            intent=OperatorIntent.parse({"request_id": intent.request_id, "gesture": "calendar_edit",
                                         "season": intent.season, "tournament_id": TID, "dimension": "date",
                                         "value": "2026-09-13", "timezone": "Europe/Oslo",
                                         "expected_revision": summary["revision"]}),
            findings=({"source": f"tournament:{TID}", "text": "placed 2026-09-12 at Arena A"},))


def test_byoai_text_intent_e2e_streams_findings_then_human_apply(tmp_path):
    ws = make_workspace(tmp_path)
    app, service = make_app(ws, router=FakeRouter())
    c = Client(app).login()
    sid = c.result("POST", "/api/v1/operate/sessions", {"season": SEASON})["session_id"]
    out = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents",
                   {"request_id": "req-text-0001", "gesture": "text", "season": SEASON,
                    "text": "Flytt U10-turneringen til søndag"})
    assert out["outcome"] == "previewed"
    kinds = [e["kind"] for e in c.result("GET", f"/api/v1/operate/sessions/{sid}/events?after=0")["events"]]
    assert kinds.count("finding") == 2 and "proposal" in kinds
    assert service.load(SEASON).schedule["plan"]["tournaments"][0]["date"] == "2026-09-12"  # AI path wrote nothing
    p = out["proposal"]
    done = c.result("POST", f"/api/v1/operate/sessions/{sid}/proposals/{p['proposal_id']}/apply", {"confirm": p["digest"]})
    assert done["outcome"] == "applied"
    assert service.load(SEASON).schedule["plan"]["tournaments"][0]["date"] == "2026-09-13"
    tail = c.result("GET", f"/api/v1/operate/sessions/{sid}/events?after=1000")
    assert tail["events"] == [] and tail["phase"] == "done"


def test_text_intent_without_provider_fails_closed_but_inspection_still_works(env):
    _, _, _, c, sid = env
    out = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents",
                   {"request_id": "req-text-0002", "gesture": "text", "season": SEASON, "text": "flytt"})
    assert out["outcome"] == "review_required" and out["reason"] == "no_provider_configured"
    assert c.result("GET", f"/api/v1/seasons/{SEASON}")["tournament_count"] == 1


def test_sealed_season_is_read_only(env, monkeypatch):
    _, app, service, c, sid = env
    monkeypatch.setattr(service, "season_lifecycle_report",
                        lambda s: {"state": "published_sealed", "sealed": True, "reconciliation": {"ok": True}})
    caps = c.result("GET", "/api/v1/capabilities")
    assert caps["writable"] is False and caps["mode"] == "read_only" and caps["season_scope"] == "sealed"
    out = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent(revision=revision(c)))
    assert out["outcome"] == "blocked" and out["reason"] == "season_sealed_read_only"


def test_process_interruption_mid_apply_leaves_canonical_state_intact(env, monkeypatch):
    ws, app, service, c, sid = env
    rev = revision(c)
    p = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent(revision=rev))["proposal"]
    before = tree_digest(ws / "season")

    def die(*a, **k):
        raise KeyboardInterrupt  # simulates the process dying inside the atomic commit

    monkeypatch.setattr(service.store, "write", die)
    with pytest.raises(KeyboardInterrupt):
        app.handle("POST", f"/api/v1/operate/sessions/{sid}/proposals/{p['proposal_id']}/apply",
                   {"Host": "127.0.0.1:5555", "Origin": "http://127.0.0.1:5555", "Cookie": c.cookie,
                    "X-CSRF-Token": c.csrf, "Content-Type": "application/json"},
                   json.dumps({"confirm": p["digest"]}).encode())
    monkeypatch.undo()
    assert tree_digest(ws / "season") == before
    app2, _ = make_app(ws)  # restart: state loads and is still at the old revision
    c2 = Client(app2).login()
    assert c2.result("GET", f"/api/v1/seasons/{SEASON}")["revision"] == rev


def test_jobs_are_cancellable_and_report_phase_progress_cost(env):
    import threading
    _, app, _, c, _ = env
    gate = threading.Event()

    def slow(ctx):
        ctx.report("working", 0.3)
        while not gate.is_set():
            ctx.checkpoint()
            gate.wait(0.01)

    job = app.jobs.submit("test", "req-job-0001", slow)
    seen = c.result("GET", f"/api/v1/jobs/{job.job_id}")
    assert seen["kind"] == "test" and "elapsed_s" in seen["cost"]
    assert c.result("POST", f"/api/v1/jobs/{job.job_id}/cancel", {})["job_id"] == job.job_id
    done = app.jobs.wait(job.job_id, 5)
    assert done.status == "cancelled"
    assert c.call("GET", "/api/v1/jobs/unknown").status == 404
