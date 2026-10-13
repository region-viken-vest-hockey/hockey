"""Target-club selection and merge semantics for calendar evidence refresh."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from tournament_scheduler.booking.calendar_availability import unclassified_intervals
from tournament_scheduler.booking.calendar_bookings import BOOKING_CONFIRMED_BOOKED
from tournament_scheduler.clubs.registry import (
    CLUB_REGISTRY,
    canonicalize_club_name,
    club_for_source_name,
)
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError

from .assessment import _classify_club_calendar_bookings
from .common import _CALENDAR_PROBLEM_KEYS, _calendar_source_summaries
from .source_policy import (
    _source_policy_changes,
    _source_policy_snapshot,
)


def resolve_target_club(requested: str | None) -> str | None:
    target = canonicalize_club_name(requested) if requested else None
    if target and target not in CLUB_REGISTRY:
        raise SeasonStateError(
            f"Unknown club '{requested}'. Known clubs: {', '.join(sorted(CLUB_REGISTRY))}"
        )
    return target


def _policy_entry_club(entry: Mapping[str, Any]) -> str | None:
    club = str(entry.get("club") or "")
    if club:
        return canonicalize_club_name(club)
    return club_for_source_name(str(entry.get("name") or ""))


def policy_for_club(
    policy: Mapping[str, Any] | None,
    club: str,
) -> dict[str, Any] | None:
    if policy is None:
        return None
    scoped = copy.deepcopy(dict(policy))
    scoped["sources"] = [
        copy.deepcopy(dict(entry))
        for entry in policy.get("sources") or []
        if isinstance(entry, Mapping) and _policy_entry_club(entry) == club
    ]
    confirmed = {
        canonicalize_club_name(str(item))
        for item in policy.get("operator_confirmed_available_clubs") or []
    }
    scoped["operator_confirmed_available_clubs"] = [club] if club in confirmed else []
    return scoped


def _merge_source_policy(
    promoted: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    club: str,
) -> dict[str, Any]:
    merged = copy.deepcopy(dict(promoted or current))
    merged.update(
        {
            key: copy.deepcopy(current.get(key))
            for key in ("schema_version", "start_date", "end_date", "age_groups")
        }
    )
    merged["sources"] = [
        copy.deepcopy(dict(entry))
        for entry in (promoted or {}).get("sources") or []
        if isinstance(entry, Mapping) and _policy_entry_club(entry) != club
    ] + [
        copy.deepcopy(dict(entry))
        for entry in current.get("sources") or []
        if isinstance(entry, Mapping) and _policy_entry_club(entry) == club
    ]
    merged["sources"].sort(
        key=lambda item: (
            str(item.get("name") or ""),
            str(item.get("type") or ""),
            str(item.get("url") or ""),
        )
    )
    confirmed = {
        canonicalize_club_name(str(item))
        for item in (promoted or {}).get("operator_confirmed_available_clubs") or []
    }
    current_confirmed = {
        canonicalize_club_name(str(item))
        for item in current.get("operator_confirmed_available_clubs") or []
    }
    confirmed.discard(club)
    if club in current_confirmed:
        confirmed.add(club)
    merged["operator_confirmed_available_clubs"] = sorted(confirmed)
    return merged


def prepare_refresh_config(
    config: dict[str, Any],
    promoted_policy: Mapping[str, Any] | None,
    target_club: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    full_current_policy = _source_policy_snapshot(config)
    if target_club is None:
        return (
            full_current_policy,
            _source_policy_changes(promoted_policy, full_current_policy),
        )
    config["sources"] = [
        copy.deepcopy(source)
        for source in config.get("sources") or []
        if isinstance(source, Mapping)
        and club_for_source_name(str(source.get("name") or "")) == target_club
    ]
    confirmed = {
        canonicalize_club_name(str(item))
        for item in config.get("operator_confirmed_available_clubs") or []
    }
    config["operator_confirmed_available_clubs"] = (
        [target_club] if target_club in confirmed else []
    )
    current_policy = _merge_source_policy(
        promoted_policy, full_current_policy, target_club
    )
    changes = _source_policy_changes(
        policy_for_club(promoted_policy, target_club),
        policy_for_club(full_current_policy, target_club),
    )
    return current_policy, changes


def merge_refresh_problem(
    problem: Mapping[str, Any],
    rebuilt: Mapping[str, Any],
    target_club: str | None,
) -> dict[str, Any]:
    if target_club is None:
        merged = copy.deepcopy(dict(problem))
        for key in _CALENDAR_PROBLEM_KEYS:
            merged[key] = copy.deepcopy(rebuilt.get(key))
        return merged
    merged = copy.deepcopy(dict(problem))
    for key in _CALENDAR_PROBLEM_KEYS:
        if key == "unclassified_calendar_events":
            continue
        existing = copy.deepcopy(problem.get(key))
        fresh = rebuilt.get(key)
        values = dict(existing) if isinstance(existing, Mapping) else {}
        values.pop(target_club, None)
        if isinstance(fresh, Mapping) and target_club in fresh:
            values[target_club] = copy.deepcopy(fresh[target_club])
        merged[key] = values
    merged["unclassified_calendar_events"] = unclassified_intervals(
        merged.get("club_busy_intervals")
    )
    return merged


def merge_refresh_source_evidence(
    *,
    scrape: Mapping[str, Any],
    current_evidence: Mapping[str, Any] | None,
    promoted_policy: Mapping[str, Any] | None,
    target_club: str | None,
    fetched_at: str,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    fresh = _calendar_source_summaries(scrape, fetched_at=fetched_at)
    if target_club is None:
        return fresh, list(scrape.get("blocked") or []), list(scrape.get("empty_sources") or [])
    old_policy = policy_for_club(promoted_policy, target_club)
    replaced_names = {
        str(entry.get("name") or "")
        for entry in (old_policy or {}).get("sources") or []
        if isinstance(entry, Mapping)
    }
    replaced_names.update(str(entry.get("name") or "") for entry in fresh)
    summaries = [
        copy.deepcopy(dict(entry))
        for entry in (current_evidence or {}).get("sources") or []
        if isinstance(entry, Mapping)
        and str(entry.get("name") or "") not in replaced_names
    ] + fresh
    summaries.sort(key=lambda item: (item["name"], item["type"], item["url"]))

    def merge_names(key: str, fresh_values: list[Any]) -> list[str]:
        previous = (current_evidence or {}).get(key) or []
        kept = [str(item) for item in previous if str(item) not in replaced_names]
        return sorted({item for item in [*kept, *(str(value) for value in fresh_values)] if item})

    return (
        summaries,
        merge_names("blocked_sources", list(scrape.get("blocked") or [])),
        merge_names("empty_sources", list(scrape.get("empty_sources") or [])),
    )


def classify_refresh_reconciliation(
    *,
    clubs: tuple[str, ...],
    plan: Mapping[str, Any],
    resolved_problem: Mapping[str, Any],
    decisions: Mapping[str, Any],
    actor: str | None,
    note: str,
    now: str,
    source_revision: str,
) -> tuple[dict[str, Any], list[str]]:
    result: dict[str, Any] = {"clubs": {}}
    for club in clubs:
        hosted = [
            tournament
            for tournament in plan.get("tournaments", []) or []
            if str(tournament.get("host_club") or "") == club
        ]
        if not hosted:
            continue
        rows, _unused_records = _classify_club_calendar_bookings(
            plan=plan,
            resolved_problem=resolved_problem,
            decisions=decisions,
            club=club,
            actor=actor,
            note=note,
            now=now,
            source_revision=source_revision,
        )
        requires_review = [
            row
            for row in rows
            if row["status"] != BOOKING_CONFIRMED_BOOKED
            or row.get("manual_conflict")
            or row.get("source_integrity_concern")
        ]
        result["clubs"][club] = {
            "classified": rows,
            "count": len(rows),
            "requires_review_count": len(requires_review),
        }
    return result, list(result["clubs"])


def refresh_impact_summary(
    *,
    problem: Mapping[str, Any],
    new_problem: Mapping[str, Any],
    target_club: str | None,
    before_fingerprint: str,
    after_fingerprint: str,
    verification_before: Mapping[str, Any],
    verification_after: Mapping[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    old_intervals = (problem.get("club_busy_intervals") or {}).get(target_club) or []
    new_intervals = (new_problem.get("club_busy_intervals") or {}).get(target_club) or []
    return {
        "evidence_summary": {
            "club": target_club,
            "old_calendar_fingerprint": before_fingerprint,
            "new_calendar_fingerprint": after_fingerprint,
            "old_target_event_count": len(old_intervals) if target_club else None,
            "new_target_event_count": len(new_intervals) if target_club else None,
        },
        "verification_impact": {
            "before_ok": bool(verification_before.get("ok")),
            "after_ok": bool(verification_after.get("ok")),
            "before_violation_count": len(verification_before.get("violations") or []),
            "after_violation_count": len(verification_after.get("violations") or []),
        },
        "canonical_state_consequences": {
            "will_commit": not dry_run,
            "will_advance_revision": not dry_run,
            "will_mark_export_stale": not dry_run,
            "will_require_fresh_audit": not dry_run,
            "schedule_placements_unchanged": True,
            "unrelated_club_evidence_preserved": bool(target_club),
        },
    }
