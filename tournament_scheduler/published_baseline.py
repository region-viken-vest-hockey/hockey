"""Published-season baseline identity and sealed-lifecycle state.

After the first successful publication a season is no longer a planning
problem to regenerate; it is an operational schedule that may only evolve
through explicit, audited canonical mutations. This module owns the durable
identity of that transition:

* the explicit lifecycle state (``planning`` -> ``promoted`` ->
  ``published_sealed``, plus an explicit emergency reopen);
* the immutable **published baseline** (the exact stable-id projection that was
  published) and its publication history.

The reconciliation invariant across the baseline and later canonical mutations
is implemented in :mod:`tournament_scheduler.published_mutation_history`. Both
modules are pure and offline: they never read Git history, files or the network.
"""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.pipeline.export_projection_guard import (
    tournament_projection,
)
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

SEASON_LIFECYCLE_KEY = "season_lifecycle"

STATE_PLANNING = "planning"
STATE_PROMOTED = "promoted"
STATE_PUBLISHED_SEALED = "published_sealed"

# Partition separator used by the export projection for participant identity.
PARTICIPANT_SEPARATOR = "\u001f"

PROJECTION_FIELDS = ("date", "start_time", "arena", "host_club", "age_group")


class SeasonSealedError(RuntimeError):
    """Raised when a sealed published season would be globally regenerated."""


class PublishedBaselineError(RuntimeError):
    """Raised when a published baseline cannot be trusted."""


# ---------------------------------------------------------------------------
# Lifecycle state
# ---------------------------------------------------------------------------


def lifecycle_record(decisions: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the durable lifecycle record, or an empty mapping for legacy state."""

    if not isinstance(decisions, Mapping):
        return {}
    record = decisions.get(SEASON_LIFECYCLE_KEY)
    return dict(record) if isinstance(record, Mapping) else {}


def lifecycle_state(decisions: Mapping[str, Any] | None) -> str:
    """Return the effective lifecycle state.

    A promoted season written before this feature has no lifecycle record; it is
    still an operational baseline, so it reports ``promoted`` (never
    ``planning``) rather than being treated as unpromoted state.
    """

    state = str(lifecycle_record(decisions).get("state") or "")
    return state or STATE_PROMOTED


def is_published_sealed(decisions: Mapping[str, Any] | None) -> bool:
    return lifecycle_state(decisions) == STATE_PUBLISHED_SEALED


def assert_season_allows_global_regeneration(
    decisions: Mapping[str, Any] | None,
    *,
    operation: str,
) -> None:
    """Refuse a whole-season regeneration/replacement on a sealed season."""

    if is_published_sealed(decisions):
        raise SeasonSealedError(
            f"Refusing {operation}: season is published and sealed. A published "
            "season is maintained only through explicit canonical operations "
            "(move, swap/replace participant, approve, booking evidence, batch, "
            "banned dates, ...). Use 'season reopen-planning --reason ... "
            "--confirm-break-published-baseline' only for a deliberate, "
            "operator-authorised full restructuring."
        )


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------


def participant_key(team: Mapping[str, Any]) -> str:
    return PARTICIPANT_SEPARATOR.join(
        str(team.get(field) or "") for field in ("club", "label", "age_group")
    )


def participant_parts(key: str) -> tuple[str, str, str]:
    club, _, rest = key.partition(PARTICIPANT_SEPARATOR)
    label, _, age_group = rest.partition(PARTICIPANT_SEPARATOR)
    return club, label, age_group


def projection_entry(tournament_id: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one projection entry into a canonical stable-id record."""

    record: dict[str, Any] = {"id": str(tournament_id)}
    for field in PROJECTION_FIELDS:
        record[field] = str(entry.get(field) or "")
    record["participants"] = sorted(str(key) for key in entry.get("participants") or [])
    return record


def _normalized_projection_tournaments(
    projection: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    tournaments: list[dict[str, Any]] = []
    for tournament_id in sorted(projection):
        entry = projection[tournament_id]
        tournaments.append(
            {
                "tournament_id": str(tournament_id),
                **{field: str(entry.get(field) or "") for field in PROJECTION_FIELDS},
                "participants": sorted(str(key) for key in entry.get("participants") or []),
            }
        )
    return tournaments


def projection_fingerprint(projection: Mapping[str, Mapping[str, Any]]) -> str:
    return stable_payload_sha256(_normalized_projection_tournaments(projection))


def projection_from_canonical_plan(plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Stable-id projection of a canonical plan payload."""

    return tournament_projection(plan)


# ---------------------------------------------------------------------------
# Baseline records
# ---------------------------------------------------------------------------


def build_baseline_record(
    *,
    season: str,
    publication_id: str,
    canonical_revision: str,
    published_at: str,
    projection: Mapping[str, Mapping[str, Any]],
    actor: str | None = None,
    note: str = "",
    migration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one immutable publication baseline record from a real projection."""

    tournaments = _normalized_projection_tournaments(projection)
    record: dict[str, Any] = {
        "publication_id": str(publication_id),
        "season": season,
        "canonical_revision": str(canonical_revision),
        "published_at": str(published_at),
        "projection_fingerprint": stable_payload_sha256(tournaments),
        "tournament_count": len(tournaments),
        "tournaments": tournaments,
        "actor": actor,
        "note": note,
    }
    if migration is not None:
        record["migration"] = dict(migration)
    return record


def baseline_projection(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Reconstruct the stable-id projection stored in a baseline record."""

    projection: dict[str, dict[str, Any]] = {}
    for entry in record.get("tournaments") or []:
        if not isinstance(entry, Mapping):
            continue
        tournament_id = str(entry.get("tournament_id") or "")
        if not tournament_id:
            continue
        projection[tournament_id] = projection_entry(tournament_id, entry)
    return projection


def active_baseline(decisions: Mapping[str, Any] | None) -> dict[str, Any] | None:
    record = lifecycle_record(decisions)
    baseline = record.get("published_baseline")
    return dict(baseline) if isinstance(baseline, Mapping) else None


def publication_history(decisions: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    record = lifecycle_record(decisions)
    history = record.get("publication_history")
    return [dict(entry) for entry in history if isinstance(entry, Mapping)] if isinstance(history, list) else []


__all__ = [
    "PARTICIPANT_SEPARATOR",
    "PROJECTION_FIELDS",
    "PublishedBaselineError",
    "SEASON_LIFECYCLE_KEY",
    "STATE_PLANNING",
    "STATE_PROMOTED",
    "STATE_PUBLISHED_SEALED",
    "SeasonSealedError",
    "active_baseline",
    "assert_season_allows_global_regeneration",
    "baseline_projection",
    "build_baseline_record",
    "is_published_sealed",
    "lifecycle_record",
    "lifecycle_state",
    "participant_key",
    "participant_parts",
    "projection_entry",
    "projection_fingerprint",
    "projection_from_canonical_plan",
    "publication_history",
]
