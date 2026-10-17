"""Embedded trust roots for signed release manifests.

Only PUBLIC keys belong here. The release workflow holds the matching private
keys in a protected secret; no private material may be committed to this
repository or packaged into the application.

Keys are keyed by a stable key ID. Rotation is additive: a client release
trusts the old and the new key IDs together, and only a later release stops
trusting the old one. Values are base64-encoded raw 32-byte Ed25519 public keys.

Public key IDs and values are added here only by a reviewed commit. The key
``release-2026-01`` is the project's long-lived release-signing key; its private
half is held outside the repository and in the protected ``software-release``
environment. If this mapping is ever empty, every manifest is untrusted, so no
update is offered and the write gate is never relaxed by release metadata.
"""

from __future__ import annotations

from collections.abc import Mapping

TRUSTED_RELEASE_KEYS: Mapping[str, str] = {
    "release-2026-01": "8/gSeZxoIkmytUeEY8ORXvRtiCLnMSLMkaR6pGiualE=",
}
