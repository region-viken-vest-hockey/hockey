"""Multi-invocation lifecycle tests for the persisted Stage 3 state machine.

Each "invocation" is a separate :meth:`Stage3SessionStore.load` /
:meth:`Stage3SessionStore.save` round trip, so this proves the session owns
candidate revision lineage, run-scoped decisions and the Stage 4 handoff
across process boundaries -- without loading planner/search internals.
"""

from __future__ import annotations

from pathlib import Path

from tournament_scheduler.application.decisions import DecisionAction
from tournament_scheduler.application.stage3_controller import (
    Stage3CapabilityResult,
    Stage3Controller,
)
from tournament_scheduler.application.stage3_session import (
    STATUS_FINALIZED,
    Stage3Session,
    candidate_content_fingerprint,
)
from tournament_scheduler.application.stage3_session_store import Stage3SessionStore


def _candidate(seed: int) -> dict:
    return {"tournaments": [{"id": f"t{seed}", "date": "2026-10-05"}]}


def _plan(seed: int) -> dict:
    return {"plan": _candidate(seed)}


def _context(capability: str, fingerprint: str) -> dict:
    return {
        "capability": capability,
        "facts": {"candidate_fingerprint": fingerprint},
        "available_actions": ["apply_repair_option", "apply_candidate", "keep_baseline", "optimize_plan"],
    }


class _RepairCapability:
    def __init__(self, seed: int, *, fixed_fingerprint: str | None = None) -> None:
        self.seed = seed
        self.fixed_fingerprint = fixed_fingerprint

    def apply(self, transition: str, session: Stage3Session, action: DecisionAction) -> Stage3CapabilityResult:
        if self.fixed_fingerprint is not None:
            return Stage3CapabilityResult(ok=False, reason=self.fixed_fingerprint)
        body = _candidate(self.seed)
        fingerprint = candidate_content_fingerprint(body)
        return Stage3CapabilityResult(
            ok=True,
            candidate=_plan(self.seed),
            candidate_fingerprint=fingerprint,
            candidate_source="local_repair",
            candidate_changed=True,
            next_context=_context("stage3_interactive", fingerprint),
            next_capability="stage3_interactive",
            next_candidates=[{"candidate": _plan(self.seed), "candidate_ref": f"stage3_interactive:attempt_{self.seed}"}],
            next_attempt=self.seed,
        )


class _FinalizeCapability:
    def __init__(self, plan: dict) -> None:
        self.plan = plan

    def apply(self, transition: str, session: Stage3Session, action: DecisionAction) -> Stage3CapabilityResult:
        body = self.plan["plan"]
        return Stage3CapabilityResult(
            ok=True,
            candidate=self.plan,
            candidate_fingerprint=candidate_content_fingerprint(body),
            candidate_source="selected",
            # Adopting the already-current revision does not create a new one;
            # finalization records the exact revision/fingerprint Stage 4 gets.
            candidate_changed=False,
            final=True,
        )


def _start_session(store: Stage3SessionStore) -> Stage3Session:
    session = Stage3Session(run_id="run-1")
    session.shared_host_decisions = [{"registration": "A/B", "age_group": "U10", "chosen_club": "A"}]
    session.attempts = {"attempts_used": 1, "best_attempt": 1}
    body = _candidate(1)
    session.advance_candidate(
        _plan(1),
        fingerprint=candidate_content_fingerprint(body),
        source="baseline",
        transition="create_baseline",
        action_id="create_baseline",
        rationale="initial baseline",
        at="T0",
    )
    session.set_pending(
        capability="stage3_interactive",
        context=_context("stage3_interactive", candidate_content_fingerprint(body)),
        candidates=[{"candidate": _plan(1), "candidate_ref": "stage3_interactive:attempt_1"}],
        attempt=1,
    )
    store.save(session)
    return session


def test_two_repairs_across_invocations_build_one_revision_lineage(tmp_path: Path):
    store = Stage3SessionStore(tmp_path)
    _start_session(store)

    # Invocation 1: local repair on revision 1 -> revision 2.
    session = store.load(expected_run_id="run-1")
    revision_before = session.candidate_revision
    action = DecisionAction(
        action_id="apply_repair_option",
        arguments={"option_id": "opt-1", "candidate_fingerprint": session.pending_fingerprint()},
        rationale="repair one",
    )
    outcome = Stage3Controller(clock=lambda: "T1").handle(session, action, _RepairCapability(2))
    assert outcome.accepted
    store.save(session)

    # Invocation 2 (separate load): another local repair on revision 2 -> 3.
    reloaded = store.load(expected_run_id="run-1")
    assert reloaded.candidate_revision == revision_before + 1
    assert reloaded.pending_fingerprint() == candidate_content_fingerprint(_candidate(2))
    # The resolved shared-host choice is not re-asked: the pending decision is
    # a candidate-scoped adoption, never a repeated run-scoped shared-host ask.
    assert reloaded.pending_decision["capability"] == "stage3_interactive"
    assert reloaded.pending_decision["scope"] == "candidate"
    action2 = DecisionAction(
        action_id="apply_repair_option",
        arguments={"option_id": "opt-2", "candidate_fingerprint": reloaded.pending_fingerprint()},
        rationale="repair two",
    )
    outcome2 = Stage3Controller(clock=lambda: "T2").handle(reloaded, action2, _RepairCapability(3))
    assert outcome2.accepted
    store.save(reloaded)

    final = store.load(expected_run_id="run-1")
    assert final.candidate_revision == 3
    transitions = [(entry["from_revision"], entry["to_revision"]) for entry in final.decision_history]
    assert (1, 2) in transitions
    assert (2, 3) in transitions
    # Every revision is explained by explicit transition provenance.
    assert transitions == sorted(transitions)
    # The run-scoped shared-host choice is never re-asked or dropped.
    assert final.shared_host_decisions[0]["chosen_club"] == "A"

    # Stage 4 receives exactly the finalized revision.
    assert not final.is_finalized()
    finalize_action = DecisionAction(
        action_id="apply_candidate",
        arguments={"candidate_ref": "stage3_interactive:attempt_3"},
        rationale="adopt",
    )
    outcome3 = Stage3Controller(clock=lambda: "T3").handle(final, finalize_action, _FinalizeCapability(_plan(3)))
    assert outcome3.accepted
    store.save(final)

    terminal = store.load(expected_run_id="run-1")
    assert terminal.status == STATUS_FINALIZED
    assert terminal.finalized_revision == 3
    assert terminal.finalized_fingerprint == candidate_content_fingerprint(_candidate(3))
    assert store.finalized_candidate_matches(_plan(3)) is True
    assert store.finalized_candidate_matches(_plan(1)) is False
    assert terminal.legal_transitions() == []


def test_stale_revision_action_across_processes_is_rejected(tmp_path: Path):
    store = Stage3SessionStore(tmp_path)
    _start_session(store)

    # Process A advances the session to revision 2 and persists it.
    session = store.load(expected_run_id="run-1")
    stale_fingerprint = session.pending_fingerprint()
    action = DecisionAction(
        action_id="apply_repair_option",
        arguments={"option_id": "opt-1", "candidate_fingerprint": stale_fingerprint},
        rationale="repair one",
    )
    assert Stage3Controller(clock=lambda: "T1").handle(session, action, _RepairCapability(2)).accepted
    store.save(session)

    # Process B still holds the revision-1 fingerprint from before the repair
    # and submits it: it must be rejected without recording provenance.
    reloaded = store.load(expected_run_id="run-1")
    stale_action = DecisionAction(
        action_id="apply_repair_option",
        arguments={"option_id": "opt-1", "candidate_fingerprint": stale_fingerprint},
        rationale="stale retry",
    )
    outcome = Stage3Controller(clock=lambda: "T2").handle(reloaded, stale_action, _RepairCapability(9))
    assert outcome.accepted is False
    assert outcome.reason == "stale_candidate_fingerprint"
    assert reloaded.candidate_revision == 2
