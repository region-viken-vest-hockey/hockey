"""Recoverable sign-in: launch the supported Claude login command.

The browser/OAuth flow is performed by the Claude CLI itself. This module only
starts ``claude auth login`` as a subprocess and returns its exit code. It does
not read, store or transform credentials.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .probe import LOGIN_ARGV
from .runner import run_login_interactive

Launcher = Callable[[Sequence[str]], int]


def launch_login(launcher: Launcher = run_login_interactive) -> int:
    """Run the Claude sign-in command and return its exit code."""
    return launcher(LOGIN_ARGV)
