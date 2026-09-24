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
    participant_parts,
    projection_entry,
)

PLACEMENT_FIELDS = ("date", "start_time", "arena", "host_club")


class PublishedMutationHistoryError(RuntimeError):
    """Raised when recorded mutation history is missing replayable provenance."""


_DECISION_ONLY_EVENTS = {
    "add_request_constraint",
    "allow_holiday_date",
    "approve",
    "ban_date",
    "confirm_calendar_booking",
    "disallow_holiday_date",
    "reconcile_calendar_bookings",
    "reconcile_config",
    "refresh_calendar_evidence",
    "release_calendar_booking",
    "release_change_protection",
    "release_request_constraint",
    "reserve_guest_slot",
    "season_baseline_advance",
    "season_baseline_create",
    "season_baseline_replace",
    "unapprove",
    "unban_date",
}


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
    if added and added not in participants:
        participants.append(added)
    entry["participants"] = sorted(participants)


def _remove_participant_label(entry: dict[str, Any] | None, label: str) -> None:
    if entry is None or not label:
        return
    entry["participants"] = sorted(
        key for key in entry.get("participants") or [] if participant_parts(str(key))[1] != label
    )


def _apply_swap(projection: dict[str, dict[str, Any]], swap: Mapping[str, Any]) -> None:
    tournament_a = str(swap.get("tournament_a_id") or "")
    tournament_b = str(swap.get("tournament_b_id") or "")
    age_group = str(swap.get("age_group") or "")
    if not tournament_a or not tournament_b:
        raise PublishedMutationHistoryError("participant swap history is missing tournament ids")
    key_a = _participant_key(swap.get("team_a") or {}, age_group)
    key_b = _participant_key(swap.get("team_b") or {}, age_group)
    if not key_a.strip(PARTICIPANT_SEPARATOR) or not key_b.strip(PARTICIPANT_SEPARATOR):
        raise PublishedMutationHistoryError("participant swap history is missing team identities")
    _replace_participant(projection.get(tournament_a), key_a, key_b)
    _replace_participant(projection.get(tournament_b), key_b, key_a)


def _projection_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    tournament_id = str(record.get("id") or record.get("tournament_id") or "")
    if not tournament_id:
        raise PublishedMutationHistoryError("scoped mutation record is missing tournament id")
    return projection_entry(
        tournament_id,
        {
            "date": str(record.get("date") or ""),
            "start_time": str(record.get("start_time") or ""),
            "arena": str(record.get("arena") or ""),
            "host_club": str(record.get("host_club") or ""),
            "age_group": str(record.get("age_group") or ""),
            "participants": sorted(str(key) for key in record.get("participants") or []),
        },
    )


def _apply_after_records(
    projection: dict[str, dict[str, Any]],
    after_records: Mapping[str, Any],
) -> list[str]:
    applied_ids: list[str] = []
    for tournament_id, record in after_records.items():
        resolved_id = str(tournament_id or "")
        if record is None:
            projection.pop(resolved_id, None)
            applied_ids.append(resolved_id)
            continue
        if not isinstance(record, Mapping):
            raise PublishedMutationHistoryError("scoped mutation after-record is not an object")
        entry = _projection_from_record(record)
        projection[str(entry["id"])] = entry
        applied_ids.append(str(entry["id"]))
    return applied_ids


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
        if kind in _DECISION_ONLY_EVENTS:
            continue
        if kind == "move":
            tournament_id = str(event.get("tournament_id") or "")
            new_placement = details.get("new_placement")
            if not tournament_id or not isinstance(new_placement, Mapping):
                raise PublishedMutationHistoryError("move history is missing tournament id or new placement")
            _set_placement(projection, tournament_id, new_placement)
            applied.append({"event": "move", "tournament_id": tournament_id})
        elif kind == "batch_maintenance":
            for move in details.get("moves") or []:
                if not isinstance(move, Mapping):
                    raise PublishedMutationHistoryError("batch move history entry is not an object")
                tournament_id = str(move.get("tournament_id") or "")
                new_placement = move.get("new_placement")
                if not tournament_id or not isinstance(new_placement, Mapping):
                    raise PublishedMutationHistoryError("batch move history is missing tournament id or new placement")
                _set_placement(projection, tournament_id, new_placement)
                applied.append({"event": "batch_move", "tournament_id": tournament_id})
            for swap in details.get("swaps") or []:
                if not isinstance(swap, Mapping):
                    raise PublishedMutationHistoryError("batch swap history entry is not an object")
                _apply_swap(projection, swap)
                applied.append({"event": "batch_swap"})
            # Cancellation is a tournament-level ``cancelled`` flag, not part of
            # the exported stable-id projection, so it never changes identity.
        elif kind == "normalize_arena_identities":
            for change in details.get("changes") or []:
                if not isinstance(change, Mapping):
                    raise PublishedMutationHistoryError("arena-normalization history entry is not an object")
                tournament_id = str(change.get("tournament_id") or "")
                to_arena = change.get("to")
                entry = projection.get(tournament_id)
                if entry is not None and to_arena is not None:
                    entry["arena"] = str(to_arena)
                    applied.append({"event": "normalize_arena", "tournament_id": tournament_id})
        elif kind == "participant_replacement":
            tournament_id = str(details.get("tournament_id") or event.get("tournament_id") or "")
            age_group = str(details.get("age_group") or "")
            if not tournament_id or not age_group:
                raise PublishedMutationHistoryError("participant replacement history is missing tournament id or age group")
            removed = _participant_key(details.get("removed_team") or {}, age_group)
            added = _participant_key(details.get("added_team") or {}, age_group)
            _replace_participant(projection.get(tournament_id), removed, added)
            applied.append({"event": "participant_replacement", "tournament_id": tournament_id})
        elif kind == "participant_swap":
            _apply_swap(projection, details)
            applied.append({"event": "participant_swap"})
        elif kind == "team_identity_rename":
            mappings = details.get("mappings")
            if not isinstance(mappings, list):
                raise PublishedMutationHistoryError("team rename history is missing mappings")
            for mapping in mappings:
                if not isinstance(mapping, Mapping):
                    raise PublishedMutationHistoryError("team rename history entry is not an object")
                source_key = _participant_key(mapping.get("from") or {}, "")
                target_key = _participant_key(mapping.get("to") or {}, "")
                for entry in projection.values():
                    if source_key in entry.get("participants") or []:
                        _replace_participant(entry, source_key, target_key)
                applied.append({"event": "team_identity_rename"})
        elif kind == "fill_guest_slot":
            tournament_id = str(event.get("tournament_id") or "")
            entry = projection.get(tournament_id)
            external_team = details.get("external_team")
            if not tournament_id or not isinstance(external_team, Mapping):
                raise PublishedMutationHistoryError("guest-fill history is missing tournament id or external team")
            age_group = str((entry or {}).get("age_group") or "")
            _replace_participant(entry, "", _participant_key(external_team, age_group))
            applied.append({"event": "fill_guest_slot", "tournament_id": tournament_id})
        elif kind == "release_guest_slot":
            tournament_id = str(event.get("tournament_id") or "")
            entry = projection.get(tournament_id)
            if not tournament_id:
                raise PublishedMutationHistoryError("guest-release history is missing tournament id")
            _remove_participant_label(entry, str(details.get("removed_guest") or ""))
            replacement = details.get("replacement_team")
            if isinstance(replacement, Mapping) and str(replacement.get("label") or ""):
                age_group = str((entry or {}).get("age_group") or "")
                _replace_participant(entry, "", _participant_key(replacement, age_group))
            applied.append({"event": "release_guest_slot", "tournament_id": tournament_id})
        elif kind == "repair_option_applied":
            after_records = details.get("after_records")
            if not isinstance(after_records, Mapping):
                raise PublishedMutationHistoryError("repair history is missing after_records")
            for tournament_id in _apply_after_records(projection, after_records):
                applied.append({"event": "repair_option_applied", "tournament_id": tournament_id})
        elif kind:
            raise PublishedMutationHistoryError(f"unknown canonical history event {kind!r}")

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

    try:
        reconciled, applied = replay_recorded_mutations(seed, history)
    except PublishedMutationHistoryError as exc:
        return {
            "ok": False,
            "published_tournament_count": len(published_projection),
            "current_tournament_count": len(current_projection),
            "attested_addition_count": len(attested_additions or {}),
            "applied_mutation_count": 0,
            "unexplained_delta": {
                "removed_tournament_ids": [],
                "added_tournament_ids": [],
                "placement_changes": [],
                "participant_changes": [],
                "changed": True,
                "replay_error": str(exc),
            },
        }
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
    "PublishedMutationHistoryError",
    "omission_projection",
    "reconcile_published_baseline",
    "replay_recorded_mutations",
]
