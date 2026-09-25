"""Read-only orchestration of one export pair's artifact parity + freshness.

This is the single entry point every caller (Stage 4 preflight, the publish
gate, the read-only CLI) uses. It parses the on-disk bytes, compares them by
stable id, checks the canonical/frozen projection and revision, and returns a
bounded machine-readable report. It never mutates canonical state or the
artifacts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .comparator import compare_projections, records_against_projection
from .freshness import evaluate_freshness
from .html_reader import read_html
from .records import (
    PARITY_FILENAME,
    PARITY_SCHEMA_VERSION,
    STATUS_FAIL,
    STATUS_NOT_CHECKABLE,
    STATUS_PASS,
    ArtifactProjection,
)
from .xlsx_reader import read_xlsx

DEFAULT_BASENAME = "season_plan"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _manifest_for(export_dir: Path) -> dict[str, Any]:
    try:
        from ..export_lifecycle import read_export_manifest
    except Exception:  # noqa: BLE001 - never let an import break a read-only check
        return {}
    return read_export_manifest(export_dir) or {}


def _unreadable(projection: ArtifactProjection) -> dict[str, Any] | None:
    name = Path(projection.path).name
    if not projection.exists:
        return {"code": "artifact_missing", "message": f"{projection.kind} artifact {name} is missing"}
    if projection.read_error:
        return {"code": "artifact_unreadable", "message": f"{projection.kind} ({name}): {projection.read_error}"}
    return None


def verify_export_parity(
    export_dir: str | Path,
    *,
    basename: str = DEFAULT_BASENAME,
    required_canonical_revision: str = "",
    requires_fresh_export: bool = False,
    manifest: dict[str, Any] | None = None,
    expected_projection: dict[str, dict[str, Any]] | None = None,
    checked_at: str | None = None,
) -> dict[str, Any]:
    """Verify ``<basename>.xlsx`` against ``<basename>.html`` in *export_dir*.

    Artifact paths are reported as names (not absolute paths) and ``checked_at``
    may be injected from an export build timestamp so the persisted report stays
    byte-reproducible.
    """

    root = Path(export_dir)
    primary = read_xlsx(root / f"{basename}.xlsx")
    secondary = read_html(root / f"{basename}.html")

    resolved_manifest = manifest if isinstance(manifest, dict) else _manifest_for(root)
    manifest_revision = str(resolved_manifest.get("canonical_revision") or "")
    projection = expected_projection or resolved_manifest.get("schedule_projection")

    report: dict[str, Any] = {
        "schema_version": PARITY_SCHEMA_VERSION,
        "checked_at": checked_at or _now_iso(),
        "basename": basename,
        "status": STATUS_NOT_CHECKABLE,
        "primary": primary.artifact_ref(),
        "secondary": secondary.artifact_ref(),
        "canonical_revision": required_canonical_revision or manifest_revision or primary.season_revision or secondary.season_revision,
        "manifest_canonical_revision": manifest_revision or None,
        "reasons": [],
        "comparison": {},
        "projection_mismatches": [],
    }

    problems: list[dict[str, Any]] = []
    if not primary.exists and not secondary.exists:
        problems.append(
            {"code": "artifacts_missing", "message": "neither XLSX nor HTML artifact exists in the export directory"}
        )
        report["reasons"] = problems
        return report
    for projection_artifact in (primary, secondary):
        problem = _unreadable(projection_artifact)
        if problem is not None:
            problems.append(problem)
    if problems:
        report["reasons"] = problems
        return report

    unsupported = sorted(set(primary.unsupported_fields) | set(secondary.unsupported_fields))
    report["unsupported_fields"] = unsupported
    if "tournament_id" in unsupported:
        report["reasons"] = [
            {
                "code": "missing_stable_identity",
                "message": (
                    "At least one artifact does not carry stable tournament ids, so parity "
                    "cannot be verified (legacy format). Re-export before publishing."
                ),
            }
        ]
        return report

    comparison = compare_projections(primary, secondary)
    report["comparison"] = comparison
    if comparison["missing_ids"]:
        problems.append(
            {"code": "missing_ids", "message": f"HTML is missing tournament id(s): {', '.join(comparison['missing_ids'])}"}
        )
    if comparison["extra_ids"]:
        problems.append(
            {"code": "extra_ids", "message": f"HTML has extra tournament id(s): {', '.join(comparison['extra_ids'])}"}
        )
    for kind, duplicates in comparison["duplicate_ids"].items():
        if duplicates:
            problems.append(
                {"code": "duplicate_ids", "message": f"{kind} contains duplicate id(s): {', '.join(duplicates)}"}
            )
    if comparison["mismatches"]:
        problems.append(
            {
                "code": "field_mismatch",
                "message": f"{len(comparison['mismatches'])} field-level mismatch(es) between XLSX and HTML",
            }
        )
    for field in comparison["uncheckable_fields"]:
        problems.append(
            {"code": "uncheckable_field", "message": f"field {field!r} is not carried by both artifacts"}
        )

    if isinstance(projection, dict) and projection:
        projection_mismatches = []
        for kind, artifact in (("xlsx", primary), ("html", secondary)):
            projection_mismatches.extend(
                records_against_projection(artifact.records_by_id(), projection, kind=kind)
            )
        report["projection_mismatches"] = projection_mismatches
        if projection_mismatches:
            problems.append(
                {
                    "code": "projection_mismatch",
                    "message": (
                        f"{len(projection_mismatches)} placement fact(s) differ from the frozen "
                        "canonical schedule projection"
                    ),
                }
            )

    freshness_failures, freshness_not_checkable = evaluate_freshness(
        primary_revision=primary.season_revision,
        secondary_revision=secondary.season_revision,
        manifest_revision=manifest_revision,
        required_revision=required_canonical_revision,
        requires_fresh_export=requires_fresh_export,
    )
    problems.extend(freshness_failures)
    problems.extend(freshness_not_checkable)

    report["reasons"] = problems
    if freshness_failures or any(
        problem["code"] in {"missing_ids", "extra_ids", "duplicate_ids", "field_mismatch", "projection_mismatch"}
        for problem in problems
    ):
        report["status"] = STATUS_FAIL
    elif freshness_not_checkable or comparison["uncheckable_fields"] or unsupported:
        report["status"] = STATUS_NOT_CHECKABLE
    else:
        report["status"] = STATUS_PASS
    return report


def write_parity_report(export_dir: str | Path, report: dict[str, Any]) -> str:
    """Persist the bounded parity report next to the artifacts."""
    path = Path(export_dir) / PARITY_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)
