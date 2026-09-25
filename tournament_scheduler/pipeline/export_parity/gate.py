"""Publication preflight gate for export artifact parity + freshness.

The gate is deliberately narrow: it applies to a real season-plan artifact pair
(``season_plan.xlsx`` and ``season_plan.html``) and, when a canonical season
export is identified from its lifecycle manifest, it fails closed even if one
half of the pair has been deleted. A legacy or routine non-season bundle (no
canonical season/projection provenance) has nothing to compare and is left
untouched by the caller. When it does apply, a ``FAIL`` or ``NOT_CHECKABLE``
result blocks publication and names the field/id in its diagnosis.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .records import STATUS_FAIL, STATUS_NOT_CHECKABLE, STATUS_PASS
from .verify import DEFAULT_BASENAME, verify_export_parity


@dataclass(frozen=True)
class CanonicalFreshness:
    """Result of resolving the current canonical revision for a season.

    ``determined`` is ``False`` when the revision could not be read (unknown
    season, absent/unreadable canonical state). A canonical publication treats
    that as ``NOT_CHECKABLE`` instead of silently skipping freshness.
    """

    revision: str = ""
    requires_fresh_export: bool = False
    determined: bool = False


def resolve_canonical_freshness(
    repo_dir: str | Path = ".",
    season: str = "",
    *,
    season_root: str | Path | None = None,
) -> CanonicalFreshness:
    """Return the current canonical revision and freshness requirement.

    A read-only gate must never raise. An unreadable/absent canonical state is
    reported as ``determined=False`` so the verifier fails closed instead of
    pretending freshness was proven.
    """

    if not season:
        return CanonicalFreshness()
    root = Path(season_root) if season_root else Path(repo_dir) / "season"
    try:
        from ...canonical_state import canonical_state_revision
        from ...infrastructure.canonical_season_store import CanonicalSeasonStore

        snapshot = CanonicalSeasonStore(root).load(season)
        revision = canonical_state_revision(snapshot.schedule, snapshot.decisions)
        export_state = snapshot.decisions.get("export_state")
        requires_fresh = bool((export_state or {}).get("requires_fresh_export"))
        return CanonicalFreshness(
            revision=str(revision or ""),
            requires_fresh_export=requires_fresh,
            determined=bool(revision),
        )
    except Exception:  # noqa: BLE001 - a read-only gate must never raise
        return CanonicalFreshness(determined=False)


def _manifest_for(export_dir: Path) -> dict[str, Any]:
    try:
        from ..export_lifecycle import read_export_manifest

        return read_export_manifest(export_dir) or {}
    except Exception:  # noqa: BLE001
        return {}


def is_canonical_season_manifest(manifest: dict[str, Any]) -> bool:
    """True when a lifecycle manifest identifies a canonical season export.

    A non-season/routine bundle is one with no manifest (or a manifest with no
    canonical season/projection provenance); only those may be skipped when a
    half of the pair is absent.
    """

    if not isinstance(manifest, dict):
        return False
    if str(manifest.get("canonical_season") or ""):
        return True
    return manifest.get("schedule_projection") is not None


def is_canonical_export_checkpoint(checkpoint: dict[str, Any] | None) -> bool:
    """True when a Stage 4 export checkpoint independently records canonical intent.

    The on-disk lifecycle manifest can be deleted, truncated or corrupted
    without touching the durable Stage 4 checkpoint. Reading canonical intent
    from the checkpoint is what lets publication refuse a damaged canonical
    export instead of silently treating it as a routine non-season bundle.
    """

    if not isinstance(checkpoint, dict):
        return False
    if str(checkpoint.get("canonical_season") or ""):
        return True
    lifecycle = checkpoint.get("export_lifecycle")
    return is_canonical_season_manifest(lifecycle if isinstance(lifecycle, dict) else {})


def publish_parity_gate(
    *,
    export_dir: str | Path,
    repo_dir: str | Path = ".",
    basename: str = DEFAULT_BASENAME,
    manifest: dict[str, Any] | None = None,
) -> "Any | None":
    """Return a blocking ``CapabilityResult``, or ``None`` when not applicable.

    ``manifest`` overrides the export directory's own lifecycle manifest. It is
    used when re-verifying a sanitized public bundle, which does not itself
    carry the source manifest but must still be checked against the same
    canonical revision and frozen projection.
    """

    root = Path(export_dir)
    resolved_manifest = manifest if manifest is not None else _manifest_for(root)
    is_canonical = is_canonical_season_manifest(resolved_manifest)

    has_primary = (root / f"{basename}.xlsx").exists()
    has_secondary = (root / f"{basename}.html").exists()
    if not (has_primary and has_secondary) and not is_canonical:
        # An explicitly identified non-season/routine bundle with no pair has
        # nothing to verify. An identified canonical season export does not get
        # this escape hatch: a removed artifact is verified as missing.
        return None

    from ..capability_result import CapabilityResult

    season = str(resolved_manifest.get("canonical_season") or "")
    if is_canonical:
        freshness = resolve_canonical_freshness(repo_dir, season)
    else:
        freshness = CanonicalFreshness()

    report = verify_export_parity(
        root,
        basename=basename,
        required_canonical_revision=freshness.revision,
        requires_fresh_export=freshness.requires_fresh_export,
        manifest=resolved_manifest,
        canonical_publication=is_canonical,
        canonical_lookup_failed=is_canonical and not freshness.determined,
    )
    if report["status"] == STATUS_PASS:
        return None

    reasons = report.get("reasons") or []
    detail = "; ".join(str(reason.get("message") or reason) for reason in reasons) or "artifact parity not verifiable"
    mismatches = (report.get("comparison") or {}).get("mismatches") or []
    mismatch_evidence = [
        f"export_parity_mismatch={mismatch.get('tournament_id')}:{mismatch.get('field')}"
        for mismatch in mismatches[:20]
    ]
    projection_mismatches = report.get("projection_mismatches") or []
    projection_evidence = [
        f"export_parity_projection_mismatch={mismatch.get('tournament_id')}:{mismatch.get('field')}"
        for mismatch in projection_mismatches[:20]
    ]
    return CapabilityResult.failed(
        f"Publisering blokkert: XLSX/HTML-artefaktparitet er {report['status']}. {detail}",
        capability="pages_publish",
        evidence=[
            f"export_parity_status={report['status']}",
            f"xlsx_sha256={report['primary'].get('sha256')}",
            f"html_sha256={report['secondary'].get('sha256')}",
            f"canonical_revision={report.get('canonical_revision')}",
            *mismatch_evidence,
            *projection_evidence,
            "export_parity=" + json.dumps(report, ensure_ascii=False),
        ],
    )


def parity_status_of(report: dict[str, Any]) -> str:
    return str(report.get("status") or STATUS_NOT_CHECKABLE)


__all__ = [
    "CanonicalFreshness",
    "is_canonical_export_checkpoint",
    "is_canonical_season_manifest",
    "publish_parity_gate",
    "resolve_canonical_freshness",
    "parity_status_of",
    "STATUS_FAIL",
]
