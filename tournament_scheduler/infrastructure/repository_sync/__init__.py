"""Local repository adapter for the canonical season repository.

Owners:

* ``git_runner``: injectable git process boundary and HTTPS token environment
* ``status``: checkout state classification (pure) and inspection
* ``refresh``: clone and fast-forward-only refresh from ``main``
* ``canonical_commit``: write-gated commit and fast-forward push of season state
* ``credentials`` / ``auth_state``: credential store seam and sign-in state model

Scheduling and publication policy stay in the application layers.
"""

from .auth_state import AuthState, AuthStatus, ProbeResult, resolve_auth_state
from .canonical_commit import CanonicalCommitResult, commit_canonical_season, enforce_write_gate
from .credentials import (
    Credential,
    CredentialStore,
    CredentialStoreError,
    InMemoryCredentialStore,
    WindowsCredentialManagerStore,
)
from .errors import GitCommandError, GitUnavailableError, RepositoryError, RepositoryStateError
from .git_runner import GitResult, GitRunner, https_auth_env, run_git
from .refresh import RefreshResult, clone_repository, refresh_main
from .status import RepoFacts, RepoSnapshot, RepoState, classify_state, inspect_checkout

__all__ = [
    "AuthState", "AuthStatus", "CanonicalCommitResult", "Credential", "CredentialStore",
    "CredentialStoreError", "GitCommandError", "GitResult", "GitRunner", "GitUnavailableError",
    "InMemoryCredentialStore", "ProbeResult", "RefreshResult", "RepoFacts", "RepoSnapshot",
    "RepoState", "RepositoryError", "RepositoryStateError", "WindowsCredentialManagerStore",
    "classify_state", "clone_repository", "commit_canonical_season", "enforce_write_gate",
    "https_auth_env", "inspect_checkout", "refresh_main", "resolve_auth_state", "run_git",
]
