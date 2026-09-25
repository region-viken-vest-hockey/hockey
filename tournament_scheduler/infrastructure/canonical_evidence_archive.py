"""Durable, revision-bound, content-addressed archive of canonical move evidence.

When complete per-move verification evidence must be retained (the explicit
migration/compaction of already oversized ``move`` history entries, or a future
opt-in), it is stored here rather than inline in ``decisions.json`` or in a
disposable ``.pipeline``/export directory that cleanup may remove.

The archive lives under ``season/<season>/evidence/moves/`` and is therefore
Git-tracked with the canonical season state. Every file is addressed by the
stable SHA-256 of the canonical JSON serialization of the *verification result
it preserves*, so the same evidence is stored once no matter how many history
entries reference it, and a reader can always detect truncation or corruption.
The shared content-addressed file holds only the hash-bound verification result
itself; event-specific provenance (``tournament_id``, canonical revision and
event time) lives on each referencing event's ``evidence_ref`` so two distinct
events that carry identical proof each keep their own identity. Writes are
atomic (stage + fsync + replace) and the stored checksum is re-verified on
read; a missing or invalid referenced archive is an explicit error, never
silently treated as verified evidence.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from tournament_scheduler.canonical_history_summary import verification_hash
from tournament_scheduler.infrastructure.canonical_season_store import (
    DEFAULT_SEASON_ROOT,
)

ARCHIVE_SCHEMA_VERSION = 1
EVIDENCE_SUBDIR = "evidence"
MOVES_SUBDIR = "moves"


class EvidenceArchiveError(RuntimeError):
    """Raised when referenced canonical evidence is missing or invalid."""


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def moves_dir(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> Path:
    return Path(root) / season / EVIDENCE_SUBDIR / MOVES_SUBDIR


def evidence_ref(
    verification_result: Mapping[str, Any],
    *,
    tournament_id: str | None = None,
    canonical_revision: str | None = None,
    event_at: str | None = None,
) -> dict[str, Any]:
    """Return the content-addressed reference plus event-specific provenance.

    ``sha256``/``path`` address the *verification result only*, so identical
    payloads share one archive file no matter how many events reference it.
    The per-event provenance (``tournament_id``, ``canonical_revision``,
    ``event_at``) lives on each referencing event's ref, not in the shared file,
    so every event keeps its own identity even when two events carry identical
    proof.
    """

    digest = verification_hash(verification_result)
    return {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "algorithm": "sha256",
        "sha256": digest,
        "path": f"{EVIDENCE_SUBDIR}/{MOVES_SUBDIR}/{digest}.json",
        "tournament_id": tournament_id,
        "canonical_revision": canonical_revision,
        "event_at": event_at,
    }


def archive_move_evidence(
    season: str,
    *,
    tournament_id: str,
    canonical_revision: str,
    event_at: str,
    verification_result: Mapping[str, Any],
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
) -> dict[str, Any]:
    """Write one full verification result to the durable archive atomically.

    Returns the :func:`evidence_ref` that a canonical history entry stores.
    Re-archiving identical evidence is idempotent (same content, same path).
    """

    ref = evidence_ref(
        verification_result,
        tournament_id=tournament_id,
        canonical_revision=canonical_revision,
        event_at=event_at,
    )
    directory = moves_dir(season, root=root)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{ref['sha256']}.json"
    # The shared file holds only the hash-bound verification result; per-event
    # provenance is carried on each referencing event's ``evidence_ref``.
    payload: dict[str, Any] = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "evidence_hash": ref["sha256"],
        "verification_result": verification_result,
    }
    if target.exists():
        _verify_existing_archive(target, ref["sha256"])
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


def load_move_evidence(
    season: str,
    ref: Mapping[str, Any],
    *,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
) -> dict[str, Any]:
    """Read and verify one referenced evidence archive.

    Raises :class:`EvidenceArchiveError` when the reference is missing, the file
    is absent, or the stored content hash does not match the file bytes -- a
    missing/invalid archive is never silently treated as verified evidence.
    """

    if not isinstance(ref, Mapping):
        raise EvidenceArchiveError("Evidence reference is not an object")
    expected = str(ref.get("sha256") or "")
    relative_path = str(ref.get("path") or "")
    if not expected or not relative_path:
        raise EvidenceArchiveError("Evidence reference is missing sha256 or path")
    path = (Path(root) / season / relative_path).resolve()
    # The archive must live under the season's own evidence root.
    allowed_root = moves_dir(season, root=root).resolve()
    if allowed_root not in path.parents and path.parent != allowed_root:
        raise EvidenceArchiveError(f"Evidence reference escapes the season archive root: {relative_path}")
    if not path.exists():
        raise EvidenceArchiveError(f"Referenced evidence archive is missing: {relative_path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceArchiveError(f"Referenced evidence archive is not readable JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise EvidenceArchiveError("Referenced evidence archive is not a JSON object")
    stored = str(payload.get("evidence_hash") or "")
    if stored != expected:
        raise EvidenceArchiveError(
            f"Referenced evidence archive has an unexpected evidence_hash ({stored} != {expected})"
        )
    verification_result = payload.get("verification_result")
    if not isinstance(verification_result, Mapping):
        raise EvidenceArchiveError("Referenced evidence archive is missing its verification_result")
    if verification_hash(verification_result) != expected:
        raise EvidenceArchiveError(
            f"Referenced evidence archive is corrupt: stored verification result does not "
            f"match content hash {expected}"
        )
    return payload


def _verify_existing_archive(path: Path, expected: str) -> None:
    """Refuse to overwrite an archive that no longer matches its content hash."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceArchiveError(f"Existing evidence archive is not readable JSON: {exc}") from exc
    stored = str(payload.get("evidence_hash") or "") if isinstance(payload, Mapping) else ""
    verification_result = payload.get("verification_result") if isinstance(payload, Mapping) else None
    if stored != expected or not isinstance(verification_result, Mapping) or verification_hash(verification_result) != expected:
        raise EvidenceArchiveError(
            f"Evidence archive {path} already exists with a different content hash; refusing to overwrite"
        )


__all__ = [
    "ARCHIVE_SCHEMA_VERSION",
    "EVIDENCE_SUBDIR",
    "MOVES_SUBDIR",
    "EvidenceArchiveError",
    "archive_move_evidence",
    "evidence_ref",
    "load_move_evidence",
    "moves_dir",
]
