"""Source-policy snapshots, signatures and drift detection."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from tournament_scheduler.club_registry import CLUB_REGISTRY, club_for_source_name
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256


_SOURCE_POLICY_SCHEMA_VERSION = 1

def _source_policy_entry(source: Mapping[str, Any]) -> dict[str, Any]:
    """Return one source's operator and code-owned policy facts.

    Operator facts (name/type/url) come from ``input.xlsx``; the parser kind,
    trust and event-classification facts come from the code-owned club
    registry so a workbook-only edit can never silently change how a source is
    interpreted.
    """

    name = str(source.get("name") or "").strip()
    club = club_for_source_name(name)
    registry = CLUB_REGISTRY.get(club) if club else None
    entry: dict[str, Any] = {
        "name": name,
        "type": str(source.get("type") or "").strip().lower(),
        "url": str(source.get("url") or "").strip(),
        "club": club,
    }
    if registry is not None:
        entry.update(
            {
                "parser": registry.kind.value,
                "trusted_for_auto_placement": bool(registry.trusted_for_auto_placement),
                "club_controlled_calendar": bool(registry.club_controlled_calendar),
                "location_filter": registry.location_filter,
                "location_exclude_substring": registry.location_exclude_substring,
                "event_classification_rules": [
                    {
                        "pattern": str(rule.pattern),
                        "classification": rule.classification.value,
                        "reason": str(rule.reason or ""),
                    }
                    for rule in registry.event_classification_rules
                ],
            }
        )
    # The broad registry kind above does not describe Stage 2's actual
    # deterministic dispatch; snapshot the effective code-owned strategy so a
    # change to the strategy table is visible as source-policy drift too.
    from tournament_scheduler.pipeline.scraper_strategies import (
        get_deterministic_scraper_type,
        get_strategy,
        needs_llm_agent,
        requires_credentials,
    )

    strategy = get_strategy(club) if club else None
    if strategy is not None:
        entry["engine"] = strategy.engine.value
        entry["deterministic_scraper"] = get_deterministic_scraper_type(strategy)
        entry["requires_credentials"] = requires_credentials(strategy)
        entry["needs_llm_agent"] = needs_llm_agent(strategy)
    return entry

def _source_policy_snapshot(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return the normalized source-policy snapshot for an effective config."""

    entries = [
        _source_policy_entry(source)
        for source in config.get("sources") or []
        if isinstance(source, Mapping)
    ]
    entries.sort(key=lambda item: (item["name"], item["type"], item["url"]))
    return {
        "schema_version": _SOURCE_POLICY_SCHEMA_VERSION,
        "start_date": str(config.get("start_date") or ""),
        "end_date": str(config.get("end_date") or ""),
        "age_groups": sorted(str(group) for group in (config.get("age_groups") or [])),
        "operator_confirmed_available_clubs": sorted(
            str(club) for club in (config.get("operator_confirmed_available_clubs") or [])
        ),
        "sources": entries,
    }

def _source_policy_from_evidence(evidence: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return the promoted source policy recorded on an evidence record.

    A full policy is used verbatim. A season refreshed before source-policy
    persistence still exposes its per-source name/type/url summaries, which are
    enough to detect URL/parser drift on the next refresh.
    """

    if not isinstance(evidence, Mapping):
        return None
    persisted = evidence.get("source_policy")
    # An explicit ``sources`` list is authoritative even when it is empty: a
    # refresh accepted with zero sources must still protect against a later
    # silent addition, so emptiness is not treated as "no policy recorded".
    if isinstance(persisted, Mapping) and isinstance(persisted.get("sources"), list):
        return copy.deepcopy(dict(persisted))
    sources = evidence.get("sources")
    if not isinstance(sources, list) or not sources:
        return None
    entries = [
        {
            "name": str(source.get("name") or ""),
            "type": str(source.get("type") or "").lower(),
            "url": str(source.get("url") or ""),
        }
        for source in sources
        if isinstance(source, Mapping) and str(source.get("name") or "")
    ]
    if not entries:
        return None
    entries.sort(key=lambda item: (item["name"], item["type"], item["url"]))
    return {"schema_version": _SOURCE_POLICY_SCHEMA_VERSION, "sources": entries}

def _source_policy_signature(entry: Mapping[str, Any]) -> str:
    """Return a stable content signature for one source-policy entry."""

    return stable_payload_sha256(entry)

def _source_policy_changes(
    promoted: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return a bounded before/after diff of promoted vs current source policy.

    Only fields actually present on the promoted policy are compared, so a
    legacy evidence record that recorded only name/type/url does not report a
    spurious change merely because the current policy now also carries the
    code-owned parser/trust facts. Sources are compared as a multiset keyed by
    name: two configured rows may share a name, so a surviving row must never
    mask the addition, removal or change of a same-name sibling.
    """

    if not isinstance(promoted, Mapping):
        return []
    changes: list[dict[str, Any]] = []
    for key in ("start_date", "end_date", "age_groups", "operator_confirmed_available_clubs"):
        if key in promoted and promoted.get(key) != current.get(key):
            changes.append({"field": key, "before": promoted.get(key), "after": current.get(key)})

    def _by_name(policy: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for entry in policy.get("sources") or []:
            if isinstance(entry, Mapping) and entry.get("name"):
                grouped.setdefault(str(entry["name"]), []).append(dict(entry))
        return grouped

    promoted_sources = _by_name(promoted)
    current_sources = _by_name(current)
    for name in sorted(set(promoted_sources) | set(current_sources)):
        before_entries = promoted_sources.get(name, [])
        after_entries = current_sources.get(name, [])
        if not before_entries:
            changes.append({"field": "sources", "source": name, "change": "added", "after": after_entries})
        elif not after_entries:
            changes.append({"field": "sources", "source": name, "change": "removed", "before": before_entries})
        elif len(before_entries) == 1 and len(after_entries) == 1:
            before = before_entries[0]
            after = after_entries[0]
            field_changes = {
                key: {"before": before.get(key), "after": after.get(key)}
                for key in before
                if key != "name" and before.get(key) != after.get(key)
            }
            if field_changes:
                changes.append(
                    {"field": "sources", "source": name, "change": "modified", "fields": field_changes}
                )
        else:
            # Project both sides onto the fields the promoted policy actually
            # recorded, so a legacy name/type/url-only duplicate-name policy
            # does not report drift merely because current entries now also
            # carry registry/strategy fields.
            compared_keys = sorted({key for entry in before_entries for key in entry})
            before_signatures = sorted(
                _source_policy_signature({key: entry.get(key) for key in compared_keys})
                for entry in before_entries
            )
            after_signatures = sorted(
                _source_policy_signature({key: entry.get(key) for key in compared_keys})
                for entry in after_entries
            )
            if before_signatures != after_signatures:
                changes.append(
                    {
                        "field": "sources",
                        "source": name,
                        "change": "multiset_changed",
                        "before_count": len(before_entries),
                        "after_count": len(after_entries),
                        "before": before_entries,
                        "after": after_entries,
                    }
                )
    return changes
