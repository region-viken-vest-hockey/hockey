"""Auditable promoted-season configuration semantic reconciliation."""

from __future__ import annotations

import copy
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from tournament_scheduler.canonical_state import canonical_state_revision, schedule_fingerprint
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.occupancy import ROUND_BUFFER_MINUTES
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256
from tournament_scheduler.planning_contract import verify_candidate

from .shared import _append_decision_history, _now_iso, _operator_identity, _resolve_plan_problem


def _current_config_from_input(input_path: str | os.PathLike[str]) -> dict[str, Any]:
    from tournament_scheduler.pipeline import stage1_config
    from tournament_scheduler.pipeline.state import PipelineState

    with tempfile.TemporaryDirectory(prefix="rvv-config-reconcile-") as tmp:
        state = PipelineState(Path(tmp))
        stage1_config.run(input_path, state, strict=True)
        return stage1_config.load_effective_config(state, input_path=input_path)


def _actual_round_count(tournament: Mapping[str, Any], configured_rounds: Mapping[str, Any]) -> int:
    rounds = [int(game.get("round_number") or 0) for game in tournament.get("games") or [] if isinstance(game, Mapping)]
    if rounds:
        return max(rounds)
    age_group = str(tournament.get("age_group") or "")
    try:
        return int(configured_rounds.get(age_group) or 0)
    except (TypeError, ValueError):
        return 0


def _end_time(start_time: Any, minutes: int | None) -> str | None:
    if not start_time or not isinstance(minutes, int) or minutes <= 0:
        return None
    try:
        parsed = datetime.strptime(str(start_time), "%H:%M")
    except ValueError:
        return None
    return (parsed + timedelta(minutes=minutes)).strftime("%H:%M")


def _build_ice_time_reconciliation(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    current_config: Mapping[str, Any],
) -> dict[str, Any]:
    stored_ice = problem.get("ice_time_minutes") or {}
    current_ice = current_config.get("ice_time_minutes") or {}
    configured_rounds = problem.get("rounds_per_tournament") or {}
    if not isinstance(stored_ice, Mapping) or not isinstance(current_ice, Mapping):
        return {"field": "ice_time_minutes", "safe": False, "changes": [], "requires_operator": []}

    by_age: dict[str, list[dict[str, Any]]] = {}
    unchanged: list[dict[str, Any]] = []
    requires_operator: list[dict[str, Any]] = []
    for tournament in plan.get("tournaments") or []:
        if not isinstance(tournament, Mapping):
            continue
        age_group = str(tournament.get("age_group") or "")
        if age_group not in stored_ice or age_group not in current_ice:
            continue
        try:
            old_value = int(stored_ice[age_group])
            current_value = int(current_ice[age_group])
        except (TypeError, ValueError):
            continue
        round_count = _actual_round_count(tournament, configured_rounds)
        legacy_effective = old_value + ROUND_BUFFER_MINUTES * round_count
        entry = {
            "tournament_id": str(tournament.get("id") or ""),
            "age_group": age_group,
            "old_value": old_value,
            "old_semantics": "ice_time_minutes_plus_5_minutes_per_actual_round",
            "round_count": round_count,
            "round_buffer_minutes": ROUND_BUFFER_MINUTES,
            "legacy_effective_occupancy_minutes": legacy_effective,
            "current_config_value": current_value,
            "proposed_migrated_value": legacy_effective,
            "start_time": tournament.get("start_time"),
            "old_end_time_under_current_semantics": _end_time(tournament.get("start_time"), old_value),
            "preserved_end_time_after_migration": _end_time(tournament.get("start_time"), legacy_effective),
            "provenance": "legacy occupancy = persisted ice_time_minutes + 5 * tournament actual round count",
        }
        if old_value == current_value:
            unchanged.append(entry)
        elif legacy_effective == current_value:
            by_age.setdefault(age_group, []).append(entry)
        else:
            entry["reason"] = (
                "persisted value differs from current config, but the old semantic effective occupancy "
                "does not match the current configured value"
            )
            requires_operator.append(entry)

    changes: list[dict[str, Any]] = []
    age_group_changes: dict[str, dict[str, int]] = {}
    for age_group, entries in sorted(by_age.items()):
        proposed_values = {int(entry["proposed_migrated_value"]) for entry in entries}
        if len(proposed_values) != 1:
            for entry in entries:
                rejected = dict(entry)
                rejected["reason"] = (
                    "tournaments in this age group imply different migrated values; "
                    "the promoted planning contract stores one ice_time_minutes value per age group"
                )
                requires_operator.append(rejected)
            continue
        old_values = {int(entry["old_value"]) for entry in entries}
        if len(old_values) != 1:
            for entry in entries:
                rejected = dict(entry)
                rejected["reason"] = "inconsistent persisted ice_time_minutes values for one age group"
                requires_operator.append(rejected)
            continue
        migrated_value = proposed_values.pop()
        old_value = old_values.pop()
        changes.extend(entries)
        age_group_changes[age_group] = {"old_value": old_value, "migrated_value": migrated_value}

    return {
        "field": "ice_time_minutes",
        "safe": not requires_operator,
        "age_group_changes": age_group_changes,
        "changes": sorted(changes, key=lambda item: (item["age_group"], item["tournament_id"])),
        "unchanged": sorted(unchanged, key=lambda item: (item["age_group"], item["tournament_id"])),
        "requires_operator": sorted(requires_operator, key=lambda item: (item["age_group"], item["tournament_id"])),
        "semantic_migration": "preserve_pre_ice_time_contract_effective_occupancy",
    }


def _migration_record_summary(migration: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "field": migration.get("field"),
        "safe": bool(migration.get("safe")),
        "semantic_migration": migration.get("semantic_migration"),
        "age_group_changes": copy.deepcopy(migration.get("age_group_changes") or {}),
        "change_count": len(migration.get("changes") or []),
        "requires_operator_count": len(migration.get("requires_operator") or []),
    }


def reconcile_config(
    service,
    *,
    season: str,
    input_path: str | os.PathLike[str] = "input.xlsx",
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
    current_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Reconcile promoted config-owned facts after a semantic contract change."""

    snapshot = service.load(season)
    schedule = copy.deepcopy(snapshot.schedule)
    decisions = copy.deepcopy(snapshot.decisions)
    plan = copy.deepcopy(schedule.get("plan") or {})
    context = copy.deepcopy(schedule.get("verification_context") or {})
    problem = copy.deepcopy(context.get("problem") or {})
    if not isinstance(problem, dict) or not problem:
        raise SeasonStateError("Canonical season carries no verification-context problem to reconcile")
    current = dict(current_config) if isinstance(current_config, Mapping) else _current_config_from_input(input_path)

    before_problem_fingerprint = stable_payload_sha256(problem)
    before_schedule_fingerprint = schedule_fingerprint(plan)
    before_revision = canonical_state_revision(schedule, decisions)
    ice_reconciliation = _build_ice_time_reconciliation(plan, problem, current)
    safe = bool(ice_reconciliation.get("safe"))
    age_group_changes = dict(ice_reconciliation.get("age_group_changes") or {})

    migrated_problem = copy.deepcopy(problem)
    migrated_ice = dict(migrated_problem.get("ice_time_minutes") or {})
    for age_group, change in age_group_changes.items():
        migrated_ice[age_group] = int(change["migrated_value"])
    migrated_problem["ice_time_minutes"] = migrated_ice
    after_problem_fingerprint = stable_payload_sha256(migrated_problem)

    preview_context = copy.deepcopy(context)
    preview_context["problem"] = migrated_problem
    preview_context["problem_fingerprint"] = after_problem_fingerprint
    preview_context.setdefault("config_reconciliation_history", [])
    persisted_migrations = [_migration_record_summary(ice_reconciliation)]
    reconciliation_record = {
        "schema_version": 1,
        "reconciled_at": _now_iso(),
        "reconciled_by": _operator_identity(actor),
        "note": note or "",
        "input_path": str(input_path),
        "dry_run": bool(dry_run),
        "before_problem_fingerprint": before_problem_fingerprint,
        "after_problem_fingerprint": after_problem_fingerprint,
        "semantic_migrations": persisted_migrations,
    }
    preview_context["config_reconciliation"] = reconciliation_record
    preview_schedule = copy.deepcopy(schedule)
    preview_schedule["verification_context"] = preview_context
    preview_schedule["updated_at"] = reconciliation_record["reconciled_at"]

    resolved_problem = _resolve_plan_problem(preview_schedule, None, decisions)
    verification = verify_candidate(plan, resolved_problem) if resolved_problem else verify_candidate(plan)
    verification_ok = bool(verification.get("ok"))
    changed = bool(age_group_changes) and before_problem_fingerprint != after_problem_fingerprint

    result = {
        "season": season,
        "dry_run": bool(dry_run),
        "changed": changed,
        "safe": safe,
        "schedule_fingerprint": before_schedule_fingerprint,
        "before_problem_fingerprint": before_problem_fingerprint,
        "after_problem_fingerprint": after_problem_fingerprint,
        "previous_canonical_state_revision": before_revision,
        "canonical_state_revision": before_revision,
        "semantic_migrations": [ice_reconciliation],
        "verification_ok": verification_ok,
        "verification_violations": list(verification.get("violations") or []),
        "manual_external_conflict_placements": list(verification.get("manual_external_conflict_placements") or []),
    }
    if not safe:
        result["refused"] = True
        result["refusal_reasons"] = ["configuration differences require explicit operator intent or replanning"]
        if not dry_run:
            raise SeasonStateError("Refusing config reconciliation: configuration differences require operator intent")
        return result
    if not verification_ok:
        result["refused"] = True
        result["refusal_reasons"] = ["migrated promoted season does not pass full hard verification"]
        if not dry_run:
            messages = "; ".join(str(v.get("message")) for v in verification.get("violations") or [])
            raise SeasonStateError("Refusing config reconciliation: migrated season does not verify" + (f": {messages}" if messages else ""))
        return result
    if dry_run or not changed:
        return result

    schedule = preview_schedule
    promoted_from = dict(schedule.get("promoted_from") or {})
    promoted_from["export_stale"] = True
    promoted_from["export_stale_reason"] = "config semantics reconciled"
    promoted_from["export_stale_at"] = reconciliation_record["reconciled_at"]
    schedule["promoted_from"] = promoted_from
    decisions["updated_at"] = reconciliation_record["reconciled_at"]
    decisions["export_state"] = {
        "status": "stale",
        "stale_reason": "config_reconciled",
        "stale_at": reconciliation_record["reconciled_at"],
        "requires_fresh_export": True,
        "requires_fresh_audit": True,
        "problem_fingerprint": after_problem_fingerprint,
    }
    _append_decision_history(
        decisions,
        event="reconcile_config",
        tournament_id="",
        actor=actor,
        now=reconciliation_record["reconciled_at"],
        note=note,
        details={
            "before_problem_fingerprint": before_problem_fingerprint,
            "after_problem_fingerprint": after_problem_fingerprint,
            "semantic_migrations": persisted_migrations,
        },
    )
    committed = service._commit(snapshot.with_schedule(schedule).with_decisions(decisions))
    result["canonical_state_revision"] = canonical_state_revision(committed.schedule, committed.decisions)
    result["revision"] = committed.schedule.get("revision")
    return result
