"""Export lifecycle manifests and retention helpers.

Stage 4 creates draft timestamped export directories. The publication boundary
promotes the exact source export to published/protected so ordinary draft
retention never removes an operational snapshot.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Collection, Mapping

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


def export_content_sha256(export_dir: str | Path) -> str:
    """Hash every artifact in an export directory (issue #682).

    The manifest is excluded because it is where this value is recorded. The hash
    covers relative paths and file bytes, so any artifact edited after review
    changes it, even when the manifest is untouched.
    """
    root = Path(export_dir)
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative == EXPORT_LIFECYCLE_FILENAME:
            continue
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


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
    schedule_projection: Mapping[str, Any] | None = None,
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
        "export_content_sha256": export_content_sha256(export_dir),
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
        "schedule_projection": (
            dict(schedule_projection)
            if schedule_projection is not None
            else previous.get("schedule_projection")
        ),
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


def canonical_export_references(decisions: Mapping[str, Any]) -> set[str]:
    """Return the export directory names canonical decisions point at.

    Two canonical records name an export: the current freshness latch
    (``export_state.export_dir``) and every ``mark_export_fresh`` history entry.
    History is append-only, so an export that was ever the fresh export for a
    canonical revision stays referenced even after a newer export replaces it.
    """
    references: set[str] = set()
    export_state = decisions.get("export_state")
    if isinstance(export_state, Mapping) and export_state.get("export_dir"):
        references.add(Path(str(export_state["export_dir"])).name)
    for entry in decisions.get("history") or []:
        if not isinstance(entry, Mapping):
            continue
        details = entry.get("details")
        if isinstance(details, Mapping) and details.get("export_dir"):
            references.add(Path(str(details["export_dir"])).name)
    references.discard("")
    return references


def prune_draft_exports(
    export_dirs: list[Path],
    *,
    keep: int,
    retained_names: Collection[str] = (),
) -> list[str]:
    """Delete old draft exports while preserving published, legacy and retained dirs.

    *retained_names* are export directory names the repository still relies on
    (canonical references, git-tracked evidence). They are never draft
    candidates, so they neither get deleted nor count toward *keep*.
    """
    if keep <= 0:
        return []
    retained = set(retained_names)
    draft_runs = [
        path
        for path in sorted(export_dirs, key=lambda p: p.name)
        if path.name not in retained and not _is_protected_export(path)
    ]
    removed: list[str] = []
    for old_run in draft_runs[:-keep]:
        shutil.rmtree(old_run, ignore_errors=True)
        removed.append(old_run.name)
    return removed


def publication_history_root(season_root: str | Path = "season") -> Path:
    """Return the repository-owned export history root for canonical seasons."""

    root = Path(season_root)
    # In normal operation ``season/`` and ``export/`` are siblings. A caller may
    # point --root at another season-state directory in tests or recovery;
    # publication history remains anchored next to that canonical root, not in
    # the newly requested --export-dir.
    return root.parent / "export"


def find_published_exports_for_season(
    season: str,
    *,
    season_root: str | Path = "season",
) -> list[dict[str, Any]]:
    """Return published lifecycle records for *season*, newest first.

    The lookup deliberately ignores the current output directory: once a season
    has been published, canonical export protection follows the season
    lifecycle/publication history.
    """

    return [
        record
        for record in find_export_manifests(publication_history_root(season_root))
        if record.get("lifecycle_status") == PUBLISHED_STATUS
        and record.get("canonical_season") == season
    ]


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
