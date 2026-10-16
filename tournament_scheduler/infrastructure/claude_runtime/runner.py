"""Production process boundary for the Claude CLI.

Only this module starts the Claude executable. Probes and dispatch receive a
runner, so tests use fakes and never reach a real Claude process or network.
Output is captured and returned to the caller; it is never logged here.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .probe import CLAUDE_EXECUTABLE, CommandResult, RuntimeNotFoundError

PROBE_TIMEOUT_SECONDS = 30
TIMEOUT_RETURNCODE = 124


def resolve_claude(argv: Sequence[str]) -> list[str]:
    """Return argv with the executable resolved on PATH, or raise if it is absent."""
    if not argv or argv[0] != CLAUDE_EXECUTABLE:
        raise ValueError("only the Claude CLI executable may be started here")
    resolved = shutil.which(CLAUDE_EXECUTABLE)
    if resolved is None:
        raise RuntimeNotFoundError(CLAUDE_EXECUTABLE)
    return [resolved, *argv[1:]]


def run_probe(argv: Sequence[str]) -> CommandResult:
    """Run a short probe command (version or auth status) with captured output."""
    try:
        completed = subprocess.run(
            resolve_claude(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise RuntimeNotFoundError(CLAUDE_EXECUTABLE) from exc
    except subprocess.TimeoutExpired:
        return CommandResult(TIMEOUT_RETURNCODE, "", "timeout")
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def run_operate(argv: Sequence[str], cwd: Path) -> CommandResult:
    """Run a dispatched operate request in the repository checkout (no timeout)."""
    try:
        completed = subprocess.run(
            resolve_claude(argv),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeNotFoundError(CLAUDE_EXECUTABLE) from exc
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def run_login_interactive(argv: Sequence[str]) -> int:
    """Start the supported login command attached to the operator's console.

    The Claude CLI owns the browser flow and stores its own credentials outside
    this repository. Nothing is captured or logged here.
    """
    return subprocess.call(resolve_claude(argv))
