"""Explicit migration of one canonical season to the partitioned layout.

Ordinary mutations keep a season's current layout. Only this repository-owned
path changes it. The migration reads the monolithic aggregate, writes the same
logical payloads as partitions through the store's compare-and-swap swap, and
then proves the effective facts and the canonical revision are unchanged. If that
proof fails, it restores the monolithic layout before reporting the error.
"""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.infrastructure.canonical_season_layout import (
    LAYOUT_MONOLITHIC,
    LAYOUT_PARTITIONED,
    SeasonStateError,
    decode_files,
    encode_layout,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    DEFAULT_SEASON_ROOT,
    CanonicalSeasonStore,
)
from tournament_scheduler.season.state import canonical_state_revision


def _same_facts(
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    expected_schedule: Mapping[str, Any],
    expected_decisions: Mapping[str, Any],
    revision: str,
) -> bool:
    return (
        dict(schedule) == dict(expected_schedule)
        and dict(decisions) == dict(expected_decisions)
        and canonical_state_revision(schedule, decisions) == revision
    )


def migrate_season_to_partitioned(
    season: str,
    *,
    root: Any = DEFAULT_SEASON_ROOT,
) -> dict[str, Any]:
    """Convert a monolithic season to the partitioned layout without changing its facts.

    The partitioned bytes are decoded in memory and must reproduce the same facts
    and revision before anything is installed. Installation is a revision-guarded
    swap. If the installed layout then fails to load or to match, the monolithic
    layout is restored under the same guard and the error is raised. Returns a
    report; ``migrated`` is ``False`` when the season is already partitioned.
    """

    store = CanonicalSeasonStore(root)
    previous_layout = store.layout(season)
    if previous_layout == LAYOUT_PARTITIONED:
        return {"season": season, "layout": LAYOUT_PARTITIONED, "migrated": False}

    snapshot = store.load(season)
    revision = canonical_state_revision(snapshot.schedule, snapshot.decisions)

    # Prove the staged layout before it can become authoritative.
    staged = encode_layout(season, snapshot.schedule, snapshot.decisions, LAYOUT_PARTITIONED)
    staged_schedule, staged_decisions = decode_files(staged, season)
    if not _same_facts(staged_schedule, staged_decisions, snapshot.schedule, snapshot.decisions, revision):
        raise SeasonStateError(f"Partitioned encoding of {season} does not reproduce its facts; nothing was installed")

    store.write(snapshot, expected_revision=revision, layout=LAYOUT_PARTITIONED)

    try:
        migrated = store.load(season)
        facts_unchanged = _same_facts(
            migrated.schedule, migrated.decisions, snapshot.schedule, snapshot.decisions, revision
        ) and migrated.export_context == snapshot.export_context
        failure = "" if facts_unchanged else "changed effective canonical facts"
    except Exception as exc:  # any failure of the installed layout must roll back
        facts_unchanged = False
        failure = f"could not be loaded ({exc})"
    if not facts_unchanged:
        # Restore the original layout under the same revision guard before failing.
        store.write(snapshot, expected_revision=revision, layout=LAYOUT_MONOLITHIC)
        raise SeasonStateError(
            f"Partitioned migration of {season} {failure}; the monolithic layout was restored"
        )
    return {
        "season": season,
        "layout": LAYOUT_PARTITIONED,
        "previous_layout": previous_layout,
        "migrated": True,
        "canonical_state_revision": revision,
    }


__all__ = ["migrate_season_to_partitioned"]
