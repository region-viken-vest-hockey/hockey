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
a copy fallback keeps evidence when hardlinks are unavailable. Readers and the
writer coordinate through an exclusive advisory per-season-directory lock, so a
read never mistakes the writer's in-progress rename window for an interrupted
swap and never restores the backup from underneath the writer. There is
intentionally no cross-process CAS/versioning machinery beyond that lock: RVV
does not run concurrent canonical-season writers, but reads can run alongside a
write.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator, Mapping

try:  # pragma: no cover - platform dependent
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

SEASON_STATE_SCHEMA_VERSION = 1
DECISIONS_SCHEMA_VERSION = 1
DEFAULT_SEASON_ROOT = Path("season")
# Durable auxiliary canonical-season artifacts (for example the content-addressed
# move-evidence archive) that live alongside the two canonical files and must
# survive the atomic directory swap rather than being dropped by it.
EVIDENCE_DIR_NAME = "evidence"


class SeasonStateError(RuntimeError):
    """Raised when canonical season state cannot be read or written safely."""


class CanonicalCommitDurabilityError(SeasonStateError):
    """The new season state is installed but its durability could not be confirmed.

    Raised when the atomic install rename succeeded but a later directory fsync
    or backup cleanup failed. The write is *committed* (the new state is
    active); the error distinguishes an uncertain durability/finalization step
    from a rolled-back failure, so a caller never mistakes it for an unchanged
    season.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.committed = True


def season_dir(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> Path:
    return Path(root) / season


def schedule_path(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> Path:
    return season_dir(season, root=root) / "schedule.json"


def decisions_path(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> Path:
    return season_dir(season, root=root) / "decisions.json"


def export_context_path(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> Path:
    return season_dir(season, root=root) / "export_context.json"


def _load_export_context_unlocked(
    season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT
) -> dict[str, Any] | None:
    """Read the export context without taking the season lock.

    Callers must already hold the season lock (see :func:`_season_read_lock`) so
    the read cannot race a concurrent swap.
    """

    path = export_context_path(season, root=root)
    if not path.exists():
        return None
    return load_json(path)


def load_export_context(
    season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT
) -> dict[str, Any] | None:
    """Return the persisted public export context, or ``None`` for legacy seasons."""
    with _season_read_lock(season, root=root):
        return _load_export_context_unlocked(season, root=root)


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


def _load_schedule_unlocked(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> dict[str, Any]:
    """Read and validate the schedule without taking the season lock.

    Callers must already hold the season lock (see :func:`_season_read_lock`).
    """

    payload = load_json(schedule_path(season, root=root))
    version = int(payload.get("schema_version", 0) or 0)
    if version != SEASON_STATE_SCHEMA_VERSION:
        raise SeasonStateError(f"Unsupported schedule schema_version: {version!r}")
    if not isinstance(payload.get("plan"), dict):
        raise SeasonStateError("Canonical schedule is missing its plan payload")
    return payload


def load_schedule(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> dict[str, Any]:
    with _season_read_lock(season, root=root):
        return _load_schedule_unlocked(season, root=root)


def _load_decisions_unlocked(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> dict[str, Any]:
    """Read and validate the decisions without taking the season lock.

    Callers must already hold the season lock (see :func:`_season_read_lock`).
    """

    payload = load_json(decisions_path(season, root=root))
    version = int(payload.get("schema_version", 0) or 0)
    if version != DECISIONS_SCHEMA_VERSION:
        raise SeasonStateError(f"Unsupported decisions schema_version: {version!r}")
    if not isinstance(payload.get("decisions"), dict):
        raise SeasonStateError("Canonical decisions file is missing its decisions object")
    return payload


def load_decisions(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> dict[str, Any]:
    with _season_read_lock(season, root=root):
        return _load_decisions_unlocked(season, root=root)


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


def _write_extra_evidence(
    staging: Path, extra_evidence: Mapping[str, bytes]
) -> list[Path]:
    """Write caller-supplied evidence files into the staged season tree.

    Keys are paths relative to the season directory. Files are written with the
    same stage+fsync+replace discipline as the canonical files, inside the same
    locked swap as ``schedule.json``/``decisions.json``, so a reference to a
    newly archived artifact and the artifact itself are never installed
    separately. An already carried-forward file is only accepted when its bytes
    are identical, so a hardlinked active file is never truncated. Returns the
    newly written files.
    """

    written: list[Path] = []
    for relative_path, content in extra_evidence.items():
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise SeasonStateError(
                f"Refusing canonical commit: illegal evidence path {relative_path!r}"
            )
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() == content:
                continue
            raise SeasonStateError(
                "Refusing canonical commit: staged evidence "
                f"{relative_path} already exists with different content"
            )
        staging_fd, staging_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        os.close(staging_fd)
        temp = Path(staging_name)
        try:
            temp.write_bytes(content)
            with temp.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)
        written.append(target)
    return written


def _discard_previous_state(backup: Path) -> None:
    """Best-effort removal of the previous state after a committed swap.

    The new state is already installed and its durability confirmed by this
    point, so a failure to remove the previous state is explicitly **non-fatal**:
    while the active directory exists, recovery never restores the backup, and
    the next write removes it before swapping again. This deliberately does not
    raise, so a transient cleanup failure is not misreported as a failed commit.
    """

    try:
        shutil.rmtree(backup)
    except OSError:
        # A retained backup is harmless and is cleaned up by the next write.
        pass


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


@contextmanager
def _season_directory_lock(season_directory: Path) -> Iterator[None]:
    """Serialize the writer's directory swap against read-time crash recovery.

    The swap temporarily renames the active season directory away before
    installing the staged tree, so for a brief window the active directory is
    absent while the last durable state sits at ``backup``. A concurrent reader
    must not mistake that in-progress window for an interrupted swap and restore
    the backup underneath the active writer. Both the writer's swap and a
    reader's recovery take this exclusive advisory lock, so recovery waits for
    the writer to finish (then observes a present active directory), while a
    genuinely crashed writer has already released the lock for the next reader.

    The lock is advisory and per-season. It is a no-op where ``fcntl`` is
    unavailable (Windows), matching the prior best-effort behavior there.
    """

    if fcntl is None:  # pragma: no cover - Windows fallback
        yield
        return
    parent = season_directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    lock_path = parent / f".{season_directory.name}.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


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


def _recover_if_needed(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> None:
    """Restore the last durable state for a season if a swap was interrupted.

    Called only while the season-directory lock is held, so it can never race
    the writer's swap.
    """

    directory = season_dir(season, root=root)
    backup = directory.parent / f".{directory.name}.backup"
    _recover_interrupted_swap(directory, backup)


@contextmanager
def _season_read_lock(
    season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT
) -> Iterator[None]:
    """Take the season lock and recover an interrupted swap before reading.

    The lock is held across both recovery and the read itself, so a reader can
    neither observe the writer's brief ``active directory renamed away`` window
    as a missing season nor restore the backup from underneath an active writer.
    A crashed writer has already released the lock, so its backup is recovered
    here on the next read.
    """

    directory = season_dir(season, root=root)
    with _season_directory_lock(directory):
        _recover_if_needed(season, root=root)
        yield


def _write_season_state_atomic(
    season_directory: Path,
    schedule_payload: dict[str, Any],
    decisions_payload: dict[str, Any],
    *,
    require_absent: bool,
    export_context: dict[str, Any] | None = None,
    extra_evidence: Mapping[str, bytes] | None = None,
) -> None:
    """Install the canonical season-state files under the swap/recovery lock.

    The exclusive season-directory lock serializes this swap against read-time
    crash recovery, so a reader that observes the brief window where the active
    directory has been renamed to the backup cannot restore the backup from
    underneath the in-progress writer.
    """

    season_directory.parent.mkdir(parents=True, exist_ok=True)
    with _season_directory_lock(season_directory):
        _write_season_state_locked(
            season_directory,
            schedule_payload,
            decisions_payload,
            require_absent=require_absent,
            export_context=export_context,
            extra_evidence=extra_evidence,
        )


def _write_season_state_locked(
    season_directory: Path,
    schedule_payload: dict[str, Any],
    decisions_payload: dict[str, Any],
    *,
    require_absent: bool,
    export_context: dict[str, Any] | None = None,
    extra_evidence: Mapping[str, bytes] | None = None,
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
        # Newly archived evidence files are written into this same staged tree
        # before the swap, so a schedule/decisions reference to them and the
        # artifact itself are installed as one atomic boundary.
        if extra_evidence:
            _write_extra_evidence(staging, extra_evidence)

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
                # The install never happened: roll the original state back.
                os.replace(backup, season_directory)
                _fsync_directory(parent)
                raise
            # From here the new state is installed and authoritative. A failure
            # of the post-install parent fsync is a committed-write durability
            # error, never a rolled-back failure: the caller must not mistake it
            # for an unchanged season.
            try:
                _fsync_directory(parent)
            except OSError as exc:
                raise CanonicalCommitDurabilityError(
                    f"Committed canonical {season_directory.name} (new state installed) "
                    f"but could not finalize its durability: {exc}"
                ) from exc
            # Previous-state cleanup is best-effort and explicitly non-fatal; the
            # fsync below is not.
            _discard_previous_state(backup)
            try:
                _fsync_directory(parent)
            except OSError as exc:
                raise CanonicalCommitDurabilityError(
                    f"Committed canonical {season_directory.name} (new state installed) "
                    f"but could not finalize its durability: {exc}"
                ) from exc
        else:
            os.replace(staging, season_directory)
            try:
                _fsync_directory(parent)
            except OSError as exc:
                raise CanonicalCommitDurabilityError(
                    f"Committed canonical {season_directory.name} (new state installed) "
                    f"but could not finalize its durability: {exc}"
                ) from exc
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
        # Read all three files under one lock acquisition so a concurrent writer
        # can never produce a torn snapshot (for example an old schedule with new
        # decisions). The unlocked helpers must only be called while holding it.
        with _season_read_lock(season, root=self.root):
            schedule = _load_schedule_unlocked(season, root=self.root)
            decisions = _load_decisions_unlocked(season, root=self.root)
            export_context = _load_export_context_unlocked(season, root=self.root)
        return CanonicalSeasonSnapshot(
            season=season,
            schedule=schedule,
            decisions=decisions,
            export_context=export_context,
        )

    def write(
        self,
        snapshot: CanonicalSeasonSnapshot,
        *,
        require_absent: bool = False,
        extra_evidence: Mapping[str, bytes] | None = None,
    ) -> None:
        _write_season_state_atomic(
            self.directory(snapshot.season),
            snapshot.schedule,
            snapshot.decisions,
            require_absent=require_absent,
            export_context=snapshot.export_context,
            extra_evidence=extra_evidence,
        )


__all__ = [
    "CanonicalCommitDurabilityError",
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
