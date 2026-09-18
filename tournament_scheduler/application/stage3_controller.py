"""Explicit transition engine for one interactive Stage 3 session.

The controller owns *lifecycle*: which transition is legal from the current
session state, whether the submitted action still targets the session's exact
candidate revision/fingerprint, how a successful transition advances the
revision, what the next pending decision is, and when the session is
finalized. It owns no hockey legality and no repair/search selection: a
:class:`Stage3Capabilities` implementation performs the deterministic domain
operation and reports what happened.

Each transition contract is::

    expected run_id + expected candidate revision/fingerprint
        -> validate action against current session state
        -> invoke deterministic domain/application capability
        -> record revision N -> N+1 (or reject stale before recording)
        -> return next DecisionContext or terminal Stage 3 result

The CLI/harness adapter is a thin transport over this boundary; it must not
encode lifecycle semantics with feature-specific fall-through branches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from .decisions import DecisionAction
from .stage3_progress import (
    EMERGENCY_STAGE3_ACTION_LIMIT,
    PROGRESS_SCOPED_ACTIONS,
    action_signature,
    repeated_no_progress_signature,
)
from .stage3_session import (
    SCOPE_CANDIDATE,
    SCOPE_RUN,
    TRANSITION_FOR_ACTION,
    Stage3Session,
    candidate_content_fingerprint,
)


@dataclass
class Stage3CapabilityResult:
    """What a deterministic domain capability did for one transition.

    ``ok=False`` means the operation was rejected by the domain (stale
    option, infeasible repair, invalid candidate ref, ...). The controller
    records nothing for a rejected transition.
    """

    ok: bool
    reason: str = ""
    candidate: dict[str, Any] | None = None
    candidate_fingerprint: str = ""
    candidate_source: str = ""
    candidate_changed: bool = False
    next_context: dict[str, Any] | None = None
    next_capability: str = ""
    next_candidates: list[dict[str, Any]] = field(default_factory=list)
    next_attempt: int | None = None
    next_marker: dict[str, Any] = field(default_factory=dict)
    search_exhausted: bool = False
    final: bool = False
    # Set when the transition is a candidate-scoped ``request_operator``: the
    # session must pause on the exact current candidate revision/fingerprint
    # rather than select, mutate or finalize one.
    operator_request: dict[str, Any] | None = None
    shared_host_decisions: list[dict[str, Any]] | None = None
    arena_decisions: list[dict[str, Any]] | None = None
    unresolved: list[dict[str, Any]] | None = None
    data: dict[str, Any] = field(default_factory=dict)


class Stage3Capabilities(Protocol):
    """Deterministic domain/application operations behind the transitions."""

    def apply(
        self, transition: str, session: Stage3Session, action: DecisionAction
    ) -> Stage3CapabilityResult:
        ...


@dataclass
class Stage3TransitionOutcome:
    accepted: bool
    session: Stage3Session
    transition: str
    reason: str = ""
    context: dict[str, Any] | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Stage3Controller:
    """Applies one :class:`DecisionAction` to one session as an explicit transition."""

    def __init__(self, clock: Any = _now) -> None:
        self._clock = clock

    def handle(
        self,
        session: Stage3Session,
        action: DecisionAction,
        capabilities: Stage3Capabilities,
    ) -> Stage3TransitionOutcome:
        reason = self.validate(session, action)
        if reason:
            return self._reject(session, TRANSITION_FOR_ACTION.get(action.action_id, ""), reason)

        transition = TRANSITION_FOR_ACTION.get(action.action_id)
        if transition is None:
            # ``abort``/``request_operator``-style terminal or non-lifecycle
            # actions are handled by the caller; the controller only applies
            # candidate-revision transitions.
            return self._reject(session, "", f"unsupported_action:{action.action_id}")
        pending = session.pending_decision
        assert pending is not None  # guaranteed by validate()

        result = capabilities.apply(transition, session, action)
        if not result.ok:
            return self._reject(
                session,
                transition,
                result.reason or "capability_rejected",
                findings=list((result.data or {}).get("responsibility_transfers") or []),
            )

        from_revision = session.candidate_revision
        if result.candidate_changed:
            candidate = result.candidate if result.candidate is not None else session.candidate
            fingerprint = result.candidate_fingerprint or _fingerprint(candidate)
            session.advance_candidate(
                candidate,
                fingerprint=fingerprint,
                source=result.candidate_source,
                transition=transition,
                action_id=action.action_id,
                rationale=action.rationale,
                at=self._clock(),
                extra={"detail": result.data} if result.data else None,
            )
        elif result.candidate is not None:
            session.candidate = result.candidate
            if result.candidate_fingerprint:
                session.candidate_fingerprint = result.candidate_fingerprint
            if result.candidate_source:
                session.candidate_source = result.candidate_source

        self._apply_provenance(session, result)

        if result.operator_request is not None:
            # Candidate-scoped escalation: record the operator question and
            # pause, keeping the current candidate revision/fingerprint and the
            # pending decision intact. It must never select or finalize a
            # candidate the operator did not choose.
            session.raise_operator_request(
                question=str(result.operator_request.get("question") or ""),
                rationale=str(result.operator_request.get("rationale") or action.rationale or ""),
                at=self._clock(),
                capability=str(result.operator_request.get("capability") or ""),
            )
            return Stage3TransitionOutcome(True, session, transition)

        if result.final:
            session.finalize(
                transition=transition,
                action_id=action.action_id,
                rationale=action.rationale,
                at=self._clock(),
            )
            return Stage3TransitionOutcome(True, session, transition)

        if not result.candidate_changed:
            # A candidate-changing transition already recorded its own
            # provenance when it advanced the revision.
            session.record_history(
                transition=transition,
                action_id=action.action_id,
                rationale=action.rationale,
                from_revision=from_revision,
                to_revision=session.candidate_revision,
                at=self._clock(),
                extra={"detail": result.data} if result.data else None,
            )

        if result.next_context is not None:
            session.set_pending(
                capability=result.next_capability or str(pending.get("capability") or ""),
                context=result.next_context,
                candidates=result.next_candidates,
                attempt=result.next_attempt,
                search_exhausted=result.search_exhausted,
                marker=result.next_marker,
            )
        else:
            session.pending_decision = None

        return Stage3TransitionOutcome(
            True, session, transition, context=result.next_context
        )

    # -- helpers ----------------------------------------------------------

    def validate(self, session: Stage3Session, action: DecisionAction) -> str:
        """Return a stable rejection reason, or ``""`` when the action is valid.

        This is the lifecycle gate a transport uses before recording any
        accepted-action provenance: a stale revision/fingerprint, a
        transition that is not legal from the current state, or no pending
        decision at all are all rejected here.
        """
        transition = TRANSITION_FOR_ACTION.get(action.action_id)
        if transition is None:
            # ``abort`` and other terminal/non-lifecycle actions do not carry
            # candidate revision scope; the caller/``decide`` owns them.
            return ""
        if session.is_finalized():
            return "session_already_finalized"
        if not session.pending_decision:
            return "no_pending_decision"
        if transition not in session.legal_transitions():
            return f"transition_not_legal:{transition}"
        stale = self._stale_reason(session, action)
        if stale:
            return stale
        breaker = self._circuit_breaker_reason(session, action)
        if breaker:
            return breaker
        repeated = self._repeated_no_progress_reason(session, action)
        if repeated:
            return repeated
        return ""

    def _reject(
        self,
        session: Stage3Session,
        transition: str,
        reason: str,
        findings: list[dict[str, Any]] | None = None,
    ) -> Stage3TransitionOutcome:
        return Stage3TransitionOutcome(
            False, session, transition, reason=reason, findings=list(findings or [])
        )

    def _stale_reason(self, session: Stage3Session, action: DecisionAction) -> str:
        if session.pending_scope() != SCOPE_CANDIDATE:
            # Run-scoped pre-plan decisions stay valid across later candidate
            # revisions while their facts remain valid.
            return ""
        expected_fingerprint = session.pending_fingerprint()
        action_fingerprint = str(action.arguments.get("candidate_fingerprint") or "")
        if expected_fingerprint and action_fingerprint and action_fingerprint != expected_fingerprint:
            return "stale_candidate_fingerprint"
        expected_revision = session.pending_revision()
        action_revision = action.arguments.get("candidate_revision")
        if expected_revision is not None and action_revision is not None:
            try:
                if int(action_revision) != int(expected_revision):
                    return "stale_candidate_revision"
            except (TypeError, ValueError):
                return "invalid_candidate_revision"
        if action.action_id == "apply_candidate" and expected_fingerprint:
            refs = {
                str(entry.get("candidate_ref"))
                for entry in (session.pending_decision or {}).get("candidates") or []
            }
            ref = str(action.arguments.get("candidate_ref") or "")
            if refs and ref not in refs:
                return "unknown_or_stale_candidate_ref"
        return ""

    def _circuit_breaker_reason(self, session: Stage3Session, action: DecisionAction) -> str:
        """Block search/repair only after a generous emergency limit.

        This is a technical safety failure (runaway or broken orchestration),
        not evidence that the schedule is unsolvable, so it never blocks the
        loop terminators (``keep_baseline``/``apply_candidate``/
        ``request_operator``). A raw attempt count is otherwise not a
        continuation gate.
        """
        if action.action_id not in PROGRESS_SCOPED_ACTIONS:
            return ""
        if len(session.search_attempts) >= EMERGENCY_STAGE3_ACTION_LIMIT:
            return "emergency_circuit_breaker:runaway_orchestration"
        return ""

    def _repeated_no_progress_reason(self, session: Stage3Session, action: DecisionAction) -> str:
        """Reject an identical action against the same candidate that already
        produced no progress, while leaving a different strategy available."""
        if action.action_id not in PROGRESS_SCOPED_ACTIONS:
            return ""
        fingerprint = session.pending_fingerprint() or session.candidate_fingerprint
        if repeated_no_progress_signature(
            session.search_attempts,
            action_id=action.action_id,
            signature=action_signature(action.action_id, action.arguments),
            candidate_fingerprint=fingerprint,
        ):
            return "repeated_no_progress_action"
        return ""

    def _apply_provenance(self, session: Stage3Session, result: Stage3CapabilityResult) -> None:
        if result.shared_host_decisions is not None:
            session.shared_host_decisions = list(result.shared_host_decisions)
        if result.arena_decisions is not None:
            session.arena_decisions = list(result.arena_decisions)
        if result.unresolved is not None:
            # Run-scoped shared-host asks and candidate-scoped arena asks are
            # kept in separate buckets so neither view can leak into the
            # other's unresolved list.
            if session.pending_scope() == SCOPE_RUN:
                session.shared_host_unresolved = list(result.unresolved)
            else:
                session.arena_unresolved = list(result.unresolved)


def _fingerprint(candidate: Mapping[str, Any] | None) -> str:
    if candidate is None:
        return ""
    return candidate_content_fingerprint(candidate)


def handle_stage3_action(
    session: Stage3Session,
    action: DecisionAction,
    capabilities: Stage3Capabilities,
    *,
    clock: Any = _now,
) -> Stage3TransitionOutcome:
    return Stage3Controller(clock=clock).handle(session, action, capabilities)
