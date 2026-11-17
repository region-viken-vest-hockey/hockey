"""Export artifacts versus publication destinations.

An *export format* (XLSX, HTML, JSON bundle) says what bytes are produced from a
canonical revision. A *destination* (a local directory, Git branch, Pages,
WordPress) says where they may go. They are separate: producing an export never
publishes it, and publication is an explicit, authorized intent bound to the
exact bundle.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from .common import PortCapabilities, PortError, PortErrorCode


@dataclass(frozen=True)
class ExportArtifact:
    name: str
    media_type: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if len(self.sha256) != 64 or self.size_bytes < 0 or not self.name:
            raise ValueError("artifact needs a name, a hex sha256 and a non-negative size")


@dataclass(frozen=True)
class ExportBundle:
    """Artifacts generated from exactly one canonical revision."""

    canonical_revision: str
    format_version: int
    artifacts: tuple[ExportArtifact, ...]

    def __post_init__(self) -> None:
        names = [artifact.name for artifact in self.artifacts]
        if not self.canonical_revision or not self.artifacts or len(set(names)) != len(names):
            raise ValueError("a bundle needs a revision and uniquely named artifacts")

    def fingerprint(self) -> str:
        """Order-independent identity of the bundle content (revision + artifact hashes)."""
        parts = [self.canonical_revision, str(self.format_version)]
        parts += sorted(f"{a.name}:{a.sha256}" for a in self.artifacts)
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


class DestinationKind(str, Enum):
    LOCAL_DIRECTORY = "local_directory"
    GIT_BRANCH = "git_branch"
    PAGES = "pages"
    WORDPRESS = "wordpress"


@dataclass(frozen=True)
class PublishIntent:
    """Explicit request to deliver one exact bundle to one destination.

    ``authorized_by`` and ``authorization_ref`` record the operator's explicit
    publication request; an intent without them is refused at construction.
    """

    destination: DestinationKind
    target: str
    bundle_fingerprint: str
    canonical_revision: str
    authorized_by: str
    authorization_ref: str

    def __post_init__(self) -> None:
        if not (self.authorized_by and self.authorization_ref):
            raise PortError(
                PortErrorCode.AUTHORIZATION_REQUIRED,
                "publication requires explicit operator authorization",
            )

    def matches(self, bundle: ExportBundle) -> bool:
        return (
            self.bundle_fingerprint == bundle.fingerprint()
            and self.canonical_revision == bundle.canonical_revision
        )


@dataclass(frozen=True)
class PublishReceipt:
    destination: DestinationKind
    target: str
    bundle_fingerprint: str
    published_ref: str


@runtime_checkable
class PublishDestinationPort(Protocol):
    def capabilities(self) -> PortCapabilities: ...

    def publish(self, intent: PublishIntent, bundle: ExportBundle) -> PublishReceipt: ...
