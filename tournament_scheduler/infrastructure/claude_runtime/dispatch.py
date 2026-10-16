"""Fail-closed dispatch of operator requests to the shared operate procedure.

The request is handed to the Claude CLI as ``/rvv-miniputt:operate <request>``, the
repository's existing Claude transport adapter, which loads
``.agents/commands/rvv-miniputt/operate.md``. No scheduling, verification, prompt
policy or audit logic lives here.

Order of checks (all before any process is started):

1. the probe result must be ``ready``;
2. the requested agent protocol must lie in the repository-owned supported range.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from tournament_scheduler.agent_protocol import (
    AGENT_PROTOCOL_MAX_SUPPORTED,
    AGENT_PROTOCOL_MIN_SUPPORTED,
    AGENT_PROTOCOL_VERSION,
)

from .contract import SUPPORTED_CONTRACT, RuntimeContract
from .probe import CLAUDE_EXECUTABLE, CommandResult, RuntimeReadiness, RuntimeState

OPERATE_COMMAND = "/rvv-miniputt:operate"

# Heuristic markers of an authentication rejection in a failed dispatch. They
# are matched case-insensitively and never copied into messages.
AUTH_FAILURE_MARKERS: tuple[str, ...] = (
    "authentication_error",
    "oauth token has expired",
    "run /login",
    "invalid api key",
    "401",
)

DispatchRunner = Callable[[Sequence[str], Path], CommandResult]


class RuntimeBlockedError(Exception):
    """Raised before dispatch when the runtime is not ready or not compatible."""

    def __init__(self, readiness: RuntimeReadiness) -> None:
        self.readiness = readiness
        detail = " ".join(part for part in (readiness.message, readiness.recovery) if part)
        super().__init__(detail)


@dataclass(frozen=True)
class DispatchOutcome:
    returncode: int
    output: str
    readiness: RuntimeReadiness


def check_dispatch_allowed(
    readiness: RuntimeReadiness,
    contract: RuntimeContract = SUPPORTED_CONTRACT,
    protocol_version: int | None = None,
) -> None:
    """Raise :class:`RuntimeBlockedError` unless dispatch may start.

    ``protocol_version`` is the agent protocol a caller asks to speak. It is
    checked against the repository-owned range in ``agent_protocol``; ``None``
    means this package's own current protocol, which is always supported.
    """
    if not readiness.ready:
        raise RuntimeBlockedError(readiness)
    requested = AGENT_PROTOCOL_VERSION if protocol_version is None else protocol_version
    if not contract.supports_protocol(requested):
        raise RuntimeBlockedError(
            RuntimeReadiness(
                state=RuntimeState.INCOMPATIBLE_VERSION,
                message=(
                    f"Agent protocol {requested} is not supported; this package supports "
                    f"protocol {AGENT_PROTOCOL_MIN_SUPPORTED} to {AGENT_PROTOCOL_MAX_SUPPORTED}."
                ),
                version=readiness.version,
            )
        )


def operate_argv(request: str) -> tuple[str, ...]:
    text = request.strip()
    if not text:
        raise ValueError("operator request is empty")
    return (CLAUDE_EXECUTABLE, "-p", f"{OPERATE_COMMAND} {text}")


def _after_dispatch(result: CommandResult, readiness: RuntimeReadiness) -> RuntimeReadiness:
    if result.returncode == 0:
        return readiness
    lowered = (result.stdout + "\n" + result.stderr).lower()
    if any(marker in lowered for marker in AUTH_FAILURE_MARKERS):
        return RuntimeReadiness(
            state=RuntimeState.AUTH_EXPIRED,
            message="Claude rejected the sign-in. It has expired or was revoked.",
            version=readiness.version,
            auth_method=readiness.auth_method,
        )
    return readiness


def dispatch_operate(
    request: str,
    *,
    readiness: RuntimeReadiness,
    runner: DispatchRunner,
    repo_root: Path,
    contract: RuntimeContract = SUPPORTED_CONTRACT,
    protocol_version: int | None = None,
) -> DispatchOutcome:
    """Check readiness and protocol, then run the operate request once.

    ``runner`` is not called when the check fails.
    """
    check_dispatch_allowed(readiness, contract, protocol_version)
    argv = operate_argv(request)
    result = runner(argv, repo_root)
    return DispatchOutcome(
        returncode=result.returncode,
        output=result.stdout,
        readiness=_after_dispatch(result, readiness),
    )
