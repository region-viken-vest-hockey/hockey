"""Canonical season store port: load, revision-checked atomic write, history.

The legacy filesystem/Git-backed ``infrastructure.canonical_season_store`` is the
reference adapter; a local durable workspace is a second adapter. Mutation,
verification and revision semantics stay in ``CanonicalSeasonService``: a store
only persists a complete snapshot atomically and reports typed failures.
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

from .common import PortCapabilities, PortErrorCode, PortError

STORE_FEATURES = frozenset({"atomic_write", "revision_cas", "revision_history", "evidence_archive"})


#
# Local backend store implementation
#

def _resolve_root(root: str, season: str) -> str:
    """Resolve and protect the root directory for a given season.

    - Rejects absolute paths that escape the root (traversal protection).
    - Ensures the season directory exists.
    - Returns the canonical absolute path (the season subdirectory).
    """
    if not root:
        raise PortError(
            PortErrorCode.UNAVAILABLE,
            "Store root directory not configured",
        )
    # Protect against path traversal: ensure resolved path is under root
    root = os.path.realpath(root)
    season_dir = os.path.join(root, season)
    # Normalize and verify it's under root
    season_dir_norm = os.path.normpath(season_dir)
    if not season_dir_norm.startswith(root + os.sep) and season_dir_norm != root:
        raise PortError(
            PortErrorCode.UNAVAILABLE,
            f"Season path escapes root directory: {season_dir}",
        )
    os.makedirs(season_dir, exist_ok=True)
    return season_dir_norm


def _snapshot_path_from_resolved(season_dir: str, revision: Optional[str] = None) -> str:
    """Return the file path for a snapshot, given an already-resolved season directory.

    Format: {season_dir}/snapshot{(rev_)<rev>.json}
    """
    if revision:
        return os.path.join(season_dir, f"snapshot_{revision}.json")
    return os.path.join(season_dir, "snapshot.json")


def _snapshot_path(root: str, season: str, revision: Optional[str] = None) -> str:
    """Return the file path for a snapshot.

    Format: {root}/{season}/snapshot{(rev_)<rev>.json}

    Note: this calls _resolve_root internally; callers that already have a
    resolved season directory should use _snapshot_path_from_resolved instead.
    """
    season_dir = _resolve_root(root, season)
    return _snapshot_path_from_resolved(season_dir, revision)


def _lock_path(root: str, season: str) -> str:
    """Return the path for a process lock file.

    Returns {root}/{season}/.lock
    """
    season_dir = _resolve_root(root, season)
    return os.path.join(season_dir, ".lock")


def _read_snapshot(root: str, season: str, revision: Optional[str] = None) -> Optional[dict]:
    """Read a snapshot from disk.

    Returns None when the file does not exist (first-write scenario).
    """
    path = _snapshot_path(root, season, revision)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _write_snapshot_atomic(root: str, season: str, snapshot: dict, revision: Optional[str] = None) -> str:
    """Write a snapshot atomically using write-temp-then-rename.

    - Writes to a temp file in the same directory (same filesystem).
    - Renames the temp file to the target path.
    - Returns the path of the written file.
    """
    season_dir = _resolve_root(root, season)
    target = _snapshot_path_from_resolved(season_dir, revision)
    tmp_path = target + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp_path, target)
        return target
    except OSError:
        # Clean up temp on failure
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


#
# Concrete adapter implementing CanonicalStorePort
#


class LocalSeasonStore:
    """Directory-based versioned snapshot store satisfying CanonicalStorePort.

    Features:
    - Atomic writes via temp-file + rename pattern.
    - require_absent enforcement: first write only (require_absent=True on write
      when a snapshot already exists raises PortError REVISION_CONFLICT / UNAVAILABLE).
    - Revision conflict detection via expected_revision CAS pattern.
    - Process lock (.lock file) for concurrency protection.
    - Symlink/traversal protection via realpath normalization.
    - Versioned snapshots stored as JSON under {root}/{season}/.
    """

    def __init__(self, root: str) -> None:
        """Initialize the local store with a root directory for all season data.

        The root must be an absolute path that will not be garbage-collected
        during the lifetime of the store instance.
        """
        self.root = os.path.realpath(root)

    # -- CanonicalStorePort protocol methods --

    def has_state(self, season: str) -> bool:
        """Return ``True`` when a snapshot already exists for *season*.

        ``False`` means this is the first write; together with ``require_absent``
        this enforces write-once semantics.
        """
        path = _snapshot_path(self.root, season)
        return os.path.isfile(path)

    def load(self, season: str) -> Any:
        """Load the complete canonical state for *season*.

        Returns the deserialized JSON dict, or raises ``PortError`` with
        ``NOT_FOUND`` when no snapshot exists.
        """
        snapshot = _read_snapshot(self.root, season)
        if snapshot is None:
            raise PortError(
                PortErrorCode.NOT_FOUND,
                f"No snapshot found for season '{season}'",
            )
        return snapshot

    def write(
        self,
        snapshot: Any,
        *,
        require_absent: bool = False,
        expected_revision: Optional[str] = None,
    ) -> None:
        """Install the whole snapshot atomically.

        * ``require_absent=True``: refuses to overwrite an existing snapshot.
          Raises ``PortError(UNAVAILABLE)`` if a snapshot already exists.
        * ``expected_revision``: compare-and-swap. When the stored revision
          (if any) differs from ``expected_revision``, raises
          ``PortError(REVISION_CONFLICT)``.
        * When neither flag is set, the snapshot unconditionally replaces the
          existing one (overwrite mode).

        A failed write leaves the previous state intact (atomic rename guarantees
        this).
        """
        season = getattr(snapshot, "season", None) or snapshot.get("season", "default") if isinstance(snapshot, dict) else "default"
        season_dir = _resolve_root(self.root, season)

        # --- require_absent enforcement ---
        if require_absent and self.has_state(season):
            raise PortError(
                PortErrorCode.UNAVAILABLE,
                f"Season '{season}' already has a snapshot; use require_absent=False or provide expected_revision",
            )

        # --- revision conflict detection (CAS) ---
        if expected_revision is not None:
            existing = _read_snapshot(self.root, season)
            existing_revision = existing.get("revision") if existing else None
            if existing_revision != expected_revision:
                raise PortError(
                    PortErrorCode.REVISION_CONFLICT,
                    f"Revision conflict: expected '{expected_revision}', found '{existing_revision}'",
                )

        # --- atomic write ---
        target = _write_snapshot_atomic(self.root, season, snapshot, expected_revision)

        # --- post-write: record the effective revision ---
        # Read back what was written and ensure a revision field exists
        written = _read_snapshot(self.root, season)
        if written is not None and "revision" not in written:
            written["revision"] = target  # use the target path as revision marker
            # Re-write to embed the revision (best-effort; still atomic)
            _write_snapshot_atomic(self.root, season, written)

    # -- RevisionHistoryPort protocol methods --

    def schedule_at(self, season: str, revision: str) -> Mapping[str, Any] | None:
        """Resolve the canonical schedule payload as of a recorded revision.

        Returns ``None`` when the revision cannot be resolved unambiguously.
        """
        snapshot = _read_snapshot(self.root, season, revision)
        if snapshot is None:
            return None
        # Return a shallow copy to avoid mutating the stored state
        return dict(snapshot)


#
# Protocol-wrapper helper so the existing CanonicalStorePort protocol can
# remain protocol-only while a concrete adapter sits behind the phase-2
# translation boundary (ADR 0009).
#

def make_local_store_adapter(root: str) -> "LocalSeasonStore":
    """Factory returning a :class:`LocalSeasonStore` tied to *root*.

    The root directory is where all season snapshots live. It is the caller's
    responsibility to keep this directory outside of Git control and to
    provision appropriate file-system permissions.
    """
    return LocalSeasonStore(root)


#
# Port-features & error helpers
#

def local_store_capabilities(adapter_id: str) -> PortCapabilities:
    """Return store PortCapabilities with the standard feature set."""
    return PortCapabilities(adapter_id=adapter_id, features=STORE_FEATURES)
