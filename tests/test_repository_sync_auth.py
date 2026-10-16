"""Sign-in state model, credential store seam and token handling (offline)."""

from __future__ import annotations

import base64

import pytest

from tournament_scheduler.infrastructure.repository_sync import (
    AuthState,
    Credential,
    CredentialStoreError,
    InMemoryCredentialStore,
    ProbeResult,
    RepoState,
    WindowsCredentialManagerStore,
    https_auth_env,
    inspect_checkout,
    resolve_auth_state,
)

NOW = 1_800_000_000.0


def _valid(_token: str) -> ProbeResult:
    return ProbeResult.VALID


def _rejected(_token: str) -> ProbeResult:
    return ProbeResult.REJECTED


def _must_not_probe(_token: str) -> ProbeResult:
    raise AssertionError("probe must not run for an expired credential")


def test_no_credential_is_signed_out() -> None:
    status = resolve_auth_state(InMemoryCredentialStore(), _valid, now=NOW)
    assert status.state is AuthState.SIGNED_OUT and status.recovered_from is None


def test_valid_credential_is_authenticated() -> None:
    store = InMemoryCredentialStore(Credential("tok", login="operator", expires_at=NOW + 3600))
    status = resolve_auth_state(store, _valid, now=NOW)
    assert status.state is AuthState.AUTHENTICATED
    assert status.login == "operator"
    assert store.load() is not None


def test_rejected_credential_recovers_to_signed_out_and_clears_store(remote_pair) -> None:
    _, checkout = remote_pair
    before_head = inspect_checkout(checkout).head
    store = InMemoryCredentialStore(Credential("revoked-token", login="operator"))
    status = resolve_auth_state(store, _rejected, now=NOW)
    assert status.state is AuthState.SIGNED_OUT
    assert status.recovered_from is AuthState.UNAUTHORIZED
    assert store.load() is None
    snapshot = inspect_checkout(checkout)
    assert snapshot.state is RepoState.READY and snapshot.head == before_head


def test_expired_credential_recovers_without_probing(remote_pair) -> None:
    store = InMemoryCredentialStore(Credential("old-token", expires_at=NOW - 1))
    status = resolve_auth_state(store, _must_not_probe, now=NOW)
    assert status.state is AuthState.SIGNED_OUT
    assert status.recovered_from is AuthState.EXPIRED
    assert store.load() is None


def test_windows_backend_seam_fails_closed() -> None:
    store = WindowsCredentialManagerStore()
    with pytest.raises(CredentialStoreError, match="not implemented"):
        store.load()
    with pytest.raises(CredentialStoreError):
        store.save(Credential("tok"))


def test_credential_repr_redacts_token() -> None:
    assert "tok-secret" not in repr(Credential("tok-secret", login="operator"))


def test_https_auth_env_carries_token_only_as_authorization_header() -> None:
    env = https_auth_env("tok-secret")
    assert env["GIT_CONFIG_KEY_0"] == "http.extraheader"
    value = env["GIT_CONFIG_VALUE_0"]
    assert value.startswith("Authorization: Basic ")
    encoded = value.rsplit(" ", 1)[1]
    assert base64.b64decode(encoded).decode("utf-8") == "x-access-token:tok-secret"
    assert https_auth_env(None) == {}
