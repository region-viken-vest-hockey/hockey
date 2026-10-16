"""Sign-in state model with recovery to a signed-out state.

Token acquisition (browser/device flow) is not implemented here; see
``docs/windows-distribution.md``. This module decides what the operator sees
for an existing credential and clears it when GitHub rejects or expires it.
The local checkout is never touched by authentication recovery.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from .credentials import CredentialStore


class AuthState(str, Enum):
    SIGNED_OUT = "signed-out"
    AUTHENTICATED = "authenticated"
    UNAUTHORIZED = "unauthorized"
    EXPIRED = "expired"


class ProbeResult(str, Enum):
    VALID = "valid"
    REJECTED = "rejected"


Probe = Callable[[str], ProbeResult]


@dataclass(frozen=True)
class AuthStatus:
    state: AuthState
    message: str
    login: str | None = None
    recovered_from: AuthState | None = None


def resolve_auth_state(store: CredentialStore, probe: Probe, *, now: float) -> AuthStatus:
    """Return the sign-in state. Expired or rejected credentials are cleared, leaving a signed-out state.

    ``probe`` performs the one network check (for example ``GET /user``) and is
    injected, so tests run offline. A store failure propagates to the caller.
    """
    credential = store.load()
    if credential is None:
        return AuthStatus(AuthState.SIGNED_OUT, "not signed in")
    if credential.expires_at is not None and now >= credential.expires_at:
        store.clear()
        return AuthStatus(
            AuthState.SIGNED_OUT,
            "the GitHub sign-in expired; sign in again to continue",
            recovered_from=AuthState.EXPIRED,
        )
    if probe(credential.token) is ProbeResult.REJECTED:
        store.clear()
        return AuthStatus(
            AuthState.SIGNED_OUT,
            "GitHub no longer accepts this sign-in (revoked or unauthorized); sign in again",
            recovered_from=AuthState.UNAUTHORIZED,
        )
    return AuthStatus(AuthState.AUTHENTICATED, "signed in", login=credential.login)
