"""Fake Claude runner for offline runtime tests. Never starts a process."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from tournament_scheduler.infrastructure.claude_runtime import (
    CommandResult,
    RuntimeNotFoundError,
)
from tournament_scheduler.infrastructure.claude_runtime.probe import (
    AUTH_STATUS_ARGV,
    VERSION_ARGV,
)

ACCOUNT_EMAIL = "operator@example.invalid"
ACCOUNT_ORG_ID = "org-0000-secret-id"


def ok(stdout: str) -> CommandResult:
    return CommandResult(0, stdout, "")


def version_output(version: str) -> CommandResult:
    return ok(f"{version} (Claude Code)\n")


def auth_output(logged_in: bool, **extra: object) -> CommandResult:
    payload: dict[str, object] = {
        "loggedIn": logged_in,
        "authMethod": "claude.ai",
        "email": ACCOUNT_EMAIL,
        "orgId": ACCOUNT_ORG_ID,
        "subscriptionType": "pro",
    }
    payload.update(extra)
    return ok(json.dumps(payload))


class FakeRunner:
    """Maps argv to a canned result or exception and records every call."""

    def __init__(self, responses: Mapping[tuple[str, ...], object]) -> None:
        self._responses = dict(responses)
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: Sequence[str]) -> CommandResult:
        key = tuple(argv)
        self.calls.append(key)
        if key not in self._responses:
            raise AssertionError(f"unexpected Claude call: {key}")
        response = self._responses[key]
        if isinstance(response, BaseException):
            raise response
        assert isinstance(response, CommandResult)
        return response


def runner_for(version: object, auth: object | None = None) -> FakeRunner:
    responses: dict[tuple[str, ...], object] = {VERSION_ARGV: version}
    if auth is not None:
        responses[AUTH_STATUS_ARGV] = auth
    return FakeRunner(responses)


def not_installed_runner() -> FakeRunner:
    return runner_for(RuntimeNotFoundError("claude"))


DispatchCall = tuple[tuple[str, ...], Path]


def recording_dispatch(
    result: CommandResult | None = None,
) -> tuple[list[DispatchCall], Callable[[Sequence[str], Path], CommandResult]]:
    calls: list[DispatchCall] = []

    def run(argv: Sequence[str], cwd: Path) -> CommandResult:
        calls.append((tuple(argv), cwd))
        return result or ok("operate finished")

    return calls, run
