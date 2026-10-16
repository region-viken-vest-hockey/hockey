"""Ed25519 verification of release manifests against embedded trust roots.

The signature covers the exact manifest bytes. The detached signature file is a
small JSON envelope::

    {"schema": 1, "algorithm": "ed25519", "key_id": "<id>", "signature": "<base64>"}

:func:`parse_verified_manifest` is the only way production code turns manifest
bytes into an :class:`UpdateManifest`: no manifest field is read before the
signature has verified. Every failure raises :class:`UntrustedManifestError`.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from . import trust_roots
from .errors import UntrustedManifestError
from .manifest import UpdateManifest, parse_manifest

SIGNATURE_SCHEMA_VERSION = 1
SIGNATURE_ALGORITHM = "ed25519"
MAX_SIGNATURE_BYTES = 4096
SIGNATURE_SUFFIX = ".sig"

_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_ENVELOPE_FIELDS = frozenset({"schema", "algorithm", "key_id", "signature"})
_ED25519_KEY_BYTES = 32
_ED25519_SIGNATURE_BYTES = 64


def _decode_b64(text: object, length: int, what: str) -> bytes:
    if not isinstance(text, str):
        raise UntrustedManifestError(f"{what} must be a base64 string")
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UntrustedManifestError(f"{what} is not valid base64") from exc
    if len(raw) != length:
        raise UntrustedManifestError(f"{what} must decode to {length} bytes")
    return raw


def parse_signature_envelope(signature_bytes: bytes) -> tuple[str, bytes]:
    """Return ``(key_id, raw_signature)`` for a well-formed envelope, or raise."""
    if len(signature_bytes) > MAX_SIGNATURE_BYTES:
        raise UntrustedManifestError("signature file exceeds the maximum accepted size")
    try:
        data = json.loads(signature_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UntrustedManifestError("signature file is not UTF-8 JSON") from exc
    if not isinstance(data, dict) or set(data) != _ENVELOPE_FIELDS:
        raise UntrustedManifestError("signature envelope must contain exactly schema, algorithm, key_id, signature")
    if data["schema"] != SIGNATURE_SCHEMA_VERSION or data["algorithm"] != SIGNATURE_ALGORITHM:
        raise UntrustedManifestError("unsupported signature schema or algorithm")
    key_id = data["key_id"]
    if not isinstance(key_id, str) or not _KEY_ID_RE.match(key_id):
        raise UntrustedManifestError("signature key_id is malformed")
    signature = _decode_b64(data["signature"], _ED25519_SIGNATURE_BYTES, "signature")
    return key_id, signature


def _load_public_key(encoded: str) -> Ed25519PublicKey:
    raw = _decode_b64(encoded, _ED25519_KEY_BYTES, "trusted public key")
    return Ed25519PublicKey.from_public_bytes(raw)


def verify_manifest_signature(
    manifest_bytes: bytes,
    signature_bytes: bytes,
    trusted_keys: Mapping[str, str] | None = None,
) -> str:
    """Verify ``manifest_bytes`` against the detached signature. Return the key ID used.

    ``trusted_keys`` maps key ID to base64 raw Ed25519 public key; it defaults to
    the embedded trust root.
    """
    keys = trust_roots.TRUSTED_RELEASE_KEYS if trusted_keys is None else trusted_keys
    key_id, signature = parse_signature_envelope(signature_bytes)
    encoded = keys.get(key_id)
    if encoded is None:
        raise UntrustedManifestError(f"signing key id {key_id!r} is not trusted by this client")
    try:
        _load_public_key(encoded).verify(signature, manifest_bytes)
    except InvalidSignature as exc:
        raise UntrustedManifestError(f"signature by key {key_id!r} does not verify") from exc
    return key_id


def parse_verified_manifest(
    manifest_bytes: bytes,
    signature_bytes: bytes,
    trusted_keys: Mapping[str, str] | None = None,
) -> UpdateManifest:
    """Verify the signature first, then parse. Unverified bytes are never interpreted."""
    verify_manifest_signature(manifest_bytes, signature_bytes, trusted_keys)
    return parse_manifest(manifest_bytes)


def load_signing_key(pem: bytes) -> Ed25519PrivateKey:
    """Load an unencrypted PEM Ed25519 private key supplied by the release workflow."""
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("signing key must be an Ed25519 private key")
    return key


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    """Base64 raw public key in the form embedded in :mod:`trust_roots`."""
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def sign_manifest_bytes(private_key: Ed25519PrivateKey, key_id: str, manifest_bytes: bytes) -> bytes:
    """Return the signature envelope for the exact ``manifest_bytes``."""
    if not _KEY_ID_RE.match(key_id):
        raise ValueError(f"invalid key id {key_id!r}")
    signature = private_key.sign(manifest_bytes)
    envelope = {
        "schema": SIGNATURE_SCHEMA_VERSION,
        "algorithm": SIGNATURE_ALGORITHM,
        "key_id": key_id,
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    return (json.dumps(envelope, sort_keys=True) + "\n").encode("utf-8")
