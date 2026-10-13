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
    decode_layout,
    detect_layout,
    encode_layout,
)

# The migration is part of the persistence boundary and must hold the same season
# lock and unguarded writer as the store, so it uses their internal primitives.
from tournament_scheduler.infrastructure.canonical_season_store import (
    DEFAULT_SEASON_ROOT,
    CanonicalSeasonStore,
    _load_export_context_unlocked,
    _load_payloads_unlocked,
    _season_directory_lock,
    _write_season_state_locked,
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

    The whole migration runs under the season directory lock, the same lock every
    canonical reader and writer takes. Inside it the monolithic facts are read,
    the partitioned bytes are decoded in memory and must reproduce the same facts
    and revision, and only then is the partitioned layout installed. The installed
    layout is verified by decoding it from disk. If that verification raises or
    disagrees, the monolithic layout is restored from the facts read under the lock.
    The restore writes without reading the installed layout, so an unreadable
    install is still rolled back. Returns a report; ``migrated`` is ``False`` when
    the season is already partitioned.
    """

    directory = CanonicalSeasonStore(root).directory(season)
    with _season_directory_lock(directory):
        previous_layout = detect_layout(directory)
        if previous_layout == LAYOUT_PARTITIONED:
            return {"season": season, "layout": LAYOUT_PARTITIONED, "migrated": False}

        schedule, decisions = _load_payloads_unlocked(season, root=root)
        export_context = _load_export_context_unlocked(season, root=root)
        revision = canonical_state_revision(schedule, decisions)

        # Prove the staged layout before it can become authoritative.
        staged = encode_layout(season, schedule, decisions, LAYOUT_PARTITIONED)
        staged_schedule, staged_decisions = decode_files(staged, season)
        if not _same_facts(staged_schedule, staged_decisions, schedule, decisions, revision):
            raise SeasonStateError(f"Partitioned encoding of {season} does not reproduce its facts; nothing was installed")

        _write_season_state_locked(
            directory, schedule, decisions, require_absent=False, export_context=export_context, layout=LAYOUT_PARTITIONED
        )

        try:
            installed_schedule, installed_decisions = decode_layout(directory, season)
            facts_unchanged = _same_facts(installed_schedule, installed_decisions, schedule, decisions, revision)
            failure = "changed effective canonical facts"
        except Exception as exc:  # any failure of the installed layout must roll back
            facts_unchanged = False
            failure = f"could not be loaded ({exc})"
        if not facts_unchanged:
            # Restore from the facts read under the lock; no read of the installed layout.
            _write_season_state_locked(
                directory, schedule, decisions, require_absent=False, export_context=export_context, layout=LAYOUT_MONOLITHIC
            )
            raise SeasonStateError(f"Partitioned migration of {season} {failure}; the monolithic layout was restored")
    return {
        "season": season,
        "layout": LAYOUT_PARTITIONED,
        "previous_layout": previous_layout,
        "migrated": True,
        "canonical_state_revision": revision,
    }


__all__ = ["migrate_season_to_partitioned"]
