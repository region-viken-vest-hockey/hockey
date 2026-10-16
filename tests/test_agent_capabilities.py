"""``rvv-miniputt capabilities --json``: output shape, single version owner and clean stdout."""

from __future__ import annotations

import argparse
import json

import pytest

from tournament_scheduler import __version__, agent_protocol, distribution
from tournament_scheduler.capabilities import build_capabilities
from tournament_scheduler.cli.args import build_parser
from tournament_scheduler.cli.rvv_cli import main
from tournament_scheduler.infrastructure.claude_runtime.contract import SUPPORTED_CONTRACT

EXPECTED_KEYS = {
    "name",
    "application_version",
    "distribution",
    "agent_protocol_version",
    "agent_protocol_supported",
    "canonical_schema_version",
    "claude_runtime_supported",
    "commands",
}


def _command_names() -> list[str]:
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return list(action.choices)
    raise AssertionError("no command group")


def _capabilities_json(capsys: pytest.CaptureFixture[str]) -> dict:
    assert main(["capabilities", "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_capabilities_json_has_the_documented_shape(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _capabilities_json(capsys)

    assert set(payload) == EXPECTED_KEYS
    assert payload["agent_protocol_supported"] == {
        "min": agent_protocol.AGENT_PROTOCOL_MIN_SUPPORTED,
        "max": agent_protocol.AGENT_PROTOCOL_MAX_SUPPORTED,
    }
    assert payload["agent_protocol_version"] == agent_protocol.AGENT_PROTOCOL_VERSION
    assert payload["claude_runtime_supported"] == {
        "min_version": str(SUPPORTED_CONTRACT.min_claude_version),
        "max_version_exclusive": str(SUPPORTED_CONTRACT.max_claude_version_exclusive),
    }
    assert set(payload["canonical_schema_version"]) == {"schedule", "decisions", "physical_layout"}
    assert all(isinstance(value, int) for value in payload["canonical_schema_version"].values())


def test_application_version_comes_from_the_single_version_owner(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _capabilities_json(capsys)
    version_payload = distribution.version_metadata()

    assert payload["application_version"] == __version__ == version_payload["version"]
    assert payload["name"] == version_payload["name"]
    assert payload["distribution"] == version_payload["distribution"]


def test_version_json_reports_the_same_application_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version", "--json"]) == 0
    version_payload = json.loads(capsys.readouterr().out)

    assert version_payload["version"] == _capabilities_json(capsys)["application_version"]


def test_command_list_matches_the_canonical_parser_and_is_sorted(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _capabilities_json(capsys)

    assert payload["commands"] == sorted(_command_names())
    assert {"capabilities", "version", "shell"} <= set(payload["commands"])


def test_capabilities_stdout_is_one_json_document(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["capabilities", "--json"]) == 0
    captured = capsys.readouterr()

    assert captured.err == ""
    assert json.loads(captured.out)
    assert captured.out.count("\n") == 1


def test_human_capabilities_output_is_available_without_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["capabilities"]) == 0
    out = capsys.readouterr().out

    assert "Agent protocol:" in out
    assert "Canonical schema:" in out
    assert "Claude runtime:" in out


def test_build_capabilities_uses_the_given_command_list() -> None:
    report = build_capabilities(["zeta", "alpha"])

    assert report["commands"] == ["alpha", "zeta"]
    assert set(report) == EXPECTED_KEYS
