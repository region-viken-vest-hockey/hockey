"""Credential store interface for the GitHub sign-in.

Tokens live only in an OS-protected store. Nothing here writes a token to
plaintext config, the repository or a log. Two backends exist:

* :class:`InMemoryCredentialStore`: the test double, used by every offline test.
* :class:`WindowsCredentialManagerStore`: a **seam only**. It is not implemented
  in this slice and raises :class:`CredentialStoreError` on every call, so a
  build without the real backend fails closed instead of falling back to disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

WINDOWS_TARGET_NAME = "RVV Miniputt/github"


class CredentialStoreError(RuntimeError):
    """The OS-protected credential store is unavailable or refused the operation."""


@dataclass(frozen=True)
class Credential:
    """A GitHub user token. ``expires_at`` is an epoch second, or ``None`` when GitHub reports none."""

    token: str
    login: str | None = None
    expires_at: float | None = None

    def __repr__(self) -> str:  # never print the secret
        return f"Credential(login={self.login!r}, expires_at={self.expires_at!r}, token=<redacted>)"


class CredentialStore(Protocol):
    def load(self) -> Credential | None: ...

    def save(self, credential: Credential) -> None: ...

    def clear(self) -> None: ...


class InMemoryCredentialStore:
    """Process-local store for tests. Holds nothing after the process exits."""

    def __init__(self, initial: Credential | None = None) -> None:
        self._credential = initial

    def load(self) -> Credential | None:
        return self._credential

    def save(self, credential: Credential) -> None:
        self._credential = credential

    def clear(self) -> None:
        self._credential = None


class WindowsCredentialManagerStore:
    """Seam for Windows Credential Manager (generic credential ``WINDOWS_TARGET_NAME``).

    NOT IMPLEMENTED. The intended backend calls ``CredReadW``/``CredWriteW``/
    ``CredDeleteW`` from ``advapi32`` with ``CRED_TYPE_GENERIC`` and per-user
    persistence. It cannot be exercised on this Linux VM, so it stays unwired.
    """

    def __init__(self, target_name: str = WINDOWS_TARGET_NAME) -> None:
        self.target_name = target_name

    def _unavailable(self) -> CredentialStoreError:
        return CredentialStoreError(
            "Windows Credential Manager backend is not implemented in this build; "
            "refusing to fall back to plaintext storage"
        )

    def load(self) -> Credential | None:
        raise self._unavailable()

    def save(self, credential: Credential) -> None:
        raise self._unavailable()

    def clear(self) -> None:
        raise self._unavailable()
