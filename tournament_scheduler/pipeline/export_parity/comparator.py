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
)


def _comparable_fields(a: ArtifactProjection, b: ArtifactProjection) -> tuple[list[str], list[str]]:
    """Return (fields to compare, fields that are uncheckable).

    A field is uncheckable when *both* artifacts have records but one format
    cannot carry it at all (declared unsupported), or when either artifact
    carries no value for it anywhere while the other does -- the latter is a
    one-sided field and must be reported as a mismatch, not skipped, so it is
    only declared uncheckable when the format genuinely lacks the column.
    """
    fields = ["date", "start_time", "end_time", "arena", "host_club", "age_group", "participants", "cancelled"]
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


def records_against_projection(
    records_by_id: dict[str, TournamentRecord],
    projection: dict[str, dict[str, Any]],
    *,
    kind: str,
) -> list[dict[str, Any]]:
    """Compare projected schedule placement fields against a frozen projection.

    Participants are intentionally excluded here: the canonical projection
    carries ``club|label|age`` keys, while the artifacts carry labels; the
    authoritative participant comparison already happens artifact-to-artifact.
    """
    mismatches: list[dict[str, Any]] = []
    projection_ids = set(projection)
    for tournament_id in sorted(projection_ids - set(records_by_id)):
        mismatches.append({"tournament_id": tournament_id, "field": "id", kind: "missing", "projection": "present"})
    for tournament_id in sorted(set(records_by_id) - projection_ids):
        mismatches.append({"tournament_id": tournament_id, "field": "id", kind: "extra", "projection": "absent"})
    for tournament_id in sorted(projection_ids & set(records_by_id)):
        record = records_by_id[tournament_id]
        entry = projection[tournament_id]
        for field in ("date", "start_time", "arena", "host_club", "age_group"):
            if field not in entry:
                continue
            expected = entry.get(field)
            actual = record.field(field)
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
    return mismatches
