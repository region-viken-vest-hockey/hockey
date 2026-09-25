"""Read-only size/shape inventory of canonical state and generated artifacts.

This is a diagnostic, not a mutation or publication boundary. It reports
bounded size and shape facts for ``season/``, ``export/`` and ``.pipeline/`` so
an operator (or an agent) can see what is growing without injecting multi-MB
canonical JSON into a prompt. Cleanup eligibility is reported through the
existing conservative :mod:`tournament_scheduler.pipeline.export_cleanup`
manifest contract (proven-superseded exports only), never by age or directory
ordering.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from tournament_scheduler.infrastructure.canonical_season_store import (
    DEFAULT_SEASON_ROOT,
    SeasonStateError,
    decisions_path,
    load_decisions,
    schedule_path,
    season_dir,
)


def _file_bytes(path: Path) -> int | None:
    try:
        return path.stat().st_size if path.is_file() else None
    except OSError:
        return None


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for child in path.rglob("*"):
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
    except OSError:
        pass
    return total


def _largest_files(path: Path, limit: int = 10) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    try:
        for child in path.rglob("*"):
            if child.is_file() and not child.is_symlink():
                files.append(
                    {"path": str(child.relative_to(path)), "bytes": child.stat().st_size}
                )
    except OSError:
        return files
    files.sort(key=lambda item: item["bytes"], reverse=True)
    return files[:limit]


def _season_inventory(season: str, *, root: str | os.PathLike[str]) -> dict[str, Any]:
    directory = season_dir(season, root=root)
    files: dict[str, Any] = {}
    for name, path in (
        ("schedule.json", schedule_path(season, root=root)),
        ("decisions.json", decisions_path(season, root=root)),
        ("export_context.json", Path(root) / season / "export_context.json"),
    ):
        size = _file_bytes(path)
        files[name] = {"path": str(path), "bytes": size} if size is not None else {"path": str(path), "bytes": None, "missing": True}

    history_events = 0
    history_events_by_type: dict[str, int] = {}
    largest_history_events: list[dict[str, Any]] = []
    largest_detail_keys: dict[str, int] = {}
    largest_detail_examples: list[dict[str, Any]] = []

    try:
        decisions = load_decisions(season, root=root)
        history = decisions.get("history") or []
        history_events = len(history)
        for entry in history:
            if not isinstance(entry, Mapping):
                continue
            kind = str(entry.get("event") or "unknown")
            history_events_by_type[kind] = history_events_by_type.get(kind, 0) + 1
            size = len(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            largest_history_events.append(
                {
                    "event": kind,
                    "tournament_id": str(entry.get("tournament_id") or ""),
                    "at": str(entry.get("at") or ""),
                    "bytes": size,
                }
            )
            details = entry.get("details") if isinstance(entry.get("details"), Mapping) else {}
            for key, value in details.items():
                key_size = len(json.dumps(value, ensure_ascii=False, sort_keys=True))
                largest_detail_keys[key] = largest_detail_keys.get(key, 0) + key_size
    except SeasonStateError:
        pass
    largest_history_events.sort(key=lambda item: item["bytes"], reverse=True)
    largest_history_events = largest_history_events[:5]
    ranked_keys = sorted(largest_detail_keys.items(), key=lambda item: item[1], reverse=True)[:10]
    largest_detail_examples = [{"key": key, "cumulative_bytes": size} for key, size in ranked_keys]

    evidence_directory = Path(root) / season / "evidence"
    evidence = {
        "path": str(evidence_directory),
        "bytes": _dir_size(evidence_directory) if evidence_directory.exists() else None,
    }

    from tournament_scheduler.infrastructure.canonical_compaction_backup import (
        list_compaction_backups,
    )

    backups = list_compaction_backups(season, root=root)

    return {
        "season": season,
        "directory": str(directory),
        "files": files,
        "total_bytes": sum((f.get("bytes") or 0) for f in files.values()),
        "history": {
            "events": history_events,
            "events_by_type": history_events_by_type,
            "largest_events": largest_history_events,
            "largest_detail_keys_cumulative_bytes": largest_detail_examples,
        },
        "evidence_archive": evidence,
        "compaction_backups": backups,
    }


def _export_inventory(export_root: str | os.PathLike[str]) -> dict[str, Any]:
    from tournament_scheduler.pipeline.export_cleanup import plan_superseded_export_cleanup

    root = Path(export_root)
    directories: list[dict[str, Any]] = []
    total = 0
    if root.exists() and root.is_dir():
        for child in sorted(root.iterdir(), key=lambda p: p.name):
            if child.is_dir() and not child.is_symlink():
                size = _dir_size(child)
                total += size
                directories.append({"export_id": child.name, "bytes": size})
    directories.sort(key=lambda item: item["bytes"], reverse=True)
    try:
        cleanup = plan_superseded_export_cleanup(export_root)
        cleanup_eligible = cleanup.get("eligible_count", 0)
        cleanup_blocked = cleanup.get("blocked_count", 0)
    except Exception:  # noqa: BLE001 - inventory must degrade, never fail the diagnostic
        cleanup_eligible = None
        cleanup_blocked = None
    return {
        "root": str(root),
        "directory_count": len(directories),
        "total_bytes": total,
        "largest_directories": directories[:10],
        "cleanup_eligible_superseded": cleanup_eligible,
        "cleanup_blocked": cleanup_blocked,
    }


def _pipeline_inventory(pipeline_root: str | os.PathLike[str]) -> dict[str, Any]:
    root = Path(pipeline_root)
    if not root.exists():
        return {"root": str(root), "exists": False, "total_bytes": 0, "largest_files": []}
    return {
        "root": str(root),
        "exists": True,
        "total_bytes": _dir_size(root),
        "largest_files": _largest_files(root),
    }


def history_inventory(
    season: str,
    *,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    export_root: str | os.PathLike[str] = "export",
    pipeline_root: str | os.PathLike[str] = ".pipeline",
) -> dict[str, Any]:
    """Return a bounded size/shape inventory for one season and its artifacts."""

    return {
        "season": _season_inventory(season, root=root),
        "export": _export_inventory(export_root),
        "pipeline": _pipeline_inventory(pipeline_root),
    }


__all__ = [
    "history_inventory",
]
