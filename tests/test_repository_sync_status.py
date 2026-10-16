"""Checkout state classification against temporary local git repositories (offline)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.repository_sync_support import (
    advance_remote,
    commit_all,
    git,
    head,
    write_schedule,
)
from tournament_scheduler.infrastructure.repository_sync import (
    GitResult,
    GitUnavailableError,
    RepoFacts,
    RepoState,
    classify_state,
    inspect_checkout,
    run_git,
)


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (RepoFacts(is_worktree=False), RepoState.NOT_CLONED),
        (RepoFacts(is_worktree=True, reachable=False), RepoState.UNAVAILABLE),
        (RepoFacts(is_worktree=True, branch="feature"), RepoState.NOT_ON_MAIN),
        (RepoFacts(is_worktree=True, branch="main", dirty_paths=("season/x",)), RepoState.LOCAL_CHANGES),
        (RepoFacts(is_worktree=True, branch="main", ahead=1, behind=2), RepoState.DIVERGED),
        (RepoFacts(is_worktree=True, branch="main", behind=2), RepoState.BEHIND),
        (RepoFacts(is_worktree=True, branch="main", ahead=1), RepoState.AHEAD),
        (RepoFacts(is_worktree=True, branch="main"), RepoState.READY),
        # Local changes take precedence over divergence: nothing is refreshed while work is dirty.
        (RepoFacts(is_worktree=True, branch="main", dirty_paths=("a",), ahead=1, behind=1), RepoState.LOCAL_CHANGES),
    ],
)
def test_classify_state_is_fail_closed_in_order(facts, expected) -> None:
    assert classify_state(facts) is expected


def test_fresh_clone_is_ready(remote_pair) -> None:
    _, checkout = remote_pair
    snapshot = inspect_checkout(checkout)
    assert snapshot.state is RepoState.READY
    assert snapshot.branch == "main"
    assert snapshot.head == head(checkout)
    assert snapshot.dirty_paths == ()


def test_untracked_file_does_not_make_checkout_dirty(remote_pair) -> None:
    _, checkout = remote_pair
    (checkout / "notes.txt").write_text("scratch\n", encoding="utf-8")
    assert inspect_checkout(checkout).state is RepoState.READY


def test_tracked_modification_is_local_changes(remote_pair) -> None:
    _, checkout = remote_pair
    write_schedule(checkout, "edited-locally")
    snapshot = inspect_checkout(checkout)
    assert snapshot.state is RepoState.LOCAL_CHANGES
    assert snapshot.dirty_paths == ((Path("season") / "2026" / "schedule.json").as_posix(),)


def test_behind_after_remote_advances(remote_pair, tmp_path) -> None:
    bare, checkout = remote_pair
    remote_sha = advance_remote(bare, tmp_path, "colleague", "rev-2")
    snapshot = inspect_checkout(checkout)
    assert snapshot.state is RepoState.BEHIND
    assert snapshot.behind == 1 and snapshot.ahead == 0
    assert snapshot.head != remote_sha


def test_ahead_with_unpushed_local_commit(remote_pair) -> None:
    _, checkout = remote_pair
    write_schedule(checkout, "local-only")
    commit_all(checkout, "local canonical change")
    snapshot = inspect_checkout(checkout)
    assert snapshot.state is RepoState.AHEAD
    assert snapshot.ahead == 1 and snapshot.behind == 0


def test_diverged_when_both_sides_have_commits(remote_pair, tmp_path) -> None:
    bare, checkout = remote_pair
    write_schedule(checkout, "local-only")
    commit_all(checkout, "local canonical change")
    advance_remote(bare, tmp_path, "colleague", "remote-only")
    snapshot = inspect_checkout(checkout)
    assert snapshot.state is RepoState.DIVERGED
    assert snapshot.ahead == 1 and snapshot.behind == 1


def test_detached_head_is_not_on_main(remote_pair) -> None:
    _, checkout = remote_pair
    git(checkout, "checkout", "--quiet", "--detach")
    assert inspect_checkout(checkout).state is RepoState.NOT_ON_MAIN


def test_missing_directory_is_not_cloned(tmp_path) -> None:
    assert inspect_checkout(tmp_path / "absent").state is RepoState.NOT_CLONED


def test_plain_directory_is_not_cloned_even_inside_another_repo(remote_pair, tmp_path) -> None:
    _, checkout = remote_pair
    nested = checkout / "plain"
    nested.mkdir()
    assert inspect_checkout(nested).state is RepoState.NOT_CLONED


def test_removed_remote_is_unavailable(remote_pair) -> None:
    bare, checkout = remote_pair
    shutil.rmtree(bare)
    snapshot = inspect_checkout(checkout)
    assert snapshot.state is RepoState.UNAVAILABLE
    assert snapshot.auth_rejected is False


def test_git_missing_is_unavailable(remote_pair) -> None:
    _, checkout = remote_pair

    def runner(args, cwd, env):
        raise GitUnavailableError("git could not run")

    assert inspect_checkout(checkout, runner=runner).state is RepoState.UNAVAILABLE


def test_authentication_rejection_is_reported_as_unavailable(remote_pair) -> None:
    _, checkout = remote_pair

    def runner(args, cwd, env):
        if args[0] == "fetch":
            return GitResult(128, "", "fatal: Authentication failed for 'https://github.com/x/y/'")
        return run_git(args, cwd, env)

    snapshot = inspect_checkout(checkout, runner=runner)
    assert snapshot.state is RepoState.UNAVAILABLE
    assert snapshot.auth_rejected is True
