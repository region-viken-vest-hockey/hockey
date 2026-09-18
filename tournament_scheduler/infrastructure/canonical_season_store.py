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
behind. There is intentionally no locking/CAS machinery: RVV does not run
concurrent canonical-season writers.
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


class SeasonStateError(RuntimeError):
    """Raised when canonical season state cannot be read or written safely."""


def season_dir(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> Path:
    return Path(root) / season


def schedule_path(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> Path:
    return season_dir(season, root=root) / "schedule.json"


def decisions_path(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> Path:
    return season_dir(season, root=root) / "decisions.json"


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
    payload = load_json(schedule_path(season, root=root))
    version = int(payload.get("schema_version", 0) or 0)
    if version != SEASON_STATE_SCHEMA_VERSION:
        raise SeasonStateError(f"Unsupported schedule schema_version: {version!r}")
    if not isinstance(payload.get("plan"), dict):
        raise SeasonStateError("Canonical schedule is missing its plan payload")
    return payload


def load_decisions(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> dict[str, Any]:
    payload = load_json(decisions_path(season, root=root))
    version = int(payload.get("schema_version", 0) or 0)
    if version != DECISIONS_SCHEMA_VERSION:
        raise SeasonStateError(f"Unsupported decisions schema_version: {version!r}")
    if not isinstance(payload.get("decisions"), dict):
        raise SeasonStateError("Canonical decisions file is missing its decisions object")
    return payload


def _write_season_state_atomic(
    season_directory: Path,
    schedule_payload: dict[str, Any],
    decisions_payload: dict[str, Any],
    *,
    require_absent: bool,
) -> None:
    """Install both canonical season-state files as one atomic boundary.

    The directory is staged and swapped, so a failure never leaves only
    ``schedule.json`` or only ``decisions.json`` behind. ``require_absent``
    refuses to replace existing canonical state (used by deliberate
    promotion); mutation callers replace it and rely on the swap for rollback.
    """

    parent = season_directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{season_directory.name}.", suffix=".tmp", dir=parent))
    backup = parent / f".{season_directory.name}.backup"
    try:
        (staging / "schedule.json").write_bytes(_json_bytes(schedule_payload))
        (staging / "decisions.json").write_bytes(_json_bytes(decisions_payload))
        for staged_file in (staging / "schedule.json", staging / "decisions.json"):
            with staged_file.open("rb") as handle:
                os.fsync(handle.fileno())
        if season_directory.exists():
            if require_absent:
                raise SeasonStateError(
                    f"Canonical season state already exists for {season_directory.name}; "
                    "use --force only for deliberate replacement"
                )
            if backup.exists():
                shutil.rmtree(backup)
            os.replace(season_directory, backup)
            try:
                os.replace(staging, season_directory)
            except Exception:
                os.replace(backup, season_directory)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        else:
            os.replace(staging, season_directory)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


@dataclass(frozen=True)
class CanonicalSeasonSnapshot:
    """The complete durable canonical state for one season.

    ``schedule`` holds the promoted schedule facts and provenance;
    ``decisions`` holds the durable operator/approval/acceptance state. They are
    read and written together so callers never observe a half-installed season.
    """

    season: str
    schedule: dict[str, Any]
    decisions: dict[str, Any]

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

    def load(self, season: str) -> CanonicalSeasonSnapshot:
        return CanonicalSeasonSnapshot(
            season=season,
            schedule=load_schedule(season, root=self.root),
            decisions=load_decisions(season, root=self.root),
        )

    def write(self, snapshot: CanonicalSeasonSnapshot, *, require_absent: bool = False) -> None:
        _write_season_state_atomic(
            self.directory(snapshot.season),
            snapshot.schedule,
            snapshot.decisions,
            require_absent=require_absent,
        )


__all__ = [
    "CanonicalSeasonSnapshot",
    "CanonicalSeasonStore",
    "DECISIONS_SCHEMA_VERSION",
    "DEFAULT_SEASON_ROOT",
    "SEASON_STATE_SCHEMA_VERSION",
    "SeasonStateError",
    "decisions_path",
    "load_decisions",
    "load_json",
    "load_schedule",
    "schedule_path",
    "season_dir",
    "season_id_from_plan",
]
