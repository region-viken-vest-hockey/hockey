"""Normalized export projection for calendar booking assessments.

The read-only assessment intentionally considers every same-host calendar event
for every hosted tournament.  That denormalized matrix is useful while making a
booking judgment, but it is not a suitable durable Stage 4 artifact.  This
module stores event metadata once, retains full non-context relation evidence,
and represents repeated ``season_context_only`` relations as compact host-club
scopes plus per-tournament summaries.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping

from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256


BOOKING_RECONCILIATION_EXPORT_SCHEMA_VERSION = 2

_EVENT_METADATA_KEYS = frozenset(
    {
        "availability",
        "club",
        "date",
        "end",
        "start",
        "title",
    }
)


def _event_fingerprint(row: Mapping[str, Any]) -> str:
    return str(row.get("event_fingerprint") or row.get("fingerprint") or "")


def _event_record(row: Mapping[str, Any], fingerprint: str) -> dict[str, Any]:
    """Return one event-table row without its denormalized inverse relations."""

    record = {
        str(key): value
        for key, value in row.items()
        if key not in {"candidate_tournaments", "fingerprint", "event_fingerprint"}
    }
    record["event_fingerprint"] = fingerprint
    return record


def _relation_record(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Return relation evidence while referring to event metadata by fingerprint."""

    return {
        str(key): value
        for key, value in candidate.items()
        if key not in _EVENT_METADATA_KEYS
    }


def _add_or_validate_event(
    events: dict[str, dict[str, Any]],
    *,
    fingerprint: str,
    row: Mapping[str, Any],
) -> None:
    if not fingerprint:
        raise ValueError("booking reconciliation event is missing a stable fingerprint")
    record = _event_record(row, fingerprint)
    existing = events.get(fingerprint)
    if existing is None:
        events[fingerprint] = record
        return

    # A candidate can provide a sparse fallback record when a test or historical
    # assessment omitted the matching event row.  Merge only previously absent
    # values; contradictory event metadata would make fingerprint references
    # unsafe and therefore fails closed.
    for key, value in record.items():
        if key not in existing or existing[key] in (None, ""):
            existing[key] = value
        elif value not in (None, "") and existing[key] != value:
            raise ValueError(
                f"booking reconciliation event {fingerprint} has conflicting {key} metadata"
            )


def normalize_booking_reconciliation(
    assessment: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the deterministic, git-suitable Stage 4 evidence projection.

    The source assessment remains the canonical matching-semantic result.  The
    returned schema is an export representation only:

    * ``events`` is a table keyed by stable event fingerprint;
    * tournament ``candidate_relations`` retain every non-context relation and
      its complete evidence/counterevidence, but no event metadata;
    * repeated context-only pairs are encoded by host-club scopes.  Within a
      scope, every event applies to every tournament unless that pair has an
      explicit candidate relation or appears in ``excluded_pairs``.
    """

    event_table: dict[str, dict[str, Any]] = {}
    for raw_event in assessment.get("events") or []:
        if not isinstance(raw_event, Mapping):
            continue
        fingerprint = _event_fingerprint(raw_event)
        _add_or_validate_event(event_table, fingerprint=fingerprint, row=raw_event)

    tournaments: list[dict[str, Any]] = []
    tournament_ids_by_club: dict[str, set[str]] = defaultdict(set)
    context_tournaments_by_club_event: dict[tuple[str, str], set[str]] = defaultdict(set)
    explicit_tournaments_by_club_event: dict[tuple[str, str], set[str]] = defaultdict(set)

    for raw_tournament in assessment.get("tournaments") or []:
        if not isinstance(raw_tournament, Mapping):
            continue
        tournament_id = str(raw_tournament.get("tournament_id") or "")
        host_club = str(raw_tournament.get("host_club") or "")
        tournament_ids_by_club[host_club].add(tournament_id)

        relations: list[dict[str, Any]] = []
        context_relation_counts: Counter[str] = Counter()
        context_counterevidence_counts: Counter[str] = Counter()
        context_count = 0
        for candidate in raw_tournament.get("candidates") or []:
            if not isinstance(candidate, Mapping):
                continue
            fingerprint = _event_fingerprint(candidate)
            _add_or_validate_event(
                event_table,
                fingerprint=fingerprint,
                row={key: candidate.get(key) for key in _EVENT_METADATA_KEYS},
            )
            key = (host_club, fingerprint)
            if candidate.get("season_context_only"):
                context_count += 1
                context_tournaments_by_club_event[key].add(tournament_id)
                context_relation_counts[str(candidate.get("relation") or "unknown")] += 1
                context_counterevidence_counts.update(
                    str(reason) for reason in candidate.get("counterevidence") or []
                )
                continue
            explicit_tournaments_by_club_event[key].add(tournament_id)
            relations.append(_relation_record(candidate))

        row = {
            str(key): value
            for key, value in raw_tournament.items()
            if key not in {"candidates", "candidate_count"}
        }
        row["candidate_relation_count"] = len(relations)
        row["candidate_relations"] = relations
        row["season_context"] = {
            "group": host_club,
            "event_count": context_count,
            "relation_counts": dict(sorted(context_relation_counts.items())),
            "counterevidence_counts": dict(
                sorted(context_counterevidence_counts.items())
            ),
        }
        tournaments.append(row)

    context_groups: list[dict[str, Any]] = []
    for club in sorted(tournament_ids_by_club):
        tournament_ids = sorted(tournament_ids_by_club[club])
        event_fingerprints = sorted(
            fingerprint
            for candidate_club, fingerprint in context_tournaments_by_club_event
            if candidate_club == club
        )
        if not event_fingerprints:
            continue
        excluded_pairs: list[dict[str, Any]] = []
        all_tournament_ids = set(tournament_ids)
        for fingerprint in event_fingerprints:
            actual_context = context_tournaments_by_club_event[(club, fingerprint)]
            explicit = explicit_tournaments_by_club_event.get((club, fingerprint), set())
            excluded = sorted(all_tournament_ids - actual_context - explicit)
            if excluded:
                excluded_pairs.append(
                    {
                        "event_fingerprint": fingerprint,
                        "tournament_ids": excluded,
                    }
                )
        context_groups.append(
            {
                "group": club,
                "host_club": club,
                "tournament_ids": tournament_ids,
                "event_fingerprints": event_fingerprints,
                "excluded_pairs": excluded_pairs,
            }
        )

    projection = {
        str(key): value
        for key, value in assessment.items()
        if key not in {"events", "schema_version", "summary", "tournaments"}
    }
    projection.update(
        {
            "schema_version": BOOKING_RECONCILIATION_EXPORT_SCHEMA_VERSION,
            "assessment_schema_version": assessment.get("schema_version"),
            "summary": assessment.get("summary") or assessment.get("counts") or {},
            "events": dict(sorted(event_table.items())),
            "tournaments": tournaments,
            "season_context": {
                "encoding": "same_host_matrix_minus_explicit_relations_and_excluded_pairs",
                "groups": context_groups,
            },
        }
    )
    projection["artifact_fingerprint"] = stable_payload_sha256(projection)
    return projection
