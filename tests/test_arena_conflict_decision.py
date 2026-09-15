"""Tests for the internal arena/time double-booking resolution decision.

Mirrors ``test_shared_host_decision.py``: the LLM/controller may only choose
one of the collision's own two tournament ids, enforced deterministically by
``application.decisions.validate_decision_action`` via ``resolve_arena_conflict``'s
``keep_tournament_id`` enum.
"""

from tournament_scheduler.application.decisions import DecisionAction, validate_decision_action
from tournament_scheduler.arena_conflict_decision import (
    arena_conflict_decision_record,
    build_arena_conflict_decision_context,
    build_arena_conflict_decision_prompt,
    collision_key,
    parse_arena_conflict_verdict,
)


def _facts():
    return {
        "arena": "Jar Isforum",
        "date": "2026-11-07",
        "overlap": "2026-11-07 09:00–11:00 / 2026-11-07 10:00–12:00",
        "sides": [
            {"tournament_id": "aaa11111", "age_group": "U11", "host_club": "Jar", "interval": "09:00–11:00", "team_count": 6},
            {"tournament_id": "bbb22222", "age_group": "U10", "host_club": "Jar", "interval": "10:00–12:00", "team_count": 5},
        ],
    }


class TestBuildArenaConflictDecisionContext:
    def test_context_carries_collision_facts_and_enum(self):
        context = build_arena_conflict_decision_context("run-1", _facts())

        assert context.capability == "arena_conflict_resolution"
        assert context.available_actions == ("resolve_arena_conflict", "request_operator")
        schema = context.action_parameters["resolve_arena_conflict"]["keep_tournament_id"]
        assert schema["enum"] == ["aaa11111", "bbb22222"]

    def test_accepts_either_side(self):
        context = build_arena_conflict_decision_context("run-1", _facts())
        for tid in ("aaa11111", "bbb22222"):
            action = DecisionAction(action_id="resolve_arena_conflict", arguments={"keep_tournament_id": tid})
            validate_decision_action(context, action)  # must not raise

    def test_rejects_a_non_pair_tournament_id(self):
        context = build_arena_conflict_decision_context("run-1", _facts())
        action = DecisionAction(action_id="resolve_arena_conflict", arguments={"keep_tournament_id": "zzz99999"})
        try:
            validate_decision_action(context, action)
            assert False, "expected a validation error for a tournament id outside this collision"
        except Exception as exc:
            assert "keep_tournament_id" in str(exc)

    def test_rejects_missing_keep_tournament_id(self):
        context = build_arena_conflict_decision_context("run-1", _facts())
        action = DecisionAction(action_id="resolve_arena_conflict", arguments={})
        try:
            validate_decision_action(context, action)
            assert False, "expected a validation error for a missing keep_tournament_id"
        except Exception as exc:
            assert "missing required argument" in str(exc)


class TestCollisionKeyStability:
    def test_key_is_independent_of_tournament_ids(self):
        # Stage 3 assigns fresh random tournament ids every rebuild -- the
        # persisted "already resolved" key must be derived from arena/date/
        # age_group/host_club only, so a decision survives a Stage 3 rerun.
        facts_a = _facts()
        facts_b = _facts()
        facts_b["sides"][0]["tournament_id"] = "different-id-1"
        facts_b["sides"][1]["tournament_id"] = "different-id-2"

        assert collision_key(facts_a) == collision_key(facts_b)

    def test_key_differs_for_a_different_collision(self):
        facts_a = _facts()
        facts_b = _facts()
        facts_b["arena"] = "Holmen ishall"

        assert collision_key(facts_a) != collision_key(facts_b)


class TestArenaConflictPromptAndVerdict:
    def test_prompt_lists_available_tournament_ids(self):
        context = build_arena_conflict_decision_context("run-1", _facts())
        prompt = build_arena_conflict_decision_prompt(context)
        assert "aaa11111" in prompt
        assert "bbb22222" in prompt

    def test_parses_a_well_formed_verdict(self):
        context = build_arena_conflict_decision_context("run-1", _facts())
        verdict = "resolve_arena_conflict\naaa11111\nJar's U11 tournament has a tighter hosting deficit."
        action = parse_arena_conflict_verdict(context, verdict)

        assert action.action_id == "resolve_arena_conflict"
        assert action.arguments == {"keep_tournament_id": "aaa11111"}
        assert "hosting deficit" in action.rationale

    def test_falls_back_to_request_operator_on_unnamed_tournament(self):
        context = build_arena_conflict_decision_context("run-1", _facts())
        verdict = "resolve_arena_conflict\nI'm not sure which one"
        action = parse_arena_conflict_verdict(context, verdict)

        assert action.action_id == "request_operator"

    def test_falls_back_to_request_operator_on_empty_verdict(self):
        context = build_arena_conflict_decision_context("run-1", _facts())
        action = parse_arena_conflict_verdict(context, "")

        assert action.action_id == "request_operator"

    def test_request_operator_action_passes_through(self):
        context = build_arena_conflict_decision_context("run-1", _facts())
        verdict = "request_operator\nneed a human here"
        action = parse_arena_conflict_verdict(context, verdict)

        assert action.action_id == "request_operator"


class TestArenaConflictDecisionRecord:
    def test_record_carries_stable_key_and_provenance(self):
        record = arena_conflict_decision_record(
            _facts(), "aaa11111", "bbb22222", "kept the higher-deficit host",
            decided_by="harness", decided_at="2026-09-15T00:00:00+00:00",
        )
        assert record["key"] == collision_key(_facts())
        assert record["keep_tournament_id"] == "aaa11111"
        assert record["manual_tournament_id"] == "bbb22222"
        assert record["decided_by"] == "harness"
