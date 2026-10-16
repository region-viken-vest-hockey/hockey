"""Exception types for the packaged update flow."""

from __future__ import annotations


class UpdateError(Exception):
    """Base class for update failures. Callers must treat these as non-mutating."""


class ManifestError(UpdateError):
    """The update manifest is malformed or not for this package."""


class NetworkError(UpdateError):
    """The release source could not be reached or returned an error."""


class UntrustedManifestError(UpdateError):
    """The manifest signature is missing, malformed, from an unknown key ID or does not verify.

    Untrusted metadata must never offer an update or change the write gate.
    """


class IntegrityError(UpdateError):
    """A downloaded or extracted artifact does not match the manifest."""


class ActivationError(UpdateError):
    """A staged release could not be activated; the previous release is restored."""


class WriteBlockedError(UpdateError):
    """The installed version may not perform canonical write operations."""
