"""Canonical tournament restoration as an in-memory plan mutation.

A restoration is the inverse domain fact of a cancellation (see
``cancellation.py``): it clears the first-class ``cancelled`` flag on one
cancelled tournament and nothing else. Batch maintenance applies it.
"""

from __future__ import annotations

from typing import Any

from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError


def _apply_restore_to_plan(
    plan: dict[str, Any],
    *,
    tournament_id: str,
    reason: str = "",
) -> dict[str, Any]:
    """Reinstate one cancelled tournament in an in-memory plan.

    Restoration clears only the first-class ``cancelled`` flag and its
    ``cancellation_reason``; the stored placement and roster are kept so a
    same-batch ``move`` can adopt the authoritative interval. It deliberately
    does *not* revive what the cancellation ended: released guest reservations
    and released booking authority stay released (reserve or re-confirm them
    through their own actions). The full canonical gates -- hard verification,
    locks, protections, request constraints, operational acceptability --
    decide whether a batch containing a restoration is acceptable; this helper
    only applies the domain fact.
    """

    tournaments = plan.get("tournaments", []) or []
    target = next((t for t in tournaments if str(t.get("id")) == tournament_id), None)
    if target is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    if not target.get("cancelled"):
        raise SeasonStateError(f"Tournament {tournament_id} is not cancelled; nothing to restore")

    cancellation_reason = str(target.get("cancellation_reason") or "")
    target.pop("cancelled", None)
    target.pop("cancellation_reason", None)

    host_club = str(target.get("host_club") or "")
    arena = str(target.get("arena") or "")
    return {
        "tournament_id": tournament_id,
        "reason": reason,
        "previous_cancellation_reason": cancellation_reason,
        "date": target.get("date"),
        "age_group": target.get("age_group"),
        "notifications_required": [
            f"Inform host club {host_club!r} (arena {arena!r}) that the "
            f"{target.get('date')} {target.get('age_group')} tournament {tournament_id} is "
            "reinstated and the ice is needed again; previously released guest "
            "reservations are not revived.",
        ],
    }
