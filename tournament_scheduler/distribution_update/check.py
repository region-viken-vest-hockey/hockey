"""Release manifest cache, injectable transport and update check.

Network access is confined to :class:`Transport`; tests inject fakes. The cache
holds only signature-verified release metadata and is never part of season
state. The cached signature is re-verified on every load, so a locally edited
cache is treated as unusable and blocks writes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from .errors import ManifestError, NetworkError, UntrustedManifestError, UpdateError, WriteBlockedError
from .manifest import (
    MAX_MANIFEST_BYTES,
    UpdateManifest,
    UpdateStatus,
    WriteDecision,
    classify,
    write_decision,
)
from .signature import MAX_SIGNATURE_BYTES, SIGNATURE_SUFFIX, parse_verified_manifest

DEFAULT_TIMEOUT_SECONDS = 20.0
CACHE_FILE_NAME = "release-manifest.json"
CACHE_SIGNATURE_NAME = CACHE_FILE_NAME + SIGNATURE_SUFFIX


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(payload)
    os.replace(temp, path)


def save_cached_manifest(
    cache_dir: Path,
    raw: bytes,
    signature: bytes,
    trusted_keys: Mapping[str, str] | None = None,
) -> None:
    """Cache a manifest with its detached signature. Unverified metadata is never cached."""
    parse_verified_manifest(raw, signature, trusted_keys)  # raises when untrusted or invalid
    _atomic_write_bytes(cache_dir / CACHE_FILE_NAME, raw)
    _atomic_write_bytes(cache_dir / CACHE_SIGNATURE_NAME, signature)


def load_cached_manifest(cache_dir: Path, trusted_keys: Mapping[str, str] | None = None) -> UpdateManifest | None:
    """Return the cached manifest, ``None`` when none exists, and raise when it is unreadable or untrusted."""
    path = cache_dir / CACHE_FILE_NAME
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        signature = (cache_dir / CACHE_SIGNATURE_NAME).read_bytes()
    except OSError as exc:
        raise ManifestError(f"cached manifest unreadable: {exc}") from exc
    try:
        return parse_verified_manifest(raw, signature, trusted_keys)
    except UntrustedManifestError as exc:
        raise ManifestError(f"cached manifest is not trusted: {exc}") from exc


def write_decision_from_cache(
    installed: str,
    cache_dir: Path,
    trusted_keys: Mapping[str, str] | None = None,
) -> WriteDecision:
    """Gate for mutation paths: a corrupt or untrusted cache blocks writes instead of being ignored."""
    try:
        manifest = load_cached_manifest(cache_dir, trusted_keys)
    except ManifestError as exc:
        return WriteDecision(False, None, f"cached release manifest is unusable ({exc}); run an update check")
    return write_decision(installed, manifest)


def require_cached_write_allowed(
    installed: str,
    cache_dir: Path,
    trusted_keys: Mapping[str, str] | None = None,
) -> None:
    """Raise :class:`WriteBlockedError` unless the cached manifest allows canonical writes.

    This is the single policy entry point for the application mutation gate and
    the repository commit gate. The message is operator-facing and states that
    nothing was changed.
    """
    decision = write_decision_from_cache(installed, cache_dir, trusted_keys)
    if not decision.allowed:
        raise WriteBlockedError(f"Canonical changes are blocked: {decision.reason}. Nothing was changed.")


@dataclass(frozen=True)
class Transport:
    """Network boundary. Tests supply fakes; production uses :func:`urllib_transport`."""

    read_bytes: Callable[[str, int], bytes]
    download: Callable[[str, Path, int], None]


def _require_https(url: str) -> None:
    if urlsplit(url).scheme != "https":
        raise NetworkError(f"refusing non-https url {url!r}")


def urllib_transport(timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Transport:
    def read_bytes(url: str, limit: int) -> bytes:
        _require_https(url)
        try:
            with urlopen(url, timeout=timeout) as response:  # noqa: S310 - https enforced above
                payload = response.read(limit + 1)
        except (OSError, ValueError) as exc:
            raise NetworkError(f"could not read {url}: {exc}") from exc
        if len(payload) > limit:
            raise NetworkError(f"response from {url} exceeds {limit} bytes")
        return payload

    def download(url: str, dest: Path, max_bytes: int) -> None:
        _require_https(url)
        written = 0
        try:
            with urlopen(url, timeout=timeout) as response, dest.open("wb") as handle:  # noqa: S310
                while chunk := response.read(64 * 1024):
                    written += len(chunk)
                    if written > max_bytes:
                        raise NetworkError(f"download from {url} exceeds the declared size")
                    handle.write(chunk)
        except (OSError, ValueError) as exc:
            raise NetworkError(f"download from {url} failed: {exc}") from exc

    return Transport(read_bytes=read_bytes, download=download)


@dataclass(frozen=True)
class UpdateCheck:
    installed_version: str
    status: UpdateStatus | None
    manifest: UpdateManifest | None
    error: str | None


def check_for_update(
    installed: str,
    manifest_url: str,
    signature_url: str,
    transport: Transport,
    cache_dir: Path | None = None,
    *,
    trusted_keys: Mapping[str, str] | None = None,
) -> UpdateCheck:
    """Fetch, verify and classify the release manifest. Never raises for offline or bad-metadata cases.

    The signature is verified before any manifest field is read. Untrusted metadata
    (unknown key ID, bad or malformed signature) yields status ``untrusted`` and offers
    no update. The cache is only replaced after a verified manifest, so a failed or
    untrusted check leaves the previous cached manifest and write gate in place.
    """
    try:
        raw = transport.read_bytes(manifest_url, MAX_MANIFEST_BYTES)
        signature = transport.read_bytes(signature_url, MAX_SIGNATURE_BYTES)
        manifest = parse_verified_manifest(raw, signature, trusted_keys)
    except UntrustedManifestError as exc:
        return UpdateCheck(installed, UpdateStatus.UNTRUSTED, None, str(exc))
    except UpdateError as exc:
        return UpdateCheck(installed, None, None, str(exc))
    if cache_dir is not None:
        try:
            save_cached_manifest(cache_dir, raw, signature, trusted_keys)
        except OSError as exc:
            return UpdateCheck(installed, None, None, f"could not cache manifest: {exc}")
    return UpdateCheck(installed, classify(installed, manifest), manifest, None)
