"""Injectable git process boundary.

Every repository operation goes through a :data:`GitRunner`. Production uses
:func:`run_git`; tests inject fakes or run real git against temporary local
repositories. Credentials are passed only through the child environment, never
through argv or files on disk.
"""

from __future__ import annotations

import base64
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import GitUnavailableError

DEFAULT_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


GitRunner = Callable[[Sequence[str], Path, "Mapping[str, str] | None"], GitResult]


def run_git(
    args: Sequence[str],
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> GitResult:
    """Run ``git`` with ``args`` in ``cwd``. Never prompts for credentials."""
    child_env = dict(os.environ)
    child_env["GIT_TERMINAL_PROMPT"] = "0"
    child_env["LC_ALL"] = "C"
    if env:
        child_env.update(env)
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=child_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitUnavailableError(f"git {args[0] if args else ''} could not run: {exc}") from exc
    return GitResult(completed.returncode, completed.stdout, completed.stderr)


def https_auth_env(token: str | None) -> dict[str, str]:
    """Return child-process environment that authenticates HTTPS git with ``token``.

    The token is delivered as a per-process ``http.extraheader`` through
    ``GIT_CONFIG_*`` variables. It is not written to ``.git/config``, a credential
    helper, a remote URL or the command line.
    """
    if not token:
        return {}
    basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }
