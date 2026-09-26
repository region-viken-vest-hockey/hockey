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
    is_published_sealed,
    lifecycle_record,
    lifecycle_state,
    projection_from_canonical_schedule,
    publication_history,
)
from tournament_scheduler.published_mutation_history import (
    reconcile_published_baseline,
)

from .shared import (
    _now_iso,
    _operator_identity,
    published_baseline_reconciliation,
)
from tournament_scheduler.pipeline.publication_evidence import (
    PublicationEvidenceError,
    build_republish_delta,
    decision_snapshot,
    diff_decision_snapshot,
    list_publication_evidence,
)


def _reconcile(
    *,
    service,
    baseline: Mapping[str, Any],
    current_projection: Mapping[str, Mapping[str, Any]],
    decisions: Mapping[str, Any],
) -> dict[str, Any]:
    published_projection, attested_additions = published_baseline_reconciliation(
        service,
        baseline,
        current_projection=current_projection,
    )
    return reconcile_published_baseline(
        published_projection=published_projection,
        current_projection=current_projection,
        history=decisions.get("history") or [],
        attested_additions=attested_additions,
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
            service=service,
            baseline=baseline,
            current_projection=projection_from_canonical_schedule(snapshot.schedule),
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
        service=service,
        baseline=baseline,
        current_projection=projection_from_canonical_schedule(snapshot.schedule),
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


def publication_evidence_report(service, *, season: str) -> dict[str, Any]:
    """Read-only republish evidence: what a replacement publication would change.

    Reports the exact previously published revision (from authoritative
    publication history, never from a supplied export directory or the moving
    ``latest/`` path), the stable-id schedule delta to current canonical state,
    the decision-only changes, and the retained immutable evidence records.
    """

    snapshot = service.load(season)
    decisions = snapshot.decisions
    current_projection = projection_from_canonical_schedule(snapshot.schedule)
    state = lifecycle_state(decisions)
    season_root = getattr(service.store, "root", "season")
    report: dict[str, Any] = {
        "season": season,
        "state": state,
        "sealed": state == STATE_PUBLISHED_SEALED,
        "current_canonical_revision": decisions.get("canonical_state_revision"),
        "publication_count": len(publication_history(decisions)),
        "active_publication": None,
        "published_to_canonical_delta": None,
        "decision_changes": None,
        "retained_evidence": list_publication_evidence(season_root, season),
    }
    baseline = active_baseline(decisions)
    if baseline is None:
        return report

    published_projection, _attested = published_baseline_reconciliation(
        service,
        baseline,
        current_projection=current_projection,
    )
    evidence = baseline.get("publication_evidence")
    before_snapshot = evidence.get("decision_snapshot") if isinstance(evidence, Mapping) else None
    decision_changes = (
        diff_decision_snapshot(before_snapshot, decision_snapshot(decisions))
        if isinstance(before_snapshot, Mapping)
        else None
    )
    report["active_publication"] = {
        "publication_id": baseline.get("publication_id"),
        "canonical_revision": baseline.get("canonical_revision"),
        "published_at": baseline.get("published_at"),
        "projection_fingerprint": baseline.get("projection_fingerprint"),
        "tournament_count": baseline.get("tournament_count"),
        "publication_evidence": evidence,
        "previous_publication": baseline.get("previous_publication"),
    }
    try:
        report["published_to_canonical_delta"] = build_republish_delta(
            published_projection, current_projection
        )
    except PublicationEvidenceError as exc:
        # Fail closed: an incomplete/legacy baseline projection must never be
        # presented to the operator as a valid republish delta.
        report["blocked"] = True
        report["delta_error"] = str(exc)
    report["decision_changes"] = decision_changes
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
    "publication_evidence_report",
    "reopen_planning",
    "season_lifecycle_report",
    "verify_sealed_reconciliation",
]
