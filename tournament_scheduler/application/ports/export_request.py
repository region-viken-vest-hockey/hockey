"""Export request and manifest ports.

Defines the contract for export requests, bundles, and publish intents.
These ports are consumed by #960 and used by the application layer;
they import no subprocess, HTTP, browser, CLI or RVV layout modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional
import json

from .common import PORT_CONTRACT_VERSION, PortCapabilities, PortError, PortErrorCode


class Format(str, Enum):
    """Canonical export formats."""
    XLSX = "xlsx"
    CSV = "csv"
    ICS = "ics"
    Spond = "spond"
    HTML = "html"


@dataclass(frozen=True)
class ExportArtifact:
    """One artifact within an export bundle."""
    name: str
    media_type: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.name or len(self.sha256) != 64 or self.size_bytes < 0:
            raise ValueError("artifact needs a name, a hex sha256 and a non-negative size")


@dataclass(frozen=True)
class ExportBundle:
    """Artifacts generated from exactly one canonical revision."""
    canonical_revision: str
    format_version: int
    artifacts: tuple[ExportArtifact, ...]

    def __post_init__(self) -> None:
        if not self.canonical_revision or not self.artifacts:
            raise ValueError("bundle needs a revision and artifacts")
        names = [a.name for a in self.artifacts]
        if len(set(names)) != len(names):
            raise ValueError("artifact names must be unique")

    def fingerprint(self) -> str:
        """Order-independent identity of the bundle content."""
        import hashlib
        parts = [self.canonical_revision, str(self.format_version)]
        parts += sorted(f"{a.name}:{a.sha256}" for a in self.artifacts)
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PublishIntent:
    """Explicit request to deliver one exact bundle to one destination."""
    destination: str
    target: str
    bundle_fingerprint: str
    canonical_revision: str
    authorized_by: str
    authorization_ref: str

    def __post_init__(self) -> None:
        if not (self.authorized_by and self.authorization_ref):
            raise PortError(
                PortErrorCode.AUTHORIZATION_REQUIRED,
                "publication requires explicit operator authorization"
            )

    def matches(self, bundle: ExportBundle) -> bool:
        return self.bundle_fingerprint == bundle.fingerprint() and self.canonical_revision == bundle.canonical_revision


@dataclass(frozen=True)
class ExportRequest:
    """Request to export one bundle from one canonical revision."""
    request_id: str
    canonical_revision: str
    format: Format
    artifacts: tuple[ExportArtifact, ...]
    privacy_classification: str
    verification_status: str


@dataclass(frozen=True)
class ExportManifest:
    """Manifest of an export request."""
    request_id: str
    format: Format
    canonical_revision: str
    artifacts: tuple[ExportArtifact, ...]
    privacy_classification: str
    verification_status: str
    fingerprint: str


def manifest_from_request(req: ExportRequest) -> ExportManifest:
    """Convert an ExportRequest to an ExportManifest."""
    from hashlib import sha256
    artifact_parts = sorted(
        [f"{a.name}:{a.sha256}" for a in req.artifacts]
    )
    parts = [req.canonical_revision, str(req.format.value)] + artifact_parts
    fingerprint = sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return ExportManifest(
        request_id=req.request_id,
        format=req.format,
        canonical_revision=req.canonical_revision,
        artifacts=req.artifacts,
        privacy_classification=req.privacy_classification,
        verification_status=req.verification_status,
        fingerprint=fingerprint,
    )


# Port capabilities
EXPORT_FEATURES = frozenset({"export", "manifest", "publish"})


def export_capabilities(adapter_id: str, *, features: frozenset[str] | None = None) -> PortCapabilities:
    unknown = (features or EXPORT_FEATURES) - EXPORT_FEATURES
    if unknown:
        raise ValueError(f"unknown export port features: {sorted(unknown)}")
    return PortCapabilities(adapter_id=adapter_id, features=frozenset(features or EXPORT_FEATURES))
