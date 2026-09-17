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

import pytest

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


# ---------------------------------------------------------------------------
# #361: hosting responsibility is an independently checked candidate semantic
# ---------------------------------------------------------------------------


def _ju12_team(club: str) -> dict:
    return {"club": club, "age_group": "JU12", "label": f"{club} JU12"}


def _ju12_problem(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {
        "ice_time_minutes": {"JU12": 30},
        "teams": (
            [_ju12_team("Frisk Asker"), _ju12_team("Frisk Asker")]
            + [_ju12_team("Ringerike"), _ju12_team("Ringerike")]
            + [_ju12_team("Kongsberg/Tønsberg"), _ju12_team("Skien"), _ju12_team("Jutul/Jar Kittens")]
        ),
    }


def _ju12_candidate(host_counts: dict[str, int]) -> dict[str, Any]:
    tournaments = []
    for host, count in host_counts.items():
        for index in range(count):
            tournaments.append(
                {
                    "id": f"{host[:3].lower()}{index}",
                    "date": "2026-10-25",
                    "arena": f"{host} Arena",
                    "age_group": "JU12",
                    "host_club": host,
                    "start_time": "10:00",
                    "teams": [{"club": host, "label": f"{host} JU12", "age_group": "JU12"}],
                    "games": [],
                    "cancelled": False,
                }
            )
    return {"tournaments": tournaments}


def _stage3_select_fixture(tmp_path, *, chosen_body: dict[str, Any], baseline_body: dict[str, Any] | None = None):
    from tournament_scheduler.application.stage3_session_store import fingerprint_plan

    state = PipelineState(str(tmp_path))
    baseline_body = baseline_body or _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 5, "Kongsberg": 2, "Skien": 2, "Jutul": 2}
    )
    plan = {"plan": baseline_body}
    state.write_stage(StageName.PLANNING, plan, status=StageStatus.DONE)
    state.write_stage(
        StageName.CONFIG, {"start_date": "2026-09-01", "end_date": "2027-04-30"}, status=StageStatus.DONE
    )

    store = Stage3SessionStore(tmp_path)
    session = Stage3Session(run_id="run-1")
    fingerprint = fingerprint_plan(plan)
    session.advance_candidate(
        plan,
        fingerprint=fingerprint,
        source="baseline",
        transition="create_baseline",
        action_id="create_baseline",
        rationale="baseline",
        at="T0",
    )
    session.set_pending(
        capability="stage3_interactive",
        context={
            "capability": "stage3_interactive",
            "facts": {"candidate_fingerprint": fingerprint},
            "available_actions": ["apply_candidate", "keep_baseline"],
        },
        candidates=[{"candidate_ref": "c1", "candidate": {"plan": chosen_body}}],
    )
    store.save(session)
    return state, store, fingerprint


def test_select_candidate_rejects_unexplained_responsibility_transfer(tmp_path):
    """A candidate that absorbs another club's hosting is refused at the
    Stage 3 revision boundary, and nothing (session or checkpoint) is committed.
    """
    transferring = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 7, "Kongsberg": 2, "Skien": 1, "Jutul": 1}
    )
    state, store, fingerprint = _stage3_select_fixture(tmp_path, chosen_body=transferring)
    baseline_before = json.dumps(state.read_stage(StageName.PLANNING), sort_keys=True)

    loaded = store.load("run-1")
    action = DecisionAction(
        action_id="apply_candidate",
        arguments={"candidate_ref": "c1", "candidate_fingerprint": fingerprint},
        rationale="more Ringerike ice is convenient",
    )
    outcome = Stage3Controller(clock=lambda: "T1").handle(
        loaded, action, _capabilities(state, "run-1", problem_fn=_ju12_problem)
    )

    assert outcome.accepted is False
    assert outcome.reason == "unexplained_hosting_responsibility_transfer"
    assert outcome.findings[0]["club"] == "Ringerike"
    # A rejected transition must not advance the session or mutate the checkpoint.
    assert loaded.candidate_revision == 1
    assert loaded.finalized_revision is None
    assert json.dumps(state.read_stage(StageName.PLANNING), sort_keys=True) == baseline_before


def test_select_candidate_accepts_responsibility_preserving_candidate(tmp_path):
    """Returning hosting to the clubs that owe it is a valid adoption."""
    unbalanced = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 7, "Kongsberg": 2, "Skien": 1, "Jutul": 1}
    )
    balanced = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 5, "Kongsberg": 2, "Skien": 2, "Jutul": 2}
    )
    state, store, fingerprint = _stage3_select_fixture(
        tmp_path, chosen_body=balanced, baseline_body=unbalanced
    )

    loaded = store.load("run-1")
    action = DecisionAction(
        action_id="apply_candidate",
        arguments={"candidate_ref": "c1", "candidate_fingerprint": fingerprint},
        rationale="balance hosting",
    )
    outcome = Stage3Controller(clock=lambda: "T1").handle(
        loaded, action, _capabilities(state, "run-1", problem_fn=_ju12_problem)
    )

    assert outcome.accepted is True
    persisted = state.read_stage(StageName.PLANNING)
    assert len(persisted["plan"]["tournaments"]) == 16


def test_apply_repair_rejects_candidate_that_transfers_responsibility(tmp_path, monkeypatch):
    """The local-repair capability is guarded too, not only candidate adoption."""
    import tournament_scheduler.local_repair_options as lro
    from tournament_scheduler.application.stage3_session_store import fingerprint_plan

    state = PipelineState(str(tmp_path))
    baseline_body = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 5, "Kongsberg": 2, "Skien": 2, "Jutul": 2}
    )
    plan = {"plan": baseline_body}
    state.write_stage(StageName.PLANNING, plan, status=StageStatus.DONE)
    state.write_stage(
        StageName.CONFIG, {"start_date": "2026-09-01", "end_date": "2027-04-30"}, status=StageStatus.DONE
    )

    transferring = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 7, "Kongsberg": 2, "Skien": 1, "Jutul": 1}
    )

    def _fake_apply(candidate, problem, **kwargs):
        return {"ok": True, "candidate": transferring, "family": "host_placement"}

    monkeypatch.setattr(lro, "apply_local_repair_option", _fake_apply)

    store = Stage3SessionStore(tmp_path)
    session = Stage3Session(run_id="run-1")
    fingerprint = fingerprint_plan(plan)
    session.advance_candidate(
        plan,
        fingerprint=fingerprint,
        source="baseline",
        transition="create_baseline",
        action_id="create_baseline",
        rationale="baseline",
        at="T0",
    )
    session.set_pending(
        capability="host_placement_repair",
        context={
            "capability": "host_placement_repair",
            "facts": {"candidate_fingerprint": fingerprint},
            "available_actions": ["apply_repair_option", "keep_baseline"],
        },
    )
    store.save(session)
    baseline_before = json.dumps(state.read_stage(StageName.PLANNING), sort_keys=True)

    loaded = store.load("run-1")
    action = DecisionAction(
        action_id="apply_repair_option",
        arguments={"option_id": "transfer-option", "candidate_fingerprint": fingerprint},
        rationale="rehost onto Ringerike",
    )
    outcome = Stage3Controller(clock=lambda: "T1").handle(
        loaded, action, _capabilities(state, "run-1", problem_fn=_ju12_problem)
    )

    assert outcome.accepted is False
    assert outcome.reason == "unexplained_hosting_responsibility_transfer"
    assert outcome.findings[0]["club"] == "Ringerike"
    assert loaded.candidate_revision == 1
    assert json.dumps(state.read_stage(StageName.PLANNING), sort_keys=True) == baseline_before


# ---------------------------------------------------------------------------
# #368 regression: candidate-scoped decisions must bind to the exact current
# attempt, not to a previous revision's adopted baseline.
# ---------------------------------------------------------------------------


def test_responsibility_guard_compares_bound_attempt_not_previous_baseline(tmp_path):
    """The guard's "before" candidate is the session's current attempt.

    When that attempt already carries its own hosting excess, a change that
    does not worsen it must not be rejected merely because the previous
    baseline was balanced -- only a mutation that really grows the excess is.
    """
    from tournament_scheduler.application.stage3_session import (
        Stage3Session,
        candidate_content_fingerprint,
    )

    state = PipelineState(str(tmp_path))
    caps = _capabilities(state, "run-1", problem_fn=_ju12_problem)
    drifted = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 7, "Kongsberg": 2, "Skien": 1, "Jutul": 1}
    )
    more_drifted = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 8, "Kongsberg": 2, "Skien": 1, "Jutul": 0}
    )

    session = Stage3Session(run_id="run-1")
    session.candidate = {"plan": drifted}
    session.candidate_fingerprint = candidate_content_fingerprint(drifted)
    session.candidate_revision = 2

    # B's own pre-existing excess is not attributed to this change.
    assert caps._responsibility_guard(session, {"plan": drifted}, _ju12_problem()) == ""
    # A mutation that really increases the excess is still rejected.
    assert (
        caps._responsibility_guard(session, {"plan": more_drifted}, _ju12_problem())
        == "unexplained_hosting_responsibility_transfer"
    )


def test_keep_baseline_restores_the_bound_baseline_and_finalizes_it(tmp_path):
    """After an attempt is bound, ``keep_baseline`` must restore the adopted
    plan (not the current attempt) and finalize that exact fingerprint, so the
    Stage 4 handoff gate accepts the restored checkpoint."""
    from tournament_scheduler.application.stage3_session_store import (
        Stage3SessionStore,
        fingerprint_plan,
    )

    state = PipelineState(str(tmp_path))
    baseline_plan = {"plan": {"tournaments": [{"id": "baseline"}]}, "warnings": []}
    attempt_plan = {"plan": {"tournaments": [{"id": "attempt"}]}, "warnings": []}
    state.write_stage(StageName.PLANNING, attempt_plan, status=StageStatus.DONE)

    store = Stage3SessionStore(tmp_path)
    store.bind_candidate(baseline_plan, run_id="run-1", source="baseline")
    store.bind_candidate(attempt_plan, run_id="run-1", source="search")
    session = store.load("run-1")
    session.set_pending(
        capability="stage3_interactive",
        context={"capability": "stage3_interactive", "available_actions": ["keep_baseline"]},
    )
    store.save(session)
    assert session.candidate_fingerprint == fingerprint_plan(attempt_plan)

    caps = _capabilities(state, "run-1", problem_fn=_ice_problem)
    outcome = Stage3Controller(clock=lambda: "T").handle(
        session, DecisionAction(action_id="keep_baseline", rationale="keep the baseline"), caps
    )
    store.save(session)

    assert outcome.accepted is True
    assert state.read_stage(StageName.PLANNING) == baseline_plan
    reloaded = store.load("run-1")
    assert reloaded.is_finalized()
    assert reloaded.finalized_fingerprint == fingerprint_plan(baseline_plan)
    assert store.finalized_candidate_matches(baseline_plan) is True
    assert store.finalized_candidate_matches(attempt_plan) is False


@pytest.mark.parametrize("keep_index", [0, 1])
def test_arena_transition_accepts_demotion_when_attempt_already_exceeds_target(tmp_path, keep_index):
    """End-to-end arena answer against the exact bound attempt B.

    Reproduces the production regression: B already hosts more than its fair
    target relative to the previous baseline A. Demoting one of B's colliding
    tournaments does not move any hosting responsibility, so both directions
    of the arena question must be answerable. The guard must compare B -> B',
    not A -> B'.
    """
    from tournament_scheduler.application.decisions import DecisionAction
    from tournament_scheduler.application.stage3_controller import Stage3Controller
    from tournament_scheduler.application.stage3_session import Stage3Session
    from tournament_scheduler.application.stage3_session_store import (
        Stage3SessionStore,
        fingerprint_plan,
    )
    from tournament_scheduler.arena_conflict_decision import (
        build_arena_conflict_decision_context,
    )

    state = PipelineState(str(tmp_path))
    attempt_b = {
        "plan": _ju12_candidate(
            {"Frisk Asker": 5, "Ringerike": 7, "Kongsberg": 2, "Skien": 1, "Jutul": 1}
        )
    }
    # Give the tournaments a round so their arena intervals are evaluable; all
    # Ringerike tournaments share ``Ringerike Arena`` at 10:00 and therefore
    # collide, exactly like a real tight-arena Stage 3 attempt.
    for tournament in attempt_b["plan"]["tournaments"]:
        tournament["games"] = [
            {
                "home": tournament["teams"][0]["label"],
                "away": tournament["teams"][0]["label"],
                "round_number": 1,
                "parallel_slot": 0,
            }
        ]
    state.write_stage(StageName.PLANNING, attempt_b, status=StageStatus.DONE)
    state.write_stage(
        StageName.CONFIG, {"start_date": "2026-09-01", "end_date": "2027-04-30"}, status=StageStatus.DONE
    )
    facts = _collision_facts(attempt_b["plan"], {"JU12": 30})[0]
    context = build_arena_conflict_decision_context("run-1", facts)

    store = Stage3SessionStore(tmp_path)
    session = Stage3Session(run_id="run-1")
    balanced_baseline = _ju12_candidate(
        {"Frisk Asker": 5, "Ringerike": 5, "Kongsberg": 2, "Skien": 2, "Jutul": 2}
    )
    session.candidate = {"plan": balanced_baseline}
    session.candidate_fingerprint = fingerprint_plan(session.candidate)
    session.candidate_revision = 1
    store.save(session)
    # The emission binds attempt B before emitting its arena context.
    store.bind_candidate(attempt_b, run_id="run-1", transition="create_baseline")
    session = store.load("run-1")
    session.set_pending(
        capability="arena_conflict_resolution", context=context.to_dict(), marker={"key": "k"}
    )
    store.save(session)
    assert session.candidate_fingerprint == facts["candidate_fingerprint"]
    assert session.pending_decision["candidate_fingerprint"] == facts["candidate_fingerprint"]

    caps = _capabilities(state, "run-1", problem_fn=_ju12_problem)
    action = DecisionAction(
        action_id="resolve_arena_conflict",
        arguments={"keep_tournament_id": facts["sides"][keep_index]["tournament_id"]},
        rationale="keep the harder slot",
    )
    outcome = Stage3Controller(clock=lambda: "T").handle(session, action, caps)

    # Either direction must be answerable because neither moves hosting between
    # clubs; only a mutation that really grows the attempt's excess is rejected.
    assert outcome.accepted is True
    persisted = state.read_stage(StageName.PLANNING)
    assert any(t.get("start_time") is None for t in persisted["plan"]["tournaments"])

