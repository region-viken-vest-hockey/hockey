"""Unit tests for tournament_scheduler.llm_judge.prompts (issue #260 Phase 2)."""

from __future__ import annotations

import pytest

from tournament_scheduler.llm_judge.prompts import (
    build_decision_context,
    build_decision_prompt,
    build_stage_prompt,
    load_stage_gating_policy,
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
    assert context.available_actions == ("proceed", "abort")
    assert context.facts["sources_scanned"] == 10
    assert context.facts["blocked_count"] == 2


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
