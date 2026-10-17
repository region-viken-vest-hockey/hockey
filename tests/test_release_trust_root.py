"""The committed release trust root is well-formed and private key material never reaches git."""

from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from tests.distribution_signing_support import sign
from tournament_scheduler import distribution_update as du
from tournament_scheduler.distribution_update import signature, trust_roots

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_KEY_ID = "release-2026-01"
# Split so this file does not contain the literal marker it scans for.
PRIVATE_KEY_MARKER = "-----BEGIN " + "PRIVATE KEY-----"
MANIFEST = b'{"schema": 1, "channel": "stable", "latest_version": "2.0.0"}'


def test_committed_release_key_id_is_accepted_by_the_envelope_grammar() -> None:
    assert set(trust_roots.TRUSTED_RELEASE_KEYS) == {RELEASE_KEY_ID}
    assert signature._KEY_ID_RE.match(RELEASE_KEY_ID)


def test_committed_public_key_is_raw_32_byte_ed25519() -> None:
    encoded = trust_roots.TRUSTED_RELEASE_KEYS[RELEASE_KEY_ID]
    raw = base64.b64decode(encoded, validate=True)
    assert len(raw) == 32
    Ed25519PublicKey.from_public_bytes(raw)


def test_manifest_signed_by_an_unrelated_key_under_the_release_id_is_refused() -> None:
    # Exercises the real verification path against the committed trust root, with no monkeypatching.
    forged = sign(MANIFEST, signer=Ed25519PrivateKey.generate(), key_id=RELEASE_KEY_ID)
    with pytest.raises(du.UntrustedManifestError, match="does not verify"):
        signature.verify_manifest_signature(MANIFEST, forged)


@pytest.mark.skipif(
    not os.environ.get("RVV_RELEASE_SIGNING_KEY"),
    reason="positive check needs the protected release private key; run locally with it set",
)
def test_private_key_matches_committed_public_key() -> None:
    private_key = signature.load_signing_key(os.environ["RVV_RELEASE_SIGNING_KEY"].encode("utf-8"))
    assert signature.public_key_b64(private_key) == trust_roots.TRUSTED_RELEASE_KEYS[RELEASE_KEY_ID]
    signed = signature.sign_manifest_bytes(private_key, RELEASE_KEY_ID, MANIFEST)
    assert signature.verify_manifest_signature(MANIFEST, signed) == RELEASE_KEY_ID


def test_no_private_key_material_is_tracked() -> None:
    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPO_ROOT, check=True, capture_output=True
        ).stdout.decode("utf-8")
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    offenders = []
    for name in filter(None, listing.split("\0")):
        path = REPO_ROOT / name
        if not path.is_file():
            continue
        if PRIVATE_KEY_MARKER in path.read_bytes().decode("utf-8", errors="ignore"):
            offenders.append(name)
    assert offenders == []
