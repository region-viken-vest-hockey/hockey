"""Durable, versioned backup of the pre-compaction canonical ``decisions.json``.

``canonical_season.history.compact_history`` migrates oversized inline move
evidence out of ``decisions.json``. Before it mutates anything it must preserve
the *complete original* file byte-for-byte so the migration is reversible and
auditable. This module owns that backup: a content-addressed, immutable copy of
the exact original bytes plus a manifest recording the source revision,
schedule fingerprint, timestamp, original path and the event indices that were
compacted.

The backup lives under ``season/<season>/evidence/backup/`` (a sibling of the
content-addressed move-evidence archive), so it is Git-tracked with the season,
carried forward verbatim by the canonical store's atomic directory swap, and
never touched by export cleanup or ``.pipeline`` cleanup. Re-running the
migration on already-compacted data produces a different file hash, so no
backup is ever duplicated; a pre-existing backup with the same content hash is
verified and reused.

Writes are atomic (stage + fsync + replace). Every read re-verifies the stored
bytes against the recorded SHA-256, so truncation or corruption is an explicit
error rather than a silently trusted backup.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from tournament_scheduler.infrastructure.canonical_season_store import (
    DEFAULT_SEASON_ROOT,
)

BACKUP_SCHEMA_VERSION = 1
BACKUP_SUBDIR = "backup"
DECISIONS_FILENAME = "decisions.json"
MANIFEST_FILENAME = "manifest.json"


class CompactionBackupError(RuntimeError):
    """Raised when a compaction backup is missing, invalid or ambiguous."""


def backup_dir(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> Path:
    return Path(root) / season / "evidence" / BACKUP_SUBDIR


def compaction_backup_id(decisions_bytes: bytes) -> str:
    """Return the content address (SHA-256 hex) of the original decisions bytes."""

    return hashlib.sha256(decisions_bytes).hexdigest()


def _backup_path(season: str, backup_id: str, *, root: str | os.PathLike[str]) -> Path:
    return backup_dir(season, root=root) / backup_id


def _manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    return (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def create_compaction_backup(
    *,
    season: str,
    decisions_bytes: bytes,
    source_canonical_revision: str,
    schedule_fingerprint: str,
    original_path: str,
    actor: str,
    note: str = "",
    created_at: str,
    history_event_count: int,
    compacted_event_indices: list[int],
    archived_evidence_hashes: list[str],
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
) -> dict[str, Any]:
    """Persist a byte-for-byte backup of the original ``decisions.json``.

    Returns the manifest. Identical content is stored once: if a backup with the
    same content address already exists it is verified and reused rather than
    duplicated.
    """

    backup_id = compaction_backup_id(decisions_bytes)
    manifest: dict[str, Any] = {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "backup_id": backup_id,
        "season": season,
        "decisions_sha256": backup_id,
        "decisions_bytes": len(decisions_bytes),
        "source_canonical_revision": source_canonical_revision,
        "schedule_fingerprint": schedule_fingerprint,
        "created_at": created_at,
        "actor": actor,
        "note": note,
        "original_path": original_path,
        "history_event_count": history_event_count,
        "compacted_event_indices": [int(i) for i in compacted_event_indices],
        "archived_evidence_hashes": [str(h) for h in archived_evidence_hashes],
    }

    directory = _backup_path(season, backup_id, root=root)
    if directory.is_dir():
        _verify_existing_backup(directory, backup_id, len(decisions_bytes), decisions_bytes)
        return manifest

    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{backup_id}.", suffix=".tmp", dir=directory.parent)
    )
    try:
        (staging / DECISIONS_FILENAME).write_bytes(decisions_bytes)
        (staging / MANIFEST_FILENAME).write_bytes(_manifest_bytes(manifest))
        for name in (DECISIONS_FILENAME, MANIFEST_FILENAME):
            with (staging / name).open("rb") as handle:
                os.fsync(handle.fileno())
        os.replace(staging, directory)
    finally:
        if staging.exists():
            import shutil

            shutil.rmtree(staging, ignore_errors=True)
    return manifest


def load_compaction_backup(
    season: str,
    backup_id: str,
    *,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
) -> tuple[dict[str, Any], bytes]:
    """Read and verify one compaction backup.

    Returns ``(manifest, decisions_bytes)``. Raises :class:`CompactionBackupError`
    when the backup directory, manifest or decisions file is missing, when the
    stored bytes no longer match the recorded SHA-256, or when the manifest does
    not round-trip through the recorded content address.
    """

    directory = _backup_path(season, backup_id, root=root)
    if not directory.is_dir():
        raise CompactionBackupError(f"Compaction backup is missing: {backup_id}")
    decisions_file = directory / DECISIONS_FILENAME
    manifest_file = directory / MANIFEST_FILENAME
    if not decisions_file.is_file():
        raise CompactionBackupError(f"Compaction backup {backup_id} is missing its decisions file")
    if not manifest_file.is_file():
        raise CompactionBackupError(f"Compaction backup {backup_id} is missing its manifest")
    decisions_bytes = decisions_file.read_bytes()
    actual = hashlib.sha256(decisions_bytes).hexdigest()
    if actual != backup_id:
        raise CompactionBackupError(
            f"Compaction backup {backup_id} is corrupt: stored bytes hash to {actual}"
        )
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompactionBackupError(f"Compaction backup {backup_id} has an unreadable manifest: {exc}") from exc
    if not isinstance(manifest, Mapping):
        raise CompactionBackupError(f"Compaction backup {backup_id} has a non-object manifest")
    if str(manifest.get("backup_id") or "") != backup_id:
        raise CompactionBackupError(
            f"Compaction backup {backup_id} manifest records a different backup_id"
        )
    if int(manifest.get("decisions_bytes") or -1) != len(decisions_bytes):
        raise CompactionBackupError(
            f"Compaction backup {backup_id} manifest byte count does not match the stored file"
        )
    # The original must still be valid logical JSON that round-trips to the
    # same content address (byte-for-byte, not merely semantically).
    try:
        json.loads(decisions_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompactionBackupError(f"Compaction backup {backup_id} is not valid JSON: {exc}") from exc
    return dict(manifest), decisions_bytes


def list_compaction_backups(
    season: str,
    *,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
) -> list[dict[str, Any]]:
    """Return the manifest of every stored compaction backup, newest first."""

    directory = backup_dir(season, root=root)
    if not directory.is_dir():
        return []
    backups: list[dict[str, Any]] = []
    for child in directory.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        manifest_file = child / MANIFEST_FILENAME
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, Mapping):
            continue
        backups.append(dict(manifest))
    backups.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return backups


def _verify_existing_backup(
    directory: Path,
    backup_id: str,
    expected_bytes: int,
    expected: bytes,
) -> None:
    """Refuse to reuse a pre-existing backup that no longer matches its content."""

    decisions_file = directory / DECISIONS_FILENAME
    if not decisions_file.is_file() or decisions_file.stat().st_size != expected_bytes:
        raise CompactionBackupError(
            f"Compaction backup {backup_id} already exists with different content; refusing to overwrite"
        )
    stored = decisions_file.read_bytes()
    if hashlib.sha256(stored).hexdigest() != backup_id or stored != expected:
        raise CompactionBackupError(
            f"Compaction backup {backup_id} already exists with different content; refusing to overwrite"
        )


__all__ = [
    "BACKUP_SCHEMA_VERSION",
    "BACKUP_SUBDIR",
    "DECISIONS_FILENAME",
    "MANIFEST_FILENAME",
    "CompactionBackupError",
    "backup_dir",
    "compaction_backup_id",
    "create_compaction_backup",
    "list_compaction_backups",
    "load_compaction_backup",
]
