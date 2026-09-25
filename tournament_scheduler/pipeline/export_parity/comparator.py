"""Pure comparison of two normalized artifact projections.

Comparison is keyed by stable tournament id (never row order or a
date+host+age tuple, because two legitimate same-day/same-host/same-age
tournaments exist). The comparator never infers a status: it only reports the
facts each artifact actually carries.
"""

from __future__ import annotations

from typing import Any

from .records import (
    STATUS_FIELDS,
    ArtifactProjection,
    TournamentRecord,
    normalize_participant_keys,
    normalize_participants,
    normalize_text,
    participant_label,
)


def _comparable_fields(a: ArtifactProjection, b: ArtifactProjection) -> tuple[list[str], list[str]]:
    """Return (fields to compare, fields that are uncheckable).

    A field is uncheckable when *both* artifacts have records but one format
    cannot carry it at all (declared unsupported), or when either artifact
    carries no value for it anywhere while the other does -- the latter is a
    one-sided field and must be reported as a mismatch, not skipped, so it is
    only declared uncheckable when the format genuinely lacks the column.
    """
    fields = ["date", "start_time", "end_time", "arena", "host_club", "age_group", "participants", "games", "cancelled"]
    uncheckable: list[str] = []
    a_unsupported = set(a.unsupported_fields)
    b_unsupported = set(b.unsupported_fields)
    for name in STATUS_FIELDS:
        if name in a_unsupported or name in b_unsupported:
            uncheckable.append(name)
        else:
            fields.append(name)
    return fields, uncheckable


def _values_differ(left: Any, right: Any) -> bool:
    if isinstance(left, tuple) or isinstance(right, tuple):
        return tuple(left) != tuple(right)
    return left != right


def compare_projections(
    primary: ArtifactProjection,
    secondary: ArtifactProjection,
) -> dict[str, Any]:
    """Compare two readable projections, returning a bounded fact report."""

    fields, uncheckable = _comparable_fields(primary, secondary)
    if "tournament_id" in primary.unsupported_fields or "tournament_id" in secondary.unsupported_fields:
        uncheckable.append("tournament_id")

    primary_by_id = primary.records_by_id()
    secondary_by_id = secondary.records_by_id()
    missing_ids = sorted(set(primary_by_id) - set(secondary_by_id))
    extra_ids = sorted(set(secondary_by_id) - set(primary_by_id))

    mismatches: list[dict[str, Any]] = []
    for tournament_id in sorted(set(primary_by_id) & set(secondary_by_id)):
        left = primary_by_id[tournament_id]
        right = secondary_by_id[tournament_id]
        for name in fields:
            left_value = left.field(name)
            right_value = right.field(name)
            if _values_differ(left_value, right_value):
                mismatches.append(
                    {
                        "tournament_id": tournament_id,
                        "field": name,
                        primary.kind: _render(left_value),
                        secondary.kind: _render(right_value),
                    }
                )
    return {
        "compared_fields": fields,
        "uncheckable_fields": sorted(set(uncheckable)),
        "missing_ids": missing_ids,
        "extra_ids": extra_ids,
        "duplicate_ids": {
            primary.kind: primary.duplicate_ids(),
            secondary.kind: secondary.duplicate_ids(),
        },
        "mismatches": mismatches,
        "record_count": {primary.kind: len(primary.records), secondary.kind: len(secondary.records)},
    }


def _render(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    return value


# Canonical operational facts a frozen projection carries and every artifact
# pair should be able to confirm. ``participants`` is handled separately because
# only one format may carry the full ``club|label|age`` identity, while both
# carry labels. ``guest_slots`` is validated from the representable summary.
_PROJECTION_FIELDS: tuple[str, ...] = (
    "date",
    "start_time",
    "end_time",
    "arena",
    "host_club",
    "age_group",
    "cancelled",
    "cancellation_reason",
)


def _projection_value(entry: dict[str, Any], field: str) -> Any:
    value = entry.get(field)
    if isinstance(value, bool):
        return value
    if field in {"date", "start_time", "end_time"}:
        return normalize_text(value)
    return normalize_text(value) if isinstance(value, str) else value


def _record_value(record: TournamentRecord, field: str) -> Any:
    value = record.field(field)
    if isinstance(value, bool):
        return value
    return normalize_text(value)


def _duration_minutes(start_time: Any, end_time: Any) -> int | None:
    start = normalize_text(start_time)
    end = normalize_text(end_time)
    if len(start) < 4 or len(end) < 4:
        return None
    try:
        start_h, start_m = (int(part) for part in start[:5].split(":"))
        end_h, end_m = (int(part) for part in end[:5].split(":"))
    except (TypeError, ValueError):
        return None
    minutes = (end_h * 60 + end_m) - (start_h * 60 + start_m)
    return minutes if minutes > 0 else None


def _projection_participants(entry: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(labels, full_identities)`` from a projection participant list."""

    keys = entry.get("participants") or []
    labels = normalize_participants(participant_label(key) for key in keys)
    return labels, normalize_participant_keys(keys)


def _projection_guest_summary(entry: dict[str, Any]) -> tuple[int, ...]:
    slots = entry.get("guest_slots") or []
    open_count = 0
    filled = 0
    released = 0
    for slot in slots:
        status = normalize_text((slot or {}).get("status")) if isinstance(slot, dict) else ""
        if status == "open":
            open_count += 1
        elif status == "filled":
            filled += 1
        elif status == "released":
            released += 1
    return (open_count, filled, open_count + filled, released)


def records_against_projection(
    artifact: ArtifactProjection,
    projection: dict[str, dict[str, Any]],
    *,
    kind: str,
) -> dict[str, list[dict[str, Any]]]:
    """Compare every representable canonical operational fact in *artifact*.

    Placement/interval/cancellation facts and participant identities are
    compared explicitly. A participant identity is only comparable when the
    format carries it (HTML does, XLSX labels alone do not); the other format is
    expected to confirm it. A projected fact no artifact can represent is
    reported as uncheckable, never silently skipped.
    """

    records_by_id = artifact.records_by_id()
    mismatches: list[dict[str, Any]] = []
    uncheckable: list[dict[str, Any]] = []
    projection_ids = set(projection)
    for tournament_id in sorted(projection_ids - set(records_by_id)):
        mismatches.append({"tournament_id": tournament_id, "field": "id", kind: "missing", "projection": "present"})
    for tournament_id in sorted(set(records_by_id) - projection_ids):
        mismatches.append({"tournament_id": tournament_id, "field": "id", kind: "extra", "projection": "absent"})
    for tournament_id in sorted(projection_ids & set(records_by_id)):
        record = records_by_id[tournament_id]
        entry = projection[tournament_id]
        for field in _PROJECTION_FIELDS:
            if field not in entry:
                continue
            # A non-strict diagnostic projection leaves an unknown occupied
            # interval empty/zero; that is "not carried", not a real empty
            # value to compare against.
            if field == "end_time" and not normalize_text(entry.get("end_time")):
                continue
            expected = _projection_value(entry, field)
            actual = _record_value(record, field)
            if actual != expected:
                mismatches.append(
                    {
                        "tournament_id": tournament_id,
                        "field": field,
                        kind: "projection_mismatch",
                        "projection": expected,
                        "artifact": actual,
                    }
                )
        if "duration_minutes" in entry and entry.get("duration_minutes") not in (None, ""):
            try:
                expected_duration = int(entry.get("duration_minutes"))
            except (TypeError, ValueError):
                expected_duration = None
            if expected_duration is not None and expected_duration <= 0:
                expected_duration = None
            derived = _duration_minutes(record.start_time, record.end_time)
            if expected_duration is not None and derived is not None and derived != expected_duration:
                mismatches.append(
                    {
                        "tournament_id": tournament_id,
                        "field": "duration_minutes",
                        kind: "projection_mismatch",
                        "projection": expected_duration,
                        "artifact": derived,
                    }
                )
        if "participants" in entry:
            expected_labels, expected_ids = _projection_participants(entry)
            if record.participants != expected_labels:
                mismatches.append(
                    {
                        "tournament_id": tournament_id,
                        "field": "participants",
                        kind: "projection_mismatch",
                        "projection": list(expected_labels),
                        "artifact": list(record.participants),
                    }
                )
            if record.participant_keys and record.participant_keys != expected_ids:
                mismatches.append(
                    {
                        "tournament_id": tournament_id,
                        "field": "participants_identity",
                        kind: "projection_mismatch",
                        "projection": list(expected_ids),
                        "artifact": list(record.participant_keys),
                    }
                )
        expected_guest = _projection_guest_summary(entry)
        if any(expected_guest):
            if record.guest_slots_summary:
                if record.guest_slots_summary != expected_guest:
                    mismatches.append(
                        {
                            "tournament_id": tournament_id,
                            "field": "guest_slots",
                            kind: "projection_mismatch",
                            "projection": list(expected_guest),
                            "artifact": list(record.guest_slots_summary),
                        }
                    )
            elif kind == "html":
                # HTML is the format that carries guest-reservation summaries;
                # a canonical reservation it cannot show is a real gap.
                uncheckable.append(
                    {
                        "tournament_id": tournament_id,
                        "field": "guest_slots",
                        kind: "projection_uncheckable",
                        "projection": list(expected_guest),
                        "artifact": None,
                    }
                )
    return {"mismatches": mismatches, "uncheckable": uncheckable}
