"""Published-season sealing (the ``published_sealed`` lifecycle transition).

Sealing is a one-time lifecycle transition, not a schedule mutation: it records
the immutable published baseline and the explicit provenance needed to reconcile
the actual publication with later explicit canonical maintenance. It never edits
tournament placement, participants, approvals or evidence.

The read-only lifecycle/status/reopen use cases live in
:mod:`tournament_scheduler.application.canonical_season.lifecycle_status`.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from tournament_scheduler.published_baseline import (
    SEASON_LIFECYCLE_KEY,
    STATE_PUBLISHED_SEALED,
    PublishedBaselineError,
    active_baseline,
    build_baseline_record,
    lifecycle_record,
    projection_entry,
    projection_fingerprint,
    projection_from_canonical_schedule,
    publication_history,
)
from tournament_scheduler.pipeline.publication_evidence import (
    PublicationEvidenceError,
    build_republish_delta,
    decision_snapshot,
    diff_decision_snapshot,
    previous_publication_link,
    validate_publication_evidence,
)
from tournament_scheduler.published_mutation_history import (
    omission_projection,
    reconcile_published_baseline,
)

from .shared import _now_iso, _operator_identity, published_baseline_reconciliation


def seal_published_season(
    service,
    *,
    season: str,
    publication_id: str,
    canonical_revision: str,
    published_at: str,
    published_projection: Mapping[str, Mapping[str, Any]],
    publication_canonical_projection: Mapping[str, Mapping[str, Any]] | None = None,
    materializations: Iterable[Mapping[str, Any]] = (),
    actor: str | None = None,
    note: str = "",
    publication_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Record the immutable published baseline and seal the season.

    The reconciliation invariant must hold *before* the seal is persisted:

        published projection + attested additions + recorded mutations
        = current canonical projection

    ``attested additions`` are publication omissions (derived from the canonical
    plan at the publication revision) plus post-publication materializations
    that the operator explicitly attested. Any unexplained stable-id delta fails
    closed; a sealed season is never produced by silently snapshotting the
    current canonical state.
    """

    snapshot = service.load(season)
    decisions = snapshot.decisions
    resolved_actor = _operator_identity(actor)
    now = _now_iso()

    current_projection = projection_from_canonical_schedule(snapshot.schedule)

    omission_entries: dict[str, dict[str, Any]] = {}
    if publication_canonical_projection is not None:
        omission_entries = omission_projection(
            publication_canonical_projection, published_projection
        )
    omission_records = [
        {
            **projection_entry(tournament_id, omission_entries[tournament_id]),
            "tournament_id": tournament_id,
            "evidence": "present in canonical plan at the publication revision but absent from the published artifact",
        }
        for tournament_id in sorted(omission_entries)
    ]

    materialization_records: list[dict[str, Any]] = []
    materialization_entries: dict[str, dict[str, Any]] = {}
    for entry in materializations:
        if not isinstance(entry, Mapping):
            continue
        tournament_id = str(entry.get("tournament_id") or "")
        provenance = str(entry.get("provenance") or "").strip()
        if not tournament_id:
            raise PublishedBaselineError("Refusing to seal: materialization attestation is missing a tournament id")
        if not provenance:
            raise PublishedBaselineError(
                f"Refusing to seal: materialization {tournament_id} has no provenance evidence"
            )
        if tournament_id in published_projection:
            raise PublishedBaselineError(
                f"Refusing to seal: {tournament_id} is part of the published artifact; it is not a materialization"
            )
        if tournament_id not in current_projection:
            raise PublishedBaselineError(
                f"Refusing to seal: attested materialization {tournament_id} is not present in canonical state"
            )
        record = {
            **projection_entry(tournament_id, current_projection[tournament_id]),
            "tournament_id": tournament_id,
            "provenance": provenance,
        }
        materialization_records.append(record)
        materialization_entries[tournament_id] = record

    reconciliation = reconcile_published_baseline(
        published_projection=published_projection,
        current_projection=current_projection,
        history=decisions.get("history") or [],
        attested_additions={**omission_entries, **materialization_entries},
    )
    if not reconciliation["ok"]:
        raise PublishedBaselineError(
            "Refusing to seal published season: canonical state cannot be reconciled "
            "to the published baseline plus attested additions and recorded canonical "
            f"mutations. Unexplained delta: {reconciliation['unexplained_delta']}"
        )

    existing = lifecycle_record(decisions)
    history = publication_history(decisions)
    previous_baseline = active_baseline(decisions)
    previous_link = previous_publication_link(previous_baseline)
    republish_delta = None
    before_snapshot = None
    if previous_baseline is not None:
        previous_projection, _attested = published_baseline_reconciliation(
            service,
            previous_baseline,
            current_projection=current_projection,
        )
        republish_delta = build_republish_delta(previous_projection, current_projection)
        previous_evidence = previous_baseline.get("publication_evidence")
        if isinstance(previous_evidence, Mapping):
            before_snapshot = previous_evidence.get("decision_snapshot")
    decision_changes = diff_decision_snapshot(
        before_snapshot if isinstance(before_snapshot, Mapping) else None,
        decision_snapshot(decisions),
    )

    resolved_evidence = None
    if publication_evidence is not None:
        try:
            resolved_evidence = validate_publication_evidence(publication_evidence)
        except PublicationEvidenceError as exc:
            raise PublishedBaselineError(str(exc)) from exc
        expected_fingerprint = projection_fingerprint(published_projection)
        if resolved_evidence["projection_fingerprint"] != expected_fingerprint:
            raise PublishedBaselineError(
                "Refusing to seal: publication evidence projection fingerprint "
                f"{resolved_evidence['projection_fingerprint']} does not match the published "
                f"projection {expected_fingerprint}"
            )
        resolved_evidence["decision_snapshot"] = decision_snapshot(decisions)

    migration = {
        "actor": resolved_actor,
        "at": now,
        "canonical_revision_at_seal": decisions.get("canonical_state_revision"),
        "publication_omissions": omission_records,
        "materializations": materialization_records,
    }
    baseline_record = build_baseline_record(
        season=season,
        publication_id=publication_id,
        canonical_revision=canonical_revision,
        published_at=published_at,
        projection=published_projection,
        actor=resolved_actor,
        note=note,
        migration=migration,
        publication_evidence=resolved_evidence,
        previous_publication=previous_link,
        republish_delta=republish_delta,
        republish_decision_changes=decision_changes,
    )

    already = next(
        (
            entry
            for entry in history
            if str(entry.get("publication_id") or "") == str(publication_id)
            and str(entry.get("projection_fingerprint") or "")
            == str(baseline_record.get("projection_fingerprint") or "")
        ),
        None,
    )
    revised_history = history if already is not None else history + [baseline_record]
    stored_baseline = already or baseline_record
    updated_lifecycle = {
        **existing,
        "state": STATE_PUBLISHED_SEALED,
        "sealed_at": existing.get("sealed_at") or now,
        "sealed_by": existing.get("sealed_by") or resolved_actor,
        "published_baseline": stored_baseline,
        "publication_history": revised_history,
    }
    previous_revision = decisions.get("canonical_state_revision")
    updated_decisions = {
        **decisions,
        "updated_at": now,
        SEASON_LIFECYCLE_KEY: updated_lifecycle,
    }
    committed = service._commit(snapshot.with_decisions(updated_decisions))
    return {
        "season": season,
        "state": STATE_PUBLISHED_SEALED,
        "publication_id": publication_id,
        "projection_fingerprint": baseline_record.get("projection_fingerprint"),
        "canonical_state_revision_before": previous_revision,
        "canonical_state_revision_after": committed.decisions.get("canonical_state_revision"),
        "publication_evidence": stored_baseline.get("publication_evidence"),
        "previous_publication": stored_baseline.get("previous_publication"),
        "republish_delta": stored_baseline.get("republish_delta"),
        "republish_decision_changes": stored_baseline.get("republish_decision_changes"),
        "reconciliation": {
            "applied_mutation_count": reconciliation["applied_mutation_count"],
            "publication_omissions": [entry["tournament_id"] for entry in omission_records],
            "materializations": [entry["tournament_id"] for entry in materialization_records],
            "unexplained_delta": reconciliation["unexplained_delta"],
        },
    }


__all__ = [
    "seal_published_season",
]
