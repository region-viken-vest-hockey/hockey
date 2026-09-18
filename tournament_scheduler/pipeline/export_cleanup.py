"""Conservative cleanup for explicitly superseded Stage 4 exports.

Normal draft retention intentionally protects published, superseded, and legacy
exports. This module provides a separate operator cleanup boundary for
superseded exports only. It never infers deletability from age or directory
ordering: a superseded export is eligible only when its lifecycle manifest
points to an existing replacement with the exact recorded id and fingerprint.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from .export_lifecycle import (
    DRAFT_STATUS,
    PUBLISHED_STATUS,
    SUPERSEDED_STATUS,
    read_export_manifest,
)

_TIMESTAMP_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{4}$")
_ALLOWED_TARGET_STATUSES = {DRAFT_STATUS, SUPERSEDED_STATUS, PUBLISHED_STATUS}


class ExportCleanupSafetyError(RuntimeError):
    """Raised when a superseded export cannot be proven safe to delete."""


def _publication_metadata_present(manifest: dict[str, Any]) -> bool:
    return any(
        manifest.get(key)
        for key in (
            "published_at",
            "pages_commit",
            "pages_branch",
            "pages_run_id",
            "pages_bundle_fingerprint",
        )
    )


def _record(path: Path, reason: str) -> dict[str, Any]:
    return {"export_dir": str(path), "export_id": path.name, "reason": reason}


def plan_superseded_export_cleanup(export_root: str | Path = "export") -> dict[str, Any]:
    """Return a deterministic cleanup plan without mutating the filesystem."""
    root = Path(export_root)
    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []

    if not root.exists():
        return {
            "export_root": str(root),
            "eligible": [],
            "blocked": [],
            "protected": [],
            "eligible_count": 0,
            "blocked_count": 0,
        }
    if not root.is_dir() or root.is_symlink():
        raise ExportCleanupSafetyError(f"Export root is not a safe directory: {root}")

    for path in sorted(root.iterdir(), key=lambda p: p.name):
        if not path.is_dir() or path.is_symlink() or not _TIMESTAMP_DIR_RE.match(path.name):
            continue

        manifest = read_export_manifest(path)
        if manifest is None:
            protected.append(_record(path, "missing_or_invalid_manifest"))
            continue

        status = str(manifest.get("lifecycle_status") or "")
        if status != SUPERSEDED_STATUS:
            protected.append(_record(path, f"status_{status or 'unknown'}"))
            continue

        if str(manifest.get("export_id") or "") != path.name:
            blocked.append(_record(path, "source_export_id_mismatch"))
            continue
        if not manifest.get("export_fingerprint"):
            blocked.append(_record(path, "source_fingerprint_missing"))
            continue
        if _publication_metadata_present(manifest):
            blocked.append(_record(path, "superseded_export_has_publication_metadata"))
            continue

        replacement = manifest.get("superseded_by")
        if not isinstance(replacement, dict):
            blocked.append(_record(path, "superseded_by_missing"))
            continue

        target_id = str(replacement.get("export_id") or "")
        target_fp = str(replacement.get("export_fingerprint") or "")
        target_ref = str(replacement.get("export_dir") or "")
        if (
            not target_id
            or not _TIMESTAMP_DIR_RE.match(target_id)
            or Path(target_id).name != target_id
            or not target_fp
        ):
            blocked.append(_record(path, "superseded_by_identity_invalid"))
            continue
        if target_ref and Path(target_ref).name != target_id:
            blocked.append(_record(path, "superseded_by_path_mismatch"))
            continue

        target = root / target_id
        if not target.is_dir() or target.is_symlink():
            blocked.append(_record(path, "replacement_export_missing"))
            continue
        target_manifest = read_export_manifest(target)
        if target_manifest is None:
            blocked.append(_record(path, "replacement_manifest_missing_or_invalid"))
            continue
        if str(target_manifest.get("export_id") or "") != target_id:
            blocked.append(_record(path, "replacement_export_id_mismatch"))
            continue
        if str(target_manifest.get("export_fingerprint") or "") != target_fp:
            blocked.append(_record(path, "replacement_fingerprint_mismatch"))
            continue
        if str(target_manifest.get("lifecycle_status") or "") not in _ALLOWED_TARGET_STATUSES:
            blocked.append(_record(path, "replacement_status_invalid"))
            continue

        eligible.append(
            {
                "export_dir": str(path),
                "export_id": path.name,
                "export_fingerprint": manifest["export_fingerprint"],
                "superseded_by": {
                    "export_dir": str(target),
                    "export_id": target_id,
                    "export_fingerprint": target_fp,
                    "lifecycle_status": target_manifest.get("lifecycle_status"),
                },
            }
        )

    return {
        "export_root": str(root),
        "eligible": eligible,
        "blocked": blocked,
        "protected": protected,
        "eligible_count": len(eligible),
        "blocked_count": len(blocked),
    }


def cleanup_superseded_exports(
    export_root: str | Path = "export",
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Plan or execute safe superseded-export cleanup.

    Apply refuses to delete anything if any manifest claiming superseded is
    ambiguous. Published, draft, and legacy/unclassified exports are protected.
    """
    plan = plan_superseded_export_cleanup(export_root)
    if not apply:
        return {**plan, "applied": False, "removed": []}

    if plan["blocked"]:
        reasons = ", ".join(
            f"{item['export_id']}:{item['reason']}" for item in plan["blocked"]
        )
        raise ExportCleanupSafetyError(
            "Refusing cleanup because one or more superseded exports are ambiguous: "
            + reasons
        )

    removed: list[str] = []
    for item in plan["eligible"]:
        path = Path(item["export_dir"])
        current = read_export_manifest(path)
        if current is None or current.get("lifecycle_status") != SUPERSEDED_STATUS:
            raise ExportCleanupSafetyError(
                f"Refusing cleanup because lifecycle changed for {path}"
            )
        shutil.rmtree(path)
        removed.append(item["export_id"])

    return {**plan, "applied": True, "removed": removed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Safely remove Stage 4 exports whose lifecycle manifests explicitly "
            "prove that they were superseded by another existing export."
        )
    )
    parser.add_argument("--export-root", default="export")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete eligible superseded exports. Default is dry-run.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = cleanup_superseded_exports(args.export_root, apply=args.apply)
    except ExportCleanupSafetyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"Export cleanup: {mode}")
    print(f"Eligible superseded exports: {result['eligible_count']}")
    for item in result["eligible"]:
        target = item["superseded_by"]
        print(
            f"  DELETE {item['export_id']} -> "
            f"{target['export_id']} ({target['lifecycle_status']})"
        )
    if result["blocked"]:
        print("Blocked superseded exports:")
        for item in result["blocked"]:
            print(f"  KEEP   {item['export_id']}: {item['reason']}")
    if args.apply:
        print(f"Removed: {len(result['removed'])}")
    else:
        print("No files changed. Re-run with --apply after reviewing this plan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
