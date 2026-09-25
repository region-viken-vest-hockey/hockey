"""Publication preflight gate for export artifact parity + freshness.

The gate is deliberately narrow: it only applies to a real season-plan artifact
pair (both ``season_plan.xlsx`` and ``season_plan.html`` present in the export
directory). A legacy or non-season bundle has nothing to compare and is left
untouched by the caller. When it does apply, a ``FAIL`` or ``NOT_CHECKABLE``
result blocks publication and names the field/id in its diagnosis.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .records import STATUS_FAIL, STATUS_NOT_CHECKABLE, STATUS_PASS
from .verify import DEFAULT_BASENAME, verify_export_parity


def resolve_canonical_freshness(
    repo_dir: str | Path = ".",
    season: str = "",
    *,
    season_root: str | Path | None = None,
) -> tuple[str, bool]:
    """Return ``(current_canonical_revision, requires_fresh_export)``.

    Fails closed to ``("", False)`` when canonical state cannot be read; the
    verifier then reports a missing revision as ``NOT_CHECKABLE`` rather than
    pretending freshness was proven.
    """

    if not season:
        return "", False
    root = Path(season_root) if season_root else Path(repo_dir) / "season"
    try:
        from ...canonical_state import canonical_state_revision
        from ...infrastructure.canonical_season_store import CanonicalSeasonStore

        snapshot = CanonicalSeasonStore(root).load(season)
        revision = canonical_state_revision(snapshot.schedule, snapshot.decisions)
        export_state = snapshot.decisions.get("export_state")
        requires_fresh = bool((export_state or {}).get("requires_fresh_export"))
        return str(revision or ""), requires_fresh
    except Exception:  # noqa: BLE001 - a read-only gate must never raise
        return "", False


def _manifest_for(export_dir: Path) -> dict[str, Any]:
    try:
        from ..export_lifecycle import read_export_manifest

        return read_export_manifest(export_dir) or {}
    except Exception:  # noqa: BLE001
        return {}


def publish_parity_gate(
    *,
    export_dir: str | Path,
    repo_dir: str | Path = ".",
    basename: str = DEFAULT_BASENAME,
) -> "Any | None":
    """Return a blocking ``CapabilityResult``, or ``None`` when not applicable."""

    root = Path(export_dir)
    if not (root / f"{basename}.xlsx").exists() or not (root / f"{basename}.html").exists():
        return None

    from ..capability_result import CapabilityResult

    manifest = _manifest_for(root)
    season = str(manifest.get("canonical_season") or "")
    required_revision, requires_fresh = resolve_canonical_freshness(repo_dir, season)
    report = verify_export_parity(
        root,
        basename=basename,
        required_canonical_revision=required_revision,
        requires_fresh_export=requires_fresh,
        manifest=manifest,
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
    return CapabilityResult.failed(
        f"Publisering blokkert: XLSX/HTML-artefaktparitet er {report['status']}. {detail}",
        capability="pages_publish",
        evidence=[
            f"export_parity_status={report['status']}",
            f"xlsx_sha256={report['primary'].get('sha256')}",
            f"html_sha256={report['secondary'].get('sha256')}",
            f"canonical_revision={report.get('canonical_revision')}",
            *mismatch_evidence,
            "export_parity=" + json.dumps(report, ensure_ascii=False),
        ],
    )


def parity_status_of(report: dict[str, Any]) -> str:
    return str(report.get("status") or STATUS_NOT_CHECKABLE)


__all__ = ["publish_parity_gate", "resolve_canonical_freshness", "parity_status_of", "STATUS_FAIL"]
