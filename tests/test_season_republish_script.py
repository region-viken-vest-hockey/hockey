"""Tests for the guarded one-command season republish orchestration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "season-republish.py"


def _fake_cli(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "calls.jsonl"
    cli = tmp_path / "fake-rvv.py"
    cli.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
with open(os.environ["CALL_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
fail_on = os.environ.get("FAIL_ON")
if fail_on and " ".join(sys.argv[1:]).startswith(fail_on):
    raise SystemExit(37)
""",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    return cli, log


def _run(tmp_path: Path, *args: str, fail_on: str | None = None):
    cli, log = _fake_cli(tmp_path)
    env = os.environ.copy()
    env.update({"RVV": str(cli), "CALL_LOG": str(log)})
    if fail_on:
        env["FAIL_ON"] = fail_on
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--season", "2026-2027", "--backend", "llm_bridge", *args],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    return result, calls


def test_preview_runs_full_guarded_chain_without_publishing(tmp_path):
    result, calls = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert calls == [
        ["season", "lifecycle", "--season", "2026-2027", "--json"],
        ["season", "export", "--season", "2026-2027"],
        ["season", "publication-evidence", "--season", "2026-2027", "--json"],
        ["operator", "audit-run", "--backend", "llm_bridge"],
        ["operator", "publish", "--dry-run"],
    ]
    assert "Nothing was published" in result.stdout


def test_confirm_public_publishes_only_after_preview_then_verifies(tmp_path):
    result, calls = _run(tmp_path, "--confirm-public")

    assert result.returncode == 0, result.stderr
    assert calls[-3:] == [
        ["operator", "publish", "--dry-run"],
        ["operator", "publish", "--confirm-public"],
        ["operator", "verify"],
    ]


def test_failure_stops_the_chain(tmp_path):
    result, calls = _run(tmp_path, "--confirm-public", fail_on="operator audit-run")

    assert result.returncode == 37
    assert calls[-1] == ["operator", "audit-run", "--backend", "llm_bridge"]
    assert ["operator", "publish", "--dry-run"] not in calls
    assert ["operator", "publish", "--confirm-public"] not in calls
