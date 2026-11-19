"""Regressions for review of 6be0fbb4: ownership, atomic idempotency, fail-closed previews, TOCTOU, gates."""

from __future__ import annotations

import os
import threading
import time

import pytest

from tests.local_api_support import SEASON, Client, make_app, make_workspace, move_intent
from tournament_scheduler.application.ports.readiness import ReadinessReason as R
from tournament_scheduler.local_api.contracts import ApiError, OperatorIntent
from tournament_scheduler.local_api.instance import WorkspaceInstance
from tournament_scheduler.local_api.readiness import LocalWorkspaceReadiness
from tournament_scheduler.local_api.usecases import _shape_preview

MANUAL = {"source_id": "skien-manual", "timezone": "Europe/Oslo", "venue_id": "skien",
          "from": "2026-09-01T00:00:00+02:00", "to": "2026-10-01T00:00:00+02:00",
          "windows": [{"start": "2026-09-13T09:00:00+02:00", "end": "2026-09-13T15:00:00+02:00", "kind": "available"}]}


@pytest.fixture
def env(tmp_path):
    ws = make_workspace(tmp_path)
    app, service = make_app(ws)
    c = Client(app).login()
    sid = c.result("POST", "/api/v1/operate/sessions", {"season": SEASON})["session_id"]
    rev = c.result("GET", f"/api/v1/seasons/{SEASON}")["revision"]
    return ws, app, service, c, sid, rev


def _apply(c, sid, p, **kw):
    return c.call("POST", f"/api/v1/operate/sessions/{sid}/proposals/{p['proposal_id']}/apply",
                  {"confirm": p["digest"]}, **kw)


# 1 ---------------------------------------------------------------------------------------------
def test_proposal_of_session_a_cannot_be_applied_via_session_b(env):
    _, _, service, c, sid_a, rev = env
    sid_b = c.result("POST", "/api/v1/operate/sessions", {"season": SEASON})["session_id"]
    p = c.result("POST", f"/api/v1/operate/sessions/{sid_a}/intents", move_intent(revision=rev))["proposal"]
    r = _apply(c, sid_b, p)
    assert r.status == 404
    assert service.load(SEASON).schedule["plan"]["tournaments"][0]["date"] == "2026-09-12"
    assert _apply(c, sid_a, p).status == 200  # the owning session can still apply it


def test_proposal_of_another_operator_is_rejected_before_any_state_change(env):
    _, app, service, c, sid, rev = env
    p = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent(revision=rev))["proposal"]
    app.operate.proposals.get(p["proposal_id"]).created_by = "someone-else"
    r = _apply(c, sid, p)
    assert r.status == 403 and r.payload["error"]["code"] == "forbidden"
    assert app.operate.proposals.get(p["proposal_id"]).status == "pending"
    assert service.load(SEASON).schedule["plan"]["tournaments"][0]["date"] == "2026-09-12"


def test_wrong_confirmation_leaves_proposal_pending(env):
    _, app, _, c, sid, rev = env
    p = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent(revision=rev))["proposal"]
    assert c.call("POST", f"/api/v1/operate/sessions/{sid}/proposals/{p['proposal_id']}/apply",
                  {"confirm": "0" * 64}).status == 400
    assert app.operate.proposals.get(p["proposal_id"]).status == "pending"


# 2 ---------------------------------------------------------------------------------------------
def test_concurrent_same_request_id_creates_one_job_and_one_proposal(env, monkeypatch):
    _, app, service, c, sid, rev = env
    calls = []
    real = service.move_tournament

    def slow(**kw):
        calls.append(kw)
        time.sleep(0.3)
        return real(**kw)

    monkeypatch.setattr(service, "move_tournament", slow)
    intent = OperatorIntent.parse(move_intent("req-race-0001", revision=rev))
    results, errors = [], []
    barrier = threading.Barrier(8)

    def go():
        barrier.wait()
        try:
            results.append(app.operate.submit(sid, intent))
        except Exception as exc:  # pragma: no cover - failure path is asserted below
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join(20) for t in threads]
    assert not errors and len(results) == 8
    assert len(calls) == 1  # a single preview ran
    assert {r["proposal"]["proposal_id"] for r in results} == {results[0]["proposal"]["proposal_id"]}
    assert sum(1 for p in app.operate.proposals._items.values()) == 1


def test_repeat_of_a_pending_request_returns_the_same_job(env, monkeypatch):
    _, app, service, c, sid, rev = env
    app.operate.wait_s = 0.05
    gate = threading.Event()
    real = service.move_tournament
    monkeypatch.setattr(service, "move_tournament", lambda **kw: (gate.wait(5), real(**kw))[1])
    intent = OperatorIntent.parse(move_intent("req-race-0002", revision=rev))
    first = app.operate.submit(sid, intent)
    again = app.operate.submit(sid, intent)
    assert first["outcome"] == again["outcome"] == "proposal_pending"
    assert first["job"]["job_id"] == again["job"]["job_id"]
    gate.set()
    final = app.operate.submit(sid, intent)
    app.operate.wait_s = 5
    final = app.operate.submit(sid, intent)
    assert final["outcome"] == "previewed" and len(app.operate.proposals._items) == 1


# 3 / 4 ----------------------------------------------------------------------------------------
def test_hard_verification_blocked_dry_run_is_blocked_not_previewed(env, monkeypatch):
    _, app, service, c, sid, rev = env
    from tournament_scheduler.application.canonical_season import placements
    real = placements.verify_canonical_candidate
    seen = []

    def verify(plan, *a, **kw):
        seen.append(1)
        out = real(plan, *a, **kw)
        return out if len(seen) == 1 else {**out, "ok": False, "violations": [{"code": "x", "message": "hard rule"}]}

    monkeypatch.setattr(placements, "verify_canonical_candidate", verify)
    out = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent(revision=rev))
    assert out["outcome"] == "blocked"
    prev = out["proposal"]["preview"]
    assert prev["verdict"]["valid"] is False and prev["blockers"] and prev["change_table"] == []
    assert _apply(c, sid, out["proposal"]).status == 422


@pytest.mark.parametrize("bad", [
    None, {}, {"dry_run": True}, {"dry_run": True, "move_preview": {}},
    {"dry_run": True, "move_preview": {"verification_result": {}, "before_canonical_revision": "r",
                                       "old_placement": {}, "new_placement": {}}},
    {"dry_run": False, "move_preview": {}},
])
def test_unexpected_preview_structure_fails_closed(bad):
    with pytest.raises(ApiError) as err:
        _shape_preview(bad, "r")
    assert err.value.code.value == "review_required"


def test_dry_run_revision_mismatch_is_stale_and_binds_to_dry_run_revision(env, monkeypatch):
    _, app, service, c, sid, rev = env
    out = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent(revision=rev))
    assert out["proposal"]["revision"] == rev  # bound to before_canonical_revision of the dry-run
    stale = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent("req-00000090", revision="f" * 16))
    assert stale["outcome"] == "stale"
    # a commit landing between "read" and the dry-run can no longer slip through: revision is from the dry-run
    real = service.move_tournament

    def racing(**kw):
        if kw.get("dry_run"):
            real(season=SEASON, tournament_id=kw["tournament_id"], start_time="11:00", actor="tab2")
        return real(**kw)

    monkeypatch.setattr(service, "move_tournament", racing)
    raced = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent("req-00000091", revision=out["proposal"]["revision"], date="2026-09-14"))
    assert raced["outcome"] == "stale"


# 5 ---------------------------------------------------------------------------------------------
def test_apply_rechecks_writability_and_readiness_before_commit(env, monkeypatch):
    ws, app, service, c, sid, rev = env
    p = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent(revision=rev))["proposal"]
    monkeypatch.setattr(service, "season_lifecycle_report",
                        lambda s: {"state": "published_sealed", "sealed": True, "reconciliation": {"ok": True}})
    r = _apply(c, sid, p)
    assert r.status in (403, 422)
    assert service.load(SEASON).schedule["plan"]["tournaments"][0]["date"] == "2026-09-12"
    monkeypatch.undo()
    assert _apply(c, sid, p).status == 200  # not burned by the refusal


def test_apply_by_a_session_of_a_different_operator_is_refused(env):
    _, app, _, c, sid, rev = env
    p = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent(revision=rev))["proposal"]
    app.operate.session(sid).operator = "other"
    assert _apply(c, sid, p).status == 403


# 6 ---------------------------------------------------------------------------------------------
def test_readiness_version_mismatch_fails_closed(tmp_path):
    ws = make_workspace(tmp_path)
    _, service = make_app(ws)
    current = {"v": "app-1+api1"}
    check = LocalWorkspaceReadiness(service, SEASON, ws / "season", expected_version="app-1+api1",
                                    workflow_version=lambda: current["v"])
    assert check.check().status.value == "READY"
    current["v"] = "app-2+api1"
    r = check.check()
    assert r.status.value == "REVIEW_REQUIRED" and r.reason_code is R.WORKFLOW_VERSION_MISMATCH


def test_version_mismatch_blocks_intent_before_any_write(env, monkeypatch):
    ws, app, service, c, sid, rev = env
    monkeypatch.setattr("tournament_scheduler.local_api.readiness.application_workflow_version", lambda: "other")
    app.operate.readiness._version = lambda: "other"
    out = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent(revision=rev))
    assert out["outcome"] == "review_required" and out["reason"] == "workflow_version_mismatch"


# 7 ---------------------------------------------------------------------------------------------
def test_evidence_change_cannot_interleave_with_a_canonical_commit(env, monkeypatch):
    _, app, service, c, sid, rev = env
    c.result("PUT", "/api/v1/ice-time/sources", MANUAL)
    src = c.result("GET", "/api/v1/ice-time/sources/skien-manual")
    p = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent(revision=rev))["proposal"]
    uses = app.operate.uses
    real_apply = uses.apply_move
    state = {}

    def apply_with_concurrent_edit(*a, **kw):
        t = threading.Thread(target=lambda: state.update(
            put=app.ice.put_manual(dict(MANUAL, expected_revision=src["revision"], windows=[]), "op")))
        t.start()
        time.sleep(0.3)
        state["blocked_during_commit"] = t.is_alive() and "put" not in state
        out = real_apply(*a, **kw)
        state["thread"] = t
        return out

    monkeypatch.setattr(uses, "apply_move", apply_with_concurrent_edit)
    assert _apply(c, sid, p).status == 200
    state["thread"].join(10)
    assert state["blocked_during_commit"] is True  # the evidence swap waited for the commit
    assert "put" in state  # ...and then went through, staling later proposals


# 8 is covered in test_local_api_boundaries (closed registry) ------------------------------------
# minors ----------------------------------------------------------------------------------------
def test_bootstrap_file_mode_is_reasserted_and_symlinks_rejected(tmp_path):
    inst = WorkspaceInstance(tmp_path)
    inst.acquire()
    inst.bootstrap_file.write_text("old")
    os.chmod(inst.bootstrap_file, 0o644)
    inst.publish(1, "newtoken")
    assert oct(inst.bootstrap_file.stat().st_mode & 0o777) == "0o600"
    assert inst.bootstrap_file.read_text() == "newtoken"
    inst.bootstrap_file.unlink()
    target = tmp_path / "elsewhere"
    target.write_text("keep")
    inst.bootstrap_file.symlink_to(target)
    with pytest.raises(OSError):
        inst.publish(1, "tok")
    assert target.read_text() == "keep"
    inst.bootstrap_file.unlink()
    inst.release()
