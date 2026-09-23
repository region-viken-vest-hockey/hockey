"""Reconcile a published baseline with recorded canonical mutations.

The invariant owned here is:

    published projection
    + attested additions (publication omissions / post-publication materializations)
    + recorded schedule-mutating canonical history
    = current canonical projection

Only operations that can change the exported stable-id projection are replayed.
Approval, booking evidence, request constraints, banned/holiday dates, calendar
refresh, season baseline and audit metadata are decision-only and never move a
tournament. Pre-publication mutations are safe to replay because a publication
baseline already reflects them, so re-applying the same placement is a no-op.

The comparison fails closed: any unexplained added/removed/moved/roster change
is returned for the caller to refuse, never silently blessed.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from tournament_scheduler.pipeline.export_projection_guard import (
    diff_tournament_projection,
)

from tournament_scheduler.published_baseline import (
    PARTICIPANT_SEPARATOR,
    projection_entry,
)

PLACEMENT_FIELDS = ("date", "start_time", "arena", "host_club")


def _participant_key(team: Mapping[str, Any], fallback_age_group: str) -> str:
    return PARTICIPANT_SEPARATOR.join(
        [
            str(team.get("club") or ""),
            str(team.get("label") or ""),
            str(team.get("age_group") or fallback_age_group),
        ]
    )


def _set_placement(projection: dict[str, dict[str, Any]], tournament_id: str, placement: Mapping[str, Any]) -> None:
    entry = projection.get(tournament_id)
    if entry is None:
        return
    for field in PLACEMENT_FIELDS:
        value = placement.get(field)
        if value is not None:
            entry[field] = str(value)


def _replace_participant(entry: dict[str, Any] | None, removed: str, added: str) -> None:
    if entry is None:
        return
    participants = [key for key in entry.get("participants") or [] if key != removed]
    if added not in participants:
        participants.append(added)
    entry["participants"] = sorted(participants)


def _apply_swap(projection: dict[str, dict[str, Any]], swap: Mapping[str, Any]) -> None:
    tournament_a = str(swap.get("tournament_a_id") or "")
    tournament_b = str(swap.get("tournament_b_id") or "")
    age_group = str(swap.get("age_group") or "")
    key_a = _participant_key(swap.get("team_a") or {}, age_group)
    key_b = _participant_key(swap.get("team_b") or {}, age_group)
    _replace_participant(projection.get(tournament_a), key_a, key_b)
    _replace_participant(projection.get(tournament_b), key_b, key_a)


def replay_recorded_mutations(
    baseline: Mapping[str, Mapping[str, Any]],
    history: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Replay schedule-mutating canonical history onto a published projection."""

    projection: dict[str, dict[str, Any]] = {
        tournament_id: projection_entry(tournament_id, entry)
        for tournament_id, entry in baseline.items()
    }
    applied: list[dict[str, Any]] = []

    for event in history:
        if not isinstance(event, Mapping):
            continue
        kind = str(event.get("event") or "")
        details = event.get("details") if isinstance(event.get("details"), Mapping) else {}
        if kind == "move":
            tournament_id = str(event.get("tournament_id") or "")
            _set_placement(projection, tournament_id, details.get("new_placement") or {})
            applied.append({"event": "move", "tournament_id": tournament_id})
        elif kind == "batch_maintenance":
            for move in details.get("moves") or []:
                if not isinstance(move, Mapping):
                    continue
                tournament_id = str(move.get("tournament_id") or "")
                _set_placement(projection, tournament_id, move.get("new_placement") or {})
                applied.append({"event": "batch_move", "tournament_id": tournament_id})
            for swap in details.get("swaps") or []:
                if isinstance(swap, Mapping):
                    _apply_swap(projection, swap)
                    applied.append({"event": "batch_swap"})
            # Cancellation is a tournament-level ``cancelled`` flag, not part of
            # the exported stable-id projection, so it never changes identity.
        elif kind == "normalize_arena_identities":
            for change in details.get("changes") or []:
                if not isinstance(change, Mapping):
                    continue
                tournament_id = str(change.get("tournament_id") or "")
                to_arena = change.get("to")
                entry = projection.get(tournament_id)
                if entry is not None and to_arena is not None:
                    entry["arena"] = str(to_arena)
                    applied.append({"event": "normalize_arena", "tournament_id": tournament_id})
        elif kind == "participant_replacement":
            tournament_id = str(details.get("tournament_id") or event.get("tournament_id") or "")
            age_group = str(details.get("age_group") or "")
            removed = _participant_key(details.get("removed_team") or {}, age_group)
            added = _participant_key(details.get("added_team") or {}, age_group)
            _replace_participant(projection.get(tournament_id), removed, added)
            applied.append({"event": "participant_replacement", "tournament_id": tournament_id})
        elif kind == "participant_swap":
            _apply_swap(projection, details)
            applied.append({"event": "participant_swap"})
        elif kind == "team_identity_rename":
            for mapping in details.get("mappings") or []:
                if not isinstance(mapping, Mapping):
                    continue
                source_key = _participant_key(mapping.get("from") or {}, "")
                target_key = _participant_key(mapping.get("to") or {}, "")
                for entry in projection.values():
                    if source_key in entry.get("participants") or []:
                        _replace_participant(entry, source_key, target_key)
                applied.append({"event": "team_identity_rename"})

    return projection, applied


def reconcile_published_baseline(
    *,
    published_projection: Mapping[str, Mapping[str, Any]],
    current_projection: Mapping[str, Mapping[str, Any]],
    history: Iterable[Mapping[str, Any]],
    attested_additions: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Reconcile published baseline + attested additions + mutations to canonical.

    ``attested_additions`` are stable-id projections of tournaments that are part
    of the published/canonical truth but were not part of the actual published
    artifact: publication *omissions* and explicitly attested post-publication
    *materializations*. The result is ``ok`` only when the reconciled projection
    equals the current canonical projection exactly.
    """

    seed: dict[str, dict[str, Any]] = {
        tournament_id: projection_entry(tournament_id, entry)
        for tournament_id, entry in published_projection.items()
    }
    for tournament_id, entry in (attested_additions or {}).items():
        seed[str(tournament_id)] = projection_entry(str(tournament_id), entry)

    reconciled, applied = replay_recorded_mutations(seed, history)
    delta = diff_tournament_projection(reconciled, current_projection)
    return {
        "ok": not delta["changed"],
        "published_tournament_count": len(published_projection),
        "current_tournament_count": len(current_projection),
        "attested_addition_count": len(attested_additions or {}),
        "applied_mutation_count": len(applied),
        "unexplained_delta": delta,
    }


def omission_projection(
    publication_canonical_projection: Mapping[str, Mapping[str, Any]],
    published_projection: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return the stable-id projection of publication-omitted tournaments."""

    return {
        tournament_id: projection_entry(tournament_id, entry)
        for tournament_id, entry in publication_canonical_projection.items()
        if tournament_id not in published_projection
    }


__all__ = [
    "omission_projection",
    "reconcile_published_baseline",
    "replay_recorded_mutations",
]
