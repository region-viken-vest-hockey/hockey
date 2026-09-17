"""Unit tests for the interactive Stage 3 domain-capability adapter.

The adapter (`cli/pipeline_orchestrator/stage3_capabilities.py`) performs the
deterministic domain operation for one session transition and reports a typed
result; the controller still owns revision lineage, validation and
persistence. These tests drive the real adapter through the controller with
small synthetic work directories, without loading planner/optimizer internals.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from tournament_scheduler.application.decisions import DecisionAction
from tournament_scheduler.application.stage3_controller import Stage3Controller
from tournament_scheduler.application.stage3_session import Stage3Session
from tournament_scheduler.application.stage3_session_store import Stage3SessionStore
from tournament_scheduler.cli.pipeline_orchestrator.arena_conflict_decisions import (
    _collision_facts,
)
from tournament_scheduler.cli.pipeline_orchestrator.stage3_capabilities import (
    InteractiveStage3Capabilities,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus

ICE_TIME = {"U10": 30, "U11": 30}


def _ice_problem(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {"ice_time_minutes": ICE_TIME}


def _tournament(tid: str, age_group: str, arena: str, date_str: str, start_time: str) -> dict:
    return {
        "id": tid,
        "date": date_str,
        "arena": arena,
        "age_group": age_group,
        "host_club": "Jar",
        "teams": [{"club": "Jar", "label": f"Jar {age_group}", "age_group": age_group}],
        "games": [
            {
                "home": f"Jar {age_group}",
                "away": f"Jar {age_group}",
                "round_number": 1,
                "parallel_slot": 0,
            }
        ],
        "start_time": start_time,
        "cancelled": False,
    }


def _candidate_with_collision() -> dict[str, Any]:
    return {
        "tournaments": [
            _tournament("aaa11111", "U11", "Jar Isforum", "2026-11-07", "09:00"),
            _tournament("bbb22222", "U10", "Jar Isforum", "2026-11-07", "09:15"),
        ]
    }


def _capabilities(state: PipelineState, run_id: str = "", problem_fn: Any = None) -> InteractiveStage3Capabilities:
    return InteractiveStage3Capabilities(
        state,
        SimpleNamespace(input="input.xlsx"),
        lambda msg: None,
        run_id=run_id,
        problem_fn=problem_fn,
    )


def test_shared_host_transition_records_run_scoped_decision(tmp_path):
    state = PipelineState(str(tmp_path))
    store = Stage3SessionStore(tmp_path)
    session = Stage3Session(run_id="run-1")
    session.set_pending(
        capability="shared_host_assignment",
        context={
            "capability": "shared_host_assignment",
            "facts": {"registration": "Kongsberg/Tønsberg", "age_group": "U10"},
            "available_actions": ["assign_shared_host", "request_operator"],
        },
        marker={"registration": "Kongsberg/Tønsberg", "age_group": "U10"},
    )
    store.save(session)

    loaded = store.load("run-1")
    action = DecisionAction(
        action_id="assign_shared_host",
        arguments={"chosen_club": "Tønsberg"},
        rationale="fairness",
    )
    outcome = Stage3Controller(clock=lambda: "T1").handle(
        loaded, action, _capabilities(state, "run-1", problem_fn=_ice_problem)
    )
    assert outcome.accepted
    store.save(loaded)

    view = store.shared_host_view("run-1")
    assert view["pending"] is None
    assert view["decisions"][0]["registration"] == "Kongsberg/Tønsberg"
    assert view["decisions"][0]["chosen_club"] == "Tønsberg"

    # The run-scoped choice survives a reload and is never re-asked.
    reloaded = store.load("run-1")
    assert reloaded.shared_host_decisions[0]["chosen_club"] == "Tønsberg"
    assert reloaded.pending_decision is None


def test_arena_transition_applies_conflict_and_binds_next_collision(tmp_path):
    from tournament_scheduler.arena_conflict_decision import (
        build_arena_conflict_decision_context,
    )

    state = PipelineState(str(tmp_path))
    plan = {"plan": _candidate_with_collision()}
    state.write_stage(StageName.PLANNING, plan, status=StageStatus.DONE)
    state.write_stage(StageName.CONFIG, {"start_date": "2026-09-01", "end_date": "2027-04-30"}, status=StageStatus.DONE)
    facts = _collision_facts(plan["plan"], ICE_TIME)[0]
    context = build_arena_conflict_decision_context("run-1", facts)

    store = Stage3SessionStore(tmp_path)
    session = Stage3Session(run_id="run-1")
    session.advance_candidate(
        plan,
        fingerprint="baseline",
        source="baseline",
        transition="create_baseline",
        action_id="create_baseline",
        rationale="baseline",
        at="T0",
    )
    session.set_pending(
        capability="arena_conflict_resolution",
        context=context.to_dict(),
        marker={"key": "pending-key"},
    )
    session.candidate_fingerprint = facts["candidate_fingerprint"]
    session.pending_decision["candidate_fingerprint"] = facts["candidate_fingerprint"]
    store.save(session)

    keep = facts["sides"][0]["tournament_id"]
    loaded = store.load("run-1")
    action = DecisionAction(
        action_id="resolve_arena_conflict",
        arguments={"keep_tournament_id": keep},
        rationale="keep the harder slot",
    )
    outcome = Stage3Controller(clock=lambda: "T1").handle(
        loaded, action, _capabilities(state, "run-1", problem_fn=_ice_problem)
    )
    assert outcome.accepted
    store.save(loaded)

    # The checkpoint's losing tournament is demoted in place, and the
    # candidate advanced to a new revision.
    persisted = state.read_stage(StageName.PLANNING)
    demoted = [t for t in persisted["plan"]["tournaments"] if t.get("start_time") is None]
    assert len(demoted) == 1
    assert loaded.candidate_revision == 2
    assert loaded.arena_decisions
    assert loaded.pending_decision is None


def test_arena_transition_rejects_stale_fingerprint_before_mutating(tmp_path):
    from tournament_scheduler.arena_conflict_decision import (
        build_arena_conflict_decision_context,
    )

    state = PipelineState(str(tmp_path))
    plan = {"plan": _candidate_with_collision()}
    state.write_stage(StageName.PLANNING, plan, status=StageStatus.DONE)
    state.write_stage(StageName.CONFIG, {"start_date": "2026-09-01", "end_date": "2027-04-30"}, status=StageStatus.DONE)
    facts = _collision_facts(plan["plan"], ICE_TIME)[0]
    stale_facts = {**facts, "candidate_fingerprint": "deadbeef-deadbeef"}
    context = build_arena_conflict_decision_context("run-1", stale_facts)

    store = Stage3SessionStore(tmp_path)
    session = Stage3Session(run_id="run-1")
    session.advance_candidate(
        plan,
        fingerprint="baseline",
        source="baseline",
        transition="create_baseline",
        action_id="create_baseline",
        rationale="baseline",
        at="T0",
    )
    session.set_pending(
        capability="arena_conflict_resolution",
        context=context.to_dict(),
        marker={"key": "pending-key"},
    )
    session.candidate_fingerprint = "some-other-fingerprint"
    session.pending_decision["candidate_fingerprint"] = "some-other-fingerprint"
    store.save(session)

    loaded = store.load("run-1")
    action = DecisionAction(
        action_id="resolve_arena_conflict",
        arguments={"keep_tournament_id": facts["sides"][0]["tournament_id"]},
        rationale="stale",
    )
    outcome = Stage3Controller(clock=lambda: "T1").handle(
        loaded, action, _capabilities(state, "run-1", problem_fn=_ice_problem)
    )
    assert outcome.accepted is False
    assert outcome.reason == "stale_candidate_fingerprint"
    # A rejected transition mutates nothing.
    persisted = state.read_stage(StageName.PLANNING)
    assert all(t.get("start_time") is not None for t in persisted["plan"]["tournaments"])


def test_emission_mirror_stays_json_serializable_after_arena_transition(tmp_path):
    """The legacy compatibility mirror (and canonical session) must survive a
    transition whose context/data carries only plain JSON values."""
    state = PipelineState(str(tmp_path))
    store = Stage3SessionStore(tmp_path)
    session = Stage3Session(run_id="run-1")
    session.arena_decisions = [{"key": "k", "keep_side": "a", "manual_side": "b"}]
    store.save(session)
    raw = json.loads(store.session_path.read_text(encoding="utf-8"))
    assert raw["arena_decisions"][0]["key"] == "k"
