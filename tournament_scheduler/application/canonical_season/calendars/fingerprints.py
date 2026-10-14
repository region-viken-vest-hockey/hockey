"""Semantic fingerprints for canonical calendar/source evidence.

Fingerprint inputs are deliberately narrower than persisted provenance.  Fetch
and cache metadata explain how evidence was acquired, but only normalized
calendar facts and source-policy/coverage facts define the semantic evidence
identity.  Lists in calendar/source evidence are treated as multisets: source,
pagination, merge, and duplicate-row order must not affect a digest, while
duplicate cardinality remains visible until the normalization owner resolves
it.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any

from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

from .common import _calendar_problem_payload

_FINGERPRINT_SCHEMA_VERSION = 1

def _canonical_unordered(value: Any) -> Any:
    """Return a JSON value with mapping keys and non-semantic list order normalized.

    Calendar evidence collections have set/multiset semantics.  Sorting by the
    complete canonical JSON value also gives deterministic ordering for events
    that share a date/start but differ in title, end, venue, or source identity.
    Duplicates are retained, so this helper does not take ownership of event
    deduplication.
    """

    if isinstance(value, Mapping):
        return {
            str(key): _canonical_unordered(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = [_canonical_unordered(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
            ),
        )
    return copy.deepcopy(value)


def _integrity_payload(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    # ``fingerprint`` is derived, and ``previous_event_count`` describes the
    # comparison run rather than the current source evidence.
    return {
        key: copy.deepcopy(value.get(key))
        for key in (
            "schema_version",
            "source",
            "type",
            "status",
            "trusted_for_negative_claim",
            "coverage_proven",
            "requested_start",
            "requested_end",
            "observed_start",
            "observed_end",
            "event_observed_start",
            "event_observed_end",
            "navigation_complete",
            "event_count",
            "reasons",
            "exceptions",
        )
        if key in value
    }


def _calendar_semantic_payload(problem: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _calendar_problem_payload(problem)
    intervals = raw.get("club_busy_intervals")
    if isinstance(intervals, Mapping):
        raw["club_busy_intervals"] = {
            str(club): [
                {
                    key: copy.deepcopy(event.get(key))
                    for key in (
                        "date",
                        "start",
                        "end",
                        "kind",
                        "availability",
                        "calendar_event",
                        "reason",
                        "resources",
                    )
                    if key in event
                }
                for event in events
                if isinstance(event, Mapping)
            ]
            for club, events in intervals.items()
            if isinstance(events, Sequence) and not isinstance(events, (str, bytes, bytearray))
        }
    details = raw.get("club_source_integrity_details")
    if isinstance(details, Mapping):
        raw["club_source_integrity_details"] = {
            str(club): _integrity_payload(value) for club, value in details.items()
        }
    # Unclassified rows are a derived subset of normalized busy intervals.
    # Project them through the same explicit event contract.
    unclassified = raw.get("unclassified_calendar_events")
    if isinstance(unclassified, Sequence) and not isinstance(unclassified, (str, bytes, bytearray)):
        raw["unclassified_calendar_events"] = [
            {
                key: copy.deepcopy(event.get(key))
                for key in (
                    "club",
                    "date",
                    "start",
                    "end",
                    "kind",
                    "availability",
                    "calendar_event",
                    "reason",
                    "resources",
                )
                if key in event
            }
            for event in unclassified
            if isinstance(event, Mapping)
        ]
    return raw


def _source_semantic_payload(source: Mapping[str, Any]) -> dict[str, Any]:
    events = []
    for event in source.get("events") or []:
        if not isinstance(event, Mapping):
            continue
        events.append(
            {
                key: copy.deepcopy(event.get(key))
                for key in (
                    "date",
                    "name",
                    "title",
                    "datetime",
                    "start",
                    "end",
                    "duration_hours",
                    "duration_minutes",
                    "all_day",
                    "location",
                    "arena",
                    "resources",
                    "order_id",
                    "resource_label_classification",
                )
                if key in event
            }
        )
    return {
        key: copy.deepcopy(source.get(key))
        for key in (
            "name",
            "url",
            "type",
            "event_count",
            "blocked",
            "block_reason",
            "skipped",
            "skip_reason",
            "scraper_error",
            "coverage",
        )
        if key in source
    } | {"events": events, "integrity": _integrity_payload(source.get("integrity"))}


def calendar_evidence_fingerprint(
    problem: Mapping[str, Any] | None,
    source_policy: Mapping[str, Any] | None,
) -> str:
    """Fingerprint normalized calendar facts plus their effective source policy.

    The source policy is intentionally order-sensitive below nested policy
    fields such as first-match event-classification rules.  Its owning builder
    already sorts unordered source/club collections.  Calendar evidence lists,
    by contrast, are canonicalized as multisets here.
    """

    payload = {
        "schema_version": _FINGERPRINT_SCHEMA_VERSION,
        "calendar": _canonical_unordered(_calendar_semantic_payload(problem)),
        "source_policy": copy.deepcopy(dict(source_policy)) if source_policy is not None else None,
    }
    return stable_payload_sha256(payload)


def source_evidence_fingerprint(source: Mapping[str, Any]) -> str:
    """Fingerprint one source result without volatile fetch/cache provenance."""

    return stable_payload_sha256(
        {
            "schema_version": _FINGERPRINT_SCHEMA_VERSION,
            "source": _canonical_unordered(_source_semantic_payload(source)),
        }
    )


def stage2_evidence_fingerprint(scrape: Mapping[str, Any]) -> str:
    """Fingerprint Stage 2's semantic evidence rather than its run envelope."""

    payload = {
        "schema_version": _FINGERPRINT_SCHEMA_VERSION,
        "sources": [
            _canonical_unordered(_source_semantic_payload(source))
            for source in scrape.get("sources") or []
            if isinstance(source, Mapping)
        ],
        "club_calendar_status": scrape.get("club_calendar_status") or {},
        "club_source_integrity": scrape.get("club_source_integrity") or {},
        "club_coverage_proven": scrape.get("club_coverage_proven") or {},
        "blocked_sources": scrape.get("blocked") or [],
        "empty_sources": scrape.get("empty_sources") or [],
        "start_date": scrape.get("start_date"),
        "end_date": scrape.get("end_date"),
    }
    return stable_payload_sha256(_canonical_unordered(payload))
