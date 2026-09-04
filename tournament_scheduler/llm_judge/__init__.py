"""LLM judge package — backend-agnostic interface for headless pipeline evaluation.

Usage::

    from tournament_scheduler.llm_judge import create_judge, get_judge_if_headless

    # In pipeline stages — only creates a judge when no harness is orchestrating:
    judge = get_judge_if_headless()
    if judge is not None:
        verdict = judge.judge(prompt)

    # Direct creation (always instantiates):
    judge = create_judge()          # reads RVV_JUDGE_BACKEND env var
    response = judge.judge(prompt)

Supported backends (value of RVV_JUDGE_BACKEND):
    claude      — Anthropic Claude API (requires ANTHROPIC_API_KEY)
    openai      — OpenAI Chat Completions API (requires OPENAI_API_KEY)
    llm_bridge  — Local LM Studio / llm-bridge at localhost:1234
"""

import os

from .backends import ClaudeJudgeBackend, LLMBridgeJudgeBackend, OpenAIJudgeBackend
from .harness import get_judge_if_headless, is_harness_active
from .interface import LLMJudge
from .prompts import build_decision_context, build_decision_prompt, build_stage_prompt

_BACKENDS: dict[str, type[LLMJudge]] = {
    "claude": ClaudeJudgeBackend,
    "openai": OpenAIJudgeBackend,
    "llm_bridge": LLMBridgeJudgeBackend,
}


def create_judge(backend: str | None = None) -> LLMJudge:
    """Instantiate and return the appropriate LLM judge backend.

    Args:
        backend: One of ``"claude"``, ``"openai"``, or ``"llm_bridge"``.
                 If *None*, the value of the ``RVV_JUDGE_BACKEND`` environment
                 variable is used.

    Returns:
        A concrete :class:`LLMJudge` instance ready to call.

    Raises:
        ValueError: If *backend* is missing or not one of the known values.
    """
    backend = backend or os.environ.get("RVV_JUDGE_BACKEND", "")
    if not backend:
        raise ValueError(
            "No judge backend specified. Set RVV_JUDGE_BACKEND to one of: "
            + ", ".join(_BACKENDS)
        )
    if backend not in _BACKENDS:
        raise ValueError(
            f"Unknown judge backend {backend!r}. Valid values: "
            + ", ".join(_BACKENDS)
        )
    return _BACKENDS[backend]()


__all__ = [
    "LLMJudge",
    "ClaudeJudgeBackend",
    "OpenAIJudgeBackend",
    "LLMBridgeJudgeBackend",
    "create_judge",
    "is_harness_active",
    "get_judge_if_headless",
    "build_stage_prompt",
    "build_decision_context",
    "build_decision_prompt",
]
