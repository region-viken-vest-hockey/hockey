"""Throwaway Ed25519 signers for offline distribution-update tests.

Keys are generated in memory for each test process and are never written to disk
or committed. They exist only to exercise verification; they are not project keys.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tournament_scheduler.distribution_update import public_key_b64, sign_manifest_bytes
from tournament_scheduler.distribution_update import trust_roots

TEST_KEY_ID = "test-release-key"
TEST_SIGNER = Ed25519PrivateKey.generate()
TEST_PUBLIC_B64 = public_key_b64(TEST_SIGNER)


def sign(raw: bytes, signer: Ed25519PrivateKey = TEST_SIGNER, key_id: str = TEST_KEY_ID) -> bytes:
    return sign_manifest_bytes(signer, key_id, raw)


def trust(monkeypatch, keys: dict[str, str] | None = None) -> None:
    """Make the embedded trust root equal ``keys`` (default: the test signer) for one test."""
    resolved = {TEST_KEY_ID: TEST_PUBLIC_B64} if keys is None else keys
    monkeypatch.setattr(trust_roots, "TRUSTED_RELEASE_KEYS", resolved)
