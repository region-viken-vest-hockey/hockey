"""Gated repository refresh from ``main``, plus the separate publication handoff.

Refresh is two steps. :func:`preview_refresh` inspects the checkout and returns
the commit it would fast-forward to. :func:`apply_refresh` mutates only when the
operator passes back the local ``HEAD`` shown by that preview and the checkout is
still merely behind. Everything else is refused with the reason, and nothing
changes. The mutation itself is ``repository_sync.refresh_main``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tournament_scheduler.infrastructure.repository_sync import (
    GitRunner,
    RepoSnapshot,
    RepoState,
    RepositoryError,
    inspect_checkout,
    refresh_main,
    run_git,
)

PUBLICATION_HANDOFF = (
    "Publication is a separate, explicitly authorized step. This shell does not publish, and a "
    "successful refresh or maintenance run never publishes. Run it with: rvv-miniputt operator publish --help"
)

_REFUSAL_TEXT: dict[RepoState, str] = {
    RepoState.NOT_CLONED: "There is no local copy to refresh. Get a local copy first.",
    RepoState.UNAVAILABLE: "The repository could not be read or refreshed from GitHub. Nothing was changed.",
    RepoState.NOT_ON_MAIN: "The local copy is not on main, so it will not be refreshed. Nothing was changed.",
    RepoState.LOCAL_CHANGES: "The local copy has uncommitted changes, so it will not be refreshed. Nothing was changed.",
    RepoState.AHEAD: "The local copy has commits that are not on GitHub main, so it will not be refreshed. Nothing was changed.",
    RepoState.DIVERGED: "The local copy and GitHub main have diverged. Nothing was changed automatically.",
}


@dataclass(frozen=True)
class RefreshPreview:
    can_refresh: bool
    snapshot: RepoSnapshot
    message: str
    confirm_token: str | None


@dataclass(frozen=True)
class RefreshOutcome:
    performed: bool
    message: str
    snapshot: RepoSnapshot | None


def preview_refresh(root: Path, *, token: str | None = None, runner: GitRunner = run_git) -> RefreshPreview:
    """Describe what a refresh would do. Changes no working-tree or branch state."""
    snapshot = inspect_checkout(Path(root), token=token, runner=runner)
    if snapshot.state is RepoState.READY:
        return RefreshPreview(False, snapshot, "Already current with GitHub main. Nothing to refresh.", None)
    if snapshot.state is RepoState.BEHIND:
        message = (
            f"Local main is {snapshot.behind} commit(s) behind GitHub main. Refresh will fast-forward it "
            "to GitHub main. No other change is made."
        )
        return RefreshPreview(True, snapshot, message, snapshot.head)
    return RefreshPreview(False, snapshot, _REFUSAL_TEXT.get(snapshot.state, snapshot.reason), None)


def apply_refresh(
    root: Path,
    *,
    confirm_head: str | None,
    token: str | None = None,
    runner: GitRunner = run_git,
) -> RefreshOutcome:
    """Fast-forward local main, but only after the operator confirms the previewed ``HEAD``."""
    if not confirm_head:
        return RefreshOutcome(False, "Refresh needs a preview first. Run the preview, then confirm the commit it shows.", None)
    preview = preview_refresh(root, token=token, runner=runner)
    if not preview.can_refresh:
        return RefreshOutcome(False, preview.message, preview.snapshot)
    if preview.snapshot.head != confirm_head.strip():
        return RefreshOutcome(
            False,
            "The local copy changed since the preview, so nothing was refreshed. Run the preview again.",
            preview.snapshot,
        )
    try:
        result = refresh_main(Path(root), token=token, runner=runner)
    except RepositoryError as exc:
        return RefreshOutcome(False, f"Refresh stopped and nothing was overwritten: {exc}", preview.snapshot)
    message = (
        "Local main now matches GitHub main."
        if result.performed
        else f"Refresh did not change the local copy: {result.reason}."
    )
    return RefreshOutcome(result.performed, message, result.after)
