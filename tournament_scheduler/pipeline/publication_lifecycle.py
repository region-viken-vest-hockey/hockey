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

from tournament_scheduler.canonical_state import canonical_state_revision
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


def _require_projection(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or not value:
        raise RuntimeError(
            "Refusing publication: canonical export manifest is missing a non-empty "
            "stable-id schedule_projection"
        )
    projection: dict[str, dict[str, Any]] = {}
    required_fields = {"id", "date", "start_time", "arena", "host_club", "age_group", "participants"}
    for tournament_id, entry in value.items():
        stable_id = str(tournament_id or "")
        if not stable_id:
            raise RuntimeError("Refusing publication: schedule_projection contains an empty tournament id")
        if not isinstance(entry, dict):
            raise RuntimeError(
                f"Refusing publication: schedule_projection entry {stable_id!r} is not an object"
            )
        missing = sorted(field for field in required_fields if field not in entry)
        if missing:
            raise RuntimeError(
                f"Refusing publication: schedule_projection entry {stable_id!r} is malformed; "
                f"missing fields: {missing}"
            )
        if str(entry.get("id") or "") != stable_id:
            raise RuntimeError(
                f"Refusing publication: schedule_projection entry {stable_id!r} has mismatched id "
                f"{entry.get('id')!r}"
            )
        participants = entry.get("participants")
        if not isinstance(participants, list):
            raise RuntimeError(
                f"Refusing publication: schedule_projection entry {stable_id!r} has malformed participants"
            )
        projection[stable_id] = dict(entry)
    return projection


def _publication_context(
    export_dir: str | os.PathLike[str],
    *,
    repo_dir: str | os.PathLike[str],
) -> dict[str, Any] | None:
    resolved = _manifest_season(export_dir)
    if resolved is None:
        return None
    season, manifest = resolved
    try:
        schedule = load_schedule(season, root=_season_root(repo_dir))
        decisions = load_decisions(season, root=_season_root(repo_dir))
    except Exception as exc:
        raise RuntimeError(
            "Refusing publication: export manifest declares canonical season "
            f"{season!r}, but its durable schedule/decision state cannot be loaded: {exc}"
        ) from exc

    current_revision = canonical_state_revision(schedule, decisions)
    manifest_revision = str(manifest.get("canonical_revision") or "")
    if not current_revision:
        raise RuntimeError(
            f"Refusing publication: canonical season {season!r} has no determinate revision"
        )
    if not manifest_revision:
        raise RuntimeError(
            "Refusing publication: canonical export manifest is missing canonical_revision"
        )
    if manifest_revision != current_revision:
        raise RuntimeError(
            "Refusing publication: the export was generated from canonical revision "
            f"{manifest_revision}, but current canonical revision is {current_revision}; "
            "regenerate with 'season export' before publishing"
        )

    published_projection = _require_projection(manifest.get("schedule_projection"))
    current_projection = projection_from_canonical_plan(schedule.get("plan") or {})
    delta = diff_tournament_projection(published_projection, current_projection)
    if delta["changed"]:
        raise RuntimeError(
            "Refusing publication: the export no longer matches the current canonical schedule; "
            f"regenerate it with 'season export' before publishing. Drift: {delta}"
        )
    return {
        "season": season,
        "manifest": manifest,
        "schedule": schedule,
        "decisions": decisions,
        "current_revision": current_revision,
        "published_projection": published_projection,
    }


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

    context = _publication_context(export_dir, repo_dir=repo_dir)
    if context is None:
        return None
    season = context["season"]
    decisions = context["decisions"]
    from ..season_state import verify_sealed_reconciliation

    if not is_published_sealed(decisions):
        return None

    report = verify_sealed_reconciliation(season, root=_season_root(repo_dir))
    if not report.get("ok", True):
        raise RuntimeError(
            "Refusing publication: canonical state no longer reconciles to the "
            "published baseline plus recorded canonical mutations. Unexplained delta: "
            f"{report.get('unexplained_delta')}"
        )

    return report


def record_publication_seal(
    export_dir: str | os.PathLike[str],
    *,
    repo_dir: str | os.PathLike[str] = ".",
    actor: str | None = None,
) -> dict[str, Any] | None:
    """Record the published baseline and seal the season (first publication).

    Returns ``None`` when the export is not a canonical-season export. Canonical
    exports fail closed when the manifest, durable canonical state or stable-id
    projection is missing or stale; callers must not downgrade that to a warning.
    """

    context = _publication_context(export_dir, repo_dir=repo_dir)
    if context is None:
        return None
    season = context["season"]
    manifest = context["manifest"]
    publication_id = str(manifest.get("export_id") or "")
    if not publication_id:
        raise RuntimeError("Refusing publication: canonical export manifest is missing export_id")
    from ..season_state import seal_published_season

    return seal_published_season(
        season=season,
        publication_id=publication_id,
        canonical_revision=context["current_revision"],
        published_at=str(manifest.get("published_at") or manifest.get("generated_at") or ""),
        published_projection=context["published_projection"],
        actor=actor,
        note="first/next successful publication",
        root=_season_root(repo_dir),
    )


__all__ = [
    "assert_publication_allowed",
    "record_publication_seal",
]
