"""Update manifest format, version ordering and write-compatibility decisions.

Pure functions only: no I/O. The manifest is the machine-readable release
description published by the release channel; see ``docs/windows-distribution.md``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlsplit

from .. import distribution
from .errors import ManifestError, WriteBlockedError

MANIFEST_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 1024 * 1024

_VERSION_RE = re.compile(r"^\d+(\.\d+)*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class UpdateStatus(str, Enum):
    CURRENT = "current"
    UPDATE_AVAILABLE = "update-available"
    UPDATE_REQUIRED = "update-required"
    UNSUPPORTED = "unsupported"


def parse_version(text: object) -> tuple[int, ...]:
    """Parse a dotted numeric version. Trailing zeros are dropped so ``2.0`` == ``2.0.0``."""
    if not isinstance(text, str) or not _VERSION_RE.match(text):
        raise ManifestError(f"invalid version {text!r}; expected dotted numbers such as 2.0.0")
    parts = [int(part) for part in text.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


@dataclass(frozen=True)
class Artifact:
    filename: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class UpdateManifest:
    channel: str
    latest_version: str
    minimum_write_version: str
    minimum_supported_version: str
    artifact: Artifact


def _require_object(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{where} must be a JSON object")
    return value


def _require_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"manifest field {key!r} must be a non-empty string")
    return value


def _parse_artifact(raw: object) -> Artifact:
    data = _require_object(raw, "artifact")
    filename = _require_text(data, "filename")
    if (
        filename != PurePosixPath(filename).name
        or "\\" in filename
        or filename in (".", "..")
        or not filename.endswith(".zip")
    ):
        raise ManifestError(f"artifact filename {filename!r} must be a plain .zip file name")
    url = _require_text(data, "url")
    if urlsplit(url).scheme != "https":
        raise ManifestError("artifact url must use https")
    sha256 = _require_text(data, "sha256")
    if not _SHA256_RE.match(sha256):
        raise ManifestError("artifact sha256 must be 64 lowercase hex characters")
    size = data.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ManifestError("artifact size must be a positive integer")
    return Artifact(filename=filename, url=url, sha256=sha256, size=size)


def parse_manifest(raw: bytes | str) -> UpdateManifest:
    """Parse and validate a release manifest. Unknown extra keys are ignored."""
    if isinstance(raw, bytes):
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ManifestError("manifest exceeds the maximum accepted size")
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ManifestError("manifest is not UTF-8") from exc
    try:
        data = _require_object(json.loads(raw), "manifest")
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {exc.msg}") from exc

    if data.get("schema") != MANIFEST_SCHEMA_VERSION:
        raise ManifestError(f"unsupported manifest schema {data.get('schema')!r}")
    if _require_text(data, "package") != distribution.PACKAGE_NAME:
        raise ManifestError("manifest is not for the rvv-miniputt package")

    manifest = UpdateManifest(
        channel=_require_text(data, "channel"),
        latest_version=_require_text(data, "latest_version"),
        minimum_write_version=_require_text(data, "minimum_write_version"),
        minimum_supported_version=_require_text(data, "minimum_supported_version"),
        artifact=_parse_artifact(data.get("artifact")),
    )
    supported = parse_version(manifest.minimum_supported_version)
    write = parse_version(manifest.minimum_write_version)
    latest = parse_version(manifest.latest_version)
    if not supported <= write <= latest:
        raise ManifestError("manifest versions must satisfy minimum_supported <= minimum_write <= latest")
    return manifest


def manifest_to_dict(manifest: UpdateManifest) -> dict[str, Any]:
    """Serialise a manifest in the wire format accepted by :func:`parse_manifest`."""
    return {
        "schema": MANIFEST_SCHEMA_VERSION,
        "package": distribution.PACKAGE_NAME,
        "channel": manifest.channel,
        "latest_version": manifest.latest_version,
        "minimum_write_version": manifest.minimum_write_version,
        "minimum_supported_version": manifest.minimum_supported_version,
        "artifact": {
            "filename": manifest.artifact.filename,
            "url": manifest.artifact.url,
            "sha256": manifest.artifact.sha256,
            "size": manifest.artifact.size,
        },
    }


def classify(installed: str, manifest: UpdateManifest) -> UpdateStatus:
    installed_key = parse_version(installed)
    if installed_key < parse_version(manifest.minimum_supported_version):
        return UpdateStatus.UNSUPPORTED
    if installed_key < parse_version(manifest.minimum_write_version):
        return UpdateStatus.UPDATE_REQUIRED
    if installed_key < parse_version(manifest.latest_version):
        return UpdateStatus.UPDATE_AVAILABLE
    return UpdateStatus.CURRENT


WRITE_ALLOWED_STATUSES = frozenset({UpdateStatus.CURRENT, UpdateStatus.UPDATE_AVAILABLE})


@dataclass(frozen=True)
class WriteDecision:
    allowed: bool
    status: UpdateStatus | None
    reason: str


def write_decision(installed: str, manifest: UpdateManifest | None) -> WriteDecision:
    """Decide whether canonical writes are allowed for ``installed``.

    Fails closed for any version below a declared minimum. When no manifest is
    cached yet there is no declared minimum to enforce, so writes are allowed
    and the reason says so; callers that need stronger guarantees must run a
    check first. A corrupt cached manifest is handled by the cache-based helper,
    which blocks writes.
    """
    if manifest is None:
        return WriteDecision(True, None, "no release manifest cached; minimum write version is unknown")
    status = classify(installed, manifest)
    if status in WRITE_ALLOWED_STATUSES:
        return WriteDecision(True, status, f"installed {installed} is {status.value}")
    if status is UpdateStatus.UPDATE_REQUIRED:
        reason = (
            f"installed {installed} is below the minimum write version "
            f"{manifest.minimum_write_version}; install {manifest.latest_version} before writing"
        )
    else:
        reason = (
            f"installed {installed} is below the minimum supported version "
            f"{manifest.minimum_supported_version}; reinstall from a full package before writing"
        )
    return WriteDecision(False, status, reason)


def require_write_allowed(installed: str, manifest: UpdateManifest | None) -> None:
    decision = write_decision(installed, manifest)
    if not decision.allowed:
        raise WriteBlockedError(decision.reason)
