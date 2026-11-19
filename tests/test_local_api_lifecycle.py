"""Round-2 regressions: single proposal state machine, ambiguous applies, evidence gate, cancel, evidence policy."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from tests.local_api_support import SEASON, Client, make_app, make_workspace, move_intent
from tournament_scheduler.local_api import sessions as sessions_mod
from tournament_scheduler.local_api.contracts import ApiError, OperatorIntent
from tournament_scheduler.local_api.proposals import ProposalBook

NOW = datetime.now(timezone.utc)
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


def _apply(c, sid, p):
    return c.call("POST", f"/api/v1/operate/sessions/{sid}/proposals/{p['proposal_id']}/apply", {"confirm": p["digest"]})


def _preview(c, sid, rev, rid="req-00000001", date="2026-09-13"):
    return c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent(rid, revision=rev, date=date))


def _intent(rid, rev, date="2026-09-13"):
    return OperatorIntent.parse(move_intent(rid, revision=rev, date=date))


# 1 ---------------------------------------------------------------------------------------------
def test_failure_after_commit_before_response_is_recovery_required_never_pending(env, monkeypatch):
    _, app, service, c, sid, rev = env
    p = _preview(c, sid, rev)["proposal"]
    uses = app.operate.uses
    real = uses.apply_move

    def commit_then_die(*a, **kw):
        real(*a, **kw)  # the canonical write has committed
        raise RuntimeError("connection dropped before the response")

    monkeypatch.setattr(uses, "apply_move", commit_then_die)
    monkeypatch.setattr(uses, "revision", lambda season: (_ for _ in ()).throw(OSError("read failed")))
    r = _apply(c, sid, p)
    assert r.status == 409 and r.payload["error"]["code"] == "recovery_required"
    assert app.operate.proposals.get(p["proposal_id"]).status == "recovery_required"
    monkeypatch.undo()
    assert service.load(SEASON).schedule["plan"]["tournaments"][0]["date"] == "2026-09-13"
    again = _apply(c, sid, p)  # never re-offered, so never applied twice
    assert again.status == 409 and again.payload["error"]["code"] == "recovery_required"
    kinds = [e["kind"] for e in c.result("GET", f"/api/v1/operate/sessions/{sid}/events")["events"]]
    assert "reconciliation_required" in kinds


def test_ambiguous_failure_that_provably_wrote_nothing_returns_to_pending(env, monkeypatch):
    _, app, service, c, sid, rev = env
    p = _preview(c, sid, rev)["proposal"]
    monkeypatch.setattr(app.operate.uses, "apply_move", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    r = _apply(c, sid, p)
    assert r.status == 500
    # RuntimeError is wrapped by the route into 500; the proposal is retryable because the revision is unchanged
    assert app.operate.proposals.get(p["proposal_id"]).status == "pending"


def test_readback_failure_after_a_successful_commit_still_reports_applied(env, monkeypatch):
    _, app, service, c, sid, rev = env
    p = _preview(c, sid, rev)["proposal"]
    monkeypatch.setattr(app.operate.uses, "revision", lambda season: (_ for _ in ()).throw(OSError("read failed")))
    r = _apply(c, sid, p)
    assert r.status == 200 and r.payload["result"]["outcome"] == "applied"
    assert r.payload["result"]["result"]["revision_after"] is None


# 2 ---------------------------------------------------------------------------------------------
def test_state_machine_only_legal_transitions_and_replaced_proposal_never_returns_to_pending(env):
    _, app, _, c, sid, rev = env
    book = ProposalBook()
    i1, i2 = _intent("req-sm-0001", rev), _intent("req-sm-0002", rev, "2026-09-14")
    p1 = book.register(i1, rev, "e", {"after_fingerprint": "a"}, "op", "s")
    book.claim(p1.proposal_id, p1.digest, session_id="s", actor="op")
    p2 = book.register(i2, rev, "e", {"after_fingerprint": "b"}, "op", "s")  # replacement while p1 applies
    assert book.release_unwritten(p1) == "superseded"  # failed apply must not resurrect the older one
    assert p2.status == "pending"
    with pytest.raises(ApiError):
        book.finish_applied(p1)  # illegal: not applying


def test_threaded_failed_apply_vs_replacement_never_leaves_two_pending(env):
    _, _, _, _, _, rev = env
    for n in range(150):
        book = ProposalBook()
        p1 = book.register(_intent("req-th-%04d" % n, rev), rev, "e", {"after_fingerprint": "a"}, "op", "s")
        book.claim(p1.proposal_id, p1.digest, session_id="s", actor="op")
        barrier = threading.Barrier(2)
        out = {}

        def fail():
            barrier.wait()
            out["fail"] = book.release_unwritten(p1)

        def replace():
            barrier.wait()
            out["new"] = book.register(_intent("req-tn-%04d" % n, rev, "2026-09-14"), rev, "e",
                                       {"after_fingerprint": "b"}, "op", "s")

        ts = [threading.Thread(target=fail), threading.Thread(target=replace)]
        [t.start() for t in ts]
        [t.join(5) for t in ts]
        pending = [p for p in book.snapshot() if p.status == "pending"]
        # either order is consistent: p1 pending is immediately superseded by the new one, or p1 is superseded
        assert len(pending) == 1 and pending[0] is out["new"], (n, [p.status for p in book.snapshot()])


def test_end_to_end_failed_apply_racing_a_new_preview_leaves_exactly_one_pending(env, monkeypatch):
    _, app, _, c, sid, rev = env
    p1 = _preview(c, sid, rev)["proposal"]
    entered, proceed = threading.Event(), threading.Event()
    real_check = app.operate.readiness.check
    apply_thread = {}

    def check():
        if threading.current_thread() is apply_thread.get("t"):
            entered.set()
            proceed.wait(5)
            raise ApiError(422, __import__("tournament_scheduler.local_api.contracts", fromlist=["x"]).ErrorCode.REVIEW_REQUIRED, "boom")
        return real_check()

    monkeypatch.setattr(app.operate.readiness, "check", check)
    result = {}

    def do_apply():
        try:
            app.operate.apply_proposal(sid, p1["proposal_id"], p1["digest"], "op")
        except ApiError as exc:
            result["err"] = exc

    t = threading.Thread(target=do_apply)
    apply_thread["t"] = t
    t.start()
    entered.wait(5)
    app.operate.wait_s = 0.1
    second = app.operate.submit(sid, _intent("req-e2e-0002", rev, "2026-09-14"))
    proceed.set()
    t.join(10)
    app.operate.wait_s = 5
    final = app.operate.submit(sid, _intent("req-e2e-0002", rev, "2026-09-14"))
    assert second["outcome"] in ("proposal_pending", "previewed") and final["outcome"] == "previewed"
    pending = [p for p in app.operate.proposals.snapshot() if p.status == "pending"]
    assert len(pending) == 1 and pending[0].proposal_id == final["proposal"]["proposal_id"]


# 3 ---------------------------------------------------------------------------------------------
def test_preview_is_bound_to_the_evidence_revision_it_was_computed_under(env, monkeypatch):
    _, app, service, c, sid, rev = env
    c.result("PUT", "/api/v1/ice-time/sources", MANUAL)
    src = c.result("GET", "/api/v1/ice-time/sources/skien-manual")
    before = app.ice.evidence_revision()
    started, release = threading.Event(), threading.Event()
    real = service.move_tournament

    def paused(**kw):
        started.set()
        release.wait(5)
        return real(**kw)

    monkeypatch.setattr(service, "move_tournament", paused)
    app.operate.wait_s = 0.1
    box = {}
    t1 = threading.Thread(target=lambda: box.update(r=app.operate.submit(sid, _intent("req-ev-0001", rev))))
    t1.start()
    started.wait(5)
    t2 = threading.Thread(target=lambda: box.update(
        put=app.ice.put_manual(dict(MANUAL, expected_revision=src["revision"], windows=[]), "op")))
    t2.start()
    time.sleep(0.3)
    assert t2.is_alive() and "put" not in box  # the evidence update waits for preview + registration
    release.set()
    t1.join(10)
    t2.join(10)
    app.operate.wait_s = 5
    done = app.operate.submit(sid, _intent("req-ev-0001", rev))
    assert done["proposal"]["evidence_revision"] == before  # not the newer revision
    assert _apply(c, sid, done["proposal"]).status == 409  # and apply sees the evidence change


# 4 ---------------------------------------------------------------------------------------------
def test_active_reservations_are_never_evicted(env, monkeypatch):
    _, app, service, c, sid, rev = env
    monkeypatch.setattr(sessions_mod, "MAX_IDEMPOTENT", 3)
    gate = threading.Event()
    real = service.move_tournament
    monkeypatch.setattr(service, "move_tournament", lambda **kw: (gate.wait(5), real(**kw))[1])
    app.operate.wait_s = 0.05
    first = app.operate.submit(sid, _intent("req-cap-0001", rev, "2026-09-13"))
    app.operate.submit(sid, _intent("req-cap-0002", rev, "2026-09-14"))
    app.operate.submit(sid, _intent("req-cap-0003", rev, "2026-09-15"))
    with pytest.raises(ApiError) as err:  # all three active: reject rather than evict one
        app.operate.submit(sid, _intent("req-cap-0004", rev, "2026-09-16"))
    assert err.value.status == 503
    again = app.operate.submit(sid, _intent("req-cap-0001", rev, "2026-09-13"))
    assert again["job"]["job_id"] == first["job"]["job_id"]  # the oldest active request kept its reservation
    gate.set()
    app.operate.wait_s = 5
    assert app.operate.submit(sid, _intent("req-cap-0001", rev, "2026-09-13"))["outcome"] == "previewed"
    app.operate.submit(sid, _intent("req-cap-0004", rev, "2026-09-16"))  # completed ones are evictable now


# 5 ---------------------------------------------------------------------------------------------
def test_cancelled_operation_creates_no_applicable_proposal(env, monkeypatch):
    _, app, service, c, sid, rev = env
    started, release = threading.Event(), threading.Event()
    real = service.move_tournament

    def paused(**kw):
        started.set()
        release.wait(5)
        return real(**kw)

    monkeypatch.setattr(service, "move_tournament", paused)
    app.operate.wait_s = 0.05
    pending = app.operate.submit(sid, _intent("req-can-0001", rev))
    started.wait(5)
    job_id = pending["job"]["job_id"]
    app.operate.cancel_job(job_id)
    release.set()
    done = app.jobs.wait(job_id, 10)
    assert done.status == "cancelled"
    assert app.operate.proposals.snapshot() == []
    app.operate.wait_s = 5
    assert app.operate.submit(sid, _intent("req-can-0001", rev))["reason"] == "cancelled"


def test_cancel_for_job_cancels_pending_proposals_of_that_job_only():
    book = ProposalBook()
    i = OperatorIntent.parse(move_intent(revision="r"))
    a = book.register(i, "r", "e", {"after_fingerprint": "a"}, "op", "s", job_id="j1")
    assert book.cancel_for_job("j1") == 1 and a.status == "cancelled"
    assert book.register(i, "r", "e", {}, "op", "s", still_active=lambda: False) is None


# 6 ---------------------------------------------------------------------------------------------
def _authority():
    return {"issuer": "rink", "scope": "season", "from": (NOW - timedelta(hours=1)).isoformat(),
            "until": (NOW + timedelta(hours=1)).isoformat(), "covers_from": "2026-09-01T00:00:00+02:00",
            "covers_to": "2026-10-01T00:00:00+02:00", "document": "d"}


def test_stale_unreadable_or_missing_required_evidence_is_review_required_not_writable(env):
    ws, app, service, c, sid, rev = env
    assert c.result("GET", "/api/v1/capabilities")["writable"] is True  # no sources: no dependency
    c.result("PUT", "/api/v1/ice-time/sources", dict(MANUAL, authority=_authority()))
    assert c.result("GET", "/api/v1/capabilities")["evidence"]["state"] == "ok"
    app.ice._clock = lambda: NOW + timedelta(days=2)  # freshness bound has passed
    caps = c.result("GET", "/api/v1/capabilities")
    assert caps["writable"] is False and caps["mode"] == "review_required" and caps["evidence"]["state"] == "stale"
    out = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent(revision=rev))
    assert out["outcome"] == "review_required" and out["reason"] == "evidence_stale"
    app.ice._clock = lambda: NOW
    (ws / "ice_time" / "skien-manual.json").write_text("{not json")
    caps = c.result("GET", "/api/v1/capabilities")
    assert caps["writable"] is False and caps["evidence"]["state"] == "unreadable"
    assert c.result("GET", "/api/v1/readiness")["reason_code"] == "inspection_failed"


def test_required_evidence_missing_blocks_writes(env):
    _, app, _, c, sid, rev = env
    app.ice.required = True
    assert c.result("GET", "/api/v1/capabilities")["writable"] is False
    out = c.result("POST", f"/api/v1/operate/sessions/{sid}/intents", move_intent(revision=rev))
    assert out["outcome"] == "review_required" and out["reason"] == "evidence_stale"


# optional ----------------------------------------------------------------------------------------
def test_bootstrap_file_replacement_is_atomic_and_leaves_no_temp(tmp_path):
    from tournament_scheduler.local_api.instance import WorkspaceInstance
    inst = WorkspaceInstance(tmp_path)
    inst.acquire()
    inst.publish(1, "a")
    inst.publish(2, "b")
    assert inst.bootstrap_file.read_text() == "b"
    assert [p.name for p in inst.state_dir.iterdir() if p.name.endswith(".tmp")] == []
    inst.release()


