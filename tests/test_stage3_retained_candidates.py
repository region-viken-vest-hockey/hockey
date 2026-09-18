"""Retained verified Stage 3 attempts stay selectable after a worse attempt.

Within one :class:`Stage3Session`, a previously generated good attempt must
remain addressable by its stable ``candidate_ref`` after a later, worse
attempt is generated. Selection still goes through the explicit
``select_candidate`` transition and is re-validated against current Stage 1/2
facts before adoption -- choosing a retained attempt is never a bypass of
verification or staleness checks.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from tournament_scheduler.application.decisions import DecisionAction
from tournament_scheduler.application.stage3_controller import Stage3Controller
from tournament_scheduler.application.stage3_session import (
    RETAINED_ATTEMPT_LIMIT,
    Stage3Session,
)
from tournament_scheduler.application.stage3_session_store import (
    Stage3SessionStore,
    fingerprint_plan,
    stage3_checkpoint_facts_fingerprint,
)
from tournament_scheduler.cli.pipeline_orchestrator.stage3_capabilities import (
    InteractiveStage3Capabilities,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus


def _candidate(tag: str) -> dict[str, Any]:
    return {"tournaments": [{"id": tag, "date": "2026-10-05"}]}


def _plan(tag: str) -> dict[str, Any]:
    return {"plan": _candidate(tag)}


def _capabilities(state: PipelineState, run_id: str = "run-1") -> InteractiveStage3Capabilities:
    return InteractiveStage3Capabilities(
        state,
        SimpleNamespace(input="input.xlsx"),
        lambda _message: None,
        run_id=run_id,
        problem_fn=lambda *args, **kwargs: {},
    )


def _session_with_portfolio(tmp_path, *, facts_fingerprint: str | None = None):
    """attempt_1 (good) then attempt_3 (worse) with attempt_3 current."""
    state = PipelineState(str(tmp_path))
    state.write_stage(
        StageName.CONFIG,
        {"start_date": "2026-09-01", "end_date": "2027-04-30"},
        status=StageStatus.DONE,
    )
    plan_one = _plan("good-attempt")
    plan_three = _plan("worse-attempt")
    state.write_stage(StageName.PLANNING, plan_three, status=StageStatus.DONE)

    store = Stage3SessionStore(tmp_path)
    session = Stage3Session(run_id="run-1")
    session.advance_candidate(
        plan_one,
        fingerprint=fingerprint_plan(plan_one),
        source="baseline",
        transition="create_baseline",
        action_id="create_baseline",
        rationale="initial baseline",
        at="T",
    )
    recorded_facts = (
        stage3_checkpoint_facts_fingerprint(state)
        if facts_fingerprint is None
        else facts_fingerprint
    )
    session.retain_candidate_attempt(
        {
            "candidate_ref": "stage3_interactive:attempt_1",
            "candidate_revision": 1,
            "candidate_fingerprint": fingerprint_plan(plan_one),
            "candidate": plan_one,
            "facts_fingerprint": recorded_facts,
            "hard_verification_ok": True,
            "source": "baseline",
        }
    )
    session.advance_candidate(
        plan_three,
        fingerprint=fingerprint_plan(plan_three),
        source="search",
        transition="run_search",
        action_id="optimize_plan",
        rationale="later attempt",
        at="T",
    )
    session.retain_candidate_attempt(
        {
            "candidate_ref": "stage3_interactive:attempt_3",
            "candidate_revision": 3,
            "candidate_fingerprint": fingerprint_plan(plan_three),
            "candidate": plan_three,
            "facts_fingerprint": recorded_facts,
            "hard_verification_ok": True,
            "source": "search",
        }
    )
    session.set_pending(
        capability="stage3_optimize",
        context={
            "capability": "stage3_optimize",
            "facts": {"candidate_fingerprint": fingerprint_plan(plan_three)},
            "available_actions": [
                "apply_candidate",
                "keep_baseline",
                "optimize_plan",
                "request_operator",
            ],
        },
        candidates=[{"candidate": plan_three, "candidate_ref": "stage3_interactive:attempt_3"}],
        attempt=3,
    )
    store.save(session)
    return state, store, plan_one, plan_three


def test_portfolio_keeps_earlier_attempt_after_later_attempt(tmp_path):
    _state, store, plan_one, plan_three = _session_with_portfolio(tmp_path)
    session = store.load("run-1")

    refs = session.retained_candidate_refs()
    assert "stage3_interactive:attempt_1" in refs
    assert "stage3_interactive:attempt_3" in refs
    attempt_one = session.find_candidate_attempt("stage3_interactive:attempt_1")
    assert attempt_one is not None
    assert attempt_one["candidate_fingerprint"] == fingerprint_plan(plan_one)
    assert attempt_one["candidate"] == plan_one
    # The current attempt is unchanged; retaining attempt 1 did not select it.
    assert session.candidate_fingerprint == fingerprint_plan(plan_three)


def test_portfolio_is_bounded_and_deduped(tmp_path):
    session = Stage3Session(run_id="run-1")
    for index in range(RETAINED_ATTEMPT_LIMIT + 3):
        session.retain_candidate_attempt(
            {
                "candidate_ref": f"ref-{index}",
                "candidate_fingerprint": f"fp-{index}",
                "candidate": {"tournaments": []},
            }
        )
        # Re-recording the same ref never duplicates it.
        session.retain_candidate_attempt(
            {
                "candidate_ref": f"ref-{index}",
                "candidate_fingerprint": f"fp-{index}",
                "candidate": {"tournaments": []},
            }
        )
    assert len(session.retained_candidate_refs()) == RETAINED_ATTEMPT_LIMIT
    assert len(set(session.retained_candidate_refs())) == RETAINED_ATTEMPT_LIMIT
    assert session.retained_candidate_refs()[-1] == f"ref-{RETAINED_ATTEMPT_LIMIT + 2}"


def test_portfolio_survives_store_round_trip(tmp_path):
    _state, store, plan_one, _plan_three = _session_with_portfolio(tmp_path)
    reloaded = Stage3SessionStore(tmp_path).load("run-1")
    attempt_one = reloaded.find_candidate_attempt("stage3_interactive:attempt_1")
    assert attempt_one is not None
    assert attempt_one["candidate"] == plan_one
    # A re-emission overlay (which rebuilds the session from the legacy mirror
    # for compatibility) must not drop the retained portfolio.
    store.record_emission(
        {"run_id": "run-1", "attempts_used": 3, "best_plan": _plan("worse-attempt")},
        run_id="run-1",
    )
    after = Stage3SessionStore(tmp_path).load("run-1")
    assert "stage3_interactive:attempt_1" in after.retained_candidate_refs()


def test_controller_accepts_retained_ref_absent_from_pending_candidates(tmp_path):
    _state, store, _plan_one, _plan_three = _session_with_portfolio(tmp_path)
    session = store.load("run-1")
    pending_refs = {
        entry.get("candidate_ref")
        for entry in (session.pending_decision or {}).get("candidates") or []
    }
    assert "stage3_interactive:attempt_1" not in pending_refs

    action = DecisionAction(
        action_id="apply_candidate",
        arguments={"candidate_ref": "stage3_interactive:attempt_1"},
        rationale="prefer the earlier hard-valid attempt",
    )
    assert Stage3Controller().validate(session, action) == ""


def test_capability_selects_retained_non_current_attempt_and_finalizes_it(tmp_path):
    state, store, plan_one, _plan_three = _session_with_portfolio(tmp_path)
    session = store.load("run-1")

    action = DecisionAction(
        action_id="apply_candidate",
        arguments={"candidate_ref": "stage3_interactive:attempt_1"},
        rationale="choose attempt 1",
    )
    outcome = Stage3Controller(clock=lambda: "T").handle(session, action, _capabilities(state))
    assert outcome.accepted is True, outcome.reason
    store.save(session)

    finalized = store.load("run-1")
    assert finalized.is_finalized()
    assert finalized.finalized_fingerprint == fingerprint_plan(plan_one)
    # Stage 4's handoff gate consumes exactly the selected fingerprint.
    assert state.read_stage(StageName.PLANNING)["plan"] == _candidate("good-attempt")
    assert store.finalized_candidate_matches(plan_one) is True


def test_capability_rejects_retained_attempt_stale_against_changed_stage1_facts(tmp_path):
    state, store, _plan_one, plan_three = _session_with_portfolio(
        tmp_path, facts_fingerprint="facts-from-a-different-config"
    )
    session = store.load("run-1")

    action = DecisionAction(
        action_id="apply_candidate",
        arguments={"candidate_ref": "stage3_interactive:attempt_1"},
        rationale="choose attempt 1",
    )
    outcome = Stage3Controller(clock=lambda: "T").handle(session, action, _capabilities(state))
    assert outcome.accepted is False
    assert outcome.reason == "stale_retained_candidate_facts"
    # A stale selection must leave canonical candidate state untouched.
    assert state.read_stage(StageName.PLANNING)["plan"] == _candidate("worse-attempt")
    assert store.load("run-1").is_finalized() is False


def test_emit_exposes_retained_portfolio_in_decision_context(tmp_path, capsys):
    from unittest.mock import patch

    from tournament_scheduler.cli.pipeline_orchestrator.interactive_decision_emit import (
        _emit_stage3_interactive_decision,
    )

    state = PipelineState(str(tmp_path))
    cfg = {"start_date": "2026-09-01", "end_date": "2027-04-30"}
    with patch(
        "tournament_scheduler.cli.pipeline_orchestrator.interactive_decision_emit._mid_planning_decision_problem",
        return_value={},
    ), patch(
        "tournament_scheduler.cli.pipeline_orchestrator.interactive_decision_emit._maybe_run_stage3_cp_sat_shadow",
        return_value=None,
    ):
        assert _emit_stage3_interactive_decision(
            state, str(tmp_path), cfg, {}, None, None, _plan("attempt-1"), lambda _m: None
        ) == 2
        first_payload = json.loads(capsys.readouterr().out)
        assert _emit_stage3_interactive_decision(
            state, str(tmp_path), cfg, {}, None, None, _plan("attempt-2"), lambda _m: None
        ) == 2
        second_payload = json.loads(capsys.readouterr().out)

    # The first attempt had only itself to offer; the second attempt keeps the
    # first addressable as a selectable target.
    assert first_payload["facts"]["retained_candidate_refs"] == [
        "stage3_interactive:attempt_1"
    ]
    enum = second_payload["action_parameters"]["apply_candidate"]["candidate_ref"]["enum"]
    assert enum == ["stage3_interactive:attempt_1", "stage3_interactive:attempt_2"]
    assert second_payload["facts"]["retained_candidate_refs"] == enum
    assert len(second_payload["facts"]["retained_candidates"]) == 2
    first_entry = next(
        entry
        for entry in second_payload["facts"]["retained_candidates"]
        if entry["candidate_ref"] == "stage3_interactive:attempt_1"
    )
    assert first_entry["hard_verification_ok"] is True
    assert "quality" in first_entry and "facts_fingerprint" in first_entry


def test_capability_rejects_retained_attempt_without_fact_provenance(tmp_path):
    state, store, _plan_one, _plan_three = _session_with_portfolio(
        tmp_path, facts_fingerprint=""
    )
    session = store.load("run-1")

    outcome = Stage3Controller(clock=lambda: "T").handle(
        session,
        DecisionAction(
            action_id="apply_candidate",
            arguments={"candidate_ref": "stage3_interactive:attempt_1"},
        ),
        _capabilities(state),
    )
    assert outcome.accepted is False
    assert outcome.reason == "retained_candidate_missing_facts_provenance"
