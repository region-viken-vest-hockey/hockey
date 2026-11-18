"""Backend-neutral readiness contract for starting an ``operate`` session.

Stdlib only and free of relative imports so a bare ``python3`` bootstrap script
can load this file directly (before any dependency is installed).

A readiness check answers one question: *is the workflow contract this session
will use verified and current?* ``READY`` carries that exact ``workflow_version``;
anything else is ``REVIEW_REQUIRED`` with one stable reason code. A check never
discards local work to turn itself green.

Readiness deliberately does **not** vouch for canonical season state. Canonical
lifecycle, revision and reconciliation are verified by the separate lifecycle
gate (``season lifecycle`` / handover), which fails closed. A backend that does
verify a canonical revision at readiness time may record it in
``canonical_revision``; it is never required to, and a ``READY`` without it
must not be read as "state verified".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

READINESS_SCHEMA_VERSION = 1


class ReadinessStatus(str, Enum):
    READY = "READY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ReadinessReason(str, Enum):
    READY_CURRENT = "ready_current"
    # Backend freshness could not be established.
    REFRESH_FAILED = "refresh_failed"
    INSPECTION_FAILED = "inspection_failed"
    # Local work would be lost or is ambiguous; never auto-resolved.
    NOT_ON_DEFAULT_LINE = "not_on_default_line"
    LOCAL_CHANGES = "local_changes"
    DIVERGED = "diverged"
    UPDATE_FAILED = "update_failed"
    CHANGED_DURING_CHECK = "changed_during_check"
    # Backend-neutral facts a non-Git workspace reports.
    STORE_UNAVAILABLE = "store_unavailable"
    SCHEMA_INCOMPATIBLE = "schema_incompatible"
    WORKFLOW_VERSION_MISMATCH = "workflow_version_mismatch"
    REVISION_UNVERIFIED = "revision_unverified"


@dataclass(frozen=True)
class ReadinessReport:
    """One readiness verdict.

    ``workflow_version`` identifies the exact repository/application contract the
    session will run (a commit id for the Git-backed backend, an application
    version/digest for a local one). ``canonical_revision`` is optional evidence:
    when present it binds the verdict to the canonical state the backend verified.
    """

    backend: str
    status: ReadinessStatus
    reason_code: ReadinessReason
    detail: str
    workflow_version: str | None = None
    canonical_revision: str | None = None
    schema_version: int = READINESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        ready = self.status is ReadinessStatus.READY
        if ready != (self.reason_code is ReadinessReason.READY_CURRENT):
            raise ValueError("READY if and only if the reason code is ready_current")
        if ready and not self.workflow_version:
            raise ValueError("READY must record the exact workflow_version used")
        if self.canonical_revision is not None and not self.canonical_revision:
            raise ValueError("canonical_revision, when recorded, must be non-empty")
        if not ready and self.workflow_version is not None:
            raise ValueError("REVIEW_REQUIRED must not claim a verified workflow_version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "backend": self.backend,
            "status": self.status.value,
            "reason_code": self.reason_code.value,
            "detail": self.detail,
            "workflow_version": self.workflow_version,
            "canonical_revision": self.canonical_revision,
        }
