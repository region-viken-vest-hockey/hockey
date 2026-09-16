"""Export lifecycle manifests and retention helpers.

Stage 4 creates draft timestamped export directories. The publication boundary
promotes the exact source export to published/protected so ordinary draft
retention never removes an operational snapshot.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPORT_LIFECYCLE_FILENAME = "export_manifest.json"
EXPORT_LIFECYCLE_SCHEMA_VERSION = 1
DRAFT_STATUS = "draft"
PUBLISHED_STATUS = "published"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def manifest_path(export_dir: str | Path) -> Path:
    return Path(export_dir) / EXPORT_LIFECYCLE_FILENAME


def read_export_manifest(export_dir: str | Path) -> dict[str, Any] | None:
    path = manifest_path(export_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_draft_manifest(
    export_dir: str | Path,
    *,
    export_id: str,
    generated_at: str,
    export_fingerprint: str | None,
    source_run_id: str | None,
) -> dict[str, Any]:
    """Write the initial lifecycle marker for a generated export."""
    path = manifest_path(export_dir)
    previous = read_export_manifest(export_dir) or {}
    payload: dict[str, Any] = {
        "schema_version": EXPORT_LIFECYCLE_SCHEMA_VERSION,
        "export_id": export_id,
        "generated_at": generated_at,
        "export_fingerprint": export_fingerprint,
        "lifecycle_status": previous.get("lifecycle_status") if previous.get("lifecycle_status") == PUBLISHED_STATUS else DRAFT_STATUS,
        "published_at": previous.get("published_at"),
        "pages_commit": previous.get("pages_commit"),
        "pages_branch": previous.get("pages_branch"),
        "pages_run_id": previous.get("pages_run_id"),
        "pages_bundle_fingerprint": previous.get("pages_bundle_fingerprint"),
        "source_run_id": source_run_id,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def promote_export_manifest(
    export_dir: str | Path,
    *,
    expected_export_fingerprint: str | None,
    source_run_id: str | None,
    pages_run_id: str,
    pages_bundle_fingerprint: str | None,
    pages_commit: str | None,
    pages_branch: str | None,
) -> dict[str, Any]:
    """Mark an export as published, refusing fingerprint/run mismatches."""
    current = read_export_manifest(export_dir)
    if current is None:
        raise ValueError(f"Export lifecycle manifest missing in {export_dir}")
    actual_fp = current.get("export_fingerprint")
    if expected_export_fingerprint and actual_fp != expected_export_fingerprint:
        raise ValueError(
            "Export fingerprint mismatch: "
            f"manifest has {actual_fp!r}, expected {expected_export_fingerprint!r}"
        )
    actual_run_id = current.get("source_run_id")
    if source_run_id and actual_run_id and actual_run_id != source_run_id:
        raise ValueError(
            "Source run id mismatch: "
            f"manifest has {actual_run_id!r}, expected {source_run_id!r}"
        )
    current.update(
        {
            "lifecycle_status": PUBLISHED_STATUS,
            "published_at": _now_iso(),
            "pages_commit": pages_commit,
            "pages_branch": pages_branch,
            "pages_run_id": pages_run_id,
            "pages_bundle_fingerprint": pages_bundle_fingerprint,
            "source_run_id": source_run_id or actual_run_id,
        }
    )
    manifest_path(export_dir).write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    return current


def _is_protected_export(path: Path) -> bool:
    manifest = read_export_manifest(path)
    if manifest is None:
        # Legacy/unclassified exports are treated conservatively: never delete
        # them in the normal draft retention window.
        return True
    return manifest.get("lifecycle_status") == PUBLISHED_STATUS


def prune_draft_exports(export_dirs: list[Path], *, keep: int) -> list[str]:
    """Delete old draft exports while preserving published and legacy dirs."""
    if keep <= 0:
        return []
    draft_runs = [path for path in sorted(export_dirs, key=lambda p: p.name) if not _is_protected_export(path)]
    removed: list[str] = []
    for old_run in draft_runs[:-keep]:
        shutil.rmtree(old_run, ignore_errors=True)
        removed.append(old_run.name)
    return removed


def find_export_manifests(export_root: str | Path) -> list[dict[str, Any]]:
    """Return lifecycle manifests under an export root, newest first."""
    root = Path(export_root)
    if not root.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob(EXPORT_LIFECYCLE_FILENAME)):
        data = read_export_manifest(path.parent)
        if data is None:
            continue
        record = dict(data)
        record["export_dir"] = str(path.parent)
        records.append(record)
    records.sort(key=lambda item: str(item.get("generated_at") or item.get("export_id") or ""), reverse=True)
    return records
