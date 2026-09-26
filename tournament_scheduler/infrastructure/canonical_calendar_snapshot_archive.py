"""Durable, content-addressed archive of pre-refresh calendar evidence snapshots.

``season refresh-calendars`` replaces a promoted season's calendar evidence in
place. The canonical files keep only bounded provenance (per-source summaries,
timestamps and fingerprints) so they stay reviewable, but an audit must also be
able to reconstruct *what evidence was replaced* -- including the first refresh
of a season promoted before ``calendar_evidence`` existed, when no previous
source summary was ever recorded. The complete previous calendar payload and
source-policy snapshot is therefore stored here, addressed by the stable SHA-256
of its canonical JSON serialization and referenced from the refreshed calendar
evidence record.

The archive lives under ``season/<season>/evidence/calendar/`` and is therefore
Git-tracked with the canonical state. The canonical-season store carries the
whole ``evidence/`` tree forward by hardlink or copy on every atomic commit, so a
later mutation never drops retained snapshot evidence. Writes are atomic
(stage + fsync + replace), the stored checksum is re-verified on read, and a
missing or invalid referenced snapshot is an explicit error rather than silently
treated as absent.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from tournament_scheduler.infrastructure.canonical_evidence_archive import (
    EVIDENCE_SUBDIR,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    DEFAULT_SEASON_ROOT,
)
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

ARCHIVE_SCHEMA_VERSION = 1
CALENDAR_SUBDIR = "calendar"

#: SHA-256 digests are content addresses, so the reference path is fully
#: determined by the digest and a mismatch means the reference is corrupt.
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class CalendarSnapshotArchiveError(RuntimeError):
    """Raised when referenced calendar snapshot evidence is missing or invalid."""


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def calendar_snapshots_dir(
    season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT
) -> Path:
    return Path(root) / season / EVIDENCE_SUBDIR / CALENDAR_SUBDIR


def calendar_snapshot_ref(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return the content-addressed reference for *snapshot* without writing."""

    digest = stable_payload_sha256(snapshot)
    return {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "algorithm": "sha256",
        "sha256": digest,
        "path": f"{EVIDENCE_SUBDIR}/{CALENDAR_SUBDIR}/{digest}.json",
    }


def archive_calendar_snapshot(
    season: str,
    *,
    snapshot: Mapping[str, Any],
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
) -> dict[str, Any]:
    """Write one pre-refresh snapshot durably and return its reference.

    Re-archiving identical content is idempotent (same content, same path). An
    existing file whose stored checksum no longer matches is refused rather than
    overwritten.
    """

    ref = calendar_snapshot_ref(snapshot)
    directory = calendar_snapshots_dir(season, root=root)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{ref['sha256']}.json"
    payload: dict[str, Any] = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "snapshot_hash": ref["sha256"],
        "snapshot": snapshot,
    }
    if target.exists():
        _verify_existing_snapshot(target, ref["sha256"])
        return ref

    staging_fd, staging_name = tempfile.mkstemp(
        prefix=f".{ref['sha256']}.", suffix=".tmp", dir=directory
    )
    os.close(staging_fd)
    staging = Path(staging_name)
    try:
        staging.write_bytes(_json_bytes(payload))
        with staging.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(staging, target)
    finally:
        if staging.exists():
            staging.unlink(missing_ok=True)
    return ref


def load_calendar_snapshot(
    season: str,
    ref: Mapping[str, Any],
    *,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
) -> dict[str, Any]:
    """Read and verify one referenced pre-refresh snapshot.

    Raises :class:`CalendarSnapshotArchiveError` when the reference is missing,
    the file is absent, or the stored content hash does not match the file bytes.
    """

    if not isinstance(ref, Mapping):
        raise CalendarSnapshotArchiveError("Calendar snapshot reference is not an object")
    expected = str(ref.get("sha256") or "")
    relative_path = str(ref.get("path") or "")
    if not _HEX64.fullmatch(expected):
        raise CalendarSnapshotArchiveError(
            f"Calendar snapshot reference has an invalid SHA-256 digest: {expected!r}"
        )
    expected_path = f"{EVIDENCE_SUBDIR}/{CALENDAR_SUBDIR}/{expected}.json"
    if relative_path != expected_path:
        raise CalendarSnapshotArchiveError(
            "Calendar snapshot reference path does not match its content digest: "
            f"{relative_path} != {expected_path}"
        )
    path = (Path(root) / season / relative_path).resolve()
    allowed_root = calendar_snapshots_dir(season, root=root).resolve()
    if allowed_root not in path.parents and path.parent != allowed_root:
        raise CalendarSnapshotArchiveError(
            f"Calendar snapshot reference escapes the season archive root: {relative_path}"
        )
    if not path.exists():
        raise CalendarSnapshotArchiveError(
            f"Referenced calendar snapshot archive is missing: {relative_path}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalendarSnapshotArchiveError(
            f"Referenced calendar snapshot archive is not readable JSON: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise CalendarSnapshotArchiveError("Referenced calendar snapshot archive is not a JSON object")
    stored = str(payload.get("snapshot_hash") or "")
    snapshot = payload.get("snapshot")
    if stored != expected or not isinstance(snapshot, Mapping) or stable_payload_sha256(snapshot) != expected:
        raise CalendarSnapshotArchiveError(
            f"Referenced calendar snapshot archive is corrupt or stale: {relative_path}"
        )
    return dict(snapshot)


def _verify_existing_snapshot(path: Path, expected: str) -> None:
    """Refuse to overwrite an archive that no longer matches its content hash."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalendarSnapshotArchiveError(
            f"Existing calendar snapshot archive is not readable JSON: {exc}"
        ) from exc
    stored = str(payload.get("snapshot_hash") or "") if isinstance(payload, Mapping) else ""
    snapshot = payload.get("snapshot") if isinstance(payload, Mapping) else None
    if stored != expected or not isinstance(snapshot, Mapping) or stable_payload_sha256(snapshot) != expected:
        raise CalendarSnapshotArchiveError(
            f"Calendar snapshot archive {path} already exists with a different content hash; "
            "refusing to overwrite"
        )


__all__ = [
    "ARCHIVE_SCHEMA_VERSION",
    "CALENDAR_SUBDIR",
    "CalendarSnapshotArchiveError",
    "archive_calendar_snapshot",
    "calendar_snapshot_ref",
    "calendar_snapshots_dir",
    "load_calendar_snapshot",
]
