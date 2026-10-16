"""Claude credentials and account identity stay out of readiness, errors and logs (offline)."""

from __future__ import annotations

import logging
from pathlib import Path

from tests.claude_runtime_support import (
    ACCOUNT_EMAIL,
    ACCOUNT_ORG_ID,
    CommandResult,
    auth_output,
    recording_dispatch,
    runner_for,
    version_output,
)
from tournament_scheduler.infrastructure.claude_runtime import (
    RuntimeBlockedError,
    RuntimeState,
    dispatch_operate,
    probe_runtime,
)

PACKAGE_DIR = (
    Path(__file__).resolve().parents[1]
    / "tournament_scheduler"
    / "infrastructure"
    / "claude_runtime"
)
TOKEN = "sk-ant-oat01-FAKE-TOKEN-VALUE"


def _ready_readiness():  # type: ignore[no-untyped-def]
    return probe_runtime(runner_for(version_output("2.1.296"), auth_output(True)))


def _assert_no_identity(text: str) -> None:
    assert ACCOUNT_EMAIL not in text
    assert ACCOUNT_ORG_ID not in text
    assert TOKEN not in text


def test_readiness_drops_account_identity_from_every_view() -> None:
    readiness = _ready_readiness()

    _assert_no_identity(repr(readiness))
    _assert_no_identity(str(readiness.to_dict()))
    _assert_no_identity(readiness.message)
    assert set(readiness.to_dict()) == {"state", "message", "version", "auth_method", "recovery"}


def test_blocked_message_never_echoes_probe_output() -> None:
    leaking = CommandResult(0, f'{{"loggedIn": false, "email": "{ACCOUNT_EMAIL}", '
                               f'"orgId": "{ACCOUNT_ORG_ID}", "token": "{TOKEN}"}}', "")
    readiness = probe_runtime(runner_for(version_output("2.1.296"), leaking))

    assert readiness.state is RuntimeState.NOT_AUTHENTICATED
    _assert_no_identity(repr(readiness) + str(readiness.to_dict()))


def test_dispatch_failure_output_is_not_copied_into_readiness_or_errors() -> None:
    failed = CommandResult(1, f"401 authentication_error for {ACCOUNT_EMAIL} {TOKEN}", "")
    _, runner = recording_dispatch(failed)

    outcome = dispatch_operate(
        "request", readiness=_ready_readiness(), runner=runner, repo_root=Path("/x")
    )

    assert outcome.readiness.state is RuntimeState.AUTH_EXPIRED
    _assert_no_identity(repr(outcome.readiness) + str(outcome.readiness.to_dict()))
    _assert_no_identity(outcome.readiness.message)


def test_blocked_error_text_carries_no_identity() -> None:
    blocked = probe_runtime(runner_for(version_output("1.0.0")))
    try:
        dispatch_operate(
            "request",
            readiness=blocked,
            runner=recording_dispatch()[1],
            repo_root=Path("/x"),
        )
    except RuntimeBlockedError as exc:
        _assert_no_identity(str(exc))
    else:  # pragma: no cover - the check must refuse
        raise AssertionError("dispatch should have been refused")


def test_runtime_package_never_reads_or_logs_credentials() -> None:
    forbidden = (".credentials", "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "import logging")
    for source in PACKAGE_DIR.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{source.name} must not contain {needle!r}"


def test_probe_and_dispatch_emit_no_log_records(caplog) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    failed = CommandResult(1, f"401 authentication_error {TOKEN}", "")
    _, runner = recording_dispatch(failed)

    dispatch_operate("request", readiness=_ready_readiness(), runner=runner, repo_root=Path("/x"))

    assert caplog.records == []
