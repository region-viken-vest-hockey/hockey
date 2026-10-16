"""Hermetic regression tests for the shared operate repository-freshness gate."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "operate-preflight.py"
spec = importlib.util.spec_from_file_location("rvv_operate_preflight", SCRIPT)
assert spec is not None and spec.loader is not None
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repositories(tmp_path: Path) -> tuple[Path, Path, Path]:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "checkout"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(origin))
    _git(tmp_path, "clone", str(origin), str(seed))
    _git(seed, "config", "user.name", "Test Operator")
    _git(seed, "config", "user.email", "operator@example.invalid")
    (seed / "workflow.txt").write_text("old contract\n", encoding="utf-8")
    _git(seed, "add", "workflow.txt")
    _git(seed, "commit", "-m", "initial workflow")
    _git(seed, "push", "origin", "main")
    _git(tmp_path, "clone", str(origin), str(checkout))
    return origin, seed, checkout


def _advance_origin(seed: Path, value: str = "fixed contract\n") -> str:
    (seed / "workflow.txt").write_text(value, encoding="utf-8")
    _git(seed, "add", "workflow.txt")
    _git(seed, "commit", "-m", "fix workflow contract")
    _git(seed, "push", "origin", "main")
    return _git(seed, "rev-parse", "HEAD")


def test_preflight_fetches_advanced_origin_and_records_executed_contract(tmp_path: Path) -> None:
    _origin, seed, checkout = _repositories(tmp_path)
    old_head = _git(checkout, "rev-parse", "HEAD")
    fixed_head = _advance_origin(seed)

    report = preflight.prepare(checkout)

    assert report["status"] == "READY"
    assert report["branch"] == "main"
    assert report["local_head_before"] == old_head
    assert report["local_head"] == fixed_head
    assert report["origin_main"] == fixed_head
    assert report["refresh_performed"] is True
    assert report["update_performed"] is True
    assert report["workflow_commit"] == fixed_head
    assert report["reason"] == (
        "Local main is tracked-clean and equals the freshly fetched origin/main."
    )
    assert (checkout / "workflow.txt").read_text(encoding="utf-8") == "fixed contract\n"


def test_preflight_preserves_untracked_artifact_while_fast_forwarding(tmp_path: Path) -> None:
    _origin, seed, checkout = _repositories(tmp_path)
    fixed_head = _advance_origin(seed)
    artifact = checkout / "local-operator-artifact.txt"
    artifact_contents = "must survive\n"
    artifact.write_text(artifact_contents, encoding="utf-8")

    report = preflight.prepare(checkout)

    assert report["status"] == "READY"
    assert report["update_performed"] is True
    assert report["local_head"] == fixed_head
    assert report["workflow_commit"] == fixed_head
    assert artifact.read_text(encoding="utf-8") == artifact_contents
    assert _git(checkout, "status", "--porcelain") == "?? local-operator-artifact.txt"


def test_preflight_refuses_stale_dirty_main_without_discarding_work(tmp_path: Path) -> None:
    _origin, seed, checkout = _repositories(tmp_path)
    old_head = _git(checkout, "rev-parse", "HEAD")
    fixed_head = _advance_origin(seed)
    local_contents = "local uncommitted work\n"
    (checkout / "workflow.txt").write_text(local_contents, encoding="utf-8")

    report = preflight.prepare(checkout)

    assert report["status"] == "REVIEW_REQUIRED"
    assert report["refresh_performed"] is True
    assert report["update_performed"] is False
    assert report["local_head"] == old_head
    assert report["origin_main"] == fixed_head
    assert report["workflow_commit"] is None
    assert "dirty" in report["reason"]
    assert _git(checkout, "rev-parse", "HEAD") == old_head
    assert (checkout / "workflow.txt").read_text(encoding="utf-8") == local_contents
