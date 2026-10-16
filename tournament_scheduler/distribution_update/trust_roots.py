"""Embedded trust roots for signed release manifests.

Only PUBLIC keys belong here. The release workflow holds the matching private
keys in a protected secret; no private material may be committed to this
repository or packaged into the application.

Keys are keyed by a stable key ID. Rotation is additive: a client release
trusts the old and the new key IDs together, and only a later release stops
trusting the old one. Values are base64-encoded raw 32-byte Ed25519 public keys.

The mapping is intentionally empty until the project generates its signing key.
While it is empty every manifest is untrusted, so no update is offered and the
write gate is never relaxed by release metadata.
"""

from __future__ import annotations

from collections.abc import Mapping

TRUSTED_RELEASE_KEYS: Mapping[str, str] = {}
