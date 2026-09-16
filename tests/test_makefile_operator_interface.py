"""Regression tests for the root Makefile human-operator interface (issue #39)."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = ROOT / "Makefile"

PUBLIC_TARGETS = [
    "help",
    "install",
    "check",
    "test",
    "dependency-lock",
    "secret-scan",
    "rules-report",
    "operator-run",
    "operator-run-force",
    "run",
    "status",
    "logs",
    "calendars",
    "calendars-refresh",
    "sources-status",
    "questions",
    "questions-all",
    "answer",
    "promote",
    "audit-context",
    "audit-evidence",
    "audit-run",
    "audit-submit",
    "publish-preview",
    "publish",
    "verify-publish",
    "publish-history",
    "rollback",
    "release-dry-run",
    "release",
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
    env = {"CALL_LOG": str(log_path), "FAKE_EXIT": str(exit_code)}
    return script, log_path.with_suffix(".env.json") if False else log_path


def _read_calls(log_path: Path) -> list[list[str]]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


class TestMakefileOperatorInterface:
    def test_help_is_default_and_lists_public_targets(self):
        explicit = _run_make("help")
        default = subprocess.run(
            ["make", "-f", str(MAKEFILE)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        assert explicit.returncode == 0, explicit.stderr
        assert default.returncode == 0, default.stderr
        assert explicit.stdout == default.stdout
        for target in PUBLIC_TARGETS:
            assert f"make {target}" in explicit.stdout or target in {"help", "all"}
        assert "never publish publicly" in explicit.stdout
        assert "CONFIRM_PUBLIC=1" in explicit.stdout

    def test_public_targets_are_declared_phony(self):
        text = MAKEFILE.read_text(encoding="utf-8")
        assert ".DEFAULT_GOAL := help" in text
        public_block = re.search(r"PUBLIC_TARGETS :=(?P<body>.*?)(?:\n\n|\Z)", text, re.DOTALL)
        assert public_block, "Makefile should keep one auditable PUBLIC_TARGETS list"
        normalized = public_block.group("body").replace("\\\n", " ")
        for target in PUBLIC_TARGETS:
            assert target in normalized
        assert ".PHONY: $(PUBLIC_TARGETS) all" in text

    def test_planning_and_preview_targets_delegate_to_rvv_cli(self, tmp_path):
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
            "audit-context": ["operator", "audit-context"],
            "audit-evidence": ["operator", "audit-evidence"],
            "publish-preview": ["operator", "publish", "--dry-run"],
            "verify-publish": ["operator", "verify"],
            "publish-history": ["operator", "publish-history"],
        }

        for target, expected in cases.items():
            result = _run_make(target, f"RVV={fake}", env=env)
            assert result.returncode == 0, result.stderr
            assert _read_calls(log_path)[-1] == expected

    def test_args_are_forwarded_to_documented_cli_wrappers(self, tmp_path):
        fake, log_path = _fake_cli(tmp_path)
        env = {"CALL_LOG": str(log_path)}

        result = _run_make("logs", f"RVV={fake}", "ARGS=--count 3", env=env)

        assert result.returncode == 0, result.stderr
        assert _read_calls(log_path)[-1] == ["logs", "list", "--count", "3"]

    def test_human_answer_preserves_spaces_and_shell_characters(self, tmp_path):
        fake, log_path = _fake_cli(tmp_path)
        env = {"CALL_LOG": str(log_path)}
        answer = "Godkjenn samling for U13 & JU14; bruk \"Askerhallen\""

        result = _run_make("answer", f"RVV={fake}", "ID=q-123", f"ANSWER={answer}", env=env)

        assert result.returncode == 0, result.stderr
        assert _read_calls(log_path)[-1] == ["operator", "answer", "q-123", answer]

    def test_promote_preserves_optional_scope_key(self, tmp_path):
        fake, log_path = _fake_cli(tmp_path)
        env = {"CALL_LOG": str(log_path)}

        result = _run_make(
            "promote",
            f"RVV={fake}",
            "ID=q-123",
            "SCOPE=season",
            "SCOPE_KEY=2026/2027 RVV",
            env=env,
        )

        assert result.returncode == 0, result.stderr
        assert _read_calls(log_path)[-1] == [
            "operator",
            "promote",
            "--scope-key",
            "2026/2027 RVV",
            "q-123",
            "season",
        ]

    def test_mutating_targets_require_explicit_variables_before_delegating(self, tmp_path):
        fake, log_path = _fake_cli(tmp_path)
        env = {"CALL_LOG": str(log_path)}

        cases = [
            ("answer", [], "requires ID"),
            ("answer", ["ID=q-1"], "requires ANSWER"),
            ("promote", [], "requires ID"),
            ("promote", ["ID=q-1"], "requires SCOPE"),
            ("audit-run", [], "requires BACKEND"),
            ("audit-submit", [], "requires RESULT_FILE"),
            ("publish", [], "CONFIRM_PUBLIC=1"),
            ("rollback", [], "requires RUN_ID"),
            ("rollback", ["RUN_ID=run-1"], "CONFIRM_PUBLIC=1"),
            ("release-dry-run", [], "requires TAG"),
            ("release", [], "requires TAG"),
        ]

        for target, assignments, message in cases:
            result = _run_make(target, f"RVV={fake}", f"RELEASE={fake}", *assignments, env=env)
            assert result.returncode != 0, target
            assert message in result.stderr
        assert _read_calls(log_path) == []

    def test_publish_rollback_and_release_delegate_only_after_safeguards(self, tmp_path):
        fake, log_path = _fake_cli(tmp_path)
        env = {"CALL_LOG": str(log_path)}

        assert _run_make("audit-run", f"RVV={fake}", "BACKEND=llm_bridge", env=env).returncode == 0
        assert _read_calls(log_path)[-1] == ["operator", "audit-run", "--backend", "llm_bridge"]

        result_file = tmp_path / "audit_result.json"
        result_file.write_text("{}", encoding="utf-8")
        assert _run_make("audit-submit", f"RVV={fake}", f"RESULT_FILE={result_file}", env=env).returncode == 0
        assert _read_calls(log_path)[-1] == ["operator", "audit-submit", "--result-file", str(result_file)]

        assert _run_make("publish", f"RVV={fake}", "CONFIRM_PUBLIC=1", env=env).returncode == 0
        assert _read_calls(log_path)[-1] == ["operator", "publish", "--confirm-public"]

        assert _run_make("rollback", f"RVV={fake}", "RUN_ID=run-2026", "CONFIRM_PUBLIC=1", env=env).returncode == 0
        assert _read_calls(log_path)[-1] == ["operator", "rollback", "run-2026", "--confirm-public"]

        assert _run_make("release-dry-run", f"RELEASE={fake}", "TAG=v2.0.0", env=env).returncode == 0
        assert _read_calls(log_path)[-1] == ["--dry-run", "v2.0.0"]

        assert _run_make("release", f"RELEASE={fake}", "TAG=v2.0.0", env=env).returncode == 0
        assert _read_calls(log_path)[-1] == ["v2.0.0"]

    def test_check_target_delegates_to_canonical_script(self, tmp_path):
        fake, log_path = _fake_cli(tmp_path)
        env = {"CALL_LOG": str(log_path)}

        result = _run_make("check", f"CHECK={fake}", env=env)

        assert result.returncode == 0, result.stderr
        assert _read_calls(log_path)[-1] == []
        makefile = MAKEFILE.read_text(encoding="utf-8")
        check_body = re.search(r"\ncheck:\n(?P<body>.*?)(?:\n\S|\Z)", makefile, re.DOTALL).group("body")
        assert "$(CHECK)" in check_body
        assert "python3 -m pytest" not in check_body

    def test_ci_invokes_scripts_check_phase_selectors(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        docs = (ROOT / "docs" / "ci.md").read_text(encoding="utf-8")
        check_script = (ROOT / "scripts" / "check").read_text(encoding="utf-8")

        for phase in ["dependency-lock", "quick", "operator", "reproducibility", "cli-smoke"]:
            assert f"scripts/check {phase}" in ci
            assert f"scripts/check {phase}" in docs
            assert f"{phase})" in check_script
        assert "scripts/check` (or `make\ncheck`" in docs
        assert "run_all" in check_script

    def test_failure_from_underlying_command_is_not_masked(self, tmp_path):
        fake, log_path = _fake_cli(tmp_path)
        env = {"CALL_LOG": str(log_path), "FAKE_EXIT": "37"}

        result = _run_make("status", f"RVV={fake}", env=env)

        assert result.returncode != 0
        assert _read_calls(log_path)[-1] == ["status"]

    def test_no_normal_run_target_contains_publish_confirmation(self):
        text = MAKEFILE.read_text(encoding="utf-8")
        for target in ["all", "run", "operator-run", "operator-run-force"]:
            match = re.search(rf"(?:^|\n){re.escape(target)}:[^\n]*(?P<body>.*?)(?:\n\S|\Z)", text, re.DOTALL)
            assert match, target
            body = match.group("body")
            assert "--confirm-public" not in body
            assert "operator publish" not in body
