"""Publication-boundary integration for the sealed published-season lifecycle.

The publication boundary is where a season first becomes operational. This
module is the thin bridge between the GitHub Pages publish action and the
canonical application lifecycle: it refuses to publish an export that no longer
matches a sealed canonical schedule, and it records the immutable published
baseline (and seals the season on first publication) once a publish succeeds.

All durable state lives in canonical ``decisions.json``; this module never edits
canonical schedule facts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from tournament_scheduler.infrastructure.canonical_season_store import (
    load_decisions,
    load_schedule,
)
from tournament_scheduler.published_baseline import (
    is_published_sealed,
    projection_from_canonical_plan,
)

from .export_lifecycle import read_export_manifest
from .export_projection_guard import diff_tournament_projection


def _season_root(repo_dir: str | os.PathLike[str]) -> Path:
    return Path(repo_dir) / "season"


def _manifest_season(export_dir: str | os.PathLike[str]) -> tuple[str, dict[str, Any]] | None:
    manifest = read_export_manifest(export_dir)
    if not manifest:
        return None
    season = str(manifest.get("canonical_season") or "")
    if not season:
        return None
    return season, manifest


def assert_publication_allowed(
    export_dir: str | os.PathLike[str],
    *,
    repo_dir: str | os.PathLike[str] = ".",
) -> dict[str, Any] | None:
    """Refuse to publish an export that cannot be defended against the sealed baseline.

    Two checks run before a public push:

    1. the canonical season must still reconcile to its published baseline plus
       recorded canonical mutations (unexplained drift is never published);
    2. the export's own projection must still equal the current canonical
       projection, so an old export cannot republish a superseded schedule.

    Returns the reconciliation report when the season is sealed, ``None`` when
    the export is not a canonical-season export or the season is not sealed.
    """

    resolved = _manifest_season(export_dir)
    if resolved is None:
        return None
    season, manifest = resolved
    from ..season_state import verify_sealed_reconciliation

    try:
        decisions = load_decisions(season, root=_season_root(repo_dir))
    except Exception:
        # The export names a canonical season whose durable state is not present
        # in this checkout; there is no sealed baseline to defend here.
        return None
    if not is_published_sealed(decisions):
        return None

    report = verify_sealed_reconciliation(season, root=_season_root(repo_dir))
    if not report.get("ok", True):
        raise RuntimeError(
            "Refusing publication: canonical state no longer reconciles to the "
            "published baseline plus recorded canonical mutations. Unexplained delta: "
            f"{report.get('unexplained_delta')}"
        )

    published_projection = manifest.get("schedule_projection")
    if isinstance(published_projection, dict) and published_projection:
        current_projection = projection_from_canonical_plan(
            load_schedule(season, root=_season_root(repo_dir)).get("plan") or {}
        )
        delta = diff_tournament_projection(published_projection, current_projection)
        if delta["changed"]:
            raise RuntimeError(
                "Refusing publication: the export no longer matches the sealed "
                "canonical schedule; regenerate it with 'season export' before "
                f"publishing. Drift: {delta}"
            )
    return report


def record_publication_seal(
    export_dir: str | os.PathLike[str],
    *,
    repo_dir: str | os.PathLike[str] = ".",
    actor: str | None = None,
) -> dict[str, Any] | None:
    """Record the published baseline and seal the season (first publication).

    Returns ``None`` when the export is not a canonical-season export. A legacy
    export without ``schedule_projection`` cannot be sealed automatically; it is
    reported as skipped so the caller can surface the migration command.
    """

    resolved = _manifest_season(export_dir)
    if resolved is None:
        return None
    season, manifest = resolved
    published_projection = manifest.get("schedule_projection")
    if not isinstance(published_projection, dict) or not published_projection:
        return {
            "season": season,
            "sealed": False,
            "skipped": "export_manifest_missing_schedule_projection",
            "migration_command": f"season seal-published --season {season}",
        }
    from ..season_state import seal_published_season

    try:
        return seal_published_season(
            season=season,
            publication_id=str(manifest.get("export_id") or ""),
            canonical_revision=str(manifest.get("canonical_revision") or ""),
            published_at=str(manifest.get("published_at") or ""),
            published_projection=published_projection,
            actor=actor,
            note="first/next successful publication",
            root=_season_root(repo_dir),
        )
    except Exception as exc:  # noqa: BLE001 - publication succeeded; caller decides how to surface it.
        return {
            "season": season,
            "sealed": False,
            "skipped": f"seal_failed: {exc}",
            "migration_command": f"season seal-published --season {season}",
        }


__all__ = [
    "assert_publication_allowed",
    "record_publication_seal",
]
