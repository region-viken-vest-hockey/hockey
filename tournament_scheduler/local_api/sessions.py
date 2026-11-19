"""``operate`` sessions: one intent ingress, routing, previews, events, human-confirmed apply.

The ingress validates a typed ``OperatorIntent`` (already parsed by the contract)
*before* any routing, is idempotent per ``request_id``, and returns one of the
distinct outcomes. Only ``apply_proposal`` writes, and only for a proposal this
module's own preview produced and a human confirmed by digest.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Callable, Mapping

from tournament_scheduler.application.ports.readiness import ReadinessStatus

from .contracts import ApiError, ErrorCode, Gesture, OperatorIntent, Outcome, Phase
from .jobs import JobContext, JobManager
from .proposals import ProposalBook
from .readiness import ReadinessCheck
from .tools import IntentRouterPort, ToolRegistry
from .usecases import SeasonUseCases, verdict_is_clean

MAX_EVENTS = 500
MAX_SESSIONS = 32
MAX_IDEMPOTENT = 200


class Reservation:
    """Atomic claim on one request_id: exactly one caller routes; repeats share its outcome."""

    def __init__(self, digest: tuple) -> None:
        self.digest = digest
        self.started = threading.Event()
        self.lock = threading.Lock()
        self.job_id: str | None = None
        self.result: dict[str, Any] | None = None
        self.error: ApiError | None = None


class OperateSession:
    def __init__(self, season: str, operator: str) -> None:
        self.session_id = uuid.uuid4().hex
        self.season, self.operator = season, operator
        self.phase = Phase.RECEIVED
        self.events: list[dict[str, Any]] = []
        self.seen: dict[str, Reservation] = {}
        self.lock = threading.Lock()

    def emit(self, kind: str, data: Mapping[str, Any]) -> None:
        with self.lock:
            self.events.append({"seq": len(self.events) + 1, "kind": kind, "data": dict(data)})
            del self.events[:-MAX_EVENTS]


class OperateService:
    def __init__(self, uses: SeasonUseCases, jobs: JobManager, readiness: ReadinessCheck,
                 tools: ToolRegistry, router: IntentRouterPort | None = None,
                 evidence_revision: Callable[[], str] = lambda: "none", wait_s: float = 5.0,
                 commit_gate: Any = None) -> None:
        self.uses, self.jobs, self.readiness, self.tools = uses, jobs, readiness, tools
        self.router, self.evidence_revision, self.wait_s = router, evidence_revision, wait_s
        # One boundary serializes evidence changes with canonical commits (shared with IceTimeEvidence).
        self.commit_gate = commit_gate or threading.RLock()
        self.proposals = ProposalBook()
        self._sessions: dict[str, OperateSession] = {}
        self._lock = threading.Lock()

    def create(self, season: str, operator: str) -> dict[str, Any]:
        report = self.readiness.check()
        with self._lock:
            if len(self._sessions) >= MAX_SESSIONS:
                raise ApiError(503, ErrorCode.REVIEW_REQUIRED, "too many operate sessions")
            session = OperateSession(season, operator)
            self._sessions[session.session_id] = session
        session.emit("readiness", report.to_dict())
        return {"session_id": session.session_id, "season": season, "phase": session.phase.value,
                "readiness": report.to_dict(), "revision": self._safe_revision(season)}

    def _safe_revision(self, season: str) -> str | None:
        try:
            return self.uses.revision(season)
        except ApiError:
            return None

    def session(self, session_id: str) -> OperateSession:
        s = self._sessions.get(session_id)
        if s is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "unknown operate session")
        return s

    def events(self, session_id: str, after: int = 0) -> dict[str, Any]:
        s = self.session(session_id)
        with s.lock:
            return {"phase": s.phase.value, "events": [e for e in s.events if e["seq"] > after]}

    # -- intent ingress ----------------------------------------------------
    def submit(self, session_id: str, intent: OperatorIntent) -> dict[str, Any]:
        s = self.session(session_id)
        if intent.season != s.season:
            raise ApiError(400, ErrorCode.BAD_REQUEST, "intent season differs from the session season")
        digest = intent.body_digest_fields()
        with s.lock:  # atomic reservation: check and claim happen under one lock
            res = s.seen.get(intent.request_id)
            owner = res is None
            if owner:
                res = s.seen[intent.request_id] = Reservation(digest)
                while len(s.seen) > MAX_IDEMPOTENT:
                    s.seen.pop(next(iter(s.seen)))
            elif res.digest != digest:
                raise ApiError(409, ErrorCode.REQUEST_ID_REUSED, "request_id was used for a different intent")
        if owner:
            try:
                self._start(s, intent, res)
            except ApiError as exc:
                res.error = exc
                with s.lock:
                    s.seen.pop(intent.request_id, None)  # a failed ingress may be retried
                raise
            finally:
                res.started.set()
        else:
            res.started.wait(self.wait_s + 5)
            if res.error is not None:
                raise res.error
        return self._resolve(s, res)

    def _resolve(self, s: OperateSession, res: Reservation) -> dict[str, Any]:
        with res.lock:
            if res.result is not None:
                return res.result
            if res.job_id is None:
                raise ApiError(503, ErrorCode.REVIEW_REQUIRED, "request is still being routed")
            done = self.jobs.wait(res.job_id, self.wait_s)
            if done.status in ("queued", "running"):
                return {"outcome": Outcome.PROPOSAL_PENDING.value, "job": done.view()}
            res.result = self._finish(s, done)
            return res.result

    def _blocked(self, s: OperateSession, outcome: Outcome, reason: str, **extra: Any) -> dict[str, Any]:
        s.phase = Phase.DONE
        s.emit("outcome", {"outcome": outcome.value, "reason": reason})
        return {"outcome": outcome.value, "reason": reason, **extra}

    def _start(self, s: OperateSession, intent: OperatorIntent, res: Reservation) -> None:
        report = self.readiness.check()
        if report.status is not ReadinessStatus.READY:
            res.result = self._blocked(s, Outcome.REVIEW_REQUIRED, report.reason_code.value,
                                       readiness=report.to_dict())
            return
        caps = self.uses.capabilities(s.season, s.operator, self.router is not None)
        if not caps["writable"]:
            res.result = self._blocked(s, Outcome.BLOCKED, "season_sealed_read_only")
            return
        if intent.gesture is Gesture.TEXT and self.router is None:
            res.result = self._blocked(s, Outcome.REVIEW_REQUIRED, "no_provider_configured")
            return
        s.phase = Phase.ROUTING
        res.job_id = self.jobs.submit("operate_intent", intent.request_id,
                                      lambda ctx: self._work(s, intent, ctx)).job_id

    def _finish(self, s: OperateSession, done) -> dict[str, Any]:
        if done.status == "cancelled":
            s.phase = Phase.CANCELLED
            return {"outcome": Outcome.REVIEW_REQUIRED.value, "reason": "cancelled", "job": done.view()}
        if done.status == "failed":
            code = (done.error or {}).get("code")
            outcome = Outcome.STALE if code == ErrorCode.STALE_REVISION.value else (
                Outcome.BLOCKED if code in (ErrorCode.READ_ONLY.value,) else Outcome.REVIEW_REQUIRED)
            s.phase = Phase.FAILED
            s.emit("error", done.error or {})
            return {"outcome": outcome.value, "error": done.error, "job": done.view()}
        return {**done.result, "job": done.view()}

    def _work(self, s: OperateSession, intent: OperatorIntent, ctx: JobContext) -> dict[str, Any]:
        if intent.gesture is Gesture.TEXT:
            ctx.report("routing", 0.1)
            decision = self.router.route(intent, self.tools, s.emit)  # type: ignore[union-attr]
            for f in decision.findings:
                s.emit("finding", f)
            if decision.intent is None:
                reason = decision.clarification or decision.unsupported or "router_returned_no_intent"
                s.emit("clarification" if decision.clarification else "unsupported", {"message": reason})
                return {"outcome": Outcome.REVIEW_REQUIRED.value, "reason": reason,
                        "clarification": decision.clarification, "unsupported": decision.unsupported}
            intent = decision.intent
            if intent.gesture is Gesture.TEXT or intent.season != s.season:
                raise ApiError(422, ErrorCode.REVIEW_REQUIRED, "router must return a structured intent for this season")
        ctx.report("previewing", 0.5)
        s.phase = Phase.PREVIEWING
        preview = self.uses.preview_move(intent)
        ctx.checkpoint()
        proposal = self.proposals.add(intent, preview["revision"], self.evidence_revision(), preview, s.operator,
                                     s.session_id)
        clean = verdict_is_clean(preview["verdict"])
        outcome = Outcome.PREVIEWED if clean else Outcome.BLOCKED
        s.phase = Phase.AWAITING_APPROVAL if clean else Phase.DONE
        s.emit("proposal", {"proposal_id": proposal.proposal_id, "outcome": outcome.value,
                            "change_table": preview["change_table"]})
        return {"outcome": outcome.value, "proposal": proposal.view()}

    # -- human-confirmed apply ----------------------------------------------
    def apply_proposal(self, session_id: str, proposal_id: str, confirm: str, actor: str) -> dict[str, Any]:
        s = self.session(session_id)
        if s.operator != actor:
            raise ApiError(403, ErrorCode.FORBIDDEN, "session belongs to a different operator")
        claimed = self.proposals.claim(proposal_id, confirm, session_id=session_id, actor=actor)
        s.phase = Phase.APPLYING
        try:
            if not verdict_is_clean(claimed.preview["verdict"]):
                raise ApiError(422, ErrorCode.REVIEW_REQUIRED, "a blocked proposal cannot be applied")
            with self.commit_gate:  # evidence changes cannot interleave between the checks and the commit
                report = self.readiness.check()
                if report.status is not ReadinessStatus.READY:
                    raise ApiError(422, ErrorCode.REVIEW_REQUIRED, "readiness no longer verified",
                                   reason=report.reason_code.value)
                if not self.uses.capabilities(s.season, s.operator, self.router is not None)["writable"]:
                    raise ApiError(403, ErrorCode.READ_ONLY, "season is no longer writable")
                if claimed.evidence_revision != self.evidence_revision():
                    raise ApiError(409, ErrorCode.STALE_REVISION, "ice-time evidence changed since the preview")
                result = self.uses.apply_move(claimed.intent, expected_revision=claimed.revision, actor=actor)
        except ApiError as exc:
            claimed.status = "stale" if exc.code is ErrorCode.STALE_REVISION else "pending"
            s.phase = Phase.FAILED
            raise
        except BaseException:
            claimed.status = "pending"
            raise
        claimed.status = "applied"
        s.phase = Phase.DONE
        s.emit("applied", result)
        return {"outcome": Outcome.APPLIED.value, "result": result}

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        return self.jobs.cancel(job_id).view()


