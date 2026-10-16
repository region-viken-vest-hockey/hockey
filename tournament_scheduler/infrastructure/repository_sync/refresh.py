"""Obtain a local checkout and refresh ``main`` by fast-forward only.

Refresh never force-pushes, never resets, never cleans and never overwrites
local or remote work. Anything other than a clean, non-diverged local ``main``
that is merely behind ``origin/main`` is reported and left untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .errors import GitCommandError, GitUnavailableError, RepositoryError
from .git_runner import GitRunner, https_auth_env, run_git
from .status import MAIN_BRANCH, REMOTE_MAIN_REF, RepoSnapshot, RepoState, inspect_checkout


@dataclass(frozen=True)
class RefreshResult:
    performed: bool
    before: RepoSnapshot
    after: RepoSnapshot
    reason: str


def clone_repository(
    url: str,
    dest: Path,
    *,
    token: str | None = None,
    runner: GitRunner = run_git,
) -> None:
    """Clone ``main`` of ``url`` into ``dest``. Refuses a non-empty destination."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise RepositoryError(f"clone destination is not empty: {dest}")
    if urlsplit(url).scheme != "https":
        raise RepositoryError("only https repository URLs are accepted")
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = runner(
        ["clone", "--quiet", "--no-tags", "--branch", MAIN_BRANCH, "--", url, str(dest)],
        dest.parent,
        https_auth_env(token),
    )
    if not result.ok:
        raise GitCommandError(("clone",), result.returncode, result.stderr)


def refresh_main(
    root: Path,
    *,
    token: str | None = None,
    runner: GitRunner = run_git,
) -> RefreshResult:
    """Fast-forward a clean local ``main`` to ``origin/main`` when it is merely behind."""
    before = inspect_checkout(root, token=token, runner=runner)
    if before.state is RepoState.READY:
        return RefreshResult(False, before, before, "already current with origin/main")
    if before.state is not RepoState.BEHIND:
        return RefreshResult(False, before, before, f"refresh refused: {before.reason}")
    try:
        merge = runner(["merge", "--ff-only", "--quiet", REMOTE_MAIN_REF], Path(root), None)
    except GitUnavailableError as exc:
        return RefreshResult(False, before, before, f"refresh refused: {exc}")
    if not merge.ok:
        # Fast-forward refused by git (for example a race with a local change). Nothing was overwritten.
        after = inspect_checkout(root, token=token, runner=runner)
        return RefreshResult(False, before, after, f"fast-forward refused by git: {merge.stderr.strip()[:300]}")
    after = inspect_checkout(root, token=token, runner=runner)
    if after.state is not RepoState.READY:
        raise RepositoryError(f"refresh finished in unexpected state {after.state.value}")
    return RefreshResult(True, before, after, "fast-forwarded local main to origin/main")
