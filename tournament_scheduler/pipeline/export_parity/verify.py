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

from .comparator import compare_html_spond, compare_projections, records_against_projection
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
from .spond_reader import read_spond
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
    canonical_publication: bool = False,
    canonical_lookup_failed: bool = False,
    require_spond: bool = False,
    require_spond_revision: bool = False,
) -> dict[str, Any]:
    """Verify written season-plan artifacts in *export_dir*.

    The historical XLSX ↔ HTML pair remains the base contract. When
    ``require_spond`` is true (or the Spond workbook exists), the independently
    parsed ``season_plan_spond.xlsx`` projection is also mandatory.

    Artifact paths are reported as names (not absolute paths) and ``checked_at``
    may be injected from an export build timestamp so the persisted report stays
    byte-reproducible.
    """

    root = Path(export_dir)
    primary = read_xlsx(root / f"{basename}.xlsx")
    secondary = read_html(root / f"{basename}.html")
    spond_path = root / "season_plan_spond.xlsx"
    check_spond = require_spond or spond_path.exists()
    spond = read_spond(spond_path) if check_spond else None

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
        "spond": spond.artifact_ref() if spond is not None else None,
        "canonical_revision": required_canonical_revision or manifest_revision or primary.season_revision or secondary.season_revision or (spond.season_revision if spond else ""),
        "manifest_canonical_revision": manifest_revision or None,
        "reasons": [],
        "comparison": {},
        "spond_comparison": {},
        "projection_mismatches": [],
    }

    problems: list[dict[str, Any]] = []
    if not primary.exists and not secondary.exists:
        problems.append(
            {"code": "artifacts_missing", "message": "neither XLSX nor HTML artifact exists in the export directory"}
        )
        report["reasons"] = problems
        return report
    for projection_artifact in (primary, secondary, *((spond,) if spond is not None else ())):
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

    for artifact in (primary, secondary):
        for issue in artifact.issues:
            problems.append({
                "code": str(issue.get("code") or "artifact_issue"),
                "message": str(issue.get("message") or "Invalid artifact companion"),
                "detail": issue,
            })

    comparison = compare_projections(primary, secondary)
    report["comparison"] = comparison
    spond_comparison: dict[str, Any] = {}
    if spond is not None:
        spond_comparison = compare_html_spond(secondary, spond)
        report["spond_comparison"] = spond_comparison
        if spond_comparison["missing_ids"]:
            problems.append({
                "code": "spond_missing_tournaments",
                "message": "Spond is missing active tournament id(s): " + ", ".join(spond_comparison["missing_ids"]),
            })
        if spond_comparison["extra_ids"]:
            problems.append({
                "code": "spond_extra_tournaments",
                "message": "Spond has extra tournament id(s): " + ", ".join(spond_comparison["extra_ids"]),
            })
        if spond_comparison["cancelled_ids_in_spond"]:
            problems.append({
                "code": "cancelled_tournaments_in_spond",
                "message": "Cancelled tournament id(s) must be absent from Spond: " + ", ".join(spond_comparison["cancelled_ids_in_spond"]),
            })
        if spond_comparison["mismatches"]:
            problems.append({
                "code": "spond_field_mismatch",
                "message": f"{len(spond_comparison['mismatches'])} field-level mismatch(es) between HTML and Spond",
            })
        for issue in spond_comparison["row_issues"]:
            problems.append({
                "code": str(issue.get("code") or "spond_row_issue"),
                "message": str(issue.get("message") or "Invalid Spond import row"),
                "detail": issue,
            })
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

    projection_uncheckable: list[dict[str, Any]] = []
    if isinstance(projection, dict) and projection:
        projection_mismatches: list[dict[str, Any]] = []
        for kind, artifact in (("xlsx", primary), ("html", secondary)):
            result = records_against_projection(artifact, projection, kind=kind)
            projection_mismatches.extend(result["mismatches"])
            projection_uncheckable.extend(result["uncheckable"])
        report["projection_mismatches"] = projection_mismatches
        report["projection_uncheckable"] = projection_uncheckable
        if projection_mismatches:
            problems.append(
                {
                    "code": "projection_mismatch",
                    "message": (
                        f"{len(projection_mismatches)} canonical operational fact(s) differ from the "
                        "frozen canonical schedule projection"
                    ),
                }
            )
        for field in sorted({entry["field"] for entry in projection_uncheckable}):
            problems.append(
                {
                    "code": "projection_field_uncheckable",
                    "message": (
                        f"canonical projection field {field!r} is not carried by any artifact, so its "
                        "parity cannot be verified"
                    ),
                }
            )

    freshness_failures, freshness_not_checkable = evaluate_freshness(
        primary_revision=primary.season_revision,
        secondary_revision=secondary.season_revision,
        manifest_revision=manifest_revision,
        required_revision=required_canonical_revision,
        requires_fresh_export=requires_fresh_export,
        canonical_required=canonical_publication,
        lookup_failed=canonical_lookup_failed,
    )
    if spond is not None:
        expected_revision = required_canonical_revision or manifest_revision or primary.season_revision or secondary.season_revision
        if expected_revision and not spond.season_revision and require_spond_revision:
            freshness_not_checkable.append({
                "code": "spond_missing_revision",
                "message": "Spond workbook does not carry canonical revision metadata; re-export before publishing",
            })
        elif expected_revision and spond.season_revision and spond.season_revision != expected_revision:
            freshness_failures.append({
                "code": "stale_spond_revision",
                "message": f"Spond revision {spond.season_revision!r} does not match expected revision {expected_revision!r}",
            })
    problems.extend(freshness_failures)
    problems.extend(freshness_not_checkable)

    report["reasons"] = problems
    hard_failure_codes = {
        "missing_ids", "extra_ids", "duplicate_ids", "field_mismatch", "projection_mismatch",
        "spond_missing_tournaments", "spond_extra_tournaments", "cancelled_tournaments_in_spond",
        "spond_field_mismatch", "duplicate_spond_row", "unmatched_spond_row", "inconsistent_spond_tournament",
        "missing_html_companion", "unexpected_html_companion",
    }
    if freshness_failures or any(
        problem["code"] in hard_failure_codes
        for problem in problems
    ):
        report["status"] = STATUS_FAIL
    elif (
        freshness_not_checkable
        or comparison["uncheckable_fields"]
        or projection_uncheckable
        or unsupported
    ):
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
