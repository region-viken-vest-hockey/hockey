"""The single canonical-season commit and maintenance-guard lifecycle."""

from __future__ import annotations

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


def _commit(service, snapshot: CanonicalSeasonSnapshot, *, require_absent: bool = False) -> CanonicalSeasonSnapshot:
    """Persist a snapshot under one fresh canonical-state revision.

    The revision is computed from the *complete* new state, so schedule
    mutations and decision-only mutations both advance it. Derived
    projections and decisions must already have been reconciled by the
    caller; the store installs both files atomically.
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
    service.store.write(committed, require_absent=require_absent)
    return committed


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
