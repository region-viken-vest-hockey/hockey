"""Regression tests for the root Makefile human-operator interface."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = ROOT / "Makefile"

PUBLIC_TARGETS = [
    "help", "install", "check", "test", "dependency-lock", "secret-scan", "rules-report",
    "operator-run", "operator-run-force", "run", "run-dotenvx", "status", "logs",
    "calendars", "calendars-refresh", "calendars-refresh-dotenvx", "sources-status",
    "questions", "questions-all", "answer", "promote", "publish-preview", "publish",
    "verify-publish", "publish-history", "rollback", "release-dry-run", "release",
]


def _run_make(target: str, *assignments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        ["make", "-f", str(MAKEFILE), target, *assignments],
        cwd=ROOT,
        env=merged_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _fake_cli(tmp_path: Path, *, exit_code: int = 0) -> tuple[Path, Path]:
    log_path = tmp_path / "calls.jsonl"
    script = tmp_path / "fake-cli.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
with open(os.environ['CALL_LOG'], 'a', encoding='utf-8') as handle:
    handle.write(json.dumps(sys.argv[1:], ensure_ascii=False) + '\\n')
sys.exit(int(os.environ.get('FAKE_EXIT', '0')))
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, log_path


def _read_calls(log_path: Path) -> list[list[str]]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def test_help_is_default_and_lists_current_public_targets():
    explicit = _run_make("help")
    default = subprocess.run(
        ["make", "-f", str(MAKEFILE)], cwd=ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    assert explicit.returncode == 0, explicit.stderr
    assert default.returncode == 0, default.stderr
    assert explicit.stdout == default.stdout
    for target in PUBLIC_TARGETS:
        assert f"make {target}" in explicit.stdout or target == "help"
    assert "desktop-start" not in explicit.stdout
    assert "build-mac" not in explicit.stdout
    assert "never publish publicly" in explicit.stdout


def test_public_targets_are_declared_phony_and_desktop_targets_are_gone():
    text = MAKEFILE.read_text(encoding="utf-8")
    public_block = re.search(r"PUBLIC_TARGETS :=(?P<body>.*?)(?:\n\n|\Z)", text, re.DOTALL)
    assert public_block
    normalized = public_block.group("body").replace("\\\n", " ")
    for target in PUBLIC_TARGETS:
        assert target in normalized
    for retired in ["desktop-start", "desktop-clean", "build-mac", "build-windows", "build-linux"]:
        assert retired not in normalized
        assert not re.search(rf"(?:^|\n){re.escape(retired)}:", text)
    assert ".PHONY: $(PUBLIC_TARGETS) all" in text


def test_planning_and_preview_targets_delegate_to_rvv_cli(tmp_path):
    fake, log_path = _fake_cli(tmp_path)
    env = {"CALL_LOG": str(log_path)}
    cases = {
        "operator-run": ["operator", "run"],
        "operator-run-force": ["operator", "run", "--force"],
        "run": ["run"],
        "status": ["status"],
        "logs": ["logs", "list"],
        "calendars": ["calendars"],
        "calendars-refresh": ["calendars", "--refresh"],
        "sources-status": ["sources", "status"],
        "questions": ["operator", "questions"],
        "questions-all": ["operator", "questions", "--all"],
        "publish-preview": ["operator", "publish", "--dry-run"],
        "verify-publish": ["operator", "verify"],
        "publish-history": ["operator", "publish-history"],
    }
    for target, expected in cases.items():
        result = _run_make(target, f"RVV={fake}", env=env)
        assert result.returncode == 0, result.stderr
        assert _read_calls(log_path)[-1] == expected


def test_mutating_targets_require_explicit_variables_before_delegating(tmp_path):
    fake, log_path = _fake_cli(tmp_path)
    env = {"CALL_LOG": str(log_path)}
    cases = [
        ("answer", [], "requires ID"),
        ("promote", [], "requires ID"),
        ("publish", [], "CONFIRM_PUBLIC=1"),
        ("rollback", [], "requires RUN_ID"),
        ("release-dry-run", [], "requires TAG"),
        ("release", [], "requires TAG"),
    ]
    for target, assignments, message in cases:
        result = _run_make(target, f"RVV={fake}", f"RELEASE={fake}", *assignments, env=env)
        assert result.returncode != 0, target
        assert message in result.stderr
    assert _read_calls(log_path) == []


def test_publish_rollback_and_release_delegate_only_after_safeguards(tmp_path):
    fake, log_path = _fake_cli(tmp_path)
    env = {"CALL_LOG": str(log_path)}

    assert _run_make("publish", f"RVV={fake}", "CONFIRM_PUBLIC=1", env=env).returncode == 0
    assert _read_calls(log_path)[-1] == ["operator", "publish", "--confirm-public"]
    assert _run_make("rollback", f"RVV={fake}", "RUN_ID=run-2026", "CONFIRM_PUBLIC=1", env=env).returncode == 0
    assert _read_calls(log_path)[-1] == ["operator", "rollback", "run-2026", "--confirm-public"]
    assert _run_make("release-dry-run", f"RELEASE={fake}", "TAG=v2.0.0", env=env).returncode == 0
    assert _read_calls(log_path)[-1] == ["--dry-run", "v2.0.0"]


def test_ci_invokes_only_current_scripts_check_phase_selectors():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "ci.md").read_text(encoding="utf-8")
    check_script = (ROOT / "scripts" / "check").read_text(encoding="utf-8")

    for phase in ["dependency-lock", "quick", "operator", "reproducibility", "cli-smoke"]:
        assert f"scripts/check {phase}" in ci
        assert f"scripts/check {phase}" in docs
        assert f"{phase})" in check_script
    for retired in ["desktop-backend", "desktop-packaging"]:
        assert retired not in ci
        assert retired not in check_script
    assert "run_all" in check_script


def test_failure_from_underlying_command_is_not_masked(tmp_path):
    fake, log_path = _fake_cli(tmp_path)
    env = {"CALL_LOG": str(log_path), "FAKE_EXIT": "37"}
    result = _run_make("status", f"RVV={fake}", env=env)
    assert result.returncode != 0
    assert _read_calls(log_path)[-1] == ["status"]


def test_normal_run_targets_cannot_publish():
    text = MAKEFILE.read_text(encoding="utf-8")
    for target in ["all", "run", "operator-run", "operator-run-force"]:
        match = re.search(rf"(?:^|\n){re.escape(target)}:[^\n]*(?P<body>.*?)(?:\n\S|\Z)", text, re.DOTALL)
        assert match, target
        assert "--confirm-public" not in match.group("body")
        assert "operator publish" not in match.group("body")
