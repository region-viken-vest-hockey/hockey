"""Read-only readiness view: software version, sign-in, repository and Claude runtime.

Every fact comes from the owning service: ``distribution_update`` for the update
gate, ``repository_sync`` for checkout state and the credential seam, and
``claude_runtime`` for the assistant runtime. This module only maps those facts to
the reasons canonical writes are blocked. It never mutates anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tournament_scheduler import distribution
from tournament_scheduler.distribution_update import (
    ManifestError,
    WriteDecision,
    classify,
    load_cached_manifest,
    write_decision_from_cache,
)
from tournament_scheduler.infrastructure.claude_runtime import ClaudeRunner, RuntimeReadiness, probe_runtime
from tournament_scheduler.infrastructure.repository_sync import (
    CredentialStore,
    CredentialStoreError,
    GitRunner,
    RepoSnapshot,
    RepoState,
    inspect_checkout,
    run_git,
)

SIGN_IN_VERIFIED = "signed-in"
SIGN_IN_UNVERIFIED = "stored-unverified"
SIGN_IN_SIGNED_OUT = "signed-out"
SIGN_IN_UNAVAILABLE = "unavailable"

_REPOSITORY_BLOCKERS: dict[RepoState, str] = {
    RepoState.NOT_CLONED: "No local copy of the season repository was found here. Get a local copy first.",
    RepoState.UNAVAILABLE: "The season repository could not be read or refreshed from GitHub. Check the connection and sign-in.",
    RepoState.NOT_ON_MAIN: "The local copy is not on main. Switch it back to main before writing.",
    RepoState.LOCAL_CHANGES: "The local copy has uncommitted changes. Resolve them before writing.",
    RepoState.BEHIND: "The local copy is behind GitHub main. Refresh from main before writing.",
    RepoState.AHEAD: "The local copy has commits that are not on GitHub main. Reconcile them before writing.",
    RepoState.DIVERGED: "The local copy and GitHub main have diverged. Writes stay blocked until they are reconciled.",
}

_SIGN_IN_BLOCKERS: dict[str, str] = {
    SIGN_IN_SIGNED_OUT: "Not signed in to GitHub. Sign-in is not available in this build yet.",
    SIGN_IN_UNVERIFIED: "A GitHub sign-in is stored but has not been verified with GitHub. Sign in again to continue.",
    SIGN_IN_UNAVAILABLE: "The stored GitHub sign-in cannot be read on this computer. Canonical writes are blocked.",
}


@dataclass(frozen=True)
class VersionView:
    name: str
    installed: str
    distribution: str
    update_status: str
    writes_allowed: bool
    reason: str


@dataclass(frozen=True)
class SignInView:
    state: str
    message: str


@dataclass(frozen=True)
class ReadinessView:
    version: VersionView
    repository: RepoSnapshot
    sign_in: SignInView
    claude: RuntimeReadiness
    blockers: tuple[str, ...] = field(default=())

    @property
    def canonical_writes_allowed(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        """Operator-safe, credential-free view for ``--json``."""
        return {
            "version": {
                "name": self.version.name,
                "installed": self.version.installed,
                "distribution": self.version.distribution,
                "update_status": self.version.update_status,
                "writes_allowed": self.version.writes_allowed,
                "reason": self.version.reason,
            },
            "repository": {
                "state": self.repository.state.value,
                "reason": self.repository.reason,
                "branch": self.repository.branch,
                "ahead": self.repository.ahead,
                "behind": self.repository.behind,
                "dirty_path_count": len(self.repository.dirty_paths),
            },
            "sign_in": {"state": self.sign_in.state, "message": self.sign_in.message},
            "claude": self.claude.to_dict(),
            "canonical_writes_allowed": self.canonical_writes_allowed,
            "blockers": list(self.blockers),
        }


def _version_view(installed: str, cache_dir: Path) -> tuple[VersionView, WriteDecision]:
    decision = write_decision_from_cache(installed, cache_dir)
    try:
        manifest = load_cached_manifest(cache_dir)
    except ManifestError:
        status = "unreadable-cache"
    else:
        status = "unknown" if manifest is None else classify(installed, manifest).value
    metadata = distribution.version_metadata()
    view = VersionView(
        name=metadata["name"],
        installed=installed,
        distribution=metadata["distribution"],
        update_status=status,
        writes_allowed=decision.allowed,
        reason=decision.reason,
    )
    return view, decision


def _sign_in_view(store: CredentialStore) -> SignInView:
    try:
        credential = store.load()
    except CredentialStoreError:
        return SignInView(SIGN_IN_UNAVAILABLE, _SIGN_IN_BLOCKERS[SIGN_IN_UNAVAILABLE])
    if credential is None:
        return SignInView(SIGN_IN_SIGNED_OUT, _SIGN_IN_BLOCKERS[SIGN_IN_SIGNED_OUT])
    # No GitHub probe exists in this build, so a stored token is never treated as verified.
    return SignInView(SIGN_IN_UNVERIFIED, _SIGN_IN_BLOCKERS[SIGN_IN_UNVERIFIED])


def _blockers(decision: WriteDecision, repository: RepoSnapshot, sign_in: SignInView) -> tuple[str, ...]:
    blockers: list[str] = []
    if not decision.allowed:
        blockers.append(f"Software version: {decision.reason}.")
    if repository.state is not RepoState.READY:
        blockers.append(_REPOSITORY_BLOCKERS.get(repository.state, repository.reason))
    if sign_in.state != SIGN_IN_VERIFIED:
        blockers.append(sign_in.message)
    return tuple(blockers)


def gather_readiness(
    repo_root: Path,
    *,
    cache_dir: Path,
    store: CredentialStore,
    claude_runner: ClaudeRunner,
    installed: str,
    token: str | None = None,
    runner: GitRunner = run_git,
) -> ReadinessView:
    """Collect every readiness fact. Read-only apart from a remote-tracking fetch of ``main``."""
    version, decision = _version_view(installed, Path(cache_dir))
    repository = inspect_checkout(Path(repo_root), token=token, runner=runner)
    sign_in = _sign_in_view(store)
    claude = probe_runtime(claude_runner)
    return ReadinessView(
        version=version,
        repository=repository,
        sign_in=sign_in,
        claude=claude,
        blockers=_blockers(decision, repository, sign_in),
    )
