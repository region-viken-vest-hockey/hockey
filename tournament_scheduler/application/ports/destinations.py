from __future__ import annotations
from typing import runtime_checkable

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .common import PortCapabilities, PortError, PortErrorCode


class DestinationKind(str, Enum):
    LOCAL_DIRECTORY = "local"
    GITHUB_PAGES = "github_pages"
    WORDPRESS = "wordpress"


@dataclass(frozen=True)
class WriteArtifact:
    name: str
    media_type: str
    sha256: str
    size_bytes: int
    content: str


@dataclass(frozen=True)
class WriteResult:
    destination: DestinationKind
    target: str
    artifacts_written: tuple[str, ...]
    failed_artifacts: tuple[str, ...]


@runtime_checkable
class DestinationAdapter(Protocol):
    def capabilities(self) -> PortCapabilities: ...
    def write(self, artifacts: tuple[WriteArtifact, ...], *, destination: DestinationKind, target: str) -> WriteResult: ...


LOCAL_FEATURES = frozenset({"local_write", "dry_run", "parity_check"})
GITHUB_PAGES_FEATURES = frozenset({"github_pages_write", "dry_run", "stale_export_refusal"})
WORDPRESS_FEATURES = frozenset({"wordpress_embed", "no_mutable_write"})


def local_capabilities(adapter_id: str, *, features: frozenset[str] | None = None) -> PortCapabilities:
    unknown = (features or LOCAL_FEATURES) - LOCAL_FEATURES
    if unknown:
        raise ValueError(f"unknown local port features: {sorted(unknown)}")
    return PortCapabilities(adapter_id=adapter_id, features=frozenset(features or LOCAL_FEATURES))


def github_pages_capabilities(adapter_id: str, *, features: frozenset[str] | None = None) -> PortCapabilities:
    unknown = (features or GITHUB_PAGES_FEATURES) - GITHUB_PAGES_FEATURES
    if unknown:
        raise ValueError(f"unknown github_pages port features: {sorted(unknown)}")
    return PortCapabilities(adapter_id=adapter_id, features=frozenset(features or GITHUB_PAGES_FEATURES))


def wordpress_capabilities(adapter_id: str, *, features: frozenset[str] | None = None) -> PortCapabilities:
    unknown = (features or WORDPRESS_FEATURES) - WORDPRESS_FEATURES
    if unknown:
        raise ValueError(f"unknown wordpress port features: {sorted(unknown)}")
    return PortCapabilities(adapter_id=adapter_id, features=frozenset(features or WORDPRESS_FEATURES))


def validate_destination_kind(kind: str) -> None:
    if DestinationKind(kind) not in DestinationKind:
        raise PortError(PortErrorCode.NOT_FOUND, f"invalid destination kind: {kind!r}")
