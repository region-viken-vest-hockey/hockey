"""Exception types for the local repository adapter."""

from __future__ import annotations


class RepositoryError(RuntimeError):
    """Base class for repository adapter failures. Callers must treat these as non-mutating."""


class GitUnavailableError(RepositoryError):
    """The git executable could not be started or did not finish in time."""


class GitCommandError(RepositoryError):
    """A git command ran and failed; ``stderr`` carries git's own diagnostic."""

    def __init__(self, args: tuple[str, ...], returncode: int, stderr: str) -> None:
        self.args_run = args
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(f"git {args[0]} exited with status {returncode}: {self.stderr[:300]}")


class RepositoryStateError(RepositoryError):
    """The checkout is not in a state that permits the requested operation."""

    def __init__(self, state: str, reason: str) -> None:
        self.state = state
        super().__init__(f"{state}: {reason}")
