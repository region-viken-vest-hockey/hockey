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
from typing import Any, Mapping

from tournament_scheduler.canonical_state import canonical_state_revision
from tournament_scheduler.infrastructure.canonical_season_store import (
    load_decisions,
    load_schedule,
)
from tournament_scheduler.published_baseline import (
    active_baseline,
    is_published_sealed,
    projection_fingerprint,
    projection_from_canonical_schedule,
)

from .export_lifecycle import read_export_manifest
from .export_projection_guard import (
    FULL_OPERATIONAL_PROJECTION_SCHEMA,
    FULL_OPERATIONAL_PROJECTION_VERSION,
    diff_tournament_projection,
)
from .publication_evidence import (
    build_publication_evidence,
    write_publication_evidence,
)


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
    required_fields = {
        "projection_schema",
        "projection_schema_version",
        "id",
        "date",
        "start_time",
        "arena",
        "host_club",
        "age_group",
        "duration_minutes",
        "end_time",
        "cancelled",
        "cancellation_reason",
        "participants",
        "guest_slots",
    }
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
        if (
            entry.get("projection_schema") != FULL_OPERATIONAL_PROJECTION_SCHEMA
            or entry.get("projection_schema_version") != FULL_OPERATIONAL_PROJECTION_VERSION
        ):
            raise RuntimeError(
                f"Refusing publication: schedule_projection entry {stable_id!r} has unsupported projection schema"
            )
        participants = entry.get("participants")
        if not isinstance(participants, list):
            raise RuntimeError(
                f"Refusing publication: schedule_projection entry {stable_id!r} has malformed participants"
            )
        if not isinstance(entry.get("guest_slots"), list):
            raise RuntimeError(
                f"Refusing publication: schedule_projection entry {stable_id!r} has malformed guest_slots"
            )
        projection[stable_id] = dict(entry)
    return projection


def _is_already_sealed_publication(
    *,
    decisions: Mapping[str, Any],
    export_id: str,
    manifest_revision: str,
) -> bool:
    """Whether this exact export already produced the active published baseline.

    Sealing advances the canonical-state revision through a decision-only write,
    so a retry of the *same* publication (for example to finish a failed evidence
    write) legitimately sees a manifest revision that is now the baseline's
    recorded revision rather than the current one. Any real schedule drift is
    still caught by the projection comparison that always runs.
    """

    if not export_id:
        return False
    baseline = active_baseline(decisions)
    if not isinstance(baseline, Mapping):
        return False
    return (
        str(baseline.get("publication_id") or "") == export_id
        and str(baseline.get("canonical_revision") or "") == manifest_revision
    )


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
    if manifest_revision != current_revision and not _is_already_sealed_publication(
        decisions=decisions,
        export_id=str(manifest.get("export_id") or ""),
        manifest_revision=manifest_revision,
    ):
        raise RuntimeError(
            "Refusing publication: the export was generated from canonical revision "
            f"{manifest_revision}, but current canonical revision is {current_revision}; "
            "regenerate with 'season export' before publishing"
        )

    published_projection = _require_projection(manifest.get("schedule_projection"))
    current_projection = projection_from_canonical_schedule(schedule)
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
    branch: str = "gh-pages",
) -> dict[str, Any] | None:
    """Refuse to publish an export that cannot be defended against the sealed baseline.

    Two checks run before a public push:

    1. the canonical season must still reconcile to its published baseline plus
       recorded canonical mutations (unexplained drift is never published);
    2. the export's own projection must still equal the current canonical
       projection, so an old export cannot republish a superseded schedule.

    For a *replacement* publication it additionally verifies that the previous
    published version is retained behind an immutable reference, so the public
    snapshot being replaced stays reachable for rollback. A branch that cannot
    be inspected reports ``unverified`` rather than pretending the previous
    version was checked.

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

    baseline = active_baseline(decisions)
    if baseline is None:
        raise RuntimeError(
            "Refusing publication: season is published_sealed but has no published "
            "baseline; the previous public version cannot be identified. Inspect "
            "'season lifecycle --season <season> --json' and repair the lifecycle "
            "state (for example with 'season seal-published') before publishing."
        )

    report = verify_sealed_reconciliation(season, root=_season_root(repo_dir))
    if not report.get("ok", True):
        raise RuntimeError(
            "Refusing publication: canonical state no longer reconciles to the "
            "published baseline plus recorded canonical mutations. Unexplained delta: "
            f"{report.get('unexplained_delta')}"
        )

    recoverability = _previous_publication_recoverability(
        season=season,
        baseline=baseline,
        repo_dir=repo_dir,
        branch=branch,
    )
    if recoverability is not None:
        report = {**report, "previous_publication": recoverability}
    return report


def _previous_publication_recoverability(
    *,
    season: str,
    baseline: Mapping[str, Any] | None,
    repo_dir: str | os.PathLike[str],
    branch: str,
) -> dict[str, Any] | None:
    """Verify the replaced public bundle is retained behind an immutable reference.

    Requires a concrete run id and a *verified* immutable snapshot (refreshed
    remote target, matching ``_meta.json`` run id and recorded bundle
    fingerprint). An unverifiable target is refused: a replacement must prove its
    predecessor is still reachable for rollback.
    """

    if not isinstance(baseline, Mapping):
        return None
    evidence = baseline.get("publication_evidence")
    run_id = ""
    expected_bundle_fingerprint = ""
    if isinstance(evidence, Mapping):
        run_id = str(evidence.get("run_id") or "")
        expected_bundle_fingerprint = str(evidence.get("bundle_fingerprint") or "")
    if not run_id:
        from .export_lifecycle import find_published_exports_for_season

        publication_id = str(baseline.get("publication_id") or "")
        for record in find_published_exports_for_season(season, season_root=_season_root(repo_dir)):
            if str(record.get("export_id") or "") == publication_id:
                run_id = str(record.get("pages_run_id") or "")
                expected_bundle_fingerprint = expected_bundle_fingerprint or str(
                    record.get("pages_bundle_fingerprint") or ""
                )
                break
    if not run_id:
        raise RuntimeError(
            "Refusing publication: the previous published baseline "
            f"{baseline.get('publication_id')!r} has no immutable run id; rollback "
            "cannot be verified. Backfill it with 'season seal-published' before "
            "replacing the public snapshot."
        )

    from . import pages_publish

    snapshot = pages_publish.verify_published_run_snapshot(
        run_id,
        repo_dir=str(repo_dir),
        branch=branch,
        expected_bundle_fingerprint=expected_bundle_fingerprint or None,
    )
    if not snapshot["verifiable"] or not snapshot["retained"] or snapshot["problems"]:
        detail = "; ".join(snapshot["problems"]) or "snapshot could not be verified"
        raise RuntimeError(
            "Refusing publication: the previous published run "
            f"{run_id!r} is not verifiably retained as an immutable "
            f"/runs/{run_id}/ snapshot on '{branch}' ({detail}); the version being "
            "replaced would not be recoverable."
        )
    return {
        "publication_id": baseline.get("publication_id"),
        "run_id": run_id,
        "bundle_fingerprint": expected_bundle_fingerprint,
        "snapshot_ref": snapshot["ref"],
        "meta_bundle_fingerprint": snapshot["meta_bundle_fingerprint"],
        "run_snapshot_retained": "true",
    }


def record_publication_seal(
    export_dir: str | os.PathLike[str],
    *,
    repo_dir: str | os.PathLike[str] = ".",
    actor: str | None = None,
) -> dict[str, Any] | None:
    """Record the published baseline and seal the season (first publication).

    On a replacement publication the new immutable baseline carries the
    previous publication link, the exact stable-id delta against it and an
    immutable reference to the public bundle just published. The before/after
    evidence is also retained under ``season/<season>/evidence/publications/``.

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
    evidence = build_publication_evidence(
        run_id=str(manifest.get("pages_run_id") or ""),
        canonical_revision=context["current_revision"],
        projection_fingerprint=projection_fingerprint(context["published_projection"]),
        bundle_fingerprint=str(manifest.get("pages_bundle_fingerprint") or ""),
        export_id=publication_id,
        export_fingerprint=str(manifest.get("export_fingerprint") or ""),
        pages_branch=str(manifest.get("pages_branch") or ""),
        pages_commit=str(manifest.get("pages_commit") or ""),
        published_at=str(manifest.get("published_at") or manifest.get("generated_at") or ""),
    )
    from ..season_state import seal_published_season

    report = seal_published_season(
        season=season,
        publication_id=publication_id,
        canonical_revision=context["current_revision"],
        published_at=str(manifest.get("published_at") or manifest.get("generated_at") or ""),
        published_projection=context["published_projection"],
        actor=actor,
        note="first/next successful publication",
        root=_season_root(repo_dir),
        publication_evidence=evidence,
    )
    report["evidence_files"] = write_publication_evidence(
        season_root=_season_root(repo_dir),
        season=season,
        publication_id=publication_id,
        evidence=report.get("publication_evidence") or evidence,
        previous_publication=report.get("previous_publication"),
        republish_delta=report.get("republish_delta"),
        canonical_revision=context["current_revision"],
        decision_changes=report.get("republish_decision_changes"),
    )
    return report


__all__ = [
    "assert_publication_allowed",
    "record_publication_seal",
]
