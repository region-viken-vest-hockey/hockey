"""Explicit migration of one canonical season to the partitioned layout.

Ordinary mutations keep a season's current layout. Only this repository-owned
path changes it. The migration reads the monolithic aggregate, writes the same
logical payloads as partitions through the store's compare-and-swap swap, and
then proves the effective facts and the canonical revision are unchanged. If that
proof fails, it restores the monolithic layout before reporting the error.
"""

from __future__ import annotations

from typing import Any

from tournament_scheduler.infrastructure.canonical_season_layout import (
    LAYOUT_MONOLITHIC,
    LAYOUT_PARTITIONED,
    SeasonStateError,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    DEFAULT_SEASON_ROOT,
    CanonicalSeasonStore,
)
from tournament_scheduler.season.state import canonical_state_revision


def migrate_season_to_partitioned(
    season: str,
    *,
    root: Any = DEFAULT_SEASON_ROOT,
) -> dict[str, Any]:
    """Convert a monolithic season to the partitioned layout without changing its facts.

    Returns a report. ``migrated`` is ``False`` when the season is already
    partitioned, so the call is safe to repeat.
    """

    store = CanonicalSeasonStore(root)
    previous_layout = store.layout(season)
    if previous_layout == LAYOUT_PARTITIONED:
        return {"season": season, "layout": LAYOUT_PARTITIONED, "migrated": False}

    snapshot = store.load(season)
    revision = canonical_state_revision(snapshot.schedule, snapshot.decisions)
    store.write(snapshot, expected_revision=revision, layout=LAYOUT_PARTITIONED)

    migrated = store.load(season)
    facts_unchanged = (
        migrated.schedule == snapshot.schedule
        and migrated.decisions == snapshot.decisions
        and migrated.export_context == snapshot.export_context
        and canonical_state_revision(migrated.schedule, migrated.decisions) == revision
    )
    if not facts_unchanged:
        # Restore the original layout under the same revision guard before failing.
        store.write(snapshot, expected_revision=revision, layout=LAYOUT_MONOLITHIC)
        raise SeasonStateError(
            f"Partitioned migration of {season} changed effective canonical facts; "
            "the monolithic layout was restored"
        )
    return {
        "season": season,
        "layout": LAYOUT_PARTITIONED,
        "previous_layout": previous_layout,
        "migrated": True,
        "canonical_state_revision": revision,
    }


__all__ = ["migrate_season_to_partitioned"]
