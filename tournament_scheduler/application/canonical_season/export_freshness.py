"""Clear the canonical "fresh export required" latch after a successful export.

A calendar-evidence refresh or a config reconciliation marks the promoted
season's ``export_state`` stale so publication refuses an artifact that
predates the change. That marker must be a one-way signal only until a fresh
export actually covers the new state; otherwise publication stays blocked
forever no matter how many times the season is re-exported.

This module owns the revision-bound clearing. It re-checks the revision at the
commit boundary through the store's expected-revision compare-and-swap, so a
concurrent refresh/reconciliation that commits between the read and the write
keeps its newer freshness requirement instead of being silently overwritten.
The call is then a no-op and the publish preflight keeps refusing the now-stale
artifact.

The transition is idempotent: when the current revision is already recorded as
fresh and no stale marker remains, the operation returns ``cleared=True``
without writing canonical state, appending another history event or churning a
timestamp. Re-running an export over unchanged canonical content therefore
leaves the canonical files byte-for-byte stable.
"""

from __future__ import annotations

import copy
from typing import Any

from tournament_scheduler.canonical_state import canonical_state_revision
from tournament_scheduler.infrastructure.canonical_season_store import (
    CanonicalRevisionConflictError,
)

from .shared import _append_decision_history, _now_iso, _operator_identity


def mark_export_fresh(
    service,
    *,
    season: str,
    expected_revision: str,
    export_dir: str | None = None,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Record that a fresh export covers the current canonical revision.

    ``expected_revision`` is the canonical revision embedded in the artifacts
    that were just generated. The clearing is deliberately revision-bound: a
    mismatch means canonical state advanced after that snapshot was read, so the
    newer state's ``requires_fresh_export`` flag is left intact.
    """

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    current_revision = canonical_state_revision(schedule, decisions)
    exported_revision = str(expected_revision or "")
    if not exported_revision or exported_revision != current_revision:
        return {
            "season": season,
            "cleared": False,
            "reason": "canonical_state_advanced_after_export",
            "exported_canonical_revision": exported_revision,
            "canonical_state_revision": current_revision,
        }

    previous_export_state = dict(decisions.get("export_state") or {})
    promoted_from = dict(schedule.get("promoted_from") or {})
    has_stale_marker = any(
        key in promoted_from
        for key in ("export_stale", "export_stale_reason", "export_stale_at")
    )
    # Repeated exports of unchanged canonical content must be idempotent: once
    # the current revision is already recorded as fresh and no stale marker is
    # left behind, re-running the export adds no history event, no timestamp
    # churn and no canonical write. This keeps ``season export`` from growing
    # the history on every run and from re-invalidating the audit it just
    # satisfied.
    if (
        str(previous_export_state.get("status") or "") == "fresh"
        and not bool(previous_export_state.get("requires_fresh_export"))
        and str(previous_export_state.get("fresh_canonical_revision") or "")
        == current_revision
        and not has_stale_marker
    ):
        return {
            "season": season,
            "cleared": True,
            "reason": "already_fresh",
            "canonical_state_revision": current_revision,
            "export_state": previous_export_state,
        }

    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    updated = copy.deepcopy(decisions)
    previous_export_state = dict(updated.get("export_state") or {})
    export_state = dict(previous_export_state)
    export_state.update(
        {
            "status": "fresh",
            "requires_fresh_export": False,
            "fresh_at": now,
            "fresh_by": resolved_actor,
            "fresh_canonical_revision": current_revision,
        }
    )
    if export_dir:
        export_state["export_dir"] = str(export_dir)
    updated["export_state"] = export_state
    updated["updated_at"] = now

    updated_schedule = dict(schedule)
    promoted_from = dict(updated_schedule.get("promoted_from") or {})
    if promoted_from:
        # Replace the stale marker with a positive "fresh as of" marker so the
        # promoted provenance does not keep advertising a stale export.
        for stale_key in ("export_stale", "export_stale_reason", "export_stale_at"):
            promoted_from.pop(stale_key, None)
        promoted_from["export_fresh_at"] = now
        promoted_from["export_fresh_revision"] = current_revision
        updated_schedule["promoted_from"] = promoted_from

    _append_decision_history(
        updated,
        event="mark_export_fresh",
        tournament_id="",
        actor=resolved_actor,
        now=now,
        note=note,
        details={
            "previous_export_status": previous_export_state.get("status"),
            "previous_requires_fresh_export": previous_export_state.get(
                "requires_fresh_export"
            ),
            "canonical_state_revision": current_revision,
            "export_dir": str(export_dir) if export_dir else None,
        },
    )

    try:
        committed = service._commit(
            snapshot.with_schedule(updated_schedule).with_decisions(updated),
            expected_revision=current_revision,
        )
    except CanonicalRevisionConflictError as exc:
        # A concurrent refresh/reconciliation committed between our read and
        # this write and armed a newer freshness requirement; leave it intact.
        return {
            "season": season,
            "cleared": False,
            "reason": "canonical_state_changed_during_commit",
            "exported_canonical_revision": exported_revision,
            "canonical_state_revision": exc.current_revision or current_revision,
        }
    return {
        "season": season,
        "cleared": True,
        "canonical_state_revision": canonical_state_revision(
            committed.schedule, committed.decisions
        ),
        "export_state": committed.decisions.get("export_state"),
    }
