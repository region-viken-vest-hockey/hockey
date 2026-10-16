"""Packaged update flow: manifest, write gate, staged download and rollback-safe activation.

Public surface is re-exported here. Owners:

* ``manifest``: manifest format, version ordering, classification, write gate
* ``check``: manifest cache, injectable transport, update check
* ``staging``: staged download, SHA-256 verification, safe extraction
* ``activation``: atomic swap with previous-release rollback
* ``errors``: exception types
"""

from .activation import (
    PREVIOUS_SUFFIX,
    activate_release,
    confirm_release,
    previous_release_path,
    rollback_release,
)
from .check import (
    CACHE_FILE_NAME,
    DEFAULT_TIMEOUT_SECONDS,
    Transport,
    UpdateCheck,
    check_for_update,
    load_cached_manifest,
    save_cached_manifest,
    urllib_transport,
    write_decision_from_cache,
)
from .errors import (
    ActivationError,
    IntegrityError,
    ManifestError,
    NetworkError,
    UpdateError,
    WriteBlockedError,
)
from .manifest import (
    MANIFEST_SCHEMA_VERSION,
    MAX_MANIFEST_BYTES,
    WRITE_ALLOWED_STATUSES,
    Artifact,
    UpdateManifest,
    UpdateStatus,
    WriteDecision,
    classify,
    manifest_to_dict,
    parse_manifest,
    parse_version,
    require_write_allowed,
    write_decision,
)
from .staging import download_verified, extract_release, verify_file

__all__ = [
    "ActivationError", "Artifact", "CACHE_FILE_NAME", "DEFAULT_TIMEOUT_SECONDS",
    "IntegrityError", "MANIFEST_SCHEMA_VERSION", "MAX_MANIFEST_BYTES", "ManifestError",
    "NetworkError", "PREVIOUS_SUFFIX", "Transport", "UpdateCheck", "UpdateError",
    "UpdateManifest", "UpdateStatus", "WRITE_ALLOWED_STATUSES", "WriteBlockedError",
    "WriteDecision", "activate_release", "check_for_update", "classify",
    "confirm_release", "download_verified", "extract_release", "load_cached_manifest",
    "manifest_to_dict", "parse_manifest", "parse_version", "previous_release_path",
    "require_write_allowed", "rollback_release", "save_cached_manifest", "urllib_transport",
    "verify_file", "write_decision", "write_decision_from_cache",
]
