"""Unit tests for tournament_scheduler.llm_judge package."""

from __future__ import annotations

import json
import os
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tournament_scheduler.llm_judge import create_judge
from tournament_scheduler.llm_judge.backends import (
    ClaudeJudgeBackend,
    LLMBridgeJudgeBackend,
    OpenAIJudgeBackend,
)
from tournament_scheduler.llm_judge.harness import get_judge_if_headless, is_harness_active
from tournament_scheduler.pipeline.state import PipelineState, StageName


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(body_dict: dict) -> MagicMock:
    body = json.dumps(body_dict).encode()
    cm = MagicMock()
    cm.__enter__ = lambda s: s
    cm.__exit__ = MagicMock(return_value=False)
    cm.read = MagicMock(return_value=body)
    return cm


# ---------------------------------------------------------------------------
# is_harness_active
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "env_var",
    ["RVV_HARNESS", "CLAUDE_CODE_SESSION_ID", "PI_SESSION_ID", "OPENCODE_SESSION_ID"],
)
def test_is_harness_active_true_for_each_var(env_var: str) -> None:
    clean = {k: "" for k in ("RVV_HARNESS", "CLAUDE_CODE_SESSION_ID", "PI_SESSION_ID", "OPENCODE_SESSION_ID")}
    with patch.dict(os.environ, {**clean, env_var: "some-session-id"}):
        assert is_harness_active() is True


def test_is_harness_active_false_when_no_vars_set() -> None:
    clean = {k: "" for k in ("RVV_HARNESS", "CLAUDE_CODE_SESSION_ID", "PI_SESSION_ID", "OPENCODE_SESSION_ID")}
    with patch.dict(os.environ, clean):
        assert is_harness_active() is False


# ---------------------------------------------------------------------------
# create_judge factory
# ---------------------------------------------------------------------------

def test_create_judge_raises_on_empty_backend() -> None:
    with patch.dict(os.environ, {"RVV_JUDGE_BACKEND": ""}):
        with pytest.raises(ValueError, match="No judge backend specified"):
            create_judge("")


def test_create_judge_raises_on_unknown_backend() -> None:
    with pytest.raises(ValueError, match="Unknown judge backend"):
        create_judge("nonexistent_backend")


def test_create_judge_returns_llm_bridge() -> None:
    judge = create_judge("llm_bridge")
    assert isinstance(judge, LLMBridgeJudgeBackend)


def test_create_judge_reads_env_var() -> None:
    with patch.dict(os.environ, {"RVV_JUDGE_BACKEND": "llm_bridge"}):
        judge = create_judge()
        assert isinstance(judge, LLMBridgeJudgeBackend)


# ---------------------------------------------------------------------------
# LLMBridgeJudgeBackend
# ---------------------------------------------------------------------------

def test_llm_bridge_judge_happy_path() -> None:
    response_body = {"choices": [{"message": {"content": "PROCEED"}}]}
    with patch("urllib.request.urlopen", return_value=_mock_response(response_body)):
        judge = LLMBridgeJudgeBackend()
        result = judge.judge("Is the plan okay?")
    assert result == "PROCEED"


def test_llm_bridge_judge_raises_on_url_error() -> None:
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        judge = LLMBridgeJudgeBackend()
        with pytest.raises(RuntimeError, match="LLM Bridge connection failed"):
            judge.judge("prompt")


def test_llm_bridge_judge_raises_on_malformed_response() -> None:
    # Response missing the expected keys
    with patch("urllib.request.urlopen", return_value=_mock_response({"bad": "response"})):
        judge = LLMBridgeJudgeBackend()
        with pytest.raises(RuntimeError, match="Unexpected LLM Bridge response shape"):
            judge.judge("prompt")


# ---------------------------------------------------------------------------
# ClaudeJudgeBackend
# ---------------------------------------------------------------------------

def test_claude_judge_raises_if_no_api_key() -> None:
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            ClaudeJudgeBackend()


def test_claude_judge_happy_path() -> None:
    response_body = {"content": [{"text": "PROCEED"}]}
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("urllib.request.urlopen", return_value=_mock_response(response_body)):
            judge = ClaudeJudgeBackend()
            result = judge.judge("prompt")
    assert result == "PROCEED"


def test_claude_judge_raises_on_url_error() -> None:
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            judge = ClaudeJudgeBackend()
            with pytest.raises(RuntimeError, match="Claude API connection failed"):
                judge.judge("prompt")


# ---------------------------------------------------------------------------
# OpenAIJudgeBackend
# ---------------------------------------------------------------------------

def test_openai_judge_raises_if_no_api_key() -> None:
    with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            OpenAIJudgeBackend()


def test_openai_judge_happy_path() -> None:
    response_body = {"choices": [{"message": {"content": "PROCEED"}}]}
    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch("urllib.request.urlopen", return_value=_mock_response(response_body)):
            judge = OpenAIJudgeBackend()
            result = judge.judge("prompt")
    assert result == "PROCEED"


# ---------------------------------------------------------------------------
# get_judge_if_headless
# ---------------------------------------------------------------------------

def test_get_judge_if_headless_returns_none_when_harness_active() -> None:
    with patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "abc123"}):
        result = get_judge_if_headless()
    assert result is None


def test_get_judge_if_headless_returns_judge_when_headless() -> None:
    clean = {k: "" for k in ("RVV_HARNESS", "CLAUDE_CODE_SESSION_ID", "PI_SESSION_ID", "OPENCODE_SESSION_ID")}
    with patch.dict(os.environ, {**clean, "RVV_JUDGE_BACKEND": "llm_bridge"}):
        result = get_judge_if_headless()
    assert isinstance(result, LLMBridgeJudgeBackend)


def test_get_judge_if_headless_raises_value_error_when_no_backend() -> None:
    clean = {k: "" for k in ("RVV_HARNESS", "CLAUDE_CODE_SESSION_ID", "PI_SESSION_ID", "OPENCODE_SESSION_ID", "RVV_JUDGE_BACKEND")}
    with patch.dict(os.environ, clean):
        with pytest.raises(ValueError, match="No judge backend specified"):
            get_judge_if_headless()


# ---------------------------------------------------------------------------
# PipelineState.write_judgment
# ---------------------------------------------------------------------------

def test_write_judgment_persists_to_checkpoint(tmp_path: "Path") -> None:
    state = PipelineState(tmp_path)
    # Write a minimal stage checkpoint first so the envelope exists.
    state.write_stage(StageName.CONFIG, {"sources": []})

    state.write_judgment(
        StageName.CONFIG,
        verdict="PROCEED",
        reasoning="Looks good",
        backend="llm_bridge",
    )

    envelope = state.read_envelope(StageName.CONFIG)
    assert "judgment" in envelope
    j = envelope["judgment"]
    assert j["verdict"] == "PROCEED"
    assert j["reasoning"] == "Looks good"
    assert j["backend"] == "llm_bridge"
    assert "judged_at" in j


def test_write_judgment_does_not_overwrite_stage_data(tmp_path: "Path") -> None:
    state = PipelineState(tmp_path)
    state.write_stage(StageName.CONFIG, {"key": "value"})
    state.write_judgment(StageName.CONFIG, verdict="PROCEED")

    # Original stage data must still be intact.
    data = state.read_stage(StageName.CONFIG)
    assert data.get("key") == "value"


def test_write_judgment_abort_verdict_stored(tmp_path: "Path") -> None:
    state = PipelineState(tmp_path)
    state.write_stage(StageName.SCRAPING, {"sources": []})

    state.write_judgment(
        StageName.SCRAPING,
        verdict="ABORT",
        reasoning="Too many blocked sources",
        backend="openai",
    )

    envelope = state.read_envelope(StageName.SCRAPING)
    assert envelope["judgment"]["verdict"] == "ABORT"
    assert envelope["judgment"]["backend"] == "openai"
