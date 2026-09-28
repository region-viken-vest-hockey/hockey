"""Clear the canonical "fresh export required" latch after a successful export.

A calendar-evidence refresh or a config reconciliation marks the promoted
season's ``export_state`` stale so publication refuses an artifact that
predates the change. That marker must be a one-way signal only until a fresh
export actually covers the new state; otherwise publication stays blocked
forever no matter how many times the season is re-exported.

This module owns the revision-bound clearing: it only clears the latch when the
canonical state still matches the revision the export was generated from. If a
later mutation committed while the export was running, that mutation's freshness
requirement must survive, so the call is a no-op and the publish preflight keeps
refusing the now-stale artifact.
"""

from __future__ import annotations

import copy
from typing import Any

from tournament_scheduler.canonical_state import canonical_state_revision

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

    committed = service._commit(
        snapshot.with_schedule(updated_schedule).with_decisions(updated)
    )
    return {
        "season": season,
        "cleared": True,
        "canonical_state_revision": canonical_state_revision(
            committed.schedule, committed.decisions
        ),
        "export_state": committed.decisions.get("export_state"),
    }
