"""Harness-presence detection for the LLM judge.

When a known harness (Claude Code, Pi, or an explicit RVV harness) is
orchestrating the pipeline it provides its own in-session judgment. The
headless LLM judge should only be instantiated when no harness is active
(e.g. cron jobs or CI).

Environment variables checked:
    RVV_HARNESS              — explicit override (any non-empty value → harness active)
    CLAUDE_CODE_SESSION_ID   — set by Claude Code
    PI_SESSION_ID            — set by the Pi harness
"""

import os

from .interface import LLMJudge

_HARNESS_ENV_VARS: tuple[str, ...] = (
    "RVV_HARNESS",
    "CLAUDE_CODE_SESSION_ID",
    "PI_SESSION_ID",
)


def is_harness_active() -> bool:
    """Return True if a known harness session is orchestrating the run."""
    return any(os.environ.get(var, "").strip() for var in _HARNESS_ENV_VARS)


def get_judge_if_headless(backend: str | None = None) -> "LLMJudge | None":
    """Return an LLMJudge instance only when no harness is active."""
    if is_harness_active():
        return None

    from . import create_judge  # noqa: PLC0415

    return create_judge(backend)
