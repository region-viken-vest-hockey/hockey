"""Hermetic lifecycle tests for the explicit Stage 3 transition controller.

These use synthetic domain capabilities only: no planner, optimizer or
verifier internals are loaded, which is the point of the session/controller
boundary (domain capability decides legality; the controller decides
lifecycle/revision/persistence).
"""

from __future__ import annotations


from tournament_scheduler.application.decisions import DecisionAction
from tournament_scheduler.application.stage3_controller import (
    Stage3CapabilityResult,
    Stage3Controller,
)
from tournament_scheduler.application.stage3_session import (
    SCOPE_CANDIDATE,
    SCOPE_RUN,
    STATUS_AWAITING_OPERATOR,
    STATUS_FINALIZED,
    Stage3Session,
    candidate_content_fingerprint,
)


def _candidate(seed: int) -> dict:
    return {"tournaments": [{"id": f"t{seed}", "date": "2026-10-05"}]}


def _plan(seed: int) -> dict:
    return {"plan": _candidate(seed)}


def _context(capability: str, *, fingerprint: str) -> dict:
    return {
        "capability": capability,
        "facts": {"candidate_fingerprint": fingerprint},
        "available_actions": ["keep_baseline", "optimize_plan", "apply_repair_option", "apply_candidate"],
    }


class _FakeCapabilities:
    """Returns a pre-programmed result per transition and records calls."""

    def __init__(self, results: dict[str, Stage3CapabilityResult]) -> None:
        self.results = results
        self.calls: list[tuple[str, DecisionAction]] = []

    def apply(self, transition: str, session: Stage3Session, action: DecisionAction) -> Stage3CapabilityResult:
        self.calls.append((transition, action))
        return self.results[transition]


def _session_with_candidate(seed: int = 1) -> Stage3Session:
    session = Stage3Session(run_id="run-1")
    session.candidate = _plan(seed)
    session.candidate_fingerprint = candidate_content_fingerprint(_candidate(seed))
    session.candidate_revision = 1
    session.set_pending(
        capability="stage3_interactive",
        context=_context("stage3_interactive", fingerprint=session.candidate_fingerprint),
        candidates=[{"candidate": _plan(seed), "candidate_ref": "stage3_interactive:attempt_1"}],
        attempt=1,
    )
    return session


class TestCandidateChangingTransitions:
    def test_search_produces_new_revision_and_records_history(self):
        session = _session_with_candidate(1)
        new_body = _candidate(2)
        new_fp = candidate_content_fingerprint(new_body)
        capabilities = _FakeCapabilities(
            {
                "run_search": Stage3CapabilityResult(
                    ok=True,
                    candidate=_plan(2),
                    candidate_fingerprint=new_fp,
                    candidate_source="stage3_optimizer",
                    candidate_changed=True,
                    next_context=_context("stage3_optimize", fingerprint=new_fp),
                    next_capability="stage3_optimize",
                    next_candidates=[{"candidate": _plan(2), "candidate_ref": "stage3_interactive:attempt_2"}],
                    next_attempt=2,
                )
            }
        )

        outcome = Stage3Controller(clock=lambda: "T").handle(
            session,
            DecisionAction(action_id="optimize_plan", rationale="try again"),
            capabilities,
        )

        assert outcome.accepted is True
        assert session.candidate_revision == 2
        assert session.candidate_fingerprint == new_fp
        assert session.pending_scope() == SCOPE_CANDIDATE
        assert session.pending_revision() == 2
        history = session.decision_history[-1]
        assert history["transition"] == "run_search"
        assert history["from_revision"] == 1
        assert history["to_revision"] == 2

    def test_repair_is_local_and_finalizes_on_the_new_revision(self):
        session = _session_with_candidate(1)
        repaired_body = _candidate(3)
        repaired_fp = candidate_content_fingerprint(repaired_body)
        capabilities = _FakeCapabilities(
            {
                "apply_repair": Stage3CapabilityResult(
                    ok=True,
                    candidate=_plan(3),
                    candidate_fingerprint=repaired_fp,
                    candidate_source="host_team_missing_repair_applied",
                    candidate_changed=True,
                    final=True,
                )
            }
        )

        outcome = Stage3Controller(clock=lambda: "T").handle(
            session,
            DecisionAction(
                action_id="apply_repair_option",
                arguments={"option_id": "opt-1", "candidate_fingerprint": session.candidate_fingerprint},
                rationale="apply verified repair",
            ),
            capabilities,
        )

        assert outcome.accepted is True
        assert session.candidate_revision == 2
        assert session.status == STATUS_FINALIZED
        assert session.finalized_revision == 2
        assert session.finalized_fingerprint == repaired_fp
        assert session.legal_transitions() == []

    def test_keep_baseline_finalizes_at_the_authoritative_plan(self):
        session = _session_with_candidate(1)
        session.attempts = {"best_plan": _plan(1), "best_attempt": 1}
        baseline_fp = candidate_content_fingerprint(_candidate(1))
        capabilities = _FakeCapabilities(
            {
                "keep_baseline": Stage3CapabilityResult(
                    ok=True,
                    candidate=_plan(1),
                    candidate_fingerprint=baseline_fp,
                    candidate_source="baseline",
                    candidate_changed=False,
                    final=True,
                )
            }
        )

        outcome = Stage3Controller(clock=lambda: "T").handle(
            session, DecisionAction(action_id="keep_baseline", rationale="not better"), capabilities
        )

        assert outcome.accepted is True
        assert session.is_finalized()
        assert session.finalized_fingerprint == baseline_fp


class TestStaleRejection:
    def test_stale_fingerprint_rejected_before_provenance_recorded(self):
        session = _session_with_candidate(1)
        capabilities = _FakeCapabilities(
            {"apply_repair": Stage3CapabilityResult(ok=True, candidate=_plan(9), candidate_changed=True)}
        )

        outcome = Stage3Controller(clock=lambda: "T").handle(
            session,
            DecisionAction(
                action_id="apply_repair_option",
                arguments={"option_id": "opt-1", "candidate_fingerprint": "stale-fingerprint"},
            ),
            capabilities,
        )

        assert outcome.accepted is False
        assert outcome.reason == "stale_candidate_fingerprint"
        assert capabilities.calls == []
        assert session.decision_history == []
        assert session.candidate_revision == 1

    def test_stale_revision_rejected(self):
        session = _session_with_candidate(1)
        capabilities = _FakeCapabilities(
            {"keep_baseline": Stage3CapabilityResult(ok=True, candidate=_plan(1), final=True)}
        )

        outcome = Stage3Controller(clock=lambda: "T").handle(
            session,
            DecisionAction(action_id="keep_baseline", arguments={"candidate_revision": 99}),
            capabilities,
        )

        assert outcome.accepted is False
        assert outcome.reason == "stale_candidate_revision"
        assert capabilities.calls == []

    def test_unknown_candidate_ref_rejected(self):
        session = _session_with_candidate(1)
        capabilities = _FakeCapabilities(
            {"select_candidate": Stage3CapabilityResult(ok=True, candidate=_plan(2), final=True)}
        )

        outcome = Stage3Controller(clock=lambda: "T").handle(
            session,
            DecisionAction(action_id="apply_candidate", arguments={"candidate_ref": "pareto:9:9"}),
            capabilities,
        )

        assert outcome.accepted is False
        assert outcome.reason == "unknown_or_stale_candidate_ref"
        assert capabilities.calls == []

    def test_capability_rejection_records_nothing(self):
        session = _session_with_candidate(1)
        capabilities = _FakeCapabilities(
            {"apply_repair": Stage3CapabilityResult(ok=False, reason="unknown_or_stale_option")}
        )

        outcome = Stage3Controller(clock=lambda: "T").handle(
            session,
            DecisionAction(
                action_id="apply_repair_option",
                arguments={"option_id": "opt-1", "candidate_fingerprint": session.candidate_fingerprint},
            ),
            capabilities,
        )

        assert outcome.accepted is False
        assert outcome.reason == "unknown_or_stale_option"
        assert session.candidate_revision == 1
        assert session.decision_history == []


class TestRunScopedDecisions:
    def test_shared_host_decision_is_not_invalidated_by_later_revision(self):
        # A run-scoped shared-host decision stays valid across later candidate
        # revisions; a candidate-scoped action is what carries revision scope.
        session = _session_with_candidate(1)
        session.shared_host_decisions = [
            {"registration": "A/B", "age_group": "U10", "chosen_club": "A"}
        ]
        new_body = _candidate(2)
        capabilities = _FakeCapabilities(
            {
                "run_search": Stage3CapabilityResult(
                    ok=True,
                    candidate=_plan(2),
                    candidate_fingerprint=candidate_content_fingerprint(new_body),
                    candidate_changed=True,
                    next_context=_context(
                        "stage3_optimize", fingerprint=candidate_content_fingerprint(new_body)
                    ),
                    next_capability="stage3_optimize",
                )
            }
        )

        Stage3Controller(clock=lambda: "T").handle(
            session, DecisionAction(action_id="optimize_plan"), capabilities
        )

        assert session.shared_host_decisions[0]["chosen_club"] == "A"

    def test_run_scoped_pending_ignores_candidate_fingerprint(self):
        session = Stage3Session(run_id="run-1")
        session.candidate = _plan(1)
        session.candidate_fingerprint = candidate_content_fingerprint(_candidate(1))
        session.set_pending(
            capability="shared_host_assignment",
            context=_context("shared_host_assignment", fingerprint="old"),
            scope=SCOPE_RUN,
        )
        capabilities = _FakeCapabilities(
            {
                "assign_shared_host": Stage3CapabilityResult(
                    ok=True,
                    shared_host_decisions=[{"registration": "A/B", "age_group": "U10", "chosen_club": "A"}],
                    next_context=None,
                )
            }
        )

        outcome = Stage3Controller(clock=lambda: "T").handle(
            session,
            DecisionAction(
                action_id="assign_shared_host",
                arguments={"chosen_club": "A", "candidate_fingerprint": "old"},
            ),
            capabilities,
        )

        assert outcome.accepted is True
        assert session.pending_decision is None
        assert session.shared_host_decisions[0]["chosen_club"] == "A"


class TestSearchExhaustion:
    def test_optimize_plan_not_legal_when_search_exhausted(self):
        session = _session_with_candidate(1)
        session.pending_decision["search_exhausted"] = True
        capabilities = _FakeCapabilities(
            {"run_search": Stage3CapabilityResult(ok=True, candidate=_plan(2), candidate_changed=True)}
        )

        outcome = Stage3Controller(clock=lambda: "T").handle(
            session, DecisionAction(action_id="optimize_plan"), capabilities
        )

        assert outcome.accepted is False
        assert outcome.reason == "transition_not_legal:run_search"
        assert "run_search" not in session.legal_transitions()


class TestContinuationPolicy:
    """A raw attempt count is not a continuation gate.

    The controller bounds *repeated no-progress* actions against the exact
    same candidate and keeps a generous emergency-only circuit breaker as a
    technical safety failure; it never removes ``optimize_plan`` merely
    because a small number of attempts were used.
    """

    def _attempt(
        self,
        *,
        revision: int,
        signature: str,
        fingerprint: str,
        progress: bool,
        action_id: str = "optimize_plan",
    ) -> dict:
        return {
            "action_id": action_id,
            "action_signature": signature,
            "candidate_revision": revision,
            "candidate_fingerprint": fingerprint,
            "transition": "run_search",
            "progress": progress,
            "hard_violations": 0,
            "at": "T",
        }

    def test_distinct_strategies_remain_available_after_many_actions(self):
        from tournament_scheduler.application.stage3_progress import action_signature

        session = _session_with_candidate(1)
        fingerprint = session.candidate_fingerprint
        for index in range(5):
            session.search_attempts.append(
                self._attempt(
                    revision=index + 1,
                    signature=action_signature("optimize_plan", {"seed": index}),
                    fingerprint=fingerprint,
                    progress=False,
                )
            )

        # A genuinely different search scope is still legal after five
        # attempts, exactly because a raw count is not a gate.
        reason = Stage3Controller().validate(
            session,
            DecisionAction(action_id="optimize_plan", arguments={"seed": 999}),
        )
        assert reason == ""
        assert "run_search" in session.legal_transitions()

    def test_repeated_identical_no_progress_action_is_rejected(self):
        from tournament_scheduler.application.stage3_progress import action_signature

        session = _session_with_candidate(1)
        fingerprint = session.candidate_fingerprint
        arguments = {"seed": 7, "iterations": 500}
        session.search_attempts.append(
            self._attempt(
                revision=1,
                signature=action_signature("optimize_plan", arguments),
                fingerprint=fingerprint,
                progress=False,
            )
        )

        reason = Stage3Controller().validate(
            session,
            DecisionAction(
                action_id="optimize_plan",
                arguments={"seed": 7, "iterations": 500},
            ),
        )
        assert reason == "repeated_no_progress_action"

    def test_repeated_action_is_allowed_against_a_different_candidate(self):
        from tournament_scheduler.application.stage3_progress import action_signature

        session = _session_with_candidate(1)
        arguments = {"seed": 7}
        session.search_attempts.append(
            self._attempt(
                revision=1,
                signature=action_signature("optimize_plan", arguments),
                fingerprint="some-old-fingerprint",
                progress=False,
            )
        )

        assert (
            Stage3Controller().validate(
                session, DecisionAction(action_id="optimize_plan", arguments={"seed": 7})
            )
            == ""
        )

    def test_repeated_action_that_made_progress_is_allowed(self):
        from tournament_scheduler.application.stage3_progress import action_signature

        session = _session_with_candidate(1)
        fingerprint = session.candidate_fingerprint
        arguments = {"seed": 7}
        session.search_attempts.append(
            self._attempt(
                revision=1,
                signature=action_signature("optimize_plan", arguments),
                fingerprint=fingerprint,
                progress=True,
            )
        )

        assert (
            Stage3Controller().validate(
                session, DecisionAction(action_id="optimize_plan", arguments={"seed": 7})
            )
            == ""
        )

    def test_stale_rejection_still_takes_precedence_over_repeat_check(self):
        from tournament_scheduler.application.stage3_progress import action_signature

        session = _session_with_candidate(1)
        fingerprint = session.candidate_fingerprint
        arguments = {"candidate_fingerprint": "stale-fingerprint", "seed": 7}
        session.search_attempts.append(
            self._attempt(
                revision=1,
                signature=action_signature("optimize_plan", arguments),
                fingerprint=fingerprint,
                progress=False,
            )
        )

        reason = Stage3Controller().validate(
            session,
            DecisionAction(action_id="optimize_plan", arguments=dict(arguments)),
        )
        assert reason == "stale_candidate_fingerprint"

    def test_emergency_circuit_breaker_bounds_search_but_not_terminators(self):
        from tournament_scheduler.application.stage3_progress import EMERGENCY_STAGE3_ACTION_LIMIT

        session = _session_with_candidate(1)
        session.search_attempts = [
            self._attempt(
                revision=index + 1,
                signature=f"sig-{index}",
                fingerprint=session.candidate_fingerprint,
                progress=True,
            )
            for index in range(EMERGENCY_STAGE3_ACTION_LIMIT)
        ]
        capabilities = _FakeCapabilities(
            {
                "run_search": Stage3CapabilityResult(ok=True, candidate=_plan(2), candidate_changed=True),
                "keep_baseline": Stage3CapabilityResult(ok=True, candidate=_plan(1), final=True),
            }
        )

        blocked = Stage3Controller().validate(
            session, DecisionAction(action_id="optimize_plan", arguments={"seed": 1})
        )
        assert blocked == "emergency_circuit_breaker:runaway_orchestration"
        # The breaker is technical safety, not a plan-quality verdict: the
        # loop terminators stay answerable.
        assert Stage3Controller().validate(session, DecisionAction(action_id="keep_baseline")) == ""

        outcome = Stage3Controller(clock=lambda: "T").handle(
            session, DecisionAction(action_id="keep_baseline"), capabilities
        )
        assert outcome.accepted is True

    def test_five_distinct_search_actions_run_without_forced_escalation(self):
        """Five materially different bounded searches can run in one session.

        Each distinct strategy advances the candidate revision; nothing forces
        escalation merely because more than the old cap of attempts were used.
        """

        class _SequenceCapabilities:
            def __init__(self) -> None:
                self.calls = 0

            def apply(self, transition, session, action):
                self.calls += 1
                seed = 10 + self.calls
                body = _candidate(seed)
                fingerprint = candidate_content_fingerprint(body)
                return Stage3CapabilityResult(
                    ok=True,
                    candidate=_plan(seed),
                    candidate_fingerprint=fingerprint,
                    candidate_source="stage3_optimizer",
                    candidate_changed=True,
                    next_context=_context("stage3_optimize", fingerprint=fingerprint),
                    next_capability="stage3_optimize",
                    next_candidates=[
                        {"candidate": _plan(seed), "candidate_ref": f"stage3_interactive:attempt_{seed}"}
                    ],
                    next_attempt=self.calls + 1,
                )

        session = _session_with_candidate(1)
        capabilities = _SequenceCapabilities()
        controller = Stage3Controller(clock=lambda: "T")

        for index in range(5):
            outcome = controller.handle(
                session,
                DecisionAction(action_id="optimize_plan", arguments={"seed": index, "iterations": 100 + index}),
                capabilities,
            )
            assert outcome.accepted is True, outcome.reason
            assert session.pending_decision is not None

        assert capabilities.calls == 5
        assert session.candidate_revision == 6
        assert session.is_finalized() is False

    def test_search_history_projection_tracks_progress_and_signatures(self):
        from tournament_scheduler.application.stage3_progress import build_search_history

        history = build_search_history(
            [
                self._attempt(revision=1, signature="a", fingerprint="f1", progress=True),
                self._attempt(revision=2, signature="b", fingerprint="f1", progress=False),
                self._attempt(revision=3, signature="c", fingerprint="f2", progress=True),
            ]
        )
        assert history["actions_used"] == 3
        assert history["unique_action_signatures"] == 3
        assert history["candidate_revisions"] == 2
        assert history["repeated_no_progress_actions"] == 1
        assert history["last_actions"] == ["optimize_plan", "optimize_plan", "optimize_plan"]
        assert history["circuit_breaker_tripped"] is False


class TestNonLifecycleActions:
    def test_abort_is_deferred_to_the_caller(self):
        session = _session_with_candidate(1)
        capabilities = _FakeCapabilities({})

        # A terminal action such as abort carries no candidate revision scope;
        # validate() must defer it rather than rejecting it as unsupported.
        assert Stage3Controller().validate(session, DecisionAction(action_id="abort")) == ""

        outcome = Stage3Controller(clock=lambda: "T").handle(
            session, DecisionAction(action_id="abort"), capabilities
        )
        assert outcome.accepted is False
        assert outcome.reason == "unsupported_action:abort"


class TestOperatorRequest:
    def test_request_operator_moves_pending_scope(self):
        session = _session_with_candidate(1)
        capabilities = _FakeCapabilities(
            {
                "request_operator": Stage3CapabilityResult(
                    ok=True,
                    next_context={
                        "capability": "stage3_operator",
                        "available_actions": ["proceed", "abort"],
                    },
                    next_capability="stage3_operator",
                )
            }
        )

        outcome = Stage3Controller(clock=lambda: "T").handle(
            session,
            DecisionAction(action_id="request_operator", arguments={"question": "which one?"}),
            capabilities,
        )

        assert outcome.accepted is True
        assert session.pending_decision is not None
        assert session.pending_decision["capability"] == "stage3_operator"

    def test_request_operator_pauses_on_the_exact_current_candidate(self):
        """Escalating must not select, mutate or finalize a candidate."""
        session = _session_with_candidate(1)
        revision_before = session.candidate_revision
        fingerprint_before = session.candidate_fingerprint
        capabilities = _FakeCapabilities(
            {
                "request_operator": Stage3CapabilityResult(
                    ok=True,
                    operator_request={
                        "question": "confirm the manual placement?",
                        "capability": "host_placement_repair",
                    },
                )
            }
        )

        outcome = Stage3Controller(clock=lambda: "T").handle(
            session,
            DecisionAction(
                action_id="request_operator",
                arguments={"question": "confirm the manual placement?"},
                rationale="host confirmation",
            ),
            capabilities,
        )

        assert outcome.accepted is True
        assert session.is_finalized() is False
        assert session.candidate_revision == revision_before
        assert session.candidate_fingerprint == fingerprint_before
        assert session.candidate == _plan(1)
        assert session.status == STATUS_AWAITING_OPERATOR
        # The pending decision is retained so the operator answer still
        # targets the same revision.
        assert session.pending_decision is not None
        assert session.pending_decision["capability"] == "stage3_interactive"
        assert session.operator_request["question"] == "confirm the manual placement?"
        assert session.operator_request["candidate_revision"] == revision_before
        assert session.operator_request["candidate_fingerprint"] == fingerprint_before

    def test_operator_resume_selects_current_candidate_and_clears_operator_request(self):
        session = _session_with_candidate(1)
        Stage3Controller(clock=lambda: "T").handle(
            session,
            DecisionAction(action_id="request_operator", arguments={"question": "which?"}),
            _FakeCapabilities(
                {
                    "request_operator": Stage3CapabilityResult(
                        ok=True, operator_request={"question": "which?"}
                    )
                }
            ),
        )
        assert session.operator_request is not None

        capabilities = _FakeCapabilities(
            {
                "select_candidate": Stage3CapabilityResult(
                    ok=True,
                    candidate=_plan(1),
                    candidate_fingerprint=candidate_content_fingerprint(_candidate(1)),
                    candidate_changed=False,
                    final=True,
                )
            }
        )
        outcome = Stage3Controller(clock=lambda: "T").handle(
            session,
            DecisionAction(
                action_id="apply_candidate",
                arguments={"candidate_ref": "stage3_interactive:attempt_1"},
                rationale="adopt the current attempt",
            ),
            capabilities,
        )

        assert outcome.accepted is True
        assert session.is_finalized()
        assert session.operator_request is None
