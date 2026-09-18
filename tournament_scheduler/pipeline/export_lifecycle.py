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
from typing import Any, Mapping

EXPORT_LIFECYCLE_FILENAME = "export_manifest.json"
EXPORT_LIFECYCLE_SCHEMA_VERSION = 1
DRAFT_STATUS = "draft"
PUBLISHED_STATUS = "published"
# A previously generated export that a later refinement export replaced. It is
# kept as immutable history (and protected from draft retention) and points at
# the export that superseded it.
SUPERSEDED_STATUS = "superseded"


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
    canonical_season: str | None = None,
    canonical_revision: str | None = None,
    supersedes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the initial lifecycle marker for a generated export.

    *supersedes* records the reviewed export this generation refines (its
    ``export_id``/``export_fingerprint``/``export_dir``) so the new export's
    provenance back to the prior reviewed handoff is explicit instead of
    inferred from directory ordering.
    """
    path = manifest_path(export_dir)
    previous = read_export_manifest(export_dir) or {}
    payload: dict[str, Any] = {
        "schema_version": EXPORT_LIFECYCLE_SCHEMA_VERSION,
        "export_id": export_id,
        "generated_at": generated_at,
        "export_fingerprint": export_fingerprint,
        "canonical_season": canonical_season,
        "canonical_revision": canonical_revision,
        "lifecycle_status": previous.get("lifecycle_status") if previous.get("lifecycle_status") == PUBLISHED_STATUS else DRAFT_STATUS,
        "published_at": previous.get("published_at"),
        "pages_commit": previous.get("pages_commit"),
        "pages_branch": previous.get("pages_branch"),
        "pages_run_id": previous.get("pages_run_id"),
        "pages_bundle_fingerprint": previous.get("pages_bundle_fingerprint"),
        "source_run_id": source_run_id,
        "supersedes": dict(supersedes) if supersedes else None,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def mark_export_superseded(
    export_dir: str | Path,
    *,
    superseded_by: Mapping[str, Any],
    at: str | None = None,
) -> dict[str, Any]:
    """Mark an existing export as superseded without touching its artifacts.

    A published export is the live public projection, so refinement refuses to
    silently replace it; that transition belongs to the export/publication
    boundary, not to candidate refinement. The superseded export is kept on
    disk (its manifest is protected from draft retention) and records which
    export replaced it.
    """
    current = read_export_manifest(export_dir)
    if current is None:
        raise ValueError(f"Export lifecycle manifest missing in {export_dir}")
    if current.get("lifecycle_status") == PUBLISHED_STATUS:
        raise ValueError(
            f"Refusing to supersede published export {current.get('export_id')!r}; "
            "use the publication/rollback boundary instead"
        )
    current.update(
        {
            "lifecycle_status": SUPERSEDED_STATUS,
            "superseded_at": at or _now_iso(),
            "superseded_by": dict(superseded_by),
        }
    )
    manifest_path(export_dir).write_text(
        json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return current


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
    return manifest.get("lifecycle_status") in (PUBLISHED_STATUS, SUPERSEDED_STATUS)


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
