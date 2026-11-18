"""Shared port vocabulary: contract version, errors, capabilities, source identity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PORT_CONTRACT_VERSION = 1


class PortErrorCode(str, Enum):
    """Stable, adapter-independent failure classes. Adapters map native errors onto these."""

    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    REVISION_CONFLICT = "revision_conflict"
    SCHEMA_INCOMPATIBLE = "schema_incompatible"
    UNSUPPORTED_FEATURE = "unsupported_feature"
    AUTHORIZATION_REQUIRED = "authorization_required"


class PortError(Exception):
    """A typed port failure. Callers fail closed; they never guess a fallback value."""

    def __init__(self, code: PortErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SchemaCompatibility:
    """Inclusive supported range of one persisted/interchange schema version."""

    name: str
    minimum: int
    maximum: int

    def __post_init__(self) -> None:
        if self.minimum > self.maximum:
            raise ValueError(f"{self.name}: minimum {self.minimum} exceeds maximum {self.maximum}")

    def require(self, found: int) -> None:
        if not self.minimum <= found <= self.maximum:
            raise PortError(
                PortErrorCode.SCHEMA_INCOMPATIBLE,
                f"{self.name} version {found} is outside the supported range "
                f"{self.minimum}..{self.maximum}",
            )


@dataclass(frozen=True)
class PortCapabilities:
    """What one adapter supports. Callers check this instead of probing for failures."""

    adapter_id: str
    contract_version: int = PORT_CONTRACT_VERSION
    features: frozenset[str] = frozenset()
    schemas: tuple[SchemaCompatibility, ...] = ()
    requires_network: bool = False

    def require(self, *features: str) -> None:
        if self.contract_version != PORT_CONTRACT_VERSION:
            raise PortError(
                PortErrorCode.SCHEMA_INCOMPATIBLE,
                f"adapter {self.adapter_id} implements port contract {self.contract_version}, "
                f"application expects {PORT_CONTRACT_VERSION}",
            )
        missing = sorted(set(features) - self.features)
        if missing:
            raise PortError(
                PortErrorCode.UNSUPPORTED_FEATURE,
                f"adapter {self.adapter_id} does not support: {', '.join(missing)}",
            )


class SourceKind(str, Enum):
    MANUAL = "manual"
    FILE = "file"
    LIVE = "live"


@dataclass(frozen=True)
class SourceRef:
    """Provenance of one fact: who/what produced it, from which document, when.

    ``MANUAL`` needs an accountable ``actor``; ``FILE`` a ``document`` and content
    ``fingerprint``; ``LIVE`` a ``fingerprint`` of the observed payload. ``timezone``
    is an IANA name and ``captured_at`` is timezone-aware, so no consumer has to
    guess either.
    """

    source_id: str
    kind: SourceKind
    captured_at: datetime
    timezone: str
    actor: str | None = None
    document: str | None = None
    fingerprint: str | None = None
    revision: str | None = None

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("source_id is required")
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone {self.timezone!r}") from exc
        if self.kind is SourceKind.MANUAL and not self.actor:
            raise ValueError("a manual source requires an accountable actor")
        if self.kind is SourceKind.FILE and not (self.document and self.fingerprint):
            raise ValueError("a file source requires document and fingerprint")
        if self.kind is SourceKind.LIVE and not self.fingerprint:
            raise ValueError("a live source requires a payload fingerprint")
