"""Unit tests for tournament_scheduler.llm_judge.prompts (issue #260 Phase 2)."""

from __future__ import annotations

import pytest

from tournament_scheduler.application.decisions import DecisionContext
from tournament_scheduler.llm_judge.prompts import (
    build_action_decision_prompt,
    build_decision_context,
    build_decision_prompt,
    build_stage_prompt,
    load_stage_gating_policy,
    parse_action_verdict,
)


@pytest.mark.parametrize("stage_key", ["config", "stage1", "scraping", "stage2", "planning", "stage3"])
def test_load_stage_gating_policy_reads_canonical_skill_md(stage_key: str) -> None:
    policy = load_stage_gating_policy(stage_key)

    assert policy
    assert "unavailable" not in policy  # did not fall back
    assert "proceed" in policy.lower() or "abort" in policy.lower()


def test_load_stage_gating_policy_falls_back_for_unknown_stage() -> None:
    assert load_stage_gating_policy("not-a-real-stage") == (
        "Canonical policy at .agents/skills/rvv/SKILL.md was unavailable. "
        "Use best judgment: proceed only if the stage facts show no hard "
        "problems; abort if the stage produced clearly insufficient or "
        "invalid data."
    )


def test_build_decision_context_carries_facts_and_proceed_abort_vocabulary() -> None:
    context = build_decision_context("scraping", {"sources_scanned": 10, "blocked": ["a", "b"]})

    assert context.capability == "scraping"
    assert context.available_actions == (
        "proceed",
        "abort",
        "retry_stage",
        "request_operator",
        "recover_source",
    )
    assert context.facts["sources_scanned"] == 10
    assert context.facts["blocked_count"] == 2


def test_build_decision_context_omits_recover_source_without_blocked_sources() -> None:
    context = build_decision_context("scraping", {"sources_scanned": 10, "blocked": []})

    assert "recover_source" not in context.available_actions


def test_build_decision_context_offers_generic_actions_for_config_stage() -> None:
    context = build_decision_context("config", {"sources": 5})

    assert context.available_actions == ("proceed", "abort", "retry_stage", "request_operator")


@pytest.mark.parametrize("stage_key", ["export", "stage4"])
def test_build_decision_context_supports_export_stage(stage_key: str) -> None:
    context = build_decision_context(stage_key, {"files_written": ["a.ics"], "errors": []})

    assert context.capability == stage_key
    assert context.facts["files_written"] == ["a.ics"]


def test_build_decision_context_rejects_unknown_stage() -> None:
    with pytest.raises(ValueError, match="Unknown stage name"):
        build_decision_context("not-a-real-stage", {})


def test_build_decision_prompt_quotes_canonical_policy_not_inline_thresholds() -> None:
    context = build_decision_context("scraping", {"sources_scanned": 4, "blocked": ["a", "b", "c"]})

    prompt = build_decision_prompt(context)

    assert "fewer than half" in prompt  # sourced from SKILL.md, not hardcoded here
    assert ".agents/skills/rvv/SKILL.md" in prompt
    assert "PROCEED" in prompt
    assert "ABORT" in prompt


def test_build_stage_prompt_still_returns_a_string_for_each_known_stage() -> None:
    prompt = build_stage_prompt("planning", {"tournaments_planned": 12, "warnings": ["x"]})

    assert "Season Planning (Stage 3)" in prompt
    assert "tournaments_planned: 12" in prompt


def test_build_stage_prompt_raises_on_unknown_stage() -> None:
    with pytest.raises(ValueError, match="Unknown stage name"):
        build_stage_prompt("not-a-real-stage", {})


def _action_context(**overrides) -> DecisionContext:
    defaults = dict(
        run_id="run-1",
        capability="stage3_optimize",
        stage="stage3",
        objective="Decide whether to adopt the rerun.",
        facts={"composite_score": 12.5},
        hard_violations=(),
        warnings=("U10: pairs_meeting_3_plus",),
        scorecard={"dominates_baseline": True},
        available_actions=("apply_candidate", "keep_baseline", "request_operator"),
    )
    defaults.update(overrides)
    return DecisionContext(**defaults)


class TestBuildActionDecisionPrompt:
    def test_includes_objective_facts_warnings_scorecard_and_action_choices(self) -> None:
        context = _action_context()

        prompt = build_action_decision_prompt(context)

        assert "Decide whether to adopt the rerun." in prompt
        assert "composite_score: 12.5" in prompt
        assert "U10: pairs_meeting_3_plus" in prompt
        assert "dominates_baseline: True" in prompt
        assert "apply_candidate, keep_baseline, request_operator" in prompt

    def test_includes_hard_violations_when_present(self) -> None:
        context = _action_context(hard_violations=("arena_interval_conflict: t1/t2",))

        prompt = build_action_decision_prompt(context)

        assert "Hard violations" in prompt
        assert "arena_interval_conflict: t1/t2" in prompt


class TestParseActionVerdict:
    def test_matches_action_id_case_insensitively(self) -> None:
        context = _action_context()

        action = parse_action_verdict(context, "apply_candidate\nBecause it dominates baseline.")

        assert action.action_id == "apply_candidate"
        assert action.rationale == "Because it dominates baseline."

    def test_matches_with_surrounding_whitespace_and_punctuation(self) -> None:
        context = _action_context()

        action = parse_action_verdict(context, "  KEEP_BASELINE.  \n")

        assert action.action_id == "keep_baseline"

    def test_unparseable_reply_falls_back_to_request_operator(self) -> None:
        context = _action_context()

        action = parse_action_verdict(context, "I'm not sure, maybe apply it?")

        assert action.action_id == "request_operator"
        assert "question" in action.arguments

    def test_unparseable_reply_without_request_operator_returns_raw_text(self) -> None:
        context = _action_context(available_actions=("apply_candidate", "keep_baseline"))

        action = parse_action_verdict(context, "garbage reply")

        assert action.action_id == "garbage reply"
