"""Durable persistence for the promoted canonical-season files.

This module is deliberately boring and hockey-policy-free. It knows only the
two canonical files

    season/<season>/schedule.json
    season/<season>/decisions.json

their schema versions, and how to install a complete snapshot atomically. All
mutation, verification, derived-state reconciliation, history and revision
semantics live in the application-layer
:class:`~tournament_scheduler.application.canonical_season_service.CanonicalSeasonService`.

Both files are always installed together through one staged directory swap, so a
failed write can never leave only ``schedule.json`` or only ``decisions.json``
behind. The swap is crash-durable: staged file contents and directory entries
are fsynced before the swap, and an interruption between the two renames is
recovered on the next write or read by restoring the backup. Durable auxiliary
artifacts (the content-addressed evidence archive) are carried forward by
hardlink rather than re-copied, since they are strictly create-once/immutable;
a copy fallback keeps evidence when hardlinks are unavailable. There is
intentionally no locking/CAS machinery: RVV does not run concurrent
canonical-season writers.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

SEASON_STATE_SCHEMA_VERSION = 1
DECISIONS_SCHEMA_VERSION = 1
DEFAULT_SEASON_ROOT = Path("season")
# Durable auxiliary canonical-season artifacts (for example the content-addressed
# move-evidence archive) that live alongside the two canonical files and must
# survive the atomic directory swap rather than being dropped by it.
EVIDENCE_DIR_NAME = "evidence"


class SeasonStateError(RuntimeError):
    """Raised when canonical season state cannot be read or written safely."""


def season_dir(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> Path:
    return Path(root) / season


def schedule_path(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> Path:
    return season_dir(season, root=root) / "schedule.json"


def decisions_path(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> Path:
    return season_dir(season, root=root) / "decisions.json"


def export_context_path(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> Path:
    return season_dir(season, root=root) / "export_context.json"


def load_export_context(
    season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT
) -> dict[str, Any] | None:
    """Return the persisted public export context, or ``None`` for legacy seasons."""
    _recover_season(season, root=root)
    path = export_context_path(season, root=root)
    if not path.exists():
        return None
    return load_json(path)


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SeasonStateError(f"Canonical season file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SeasonStateError(f"Invalid JSON in canonical season file {path}: {exc}") from exc


def season_id_from_plan(plan_dict: dict[str, Any]) -> str:
    start = str(plan_dict.get("start_date") or "")
    end = str(plan_dict.get("end_date") or "")
    if len(start) >= 4 and len(end) >= 4:
        return f"{start[:4]}-{end[:4]}"
    dates = sorted(str(t.get("date")) for t in plan_dict.get("tournaments", []) if t.get("date"))
    if dates:
        first_year = int(dates[0][:4])
        last_year = int(dates[-1][:4])
        return f"{first_year}-{last_year}"
    raise SeasonStateError("Cannot infer season id; pass --season explicitly")


def load_schedule(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> dict[str, Any]:
    _recover_season(season, root=root)
    payload = load_json(schedule_path(season, root=root))
    version = int(payload.get("schema_version", 0) or 0)
    if version != SEASON_STATE_SCHEMA_VERSION:
        raise SeasonStateError(f"Unsupported schedule schema_version: {version!r}")
    if not isinstance(payload.get("plan"), dict):
        raise SeasonStateError("Canonical schedule is missing its plan payload")
    return payload


def load_decisions(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> dict[str, Any]:
    _recover_season(season, root=root)
    payload = load_json(decisions_path(season, root=root))
    version = int(payload.get("schema_version", 0) or 0)
    if version != DECISIONS_SCHEMA_VERSION:
        raise SeasonStateError(f"Unsupported decisions schema_version: {version!r}")
    if not isinstance(payload.get("decisions"), dict):
        raise SeasonStateError("Canonical decisions file is missing its decisions object")
    return payload


def _carry_forward_evidence(existing_evidence: Path, staging_evidence: Path) -> list[Path]:
    """Carry retained evidence forward into the staged tree without bulk copying.

    Retained evidence is content-addressed and strictly create-once (never
    modified in place), so its files are immutable and safe to hardlink.
    Hardlinks avoid re-reading and re-copying the retained bytes on every
    canonical mutation. When hardlinks are unavailable (for example a
    cross-device staging directory or a filesystem without link support) each
    file is copied instead, so evidence is never dropped. Returns the files that
    were actually copied and therefore still need their contents fsynced
    (hardlinked files are already durable on disk).
    """

    copied: list[Path] = []
    for src in sorted(existing_evidence.rglob("*")):
        if src.is_symlink() or not src.is_file():
            continue
        relative = src.relative_to(existing_evidence)
        dst = staging_evidence / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
            copied.append(dst)
    return copied


def _fsync_directory(path: Path) -> None:
    """Make a directory's entries durable, raising when that cannot be guaranteed.

    A no-op on platforms without directory-fsync support (Windows). An fsync
    failure is surfaced rather than swallowed: the caller is promising crash
    durability, and silently ignoring the failure would report a durable write
    that is not actually durable.
    """

    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_tree(root: Path) -> None:
    """fsync every directory in a tree, deepest first, including the root."""

    if os.name == "nt":
        return
    directories = [root]
    directories.extend(p for p in root.rglob("*") if p.is_dir() and not p.is_symlink())
    for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        _fsync_directory(directory)


def _recover_interrupted_swap(season_directory: Path, backup: Path) -> None:
    """Restore the last durable state if a previous swap was interrupted.

    The swap moves the active directory to ``backup`` before moving the staged
    directory into place, so a crash in that window leaves the active directory
    absent while the last durable state survives at ``backup``. Restoring it here
    makes the next write (and any read) observe the last committed state instead
    of a missing season.
    """

    if season_directory.exists() or not backup.exists():
        return
    os.replace(backup, season_directory)


def _recover_season(
    season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT
) -> None:
    """Restore the last durable state for a season if a swap was interrupted.

    Reads and writes both recover a season whose active directory is missing but
    whose backup still holds the last committed state, so an interrupted swap is
    never observed as a missing season by a read.
    """

    directory = season_dir(season, root=root)
    backup = directory.parent / f".{directory.name}.backup"
    _recover_interrupted_swap(directory, backup)


def _write_season_state_atomic(
    season_directory: Path,
    schedule_payload: dict[str, Any],
    decisions_payload: dict[str, Any],
    *,
    require_absent: bool,
    export_context: dict[str, Any] | None = None,
) -> None:
    """Install the canonical season-state files as one crash-durable boundary.

    The directory is staged and swapped, so a failure never leaves only
    ``schedule.json`` or only ``decisions.json`` behind, and a crash between the
    two renames is recovered on the next write or read by restoring the backup.
    ``require_absent`` refuses to replace existing canonical state (used by
    deliberate promotion); mutation callers replace it and rely on the swap for
    rollback.

    ``export_context.json`` is the immutable public/source presentation
    snapshot promoted with the reviewed handoff. It is written only when
    present; mutation callers carry the loaded snapshot forward so an existing
    file is never dropped by a swap.

    Durable auxiliary artifacts (the content-addressed evidence archive) are
    carried forward by hardlink (with a copy fallback) rather than re-copied,
    and all staged file contents plus directory entries are fsynced before the
    swap so the installed tree is durable on disk.
    """

    parent = season_directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{season_directory.name}.", suffix=".tmp", dir=parent))
    backup = parent / f".{season_directory.name}.backup"
    try:
        _recover_interrupted_swap(season_directory, backup)

        (staging / "schedule.json").write_bytes(_json_bytes(schedule_payload))
        (staging / "decisions.json").write_bytes(_json_bytes(decisions_payload))
        staged_files = [staging / "schedule.json", staging / "decisions.json"]
        if export_context is not None:
            (staging / "export_context.json").write_bytes(_json_bytes(export_context))
            staged_files.append(staging / "export_context.json")

        # Durable auxiliary artifacts (the content-addressed evidence archive)
        # are carried forward verbatim so a mutation never drops retained
        # evidence referenced by decision history. They are immutable, so they
        # are hardlinked (not copied) with a safe copy fallback.
        existing_evidence = season_directory / EVIDENCE_DIR_NAME
        copied_evidence: list[Path] = []
        if existing_evidence.is_dir():
            copied_evidence = _carry_forward_evidence(
                existing_evidence, staging / EVIDENCE_DIR_NAME
            )

        # fsync the newly written primary files and any evidence files that had
        # to be copied (hardlinked evidence is already durable on disk).
        for staged_file in [*staged_files, *copied_evidence]:
            with staged_file.open("rb") as handle:
                os.fsync(handle.fileno())
        # fsync directory entries (new files and hardlinks) before the swap.
        _fsync_tree(staging)

        if season_directory.exists():
            if require_absent:
                raise SeasonStateError(
                    f"Canonical season state already exists for {season_directory.name}; "
                    "use --force only for deliberate replacement"
                )
            if backup.exists():
                shutil.rmtree(backup)
            os.replace(season_directory, backup)
            _fsync_directory(parent)
            try:
                os.replace(staging, season_directory)
            except Exception:
                os.replace(backup, season_directory)
                _fsync_directory(parent)
                raise
            _fsync_directory(parent)
            shutil.rmtree(backup, ignore_errors=True)
            _fsync_directory(parent)
        else:
            os.replace(staging, season_directory)
            _fsync_directory(parent)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


@dataclass(frozen=True)
class CanonicalSeasonSnapshot:
    """The complete durable canonical state for one season.

    ``schedule`` holds the promoted schedule facts and provenance;
    ``decisions`` holds the durable operator/approval/acceptance state; and
    ``export_context`` holds the immutable public/source presentation snapshot
    promoted with the reviewed handoff (``None`` for legacy seasons). They are
    read and written together so callers never observe a half-installed season.
    """

    season: str
    schedule: dict[str, Any]
    decisions: dict[str, Any]
    export_context: dict[str, Any] | None = None

    @property
    def plan(self) -> dict[str, Any]:
        return dict(self.schedule.get("plan") or {})

    @property
    def tournament_fingerprint(self) -> str:
        return str(self.schedule.get("fingerprint") or "")

    def with_schedule(self, schedule: Mapping[str, Any]) -> "CanonicalSeasonSnapshot":
        return replace(self, schedule=dict(schedule))

    def with_decisions(self, decisions: Mapping[str, Any]) -> "CanonicalSeasonSnapshot":
        return replace(self, decisions=dict(decisions))


class CanonicalSeasonStore:
    """Deterministic file persistence for one canonical-season root."""

    def __init__(self, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> None:
        self.root = root

    def directory(self, season: str) -> Path:
        return season_dir(season, root=self.root)

    def schedule_path(self, season: str) -> Path:
        return schedule_path(season, root=self.root)

    def decisions_path(self, season: str) -> Path:
        return decisions_path(season, root=self.root)

    def load_export_context(self, season: str) -> dict[str, Any] | None:
        return load_export_context(season, root=self.root)

    def load(self, season: str) -> CanonicalSeasonSnapshot:
        return CanonicalSeasonSnapshot(
            season=season,
            schedule=load_schedule(season, root=self.root),
            decisions=load_decisions(season, root=self.root),
            export_context=load_export_context(season, root=self.root),
        )

    def write(self, snapshot: CanonicalSeasonSnapshot, *, require_absent: bool = False) -> None:
        _write_season_state_atomic(
            self.directory(snapshot.season),
            snapshot.schedule,
            snapshot.decisions,
            require_absent=require_absent,
            export_context=snapshot.export_context,
        )


__all__ = [
    "CanonicalSeasonSnapshot",
    "CanonicalSeasonStore",
    "DECISIONS_SCHEMA_VERSION",
    "DEFAULT_SEASON_ROOT",
    "SEASON_STATE_SCHEMA_VERSION",
    "SeasonStateError",
    "decisions_path",
    "export_context_path",
    "load_decisions",
    "load_export_context",
    "load_json",
    "load_schedule",
    "schedule_path",
    "season_dir",
    "season_id_from_plan",
]
