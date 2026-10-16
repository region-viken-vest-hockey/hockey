"""Claude runtime readiness probe behind an injectable runner.

The probe runs ``claude --version`` and ``claude auth status --json`` through a
:data:`ClaudeRunner`. It never reads credential files, and it keeps only the
non-secret fields it needs (version, login flag, auth method). Account identity
fields in the status payload are dropped before anything is returned or logged.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .contract import SUPPORTED_CONTRACT, RuntimeContract, Version

CLAUDE_EXECUTABLE = "claude"
VERSION_ARGV: tuple[str, ...] = (CLAUDE_EXECUTABLE, "--version")
AUTH_STATUS_ARGV: tuple[str, ...] = (CLAUDE_EXECUTABLE, "auth", "status", "--json")
LOGIN_ARGV: tuple[str, ...] = (CLAUDE_EXECUTABLE, "auth", "login")


class RuntimeState(str, Enum):
    NOT_INSTALLED = "not-installed"
    NOT_AUTHENTICATED = "not-authenticated"
    INCOMPATIBLE_VERSION = "incompatible-version"
    READY = "ready"
    AUTH_EXPIRED = "auth-expired"
    UNVERIFIABLE = "unverifiable"


class RuntimeNotFoundError(Exception):
    """Raised by a runner when the Claude executable cannot be started."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


# A runner receives argv and returns the result, or raises RuntimeNotFoundError.
ClaudeRunner = Callable[[Sequence[str]], CommandResult]

_RECOVERY: Mapping[RuntimeState, str | None] = {
    RuntimeState.READY: None,
    RuntimeState.NOT_INSTALLED: (
        "Install the supported Claude Code CLI, then restart RVV Miniputt. "
        "No API key is needed."
    ),
    RuntimeState.NOT_AUTHENTICATED: (
        "Sign in to Claude from RVV Miniputt (runs 'claude auth login'). "
        "No reinstall is needed."
    ),
    RuntimeState.AUTH_EXPIRED: (
        "Your Claude sign-in has expired. Sign in again (runs 'claude auth login'). "
        "No reinstall is needed."
    ),
    RuntimeState.INCOMPATIBLE_VERSION: "Install a supported Claude Code CLI version.",
    RuntimeState.UNVERIFIABLE: (
        "Claude readiness could not be verified. Run the readiness check again."
    ),
}


@dataclass(frozen=True)
class RuntimeReadiness:
    state: RuntimeState
    message: str
    version: str | None = None
    auth_method: str | None = None

    @property
    def ready(self) -> bool:
        return self.state is RuntimeState.READY

    @property
    def recovery(self) -> str | None:
        return _RECOVERY[self.state]

    def to_dict(self) -> dict[str, Any]:
        """Operator-safe view. Contains no account identity or token material."""
        return {
            "state": self.state.value,
            "message": self.message,
            "version": self.version,
            "auth_method": self.auth_method,
            "recovery": self.recovery,
        }


def _blocked(state: RuntimeState, message: str, **fields: str | None) -> RuntimeReadiness:
    return RuntimeReadiness(state=state, message=message, **fields)


def parse_auth_status(stdout: str) -> dict[str, Any] | None:
    """Return the non-secret fields of ``claude auth status --json``, or ``None``."""
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("loggedIn"), bool):
        return None
    method = payload.get("authMethod")
    return {
        "loggedIn": payload["loggedIn"],
        "authMethod": method if isinstance(method, str) else None,
    }


def probe_runtime(
    runner: ClaudeRunner,
    contract: RuntimeContract = SUPPORTED_CONTRACT,
) -> RuntimeReadiness:
    """Classify the local Claude runtime. Checks run in order and fail closed."""
    try:
        version_result = runner(VERSION_ARGV)
    except RuntimeNotFoundError:
        return _blocked(
            RuntimeState.NOT_INSTALLED,
            "Claude Code CLI was not found on this computer.",
        )
    version = Version.parse(version_result.stdout) if version_result.returncode == 0 else None
    if version is None:
        return _blocked(
            RuntimeState.UNVERIFIABLE,
            "Claude Code CLI did not report a version.",
        )
    if not contract.supports(version):
        return _blocked(
            RuntimeState.INCOMPATIBLE_VERSION,
            f"Claude Code CLI {version} is not supported. "
            f"Supported range: {contract.describe_range()}.",
            version=str(version),
        )

    auth_result = runner(AUTH_STATUS_ARGV)
    auth = parse_auth_status(auth_result.stdout) if auth_result.returncode == 0 else None
    if auth is None:
        return _blocked(
            RuntimeState.UNVERIFIABLE,
            "Claude sign-in status could not be read.",
            version=str(version),
        )
    if not auth["loggedIn"]:
        return _blocked(
            RuntimeState.NOT_AUTHENTICATED,
            "Not signed in to Claude.",
            version=str(version),
        )
    return RuntimeReadiness(
        state=RuntimeState.READY,
        message="Claude is ready.",
        version=str(version),
        auth_method=auth["authMethod"],
    )
