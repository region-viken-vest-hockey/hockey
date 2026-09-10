"""Unit tests for _check_stage2_checkpoint in pipeline_orchestrator."""

from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from tournament_scheduler.cli.pipeline_orchestrator.stage2 import _check_stage2_checkpoint


def _console() -> Console:
    return Console(file=open("/dev/null", "w"))


def _make_checkpoint(
    *,
    sources: list[dict] | None = None,
    blocked: list[str] | None = None,
) -> dict:
    """Return a minimal Stage 2 checkpoint dict."""
    if sources is None:
        sources = [{"name": "source_a", "event_count": 5, "blocked": False}]
    return {
        "sources": sources,
        "blocked": blocked if blocked is not None else [],
    }


# ---------------------------------------------------------------------------
# Happy path — all sources OK
# ---------------------------------------------------------------------------


def test_all_sources_ok_returns_true():
    """When all sources have events and none are blocked, gate must return True."""
    console = _console()
    log_fn = MagicMock()
    chk = _make_checkpoint(
        sources=[
            {"name": "a", "event_count": 3, "blocked": False},
            {"name": "b", "event_count": 7, "blocked": False},
        ]
    )
    result = _check_stage2_checkpoint(chk, True, console, log_fn)
    assert result is True


# ---------------------------------------------------------------------------
# Zero events — should fail in strict mode
# ---------------------------------------------------------------------------


def test_zero_events_strict_returns_false():
    """When no source has events, strict mode must return False."""
    console = _console()
    log_fn = MagicMock()
    chk = _make_checkpoint(
        sources=[{"name": "a", "event_count": 0, "blocked": False}]
    )
    with patch("builtins.input", side_effect=AssertionError("input must not be called")):
        result = _check_stage2_checkpoint(chk, True, console, log_fn)
    assert result is False


def test_zero_events_non_strict_returns_true():
    """When no source has events, non-strict mode must return True."""
    console = _console()
    log_fn = MagicMock()
    chk = _make_checkpoint(
        sources=[{"name": "a", "event_count": 0, "blocked": False}]
    )
    result = _check_stage2_checkpoint(chk, False, console, log_fn)
    assert result is True


def test_zero_events_strict_interactive_reviewed_defers_instead_of_failing():
    """Interactive mode: zero-events sufficiency is not pre-empted by this
    gate — it defers to the DecisionContext an interactive harness reads
    right after (issue #260 P1)."""
    console = _console()
    log_fn = MagicMock()
    chk = _make_checkpoint(
        sources=[{"name": "a", "event_count": 0, "blocked": False}]
    )
    with patch("builtins.input", side_effect=AssertionError("input must not be called")):
        result = _check_stage2_checkpoint(chk, True, console, log_fn, interactive_reviewed=True)
    assert result is True


def test_zero_events_strict_unattended_still_fails():
    """Not interactive and no judge (fully unattended) keeps the existing
    hard-fail safety net unchanged."""
    console = _console()
    log_fn = MagicMock()
    chk = _make_checkpoint(
        sources=[{"name": "a", "event_count": 0, "blocked": False}]
    )
    result = _check_stage2_checkpoint(chk, True, console, log_fn, interactive_reviewed=False)
    assert result is False


# ---------------------------------------------------------------------------
# Blocked sources — with events from at least one source
# ---------------------------------------------------------------------------


def test_blocked_with_events_harness_returns_true():
    """When harness active and at least one source has events, must auto-proceed."""
    console = _console()
    log_fn = MagicMock()
    chk = _make_checkpoint(
        sources=[
            {"name": "a", "event_count": 4, "blocked": False},
            {"name": "b", "event_count": 0, "blocked": True},
        ],
        blocked=["b"],
    )
    with patch("builtins.input", side_effect=AssertionError("input must not be called in harness mode")):
        result = _check_stage2_checkpoint(chk, True, console, log_fn, harness_active=True)
    assert result is True


def test_blocked_with_events_non_strict_returns_true():
    """Blocked sources + non-strict: must return True without prompting."""
    console = _console()
    log_fn = MagicMock()
    chk = _make_checkpoint(
        sources=[
            {"name": "a", "event_count": 2, "blocked": False},
            {"name": "b", "event_count": 0, "blocked": True},
        ],
        blocked=["b"],
    )
    with patch("builtins.input", side_effect=AssertionError("input must not be called in non-strict mode")):
        result = _check_stage2_checkpoint(chk, False, console, log_fn)
    assert result is True


# ---------------------------------------------------------------------------
# Strict + interactive — operator approves or declines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["j", "y", "ja", "yes"])
def test_strict_operator_approves_returns_true(answer: str):
    """All accepted Norwegian/English approval answers must result in True."""
    console = _console()
    log_fn = MagicMock()
    chk = _make_checkpoint(
        sources=[
            {"name": "a", "event_count": 3, "blocked": False},
            {"name": "b", "event_count": 0, "blocked": True},
        ],
        blocked=["b"],
    )
    with patch("builtins.input", return_value=answer):
        result = _check_stage2_checkpoint(chk, True, console, log_fn, harness_active=False)
    assert result is True


def test_strict_operator_declines_returns_false():
    """'n' answer in strict mode must return False."""
    console = _console()
    log_fn = MagicMock()
    chk = _make_checkpoint(
        sources=[
            {"name": "a", "event_count": 3, "blocked": False},
            {"name": "b", "event_count": 0, "blocked": True},
        ],
        blocked=["b"],
    )
    with patch("builtins.input", return_value="n"):
        result = _check_stage2_checkpoint(chk, True, console, log_fn, harness_active=False)
    assert result is False


def test_strict_empty_answer_returns_false():
    """An empty answer in strict mode must return False."""
    console = _console()
    log_fn = MagicMock()
    chk = _make_checkpoint(
        sources=[
            {"name": "a", "event_count": 3, "blocked": False},
            {"name": "b", "event_count": 0, "blocked": True},
        ],
        blocked=["b"],
    )
    with patch("builtins.input", return_value=""):
        result = _check_stage2_checkpoint(chk, True, console, log_fn, harness_active=False)
    assert result is False


def test_strict_eof_returns_false():
    """EOFError from input() in strict mode must be treated as 'n'."""
    console = _console()
    log_fn = MagicMock()
    chk = _make_checkpoint(
        sources=[
            {"name": "a", "event_count": 3, "blocked": False},
            {"name": "b", "event_count": 0, "blocked": True},
        ],
        blocked=["b"],
    )
    with patch("builtins.input", side_effect=EOFError):
        result = _check_stage2_checkpoint(chk, True, console, log_fn, harness_active=False)
    assert result is False


def test_strict_keyboard_interrupt_returns_false():
    """KeyboardInterrupt from input() in strict mode must be treated as 'n'."""
    console = _console()
    log_fn = MagicMock()
    chk = _make_checkpoint(
        sources=[
            {"name": "a", "event_count": 3, "blocked": False},
            {"name": "b", "event_count": 0, "blocked": True},
        ],
        blocked=["b"],
    )
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        result = _check_stage2_checkpoint(chk, True, console, log_fn, harness_active=False)
    assert result is False
