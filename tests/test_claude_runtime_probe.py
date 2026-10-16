"""Claude runtime readiness states, classified offline through a fake runner."""

from __future__ import annotations

import pytest

from tests.claude_runtime_support import auth_output, not_installed_runner, runner_for, version_output
from tournament_scheduler.infrastructure.claude_runtime import (
    CommandResult,
    RuntimeState,
    Version,
    probe_runtime,
)
from tournament_scheduler.infrastructure.claude_runtime.probe import (
    AUTH_STATUS_ARGV,
    parse_auth_status,
)


def test_not_installed_when_executable_is_missing() -> None:
    readiness = probe_runtime(not_installed_runner())

    assert readiness.state is RuntimeState.NOT_INSTALLED
    assert readiness.ready is False
    assert readiness.version is None
    assert readiness.recovery and "Install" in readiness.recovery


def test_unauthenticated_when_status_reports_signed_out() -> None:
    runner = runner_for(version_output("2.1.296"), auth_output(False))

    readiness = probe_runtime(runner)

    assert readiness.state is RuntimeState.NOT_AUTHENTICATED
    assert readiness.version == "2.1.296"
    assert readiness.recovery and "claude auth login" in readiness.recovery


def test_incompatible_version_fails_before_auth_is_checked() -> None:
    runner = runner_for(version_output("1.9.4"))

    readiness = probe_runtime(runner)

    assert readiness.state is RuntimeState.INCOMPATIBLE_VERSION
    assert readiness.version == "1.9.4"
    assert "2.1.0" in readiness.message
    assert AUTH_STATUS_ARGV not in runner.calls


@pytest.mark.parametrize("version", ["3.0.0", "3.2.1"])
def test_next_major_version_is_excluded_by_contract(version: str) -> None:
    readiness = probe_runtime(runner_for(version_output(version)))

    assert readiness.state is RuntimeState.INCOMPATIBLE_VERSION


def test_ready_when_version_in_range_and_signed_in() -> None:
    readiness = probe_runtime(runner_for(version_output("2.1.296"), auth_output(True)))

    assert readiness.state is RuntimeState.READY
    assert readiness.ready is True
    assert readiness.version == "2.1.296"
    assert readiness.auth_method == "claude.ai"
    assert readiness.recovery is None


def test_unparseable_version_is_unverifiable_not_ready() -> None:
    readiness = probe_runtime(runner_for(CommandResult(0, "something odd", "")))

    assert readiness.state is RuntimeState.UNVERIFIABLE
    assert readiness.ready is False


def test_failed_version_command_is_unverifiable() -> None:
    readiness = probe_runtime(runner_for(CommandResult(1, "", "boom")))

    assert readiness.state is RuntimeState.UNVERIFIABLE


@pytest.mark.parametrize("auth", [CommandResult(1, "", "err"), CommandResult(0, "not json", "")])
def test_unreadable_auth_status_is_unverifiable(auth: CommandResult) -> None:
    readiness = probe_runtime(runner_for(version_output("2.1.296"), auth))

    assert readiness.state is RuntimeState.UNVERIFIABLE
    assert readiness.version == "2.1.296"


def test_parse_auth_status_requires_boolean_login_flag() -> None:
    assert parse_auth_status('{"loggedIn": "yes"}') is None
    assert parse_auth_status("[]") is None
    assert parse_auth_status('{"loggedIn": false}') == {"loggedIn": False, "authMethod": None}


def test_version_parsing_and_ordering() -> None:
    assert Version.parse("2.1.296 (Claude Code)") == Version(2, 1, 296)
    assert Version.parse("no version here") is None
    assert Version(2, 0, 9) < Version(2, 1, 0) < Version(3, 0, 0)
