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
    FULL_OPERATIONAL_PROJECTION_SCHEMA,
    FULL_OPERATIONAL_PROJECTION_VERSION,
    occupied_end_time,
    tournament_projection,
)

from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

LEGACY_BASELINE_BACKFILL = "legacy_published_baseline_backfill"

SEASON_LIFECYCLE_KEY = "season_lifecycle"

STATE_PLANNING = "planning"
STATE_PROMOTED = "promoted"
STATE_PUBLISHED_SEALED = "published_sealed"

# Partition separator used by the export projection for participant identity.
PARTICIPANT_SEPARATOR = "\u001f"

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

    record: dict[str, Any] = {
        "id": str(tournament_id),
        "projection_schema": entry.get("projection_schema"),
        "projection_schema_version": entry.get("projection_schema_version"),
    }
    for field in ("date", "start_time", "arena", "host_club", "age_group", "end_time", "cancellation_reason"):
        record[field] = str(entry.get(field) or "")
    try:
        record["duration_minutes"] = int(entry.get("duration_minutes") or 0)
    except (TypeError, ValueError):
        record["duration_minutes"] = entry.get("duration_minutes")
    record["cancelled"] = bool(entry.get("cancelled", False))
    record["guest_slots"] = [dict(slot) for slot in entry.get("guest_slots") or [] if isinstance(slot, Mapping)]
    record["participants"] = sorted(str(key) for key in entry.get("participants") or [])
    return record


def _normalized_projection_tournaments(
    projection: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    tournaments: list[dict[str, Any]] = []
    for tournament_id in sorted(projection):
        entry = projection[tournament_id]
        record = projection_entry(str(tournament_id), entry)
        record["tournament_id"] = str(tournament_id)
        tournaments.append(record)
    return tournaments


def projection_fingerprint(projection: Mapping[str, Mapping[str, Any]]) -> str:
    return stable_payload_sha256(_normalized_projection_tournaments(projection))


def projection_problem_from_schedule(schedule: Mapping[str, Any] | None) -> dict[str, Any] | None:
    context = (schedule or {}).get("verification_context")
    if not isinstance(context, Mapping):
        return None
    problem = context.get("problem")
    return dict(problem) if isinstance(problem, Mapping) else None


def projection_from_canonical_plan(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Stable-id projection of a canonical plan payload."""

    return tournament_projection(plan, problem)


def projection_from_canonical_schedule(schedule: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Stable-id projection of a durable canonical schedule payload."""

    return projection_from_canonical_plan(
        schedule.get("plan") or {},
        projection_problem_from_schedule(schedule),
    )


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
    publication_evidence: Mapping[str, Any] | None = None,
    previous_publication: Mapping[str, Any] | None = None,
    republish_delta: Mapping[str, Any] | None = None,
    republish_decision_changes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one immutable publication baseline record from a real projection.

    ``publication_evidence`` is the immutable reference to the public artifact
    (Pages run/commit and bundle fingerprint). ``previous_publication`` links a
    replacement publication to the exact version it replaced, and
    ``republish_delta`` records the stable-id schedule delta from that version.
    All three are persisted unchanged in ``decisions.json`` so a later republish
    can prove what it replaced and rollback remains reachable.
    """

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
    if publication_evidence is not None:
        record["publication_evidence"] = dict(publication_evidence)
    if previous_publication is not None:
        record["previous_publication"] = dict(previous_publication)
    if republish_delta is not None:
        record["republish_delta"] = dict(republish_delta)
    if republish_decision_changes is not None:
        record["republish_decision_changes"] = dict(republish_decision_changes)
    return record


def _is_versioned_projection_entry(entry: Mapping[str, Any]) -> bool:
    return (
        entry.get("projection_schema") == FULL_OPERATIONAL_PROJECTION_SCHEMA
        and entry.get("projection_schema_version") == FULL_OPERATIONAL_PROJECTION_VERSION
    )


def entries_require_backfill(entries: Any) -> bool:
    """True when any projection record predates the versioned operational schema."""

    return any(
        isinstance(entry, Mapping) and not _is_versioned_projection_entry(entry)
        for entry in entries or []
    )


def baseline_requires_backfill(record: Mapping[str, Any]) -> bool:
    """True when a baseline record predates the versioned operational projection."""

    return entries_require_backfill(record.get("tournaments"))


def baseline_migration_requires_backfill(record: Mapping[str, Any]) -> bool:
    """True when a baseline's attested omissions/materializations are legacy."""

    migration = record.get("migration") if isinstance(record.get("migration"), Mapping) else {}
    return entries_require_backfill(migration.get("publication_omissions")) or entries_require_backfill(
        migration.get("materializations")
    )


def backfill_projection_entry(
    entry: Mapping[str, Any],
    *,
    authoritative: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Upgrade one legacy projection record with authoritative operational facts.

    The immutable recorded placement/participants are preserved; only the
    operational facts the legacy record never carried (occupied interval,
    cancellation, guest reservations) are supplied from the historically
    authoritative source.
    """

    tournament_id = str(entry.get("tournament_id") or entry.get("id") or "")
    if _is_versioned_projection_entry(entry) or not isinstance(authoritative, Mapping):
        return projection_entry(tournament_id, entry)
    merged = dict(authoritative)
    for field in ("id", "date", "start_time", "arena", "host_club", "age_group", "participants"):
        value = entry.get(field)
        if value not in (None, ""):
            merged[field] = value
    merged["end_time"] = occupied_end_time(merged.get("start_time"), merged.get("duration_minutes"))
    merged["projection_migration"] = LEGACY_BASELINE_BACKFILL
    return projection_entry(tournament_id, merged)


def baseline_projection(
    record: Mapping[str, Any],
    *,
    publication_canonical_projection: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Reconstruct the stable-id projection stored in a baseline record.

    A baseline recorded before the versioned operational projection is
    backfilled from the historically authoritative canonical projection at the
    publication revision. The immutable baseline record is never rewritten; the
    upgrade only supplies the operational facts (occupied interval, cancellation
    and guest reservations) that legacy publication artifacts did not carry.
    """

    projection: dict[str, dict[str, Any]] = {}
    for entry in record.get("tournaments") or []:
        if not isinstance(entry, Mapping):
            continue
        tournament_id = str(entry.get("tournament_id") or "")
        if not tournament_id:
            continue
        projection[tournament_id] = projection_entry(tournament_id, entry)
    if not publication_canonical_projection:
        return projection
    for tournament_id, entry in list(projection.items()):
        if _is_versioned_projection_entry(entry):
            continue
        canonical = publication_canonical_projection.get(tournament_id)
        if not isinstance(canonical, Mapping):
            continue
        merged = dict(canonical)
        # Placement/participants are the actually published facts; the
        # publication-time canonical projection supplies the operational facts
        # the legacy artifact never recorded.
        for field in ("id", "date", "start_time", "arena", "host_club", "age_group", "participants"):
            value = entry.get(field)
            if value not in (None, ""):
                merged[field] = value
        merged["end_time"] = occupied_end_time(merged.get("start_time"), merged.get("duration_minutes"))
        merged["projection_migration"] = LEGACY_BASELINE_BACKFILL
        projection[tournament_id] = projection_entry(tournament_id, merged)
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
    "LEGACY_BASELINE_BACKFILL",
    "PARTICIPANT_SEPARATOR",
    "PublishedBaselineError",
    "SEASON_LIFECYCLE_KEY",
    "STATE_PLANNING",
    "STATE_PROMOTED",
    "STATE_PUBLISHED_SEALED",
    "SeasonSealedError",
    "active_baseline",
    "assert_season_allows_global_regeneration",
    "backfill_projection_entry",
    "baseline_migration_requires_backfill",
    "baseline_projection",
    "baseline_requires_backfill",
    "entries_require_backfill",
    "build_baseline_record",
    "is_published_sealed",
    "lifecycle_record",
    "lifecycle_state",
    "participant_key",
    "participant_parts",
    "projection_entry",
    "projection_fingerprint",
    "projection_from_canonical_plan",
    "projection_from_canonical_schedule",
    "projection_problem_from_schedule",
    "publication_history",
]
