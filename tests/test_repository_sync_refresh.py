"""Fast-forward-only refresh and clone behavior against temporary local git repositories (offline)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.repository_sync_support import advance_remote, commit_all, git, head, write_schedule
from tournament_scheduler.infrastructure.repository_sync import (
    GitResult,
    RepoState,
    RepositoryError,
    clone_repository,
    refresh_main,
)

SCHEDULE = Path("season") / "2026" / "schedule.json"


def test_refresh_fast_forwards_behind_checkout(remote_pair, tmp_path) -> None:
    bare, checkout = remote_pair
    remote_sha = advance_remote(bare, tmp_path, "colleague", "rev-2")
    result = refresh_main(checkout)
    assert result.performed is True
    assert result.before.state is RepoState.BEHIND
    assert result.after.state is RepoState.READY
    assert head(checkout) == remote_sha
    assert "rev-2" in (checkout / SCHEDULE).read_text(encoding="utf-8")


def test_refresh_when_current_is_a_no_op(remote_pair) -> None:
    _, checkout = remote_pair
    before = head(checkout)
    result = refresh_main(checkout)
    assert result.performed is False
    assert head(checkout) == before


def test_refresh_refuses_diverged_checkout_without_touching_either_side(remote_pair, tmp_path) -> None:
    bare, checkout = remote_pair
    write_schedule(checkout, "local-only")
    local_sha = commit_all(checkout, "local canonical change")
    advance_remote(bare, tmp_path, "colleague", "remote-only")
    result = refresh_main(checkout)
    assert result.performed is False
    assert result.after.state is RepoState.DIVERGED
    assert head(checkout) == local_sha
    assert "local-only" in (checkout / SCHEDULE).read_text(encoding="utf-8")
    assert git(bare, "rev-parse", "main") != local_sha


def test_refresh_refuses_dirty_checkout_and_keeps_edits(remote_pair, tmp_path) -> None:
    bare, checkout = remote_pair
    advance_remote(bare, tmp_path, "colleague", "rev-2")
    write_schedule(checkout, "unsaved-edit")
    before = head(checkout)
    result = refresh_main(checkout)
    assert result.performed is False
    assert result.after.state is RepoState.LOCAL_CHANGES
    assert head(checkout) == before
    assert "unsaved-edit" in (checkout / SCHEDULE).read_text(encoding="utf-8")


def test_refresh_refuses_ahead_checkout(remote_pair) -> None:
    _, checkout = remote_pair
    write_schedule(checkout, "local-only")
    local_sha = commit_all(checkout, "local canonical change")
    result = refresh_main(checkout)
    assert result.performed is False
    assert result.after.state is RepoState.AHEAD
    assert head(checkout) == local_sha


def test_refresh_reports_unavailable_when_remote_is_gone(remote_pair, tmp_path) -> None:
    bare, checkout = remote_pair
    before = head(checkout)
    git(tmp_path, "--git-dir", str(bare), "update-ref", "-d", "refs/heads/main")
    result = refresh_main(checkout)
    assert result.performed is False
    assert result.after.state is RepoState.UNAVAILABLE
    assert head(checkout) == before


def test_clone_refuses_non_https_url(tmp_path) -> None:
    with pytest.raises(RepositoryError, match="https"):
        clone_repository("http://example.invalid/repo.git", tmp_path / "dest")


def test_clone_refuses_non_empty_destination(tmp_path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "keep.txt").write_text("operator file\n", encoding="utf-8")
    with pytest.raises(RepositoryError, match="not empty"):
        clone_repository("https://example.invalid/repo.git", dest)
    assert (dest / "keep.txt").exists()


def test_clone_passes_token_through_environment_only(tmp_path) -> None:
    calls = []

    def runner(args, cwd, env):
        calls.append((list(args), dict(env or {})))
        return GitResult(0, "", "")

    dest = tmp_path / "dest"
    clone_repository("https://example.invalid/repo.git", dest, token="ghp_SECRET_TOKEN", runner=runner)
    args, env = calls[0]
    assert args[:2] == ["clone", "--quiet"] and "--branch" in args and "main" in args
    assert all("ghp_SECRET_TOKEN" not in part for part in args)
    assert all("ghp_SECRET_TOKEN" not in value for value in env.values())
    assert env["GIT_CONFIG_KEY_0"] == "http.extraheader"
