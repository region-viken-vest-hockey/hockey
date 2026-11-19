"""Round-2 regressions: single proposal state machine, ambiguous applies, evidence gate, cancel, evidence policy."""

from __future__ import annotations

import os
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


@pytest.mark.parametrize("make", [
    lambda: RuntimeError("unknown"), lambda: OSError("disk full during swap"),
    lambda: __import__("tournament_scheduler.infrastructure.canonical_season_store", fromlist=["x"])
    .CanonicalCommitDurabilityError("durability uncertain"),
    lambda: __import__("tournament_scheduler.application.canonical_season.shared", fromlist=["x"])
    .SeasonStateError("validation-looking refusal"),
])
def test_any_untyped_failure_after_the_commit_boundary_is_recovery_required_not_retryable(env, monkeypatch, make):
    """Unchanged revision is NOT proof of no write: only the service's typed pre-write errors may release."""
    _, app, service, c, sid, rev = env
    p = _preview(c, sid, rev)["proposal"]

    def boom(**kw):
        if not kw.get("dry_run"):
            raise make()
        return real(**kw)

    real = service.move_tournament
    monkeypatch.setattr(service, "move_tournament", boom)
    r = _apply(c, sid, p)
    assert r.status == 409 and r.payload["error"]["code"] == "recovery_required"
    assert app.operate.proposals.get(p["proposal_id"]).status == "recovery_required"
    monkeypatch.undo()
    again = _apply(c, sid, p)
    assert again.status == 409 and again.payload["error"]["code"] == "recovery_required"  # not retryable


def test_typed_no_write_rejections_are_the_only_ones_that_release_or_stale(env, monkeypatch):
    from tournament_scheduler.distribution_update import WriteBlockedError
    from tournament_scheduler.infrastructure.canonical_season_store import CanonicalRevisionConflictError
    _, app, service, c, sid, rev = env
    p = _preview(c, sid, rev)["proposal"]
    real = service.move_tournament

    def raiser(exc):
        def f(**kw):
            if kw.get("dry_run"):
                return real(**kw)
            raise exc
        return f

    monkeypatch.setattr(service, "move_tournament", raiser(WriteBlockedError("blocked by manifest")))
    r = _apply(c, sid, p)
    assert r.status == 403 and r.payload["error"]["code"] == "read_only"
    assert app.operate.proposals.get(p["proposal_id"]).status == "pending"  # gate runs before any work
    monkeypatch.setattr(service, "move_tournament",
                        raiser(CanonicalRevisionConflictError(expected_revision=rev, current_revision="x")))
    r = _apply(c, sid, p)
    assert r.status == 409 and r.payload["error"]["code"] == "stale_revision"
    assert app.operate.proposals.get(p["proposal_id"]).status == "stale"


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


def test_cancel_after_registration_loses_job_succeeds_and_proposal_stays_pending(env, monkeypatch):
    """Barrier fires cancel between registration and worker return: cancel is 'too late', one terminal state."""
    _, app, service, c, sid, rev = env
    box = {}
    real_register = app.operate.proposals.register

    def register_then_cancel(*a, **kw):
        prop = real_register(*a, **kw)
        t = threading.Thread(target=lambda: box.update(cancel=app.operate.cancel_job(kw["job_id"])))
        t.start()
        t.join(5)
        return prop

    monkeypatch.setattr(app.operate.proposals, "register", register_then_cancel)
    out = app.operate.submit(sid, _intent("req-cr-0001", rev))
    assert box["cancel"]["cancel"] == "too_late"
    assert out["outcome"] == "previewed" and out["job"]["status"] == "succeeded"
    (prop,) = app.operate.proposals.snapshot()
    assert prop.status == "pending" and _apply(c, sid, out["proposal"]).status == 200


def test_cancel_before_registration_wins_job_cancelled_and_no_proposal(env, monkeypatch):
    _, app, service, c, sid, rev = env
    real_preview = app.operate.uses.preview_move
    box = {}

    def preview_then_cancel(intent):
        result = real_preview(intent)
        job_id = app.jobs._jobs and next(iter(app.jobs._jobs))
        box["cancel"] = app.operate.cancel_job(job_id)  # fires after preview, before registration
        return result

    monkeypatch.setattr(app.operate.uses, "preview_move", preview_then_cancel)
    out = app.operate.submit(sid, _intent("req-cr-0002", rev))
    assert box["cancel"]["cancel"] == "requested"
    assert out["outcome"] == "review_required" and out["reason"] == "cancelled"
    assert out["job"]["status"] == "cancelled" and app.operate.proposals.snapshot() == []


def test_try_commit_is_the_single_boundary_even_under_a_cancel_race():
    from tournament_scheduler.local_api.jobs import JobManager
    for _ in range(100):
        jm = JobManager()
        go, hold, seen = threading.Event(), threading.Event(), {}

        def work(ctx):
            go.set()
            hold.wait(5)
            seen["committed"] = ctx.try_commit()
            return "r"

        job = jm.submit("t", "req-tc-00001", work)
        go.wait(5)
        t = threading.Thread(target=lambda: seen.update(cancel=jm.cancel(job.job_id)[1]))
        t.start()
        hold.set()
        t.join(5)
        done = jm.wait(job.job_id, 5)
        assert (done.status == "succeeded") == seen["committed"]  # never succeeded-and-cancelled or neither
        assert seen["cancel"] != seen["committed"]  # exactly one side wins
        jm.shutdown()


# 3 (round 3) ---------------------------------------------------------------------------------
def test_evicted_request_ids_are_tombstoned_never_reused_as_new(env, monkeypatch):
    _, app, service, c, sid, rev = env
    monkeypatch.setattr(sessions_mod, "MAX_IDEMPOTENT", 2)
    calls = []
    real = service.move_tournament
    monkeypatch.setattr(service, "move_tournament", lambda **kw: (calls.append(kw), real(**kw))[1])
    for n, d in enumerate(("2026-09-13", "2026-09-14", "2026-09-15", "2026-09-16"), 1):
        app.operate.submit(sid, _intent(f"req-ts-000{n}", rev, d))
    s = app.operate.session(sid)
    assert "req-ts-0001" not in s.seen and "req-ts-0001" in s.tombstones
    before = len(calls)
    with pytest.raises(ApiError) as err:
        app.operate.submit(sid, _intent("req-ts-0001", rev, "2026-09-13"))
    assert err.value.status == 410 and err.value.code.value == "request_id_expired"
    assert err.value.details["recorded_outcome"] == "previewed"
    with pytest.raises(ApiError) as other:  # same id, different body: still a reuse conflict
        app.operate.submit(sid, _intent("req-ts-0001", rev, "2026-09-20"))
    assert other.value.code.value == "request_id_reused"
    assert len(calls) == before  # no fresh job, no new preview


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




# 4 (round 3) ---------------------------------------------------------------------------------
def test_launcher_failure_during_bootstrap_publish_releases_lock_and_removes_files(tmp_path, monkeypatch):
    from tournament_scheduler.local_api import launcher
    from tournament_scheduler.local_api.instance import WorkspaceInstance
    ws = make_workspace(tmp_path)

    def failing_publish(self, port, token):
        self.bootstrap_file.write_text("partial")
        raise OSError("disk full")

    monkeypatch.setattr(WorkspaceInstance, "publish", failing_publish)
    with pytest.raises(OSError):
        launcher.run(ws, SEASON, open_browser=False)
    probe = WorkspaceInstance(ws)
    probe.acquire()  # the lock was released
    assert not probe.bootstrap_file.exists()
    probe.release()


def test_launcher_failure_while_building_the_app_releases_the_instance(tmp_path, monkeypatch):
    from tournament_scheduler.local_api import launcher
    from tournament_scheduler.local_api.instance import WorkspaceInstance
    ws = make_workspace(tmp_path)
    monkeypatch.setattr(launcher, "build_app", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("init failed")))
    with pytest.raises(RuntimeError):
        launcher.run(ws, SEASON, open_browser=False)
    probe = WorkspaceInstance(ws)
    probe.acquire()
    probe.release()


def test_publish_failure_removes_the_temp_bootstrap_file(tmp_path, monkeypatch):
    from tournament_scheduler.local_api.instance import WorkspaceInstance
    inst = WorkspaceInstance(tmp_path)
    inst.acquire()
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("rename failed")))
    with pytest.raises(OSError):
        inst.publish(1, "tok")
    assert not list(inst.state_dir.glob("*.tmp"))
    monkeypatch.undo()
    inst.release()


def test_readiness_detail_keeps_source_specific_reasons(env):
    ws, app, _, c, sid, rev = env
    c.result("PUT", "/api/v1/ice-time/sources", dict(MANUAL, authority=_authority()))
    app.ice._clock = lambda: NOW + timedelta(days=2)
    detail = c.result("GET", "/api/v1/readiness")["detail"]
    assert "skien-manual (stale)" in detail
