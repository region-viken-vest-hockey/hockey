"""Fail-closed pre-mutation check and the single operate dispatch path (offline)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.claude_runtime_support import auth_output, ok, recording_dispatch, runner_for, version_output
from tournament_scheduler.infrastructure.claude_runtime import (
    SUPPORTED_CONTRACT,
    CommandResult,
    RuntimeBlockedError,
    RuntimeReadiness,
    RuntimeState,
    dispatch_operate,
    launch_login,
    operate_argv,
    probe_runtime,
)
from tournament_scheduler.infrastructure.claude_runtime.probe import LOGIN_ARGV

REPO = Path("/unused/checkout")


def _ready() -> RuntimeReadiness:
    return probe_runtime(runner_for(version_output("2.1.296"), auth_output(True)))


def _blocked_states() -> list[RuntimeReadiness]:
    return [
        RuntimeReadiness(RuntimeState.NOT_INSTALLED, "missing"),
        RuntimeReadiness(RuntimeState.NOT_AUTHENTICATED, "signed out"),
        RuntimeReadiness(RuntimeState.AUTH_EXPIRED, "expired"),
        RuntimeReadiness(RuntimeState.INCOMPATIBLE_VERSION, "too old", version="1.0.0"),
        RuntimeReadiness(RuntimeState.UNVERIFIABLE, "unknown"),
    ]


@pytest.mark.parametrize("readiness", _blocked_states(), ids=lambda r: r.state.value)
def test_dispatch_is_refused_before_any_process_starts(readiness: RuntimeReadiness) -> None:
    calls, runner = recording_dispatch()

    with pytest.raises(RuntimeBlockedError) as raised:
        dispatch_operate(
            "reviewer request", readiness=readiness, runner=runner, repo_root=REPO
        )

    assert raised.value.readiness.state is readiness.state
    assert calls == []


def test_protocol_mismatch_fails_closed_before_dispatch() -> None:
    calls, runner = recording_dispatch()

    with pytest.raises(RuntimeBlockedError) as raised:
        dispatch_operate(
            "request",
            readiness=_ready(),
            runner=runner,
            repo_root=REPO,
            protocol_version=SUPPORTED_CONTRACT.protocol_version + 1,
        )

    assert raised.value.readiness.state is RuntimeState.INCOMPATIBLE_VERSION
    assert "protocol" in str(raised.value)
    assert calls == []


def test_ready_dispatch_runs_operate_once_in_repo_root() -> None:
    calls, runner = recording_dispatch(ok("done"))

    outcome = dispatch_operate("show status", readiness=_ready(), runner=runner, repo_root=REPO)

    assert calls == [(operate_argv("show status"), REPO)]
    assert outcome.returncode == 0
    assert outcome.output == "done"
    assert outcome.readiness.ready is True


def test_operate_argv_routes_through_the_repository_transport() -> None:
    argv = operate_argv("  review the schedule  ")

    assert argv == ("claude", "-p", "/rvv-miniputt:operate review the schedule")


def test_empty_request_is_rejected_without_dispatch() -> None:
    calls, runner = recording_dispatch()

    with pytest.raises(ValueError):
        dispatch_operate("   ", readiness=_ready(), runner=runner, repo_root=REPO)
    assert calls == []


def test_auth_rejection_during_dispatch_is_reported_as_auth_expired() -> None:
    failed = CommandResult(1, "", 'API Error 401 authentication_error: OAuth token has expired')
    _, runner = recording_dispatch(failed)

    outcome = dispatch_operate("request", readiness=_ready(), runner=runner, repo_root=REPO)

    assert outcome.returncode == 1
    assert outcome.readiness.state is RuntimeState.AUTH_EXPIRED
    assert outcome.readiness.recovery and "claude auth login" in outcome.readiness.recovery


def test_ordinary_failure_keeps_ready_state() -> None:
    _, runner = recording_dispatch(CommandResult(2, "schedule conflict", ""))

    outcome = dispatch_operate("request", readiness=_ready(), runner=runner, repo_root=REPO)

    assert outcome.returncode == 2
    assert outcome.readiness.ready is True


def test_login_launches_only_the_supported_command() -> None:
    seen: list[tuple[str, ...]] = []

    def launcher(argv):  # type: ignore[no-untyped-def]
        seen.append(tuple(argv))
        return 0

    assert launch_login(launcher) == 0
    assert seen == [LOGIN_ARGV]
    assert LOGIN_ARGV == ("claude", "auth", "login")
