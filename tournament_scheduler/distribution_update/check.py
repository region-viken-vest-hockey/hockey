"""Release manifest cache, injectable transport and update check.

Network access is confined to :class:`Transport`; tests inject fakes. The cache
holds only validated release metadata and is never part of season state.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from .errors import ManifestError, NetworkError, UpdateError
from .manifest import (
    MAX_MANIFEST_BYTES,
    UpdateManifest,
    UpdateStatus,
    WriteDecision,
    classify,
    parse_manifest,
    write_decision,
)

DEFAULT_TIMEOUT_SECONDS = 20.0
CACHE_FILE_NAME = "release-manifest.json"


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(payload)
    os.replace(temp, path)


def save_cached_manifest(cache_dir: Path, raw: bytes) -> None:
    parse_manifest(raw)  # only validated manifests are cached
    _atomic_write_bytes(cache_dir / CACHE_FILE_NAME, raw)


def load_cached_manifest(cache_dir: Path) -> UpdateManifest | None:
    """Return the cached manifest, ``None`` when none exists, and raise when it is unreadable."""
    path = cache_dir / CACHE_FILE_NAME
    if not path.exists():
        return None
    try:
        return parse_manifest(path.read_bytes())
    except OSError as exc:
        raise ManifestError(f"cached manifest unreadable: {exc}") from exc


def write_decision_from_cache(installed: str, cache_dir: Path) -> WriteDecision:
    """Gate for mutation paths: a corrupt cache blocks writes instead of being ignored."""
    try:
        manifest = load_cached_manifest(cache_dir)
    except ManifestError as exc:
        return WriteDecision(False, None, f"cached release manifest is unusable ({exc}); run an update check")
    return write_decision(installed, manifest)


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
    transport: Transport,
    cache_dir: Path | None = None,
) -> UpdateCheck:
    """Fetch and classify the release manifest. Never raises for offline or bad-manifest cases.

    The cache is only replaced after a manifest validates, so a failed check leaves
    the previous cached manifest (and therefore the previous write gate) in place.
    """
    try:
        raw = transport.read_bytes(manifest_url, MAX_MANIFEST_BYTES)
        manifest = parse_manifest(raw)
    except UpdateError as exc:
        return UpdateCheck(installed, None, None, str(exc))
    if cache_dir is not None:
        try:
            save_cached_manifest(cache_dir, raw)
        except OSError as exc:
            return UpdateCheck(installed, None, None, f"could not cache manifest: {exc}")
    return UpdateCheck(installed, classify(installed, manifest), manifest, None)
