"""The single canonical-season commit and maintenance-guard lifecycle."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from tournament_scheduler.canonical_state import (
    CANONICAL_STATE_REVISION_KEY,
    compute_canonical_state_revision,
    migrate_participation_acceptance_ids,
)
from tournament_scheduler.change_protections import (
    protection_violations,
)
from tournament_scheduler.request_constraints import (
    request_constraint_violations,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    CanonicalSeasonSnapshot,
    SeasonStateError,
)
from tournament_scheduler.published_baseline import (
    active_baseline,
    is_published_sealed,
    projection_from_canonical_schedule,
)
from tournament_scheduler.published_mutation_history import reconcile_published_baseline

from .shared import published_baseline_reconciliation


def _commit(
    service,
    snapshot: CanonicalSeasonSnapshot,
    *,
    require_absent: bool = False,
    extra_evidence: Mapping[str, bytes] | None = None,
) -> CanonicalSeasonSnapshot:
    """Persist a snapshot under one fresh canonical-state revision.

    The revision is computed from the *complete* new state, so schedule
    mutations and decision-only mutations both advance it. Derived
    projections and decisions must already have been reconciled by the
    caller; the store installs both files atomically. ``extra_evidence`` maps
    season-relative evidence paths to bytes that must be installed in the same
    atomic swap as the canonical files (for example a content-addressed
    pre-refresh snapshot referenced from the new decisions state).
    """

    decisions = dict(snapshot.decisions)
    migrate_participation_acceptance_ids(decisions)
    guard_violations = protection_violations(snapshot.schedule.get("plan") or {}, decisions)
    if guard_violations:
        messages = "; ".join(str(item.get("message")) for item in guard_violations)
        raise SeasonStateError(
            "Refusing canonical commit: it would undo an accepted change: " + messages
        )
    decisions[CANONICAL_STATE_REVISION_KEY] = compute_canonical_state_revision(
        snapshot.schedule, decisions
    )
    committed = snapshot.with_decisions(decisions)
    service.store.write(
        committed, require_absent=require_absent, extra_evidence=extra_evidence
    )
    return committed


def _commit_history_only(
    service,
    snapshot: CanonicalSeasonSnapshot,
) -> CanonicalSeasonSnapshot:
    """Persist a history-only change without semantic migration or revision recompute.

    Canonical history compaction changes only ``history`` and must preserve every
    other durable field -- including the stored ``canonical_state_revision`` --
    byte-for-byte. A pending semantic migration (legacy participation-acceptance
    ids) is refused rather than silently applied alongside the history change,
    so compaction never performs a second, unannounced semantic migration.
    """

    decisions = dict(snapshot.decisions)
    probe = deepcopy(decisions)
    if migrate_participation_acceptance_ids(probe):
        raise SeasonStateError(
            "Refusing history-only commit: the canonical state carries legacy "
            "participation-acceptance ids that a normal commit would migrate; "
            "migrate via a normal canonical mutation before compacting history"
        )
    guard_violations = protection_violations(snapshot.schedule.get("plan") or {}, decisions)
    if guard_violations:
        messages = "; ".join(str(item.get("message")) for item in guard_violations)
        raise SeasonStateError(
            "Refusing canonical commit: it would undo an accepted change: " + messages
        )
    committed = snapshot.with_decisions(decisions)
    service.store.write(committed)
    return committed


def _assert_published_sealed_reconciliation(
    service,
    snapshot: CanonicalSeasonSnapshot,
    *,
    action: str,
) -> None:
    """Fail closed before writing an unexplained sealed-season schedule delta."""

    if not is_published_sealed(snapshot.decisions):
        return
    baseline = active_baseline(snapshot.decisions)
    if baseline is None:
        return
    current_projection = projection_from_canonical_schedule(snapshot.schedule)
    published_projection, attested_additions = published_baseline_reconciliation(
        service,
        baseline,
        current_projection=current_projection,
    )
    report = reconcile_published_baseline(
        published_projection=published_projection,
        current_projection=current_projection,
        history=snapshot.decisions.get("history") or [],
        attested_additions=attested_additions,
    )
    if report.get("ok"):
        return
    raise SeasonStateError(
        f"Refusing canonical {action}: sealed-season state would not reconcile to "
        "the published baseline plus recorded canonical mutations. Unexplained delta: "
        f"{report.get('unexplained_delta')}"
    )


def _assert_request_constraints_satisfied(
    service,
    plan: Mapping[str, Any],
    decisions: Mapping[str, Any],
    *,
    action: str,
) -> None:
    """Refuse a schedule-changing commit that violates an active constraint.

    Request constraints are hard maintenance requirements: every canonical
    schedule-changing entry point calls this at its application boundary, so
    a generator/search that already considered the constraints is still
    re-checked against the authoritative active set. Decision-only writes
    (recording a new request, approval, release) deliberately do not.
    """

    violations = request_constraint_violations(plan, decisions)
    if not violations:
        return
    messages = "; ".join(str(item.get("message")) for item in violations)
    raise SeasonStateError(
        f"Refusing canonical {action}: it violates an active request constraint: {messages}"
    )
