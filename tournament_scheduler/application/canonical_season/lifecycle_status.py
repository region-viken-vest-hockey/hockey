"""Read-only published-season lifecycle status and the emergency reopen hatch.

This is the status/diagnostic surface for the ``published_sealed`` lifecycle:
it reports the current state, the recorded published baseline, and whether
current canonical state still reconciles to

    published baseline + attested additions + recorded canonical mutations.

It also owns the explicit, operator-only emergency reopen. Reopening never edits
the recorded publication history; it only drops the season back to ``promoted``
so ordinary publication protections apply again to any later replacement.
"""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.published_baseline import (
    SEASON_LIFECYCLE_KEY,
    STATE_PROMOTED,
    STATE_PUBLISHED_SEALED,
    active_baseline,
    baseline_projection,
    is_published_sealed,
    lifecycle_record,
    lifecycle_state,
    projection_from_canonical_plan,
    publication_history,
)
from tournament_scheduler.published_mutation_history import (
    reconcile_published_baseline,
)

from .shared import _now_iso, _operator_identity


def _attested_additions(baseline: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Reconstruct the attested additions stored with a sealed baseline."""

    migration = baseline.get("migration") if isinstance(baseline.get("migration"), Mapping) else {}
    additions: dict[str, dict[str, Any]] = {}
    for key in ("publication_omissions", "materializations"):
        for entry in migration.get(key) or []:
            if not isinstance(entry, Mapping):
                continue
            tournament_id = str(entry.get("tournament_id") or "")
            if not tournament_id:
                continue
            additions[tournament_id] = {
                "id": tournament_id,
                "date": str(entry.get("date") or ""),
                "start_time": str(entry.get("start_time") or ""),
                "arena": str(entry.get("arena") or ""),
                "host_club": str(entry.get("host_club") or ""),
                "age_group": str(entry.get("age_group") or ""),
                "participants": sorted(str(participant) for participant in entry.get("participants") or []),
            }
    return additions


def _reconcile(
    *,
    baseline: Mapping[str, Any],
    current_projection: Mapping[str, Mapping[str, Any]],
    decisions: Mapping[str, Any],
) -> dict[str, Any]:
    return reconcile_published_baseline(
        published_projection=baseline_projection(baseline),
        current_projection=current_projection,
        history=decisions.get("history") or [],
        attested_additions=_attested_additions(baseline),
    )


def _migration_ids(baseline: Mapping[str, Any], key: str) -> list[str]:
    migration = baseline.get("migration") if isinstance(baseline.get("migration"), Mapping) else {}
    return sorted(
        str(entry.get("tournament_id") or "")
        for entry in migration.get(key) or []
        if isinstance(entry, Mapping)
    )


def season_lifecycle_report(service, *, season: str) -> dict[str, Any]:
    """Read-only lifecycle + reconciliation status for one canonical season."""

    snapshot = service.load(season)
    decisions = snapshot.decisions
    state = lifecycle_state(decisions)
    baseline = active_baseline(decisions)
    report: dict[str, Any] = {
        "season": season,
        "state": state,
        "sealed": state == STATE_PUBLISHED_SEALED,
        "canonical_state_revision": decisions.get("canonical_state_revision"),
        "publication_count": len(publication_history(decisions)),
    }
    if baseline is not None:
        report["published_baseline"] = {
            "publication_id": baseline.get("publication_id"),
            "canonical_revision": baseline.get("canonical_revision"),
            "published_at": baseline.get("published_at"),
            "projection_fingerprint": baseline.get("projection_fingerprint"),
            "tournament_count": baseline.get("tournament_count"),
        }
        reconciliation = _reconcile(
            baseline=baseline,
            current_projection=projection_from_canonical_plan(snapshot.plan),
            decisions=decisions,
        )
        report["reconciliation"] = {
            "ok": reconciliation["ok"],
            "applied_mutation_count": reconciliation["applied_mutation_count"],
            "publication_omissions": _migration_ids(baseline, "publication_omissions"),
            "materializations": _migration_ids(baseline, "materializations"),
            "unexplained_delta": reconciliation["unexplained_delta"],
        }
    return report


def verify_sealed_reconciliation(service, *, season: str) -> dict[str, Any]:
    """Return the reconciliation report, failing closed when it cannot hold."""

    snapshot = service.load(season)
    baseline = active_baseline(snapshot.decisions)
    if baseline is None:
        return {"season": season, "ok": True, "sealed": False, "reason": "no_published_baseline"}
    report = _reconcile(
        baseline=baseline,
        current_projection=projection_from_canonical_plan(snapshot.plan),
        decisions=snapshot.decisions,
    )
    report.update(
        {
            "season": season,
            "sealed": is_published_sealed(snapshot.decisions),
            "publication_omissions": _migration_ids(baseline, "publication_omissions"),
            "materializations": _migration_ids(baseline, "materializations"),
        }
    )
    return report


def reopen_planning(
    service,
    *,
    season: str,
    reason: str,
    confirm_break_published_baseline: bool,
    actor: str | None = None,
) -> dict[str, Any]:
    """Explicit operator-only escape hatch for a fundamental restructuring."""

    if not confirm_break_published_baseline:
        raise SeasonStateError(
            "Refusing to reopen planning: pass --confirm-break-published-baseline "
            "to acknowledge that this removes the sealed-season protection."
        )
    if not str(reason or "").strip():
        raise SeasonStateError("Refusing to reopen planning without an explicit --reason")

    snapshot = service.load(season)
    decisions = snapshot.decisions
    if not is_published_sealed(decisions):
        raise SeasonStateError(
            f"Refusing to reopen planning: season {season} is not published_sealed "
            f"(state={lifecycle_state(decisions)})"
        )
    resolved_actor = _operator_identity(actor)
    now = _now_iso()
    existing = lifecycle_record(decisions)
    reopen_events = list(existing.get("reopen_history") or [])
    reopen_events.append(
        {
            "actor": resolved_actor,
            "at": now,
            "reason": str(reason).strip(),
            "previous_baseline_publication_id": (active_baseline(decisions) or {}).get("publication_id"),
        }
    )
    updated_lifecycle = {
        **existing,
        "state": STATE_PROMOTED,
        "reopen_history": reopen_events,
    }
    updated_decisions = {**decisions, "updated_at": now, SEASON_LIFECYCLE_KEY: updated_lifecycle}
    committed = service._commit(snapshot.with_decisions(updated_decisions))
    return {
        "season": season,
        "state": STATE_PROMOTED,
        "reopened": True,
        "reason": str(reason).strip(),
        "canonical_state_revision_after": committed.decisions.get("canonical_state_revision"),
        "reopen_history": reopen_events,
    }


__all__ = [
    "reopen_planning",
    "season_lifecycle_report",
    "verify_sealed_reconciliation",
]
