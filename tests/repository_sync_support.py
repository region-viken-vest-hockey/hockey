"""Temporary local git repositories (bare remote plus clone) for repository adapter tests.

No test built on these helpers touches the network: the "remote" is a bare
repository on the local filesystem.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.distribution_signing_support import sign
from tournament_scheduler.distribution_update import save_cached_manifest

SEASON_REL = Path("season") / "2026"
INSTALLED = "2.1.0"


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, f"git {args} failed: {completed.stderr}"
    return completed.stdout.strip()


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def write_schedule(repo: Path, body: str) -> None:
    target = repo / SEASON_REL / "schedule.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"revision": body}, sort_keys=True) + "\n", encoding="utf-8")


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", message)
    return head(repo)


@pytest.fixture
def git_identity(monkeypatch):
    for name, value in {
        "GIT_AUTHOR_NAME": "Test Operator",
        "GIT_AUTHOR_EMAIL": "operator@example.invalid",
        "GIT_COMMITTER_NAME": "Test Operator",
        "GIT_COMMITTER_EMAIL": "operator@example.invalid",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def remote_pair(tmp_path, git_identity):
    """Return ``(bare_remote, checkout)`` with one committed season on ``main``."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "--quiet", "-b", "main")
    write_schedule(seed, "rev-1")
    commit_all(seed, "seed season")
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(bare))
    git(seed, "push", "--quiet", str(bare), "main")
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", "--quiet", "--branch", "main", str(bare), str(checkout))
    return bare, checkout


def advance_remote(bare: Path, tmp_path: Path, name: str, body: str) -> str:
    """Push one commit to ``main`` of ``bare`` from a separate clone, simulating a colleague."""
    other = tmp_path / name
    git(tmp_path, "clone", "--quiet", "--branch", "main", str(bare), str(other))
    write_schedule(other, body)
    sha = commit_all(other, f"remote change {name}")
    git(other, "push", "--quiet", "origin", "main")
    return sha


def write_cache(cache_dir: Path, *, minimum_write: str = "2.0.0", latest: str = "2.1.0") -> None:
    raw = json.dumps(
        {
            "schema": 1,
            "package": "rvv-miniputt",
            "channel": "windows-stable",
            "latest_version": latest,
            "minimum_write_version": minimum_write,
            "minimum_supported_version": "1.9.0",
            "artifact": {
                "filename": "rvv-miniputt-2.1.0-windows-x64.zip",
                "url": "https://example.invalid/rvv-miniputt-2.1.0-windows-x64.zip",
                "sha256": "a" * 64,
                "size": 1000,
            },
        }
    ).encode("utf-8")
    save_cached_manifest(cache_dir, raw, sign(raw))
