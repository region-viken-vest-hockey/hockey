"""Public CLI contract suite: the thin external boundary of every top-level command.

These tests deliberately sit *outside* the Python call graph: each case runs the
real ``rvv-miniputt`` module in a subprocess (``python -m
tournament_scheduler.cli.rvv_cli``) and asserts only the contract an operator or
harness adapter actually observes -- usage text, exit codes, stdout vs stderr,
and machine-parseable ``--json`` stdout. They complement, and do not replace,
the internal unit/component tests that exercise ``build_parser()`` and
``_cmd_*`` handlers directly.

Marked ``integration`` (subprocess-based) so it runs in ``scripts/check
full`` / the dedicated ``cli-contracts`` phase but not the quick unit suite.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.integration


def _top_level_commands() -> list[str]:
    """Discover the maintained top-level commands from the canonical parser.

    Deriving the list from the parser keeps the contract suite in lockstep with
    the CLI surface: adding a command without contract coverage extends this
    parameterization instead of silently escaping it.
    """
    from tournament_scheduler.cli.args import build_parser

    parser = build_parser()
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if action.dest == "command" and isinstance(choices, dict):
            return sorted(choices)
    raise AssertionError("could not discover top-level commands from the CLI parser")


TOP_LEVEL_COMMANDS = _top_level_commands()


def _run_cli(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tournament_scheduler.cli.rvv_cli", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.mark.parametrize("command", TOP_LEVEL_COMMANDS)
def test_top_level_command_help_exits_zero_with_usage(command: str) -> None:
    result = _run_cli([command, "--help"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "usage:" in result.stdout.lower()
    assert command in result.stdout
    assert "Traceback" not in result.stderr


def test_bare_invocation_prints_help_without_traceback() -> None:
    result = _run_cli([])
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()
    assert "Traceback" not in result.stderr


def test_unknown_command_is_an_argparse_error_without_traceback() -> None:
    result = _run_cli(["definitely-not-a-command"])
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "usage:" in result.stderr.lower()


@pytest.mark.parametrize(
    "argv",
    [
        ["season", "status"],  # requires --season
        ["registrations", "validate"],  # requires --input and a source path
        ["scrape"],  # requires --club
        ["waiver", "create"],  # requires record arguments
    ],
)
def test_expected_validation_errors_use_stderr_and_exit_2(argv: list[str]) -> None:
    """Expected user errors are argparse-style: exit 2, usage on stderr, no
    traceback, and no stray usage on stdout."""
    result = _run_cli([*argv, "--work-dir", "/tmp/rvv-contract-nonexistent"])
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert "usage:" in result.stderr.lower()
    assert result.stdout.strip() == ""


def test_domain_error_is_reported_cleanly_without_traceback() -> None:
    """A domain-level user error (missing registrations file) exits non-zero
    with a readable message rather than an uncaught traceback."""
    result = _run_cli(
        [
            "registrations",
            "validate",
            "/tmp/rvv-contract-missing-registrations.csv",
            "--input",
            "/tmp/rvv-contract-missing-input.xlsx",
        ]
    )
    assert result.returncode in (1, 2), result.stdout + result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "argv",
    [
        ["status", "--json"],
        ["sources", "status", "--json"],
        ["operator", "questions", "--json"],
        ["stage3", "session", "--json"],
    ],
)
def test_json_commands_emit_only_machine_parseable_stdout(argv: list[str], tmp_path: Path) -> None:
    """A ``--json`` command must print *only* JSON on stdout so a harness can
    parse the process output directly, with no Rich decoration or log lines."""
    work_dir = tmp_path / ".pipeline"
    result = _run_cli([*argv, "--work-dir", str(work_dir)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    # The entire stdout must be one JSON document (whitespace aside).
    parsed = json.loads(result.stdout)
    assert parsed is not None


def test_season_publication_evidence_json_is_machine_readable(tmp_path: Path) -> None:
    from tests.test_published_season_sealing import _tournaments_abc, _write_canonical

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())

    result = _run_cli(
        [
            "season",
            "publication-evidence",
            "--season",
            "2026-2027",
            "--root",
            str(root),
            "--json",
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["season"] == "2026-2027"
    assert payload["active_publication"] is None
    assert payload["retained_evidence"] == []
