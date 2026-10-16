"""Commit and push canonical season state through the repository adapter.

This is the only Git write path in the adapter. It fails closed in this order:

1. write gate: the installed version must be allowed to write (``require_write_allowed``);
2. checkout state must be ``ready`` or ``local-changes`` with no ahead/behind commits;
3. every tracked change must lie under the season root, and the index must be empty;
4. only the season root is staged and committed;
5. the push is a plain fast-forward ``HEAD:refs/heads/main``. A refused push keeps
   the local commit and reports it. Nothing is reset, rebased or force-pushed.

The commit payload is whatever the canonical store already wrote under the season
root. This module does not decide what the season state should be.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tournament_scheduler.distribution_update import (
    ManifestError,
    WriteBlockedError,
    load_cached_manifest,
    require_write_allowed,
)

from .errors import GitCommandError, RepositoryStateError
from .git_runner import GitRunner, https_auth_env, run_git
from .status import REMOTE_NAME, RepoState, inspect_checkout


@dataclass(frozen=True)
class CanonicalCommitResult:
    committed: bool
    pushed: bool
    commit: str | None
    reason: str


def enforce_write_gate(installed_version: str, cache_dir: Path) -> None:
    """Raise :class:`WriteBlockedError` unless the cached release manifest allows writes.

    A corrupt cached manifest blocks writes rather than being ignored.
    """
    try:
        manifest = load_cached_manifest(Path(cache_dir))
    except ManifestError as exc:
        raise WriteBlockedError(
            f"cached release manifest is unusable ({exc}); run an update check before writing"
        ) from exc
    require_write_allowed(installed_version, manifest)


def _season_relative(root: Path, season_root: Path) -> str:
    root_resolved = root.resolve()
    target = (season_root if season_root.is_absolute() else root_resolved / season_root).resolve()
    try:
        relative = target.relative_to(root_resolved)
    except ValueError as exc:
        raise RepositoryStateError("not-ready", f"season root {season_root} is outside the repository") from exc
    if relative.as_posix() in ("", "."):
        raise RepositoryStateError("not-ready", "the season root must be a subdirectory of the repository")
    return relative.as_posix()


def _within(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def commit_canonical_season(
    root: Path,
    season_root: Path,
    message: str,
    *,
    installed_version: str,
    cache_dir: Path,
    token: str | None = None,
    push: bool = True,
    runner: GitRunner = run_git,
) -> CanonicalCommitResult:
    root = Path(root)
    enforce_write_gate(installed_version, cache_dir)

    snapshot = inspect_checkout(root, token=token, runner=runner)
    if snapshot.state not in (RepoState.READY, RepoState.LOCAL_CHANGES) or snapshot.ahead or snapshot.behind:
        raise RepositoryStateError(snapshot.state.value, snapshot.reason)

    season_rel = _season_relative(root, Path(season_root))
    outside = [path for path in snapshot.dirty_paths if not _within(path, season_rel)]
    if outside:
        raise RepositoryStateError(
            RepoState.LOCAL_CHANGES.value,
            "tracked changes outside the season root block the commit: " + ", ".join(sorted(outside))[:300],
        )

    staged = runner(["diff", "--cached", "--name-only", "-z"], root, None)
    if not staged.ok:
        raise GitCommandError(("diff",), staged.returncode, staged.stderr)
    if staged.stdout.strip("\0"):
        raise RepositoryStateError(RepoState.LOCAL_CHANGES.value, "the index already has staged changes")

    if (root / season_rel).exists():
        added = runner(["add", "-A", "--", season_rel], root, None)
        if not added.ok:
            raise GitCommandError(("add",), added.returncode, added.stderr)
    changed = runner(["diff", "--cached", "--quiet", "--", season_rel], root, None)
    if changed.returncode == 0:
        return CanonicalCommitResult(False, False, None, "no canonical season changes to commit")
    if changed.returncode != 1:
        raise GitCommandError(("diff",), changed.returncode, changed.stderr)

    committed = runner(["commit", "--quiet", "-m", message], root, None)
    if not committed.ok:
        raise GitCommandError(("commit",), committed.returncode, committed.stderr)
    head = runner(["rev-parse", "HEAD"], root, None).stdout.strip()

    if not push:
        return CanonicalCommitResult(True, False, head, "committed locally; push not requested")

    pushed = runner(
        ["push", "--quiet", REMOTE_NAME, "HEAD:refs/heads/main"],
        root,
        https_auth_env(token),
    )
    if not pushed.ok:
        return CanonicalCommitResult(
            True,
            False,
            head,
            "commit kept locally; push was refused (local main is ahead of origin/main). "
            "Refresh or reconcile before retrying; nothing was force-pushed or reset. "
            + pushed.stderr.strip()[:300],
        )
    return CanonicalCommitResult(True, True, head, "committed and pushed to origin/main")
