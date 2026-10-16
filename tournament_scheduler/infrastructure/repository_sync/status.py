"""Checkout status classification for the canonical season repository.

:func:`classify_state` is pure and decides the operator-facing state from
observed facts. :func:`inspect_checkout` gathers those facts with git. Both
are read-only apart from a remote-tracking fetch of ``main``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .errors import GitCommandError, GitUnavailableError
from .git_runner import GitRunner, https_auth_env, run_git

MAIN_BRANCH = "main"
REMOTE_NAME = "origin"
REMOTE_MAIN_REF = "refs/remotes/origin/main"
FETCH_MAIN_REFSPEC = f"+refs/heads/{MAIN_BRANCH}:{REMOTE_MAIN_REF}"
_AUTH_FAILURE_RE = re.compile(
    r"authentication failed|could not read username|invalid username or password"
    r"|the requested URL returned error: 40[13]|requested url returned error: 40[13]",
    re.IGNORECASE,
)


class RepoState(str, Enum):
    NOT_CLONED = "not-cloned"
    READY = "ready"
    LOCAL_CHANGES = "local-changes"
    BEHIND = "behind"
    AHEAD = "ahead"
    DIVERGED = "diverged"
    NOT_ON_MAIN = "not-on-main"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class RepoFacts:
    """Observed checkout facts. ``reachable`` is False when the refresh fetch failed."""

    is_worktree: bool
    reachable: bool = True
    branch: str | None = None
    dirty_paths: tuple[str, ...] = ()
    ahead: int = 0
    behind: int = 0


@dataclass(frozen=True)
class RepoSnapshot:
    state: RepoState
    reason: str
    branch: str | None = None
    head: str | None = None
    ahead: int = 0
    behind: int = 0
    dirty_paths: tuple[str, ...] = ()
    auth_rejected: bool = False


def classify_state(facts: RepoFacts) -> RepoState:
    """Map observed facts to one state. Order is fail-closed: earlier rows win."""
    if not facts.is_worktree:
        return RepoState.NOT_CLONED
    if not facts.reachable:
        return RepoState.UNAVAILABLE
    if facts.branch != MAIN_BRANCH:
        return RepoState.NOT_ON_MAIN
    if facts.dirty_paths:
        return RepoState.LOCAL_CHANGES
    if facts.ahead and facts.behind:
        return RepoState.DIVERGED
    if facts.behind:
        return RepoState.BEHIND
    if facts.ahead:
        return RepoState.AHEAD
    return RepoState.READY


_REASONS = {
    RepoState.NOT_CLONED: "no local checkout of the season repository exists here",
    RepoState.READY: "local main equals origin/main and the working tree is clean",
    RepoState.LOCAL_CHANGES: "tracked files have uncommitted changes",
    RepoState.BEHIND: "local main is behind origin/main and can be fast-forwarded",
    RepoState.AHEAD: "local main has commits that are not on origin/main",
    RepoState.DIVERGED: "local main and origin/main have diverged; nothing is changed automatically",
    RepoState.NOT_ON_MAIN: "the checkout is not on main (or HEAD is detached)",
    RepoState.UNAVAILABLE: "the repository could not be read or refreshed from origin",
}


def describe(state: RepoState) -> str:
    return _REASONS[state]


def _rev_parse(runner: GitRunner, cwd: Path, *args: str) -> str:
    result = runner(["rev-parse", *args], cwd, None)
    if not result.ok:
        raise GitCommandError(("rev-parse", *args), result.returncode, result.stderr)
    return result.stdout.strip()


def _toplevel_matches(runner: GitRunner, root: Path) -> bool:
    if not root.is_dir():
        return False
    result = runner(["rev-parse", "--show-toplevel"], root, None)
    if not result.ok:
        return False
    return Path(result.stdout.strip()).resolve() == root.resolve()


def _dirty_paths(runner: GitRunner, root: Path) -> tuple[str, ...]:
    result = runner(["status", "--porcelain=v1", "-z", "--untracked-files=no", "--no-renames"], root, None)
    if not result.ok:
        raise GitCommandError(("status",), result.returncode, result.stderr)
    entries = [entry for entry in result.stdout.split("\0") if entry]
    return tuple(entry[3:] for entry in entries)


def inspect_checkout(
    root: Path,
    *,
    token: str | None = None,
    runner: GitRunner = run_git,
) -> RepoSnapshot:
    """Refresh the remote-tracking ``main`` and classify the checkout at ``root``.

    Never changes the working tree, the index or local branches. Git being
    unavailable, an unreadable checkout or a failed fetch yields ``UNAVAILABLE``.
    """
    root = Path(root)
    try:
        if not _toplevel_matches(runner, root):
            return RepoSnapshot(RepoState.NOT_CLONED, describe(RepoState.NOT_CLONED))
        fetch = runner(
            ["fetch", "--no-tags", "--quiet", REMOTE_NAME, FETCH_MAIN_REFSPEC],
            root,
            https_auth_env(token),
        )
        if not fetch.ok:
            auth_rejected = bool(_AUTH_FAILURE_RE.search(fetch.stderr))
            return RepoSnapshot(
                RepoState.UNAVAILABLE,
                describe(RepoState.UNAVAILABLE),
                auth_rejected=auth_rejected,
            )
        branch_result = runner(["symbolic-ref", "--quiet", "--short", "HEAD"], root, None)
        branch = branch_result.stdout.strip() if branch_result.ok else None
        head = _rev_parse(runner, root, "HEAD")
        counts = runner(["rev-list", "--left-right", "--count", f"HEAD...{REMOTE_MAIN_REF}"], root, None)
        if not counts.ok:
            return RepoSnapshot(RepoState.UNAVAILABLE, describe(RepoState.UNAVAILABLE), branch=branch, head=head)
        ahead_text, behind_text = counts.stdout.split()
        dirty = _dirty_paths(runner, root)
    except GitUnavailableError:
        return RepoSnapshot(RepoState.UNAVAILABLE, describe(RepoState.UNAVAILABLE))
    except (GitCommandError, ValueError):
        return RepoSnapshot(RepoState.UNAVAILABLE, describe(RepoState.UNAVAILABLE))

    facts = RepoFacts(
        is_worktree=True,
        reachable=True,
        branch=branch,
        dirty_paths=dirty,
        ahead=int(ahead_text),
        behind=int(behind_text),
    )
    state = classify_state(facts)
    return RepoSnapshot(
        state=state,
        reason=describe(state),
        branch=branch,
        head=head,
        ahead=facts.ahead,
        behind=facts.behind,
        dirty_paths=dirty,
    )
