"""Canonical selective-evidence index + queries for the semantic safety-net
audit (issue #356).

The audit must never hand an LLM a season-sized monolithic JSON blob. Instead
:func:`~tournament_scheduler.pipeline.audit_context.build_audit_context`
returns a small, bounded overview that tells the judge *what detailed evidence
exists and how to request it*, and
:func:`~tournament_scheduler.pipeline.audit_context.build_audit_evidence`
returns only the requested subset.

This module is deliberately a pure transformation over the already-assembled
raw evidence dict produced by ``audit_context._assemble_raw_audit_evidence``:
it performs no filesystem/manifest access of its own, re-derives no planning
policy, and never decides PASS/FAIL. It exists so every harness (Pi, Claude,
Codex, ChatGPT, headless cron) consumes one repository-owned query capability
instead of independently parsing the persisted artifacts.
"""

from __future__ import annotations

import json
from typing import Any

from ..host_representation import clubs_represent_same_club

# Explicit, tested upper bound for the *default* audit context payload. The
# bound covers the serialized overview; detailed evidence is fetched
# separately through the query API. Keeping this a committed constant (rather
# than a runtime heuristic) lets a regression test fail loudly if large
# top-level lists creep back into the default context.
AUDIT_CONTEXT_MAX_SERIALIZED_BYTES = 40_000

# How many representative/worst examples a bounded overview category carries.
EVIDENCE_OVERVIEW_MAX_EXAMPLES = 5

# How many full records one selective query returns before reporting
# truncation. The complete subset remains available with ``--limit``.
EVIDENCE_QUERY_MAX_RECORDS = 200

# A hard ceiling on the serialized bytes one query response may return, so a
# single broad selector cannot re-introduce an unbounded payload.
EVIDENCE_QUERY_MAX_SERIALIZED_BYTES = 500_000

# Bounds for a *batch* of evidence queries (the headless/Pi expansion round),
# so several selectors cannot together re-create a season-sized prompt.
EVIDENCE_EXPANSION_MAX_BYTES = 60_000
EVIDENCE_EXPANSION_QUERY_LIMIT = 25

_SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2, "info": 3}

# Canonical evidence categories, aligned to the finding lists/finding types
# the pipeline already persists rather than a parallel taxonomy. ``checklist
# item`` is the operator-checklist item the category primarily supports;
# ``unresolved`` marks the categories that represent an open/manual finding.
EVIDENCE_CATEGORIES: dict[str, dict[str, Any]] = {
    "participation_shortfalls": {
        "description": "Teams whose final participation count is below target (per-team category/reason).",
        "checklist_item": 1,
        "unresolved": True,
        "severity": "major",
    },
    "club_participation_fairness": {
        "description": "Per club/age-group proportional participation share (target vs actual).",
        "checklist_item": 1,
        "unresolved": False,
        "severity": "info",
    },
    "manual_participation_placements": {
        "description": "Manual participation placements the final verifier surfaced for operator follow-up.",
        "checklist_item": 1,
        "unresolved": True,
        "severity": "major",
    },
    "participation_target_deviations": {
        "description": (
            "Bounded participation-target deviations (over or under) with the deterministic "
            "avoidability classification (avoidable / proven_infeasible / bounded_search_exhausted / "
            "operator_accepted) the controller uses to judge the residual trade-off."
        ),
        "checklist_item": 1,
        "unresolved": True,
        "severity": "major",
    },
    "unresolved_hosting_obligations": {
        "description": "Club x age-group hosting obligations with no placed tournament, including untried reallocation candidates.",
        "checklist_item": 2,
        "unresolved": True,
        "severity": "major",
    },
    "same_age_hosting_repairs": {
        "description": "Same-age hosting-coverage repairs attempted (repaired or rejected-with-reason).",
        "checklist_item": 2,
        "unresolved": False,
        "severity": "info",
    },
    "cross_age_hosting_repairs": {
        "description": "Cross-age hosting-coverage repairs attempted (repaired or rejected-with-reason).",
        "checklist_item": 2,
        "unresolved": False,
        "severity": "info",
    },
    "unresolved_tournament_placements": {
        "description": "Concrete rosters/dates that could not be given a participant-host arena/time.",
        "checklist_item": 2,
        "unresolved": True,
        "severity": "major",
    },
    "unresolved_external_conflicts": {
        "description": "Hosts with an external calendar conflict the plan could not route around.",
        "checklist_item": 4,
        "unresolved": True,
        "severity": "major",
    },
    "manual_external_conflict_placements": {
        "description": "External calendar conflicts the final verifier surfaced for operator follow-up.",
        "checklist_item": 4,
        "unresolved": True,
        "severity": "major",
    },
    "movable_allocations_used": {
        "description": (
            "Tournaments placed in a host-controlled movable_busy interval (e.g. Kongsberg open ice): "
            "feasible subject to the host moving/replacing the listed event."
        ),
        "checklist_item": 4,
        "unresolved": True,
        "severity": "major",
    },
    "calendar_interpretations_used": {
        "description": (
            "Controller-requested inferred movable_busy interpretations recorded on the "
            "candidate (host confirmation required); the source calendar is unchanged."
        ),
        "checklist_item": 4,
        "unresolved": True,
        "severity": "major",
    },
    "tournament_duration": {
        "description": "Per-tournament required duration/end time derived from configured ice time and rounds.",
        "checklist_item": 3,
        "unresolved": False,
        "severity": "info",
    },
    "blocking_byes": {
        "description": "Tournaments with pause/bye teams or an invalid no-bye roster (a hard policy violation).",
        "checklist_item": 3,
        "unresolved": True,
        "severity": "critical",
    },
    "input_constrained_shape": {
        "description": "Tournaments whose odd/bye shape is a legitimate input-constrained adaptation, not a defect.",
        "checklist_item": 3,
        "unresolved": False,
        "severity": "info",
    },
    "underfilled_tournaments": {
        "description": "Tournaments below their configured full-capacity game count.",
        "checklist_item": 4,
        "unresolved": True,
        "severity": "minor",
    },
    "calendar_sources": {
        "description": "Persisted Stage 2 source/calendar health (blocked, empty, cached, warnings).",
        "checklist_item": 4,
        "unresolved": False,
        "severity": "info",
    },
    "host_participation": {
        "description": "Tournaments whose host club has no participating team.",
        "checklist_item": 5,
        "unresolved": True,
        "severity": "major",
    },
    "duplicate_team_days": {
        "description": "Teams scheduled in more than one tournament on the same day.",
        "checklist_item": 6,
        "unresolved": True,
        "severity": "major",
    },
    "same_club_participants": {
        "description": "Tournaments with more than two teams from the same club.",
        "checklist_item": 7,
        "unresolved": False,
        "severity": "minor",
    },
    "same_club_participants_hard_max": {
        "description": "Tournaments above the hard maximum number of teams from one club (an invalid plan).",
        "checklist_item": 7,
        "unresolved": True,
        "severity": "critical",
    },
    "export_consistency": {
        "description": "Per-output-format row/event counts cross-checked against the selected plan.",
        "checklist_item": 8,
        "unresolved": False,
        "severity": "info",
    },
    "verify_violations": {
        "description": "Violations from the deterministic Stage 4 verification result.",
        "checklist_item": 9,
        "unresolved": True,
        "severity": "critical",
    },
    "operator_waivers": {
        "description": "Active operator waivers that authorize a precise exception to a hard rule.",
        "checklist_item": 9,
        "unresolved": False,
        "severity": "info",
    },
    "waived_violations": {
        "description": "Hard-rule violations that passed only because of an operator waiver.",
        "checklist_item": 9,
        "unresolved": False,
        "severity": "minor",
    },
    "stale_approvals": {
        "description": "Per-tournament approvals whose protected fields changed and must be re-approved.",
        "checklist_item": 9,
        "unresolved": True,
        "severity": "major",
    },
}

_SELECTOR_COMMANDS = {
    "item": "operator audit-evidence --item <n>",
    "tournament": "operator audit-evidence --tournament <durable-id>",
    "club": "operator audit-evidence --club <club>",
    "age_group": "operator audit-evidence --age-group <age-group>",
    "category": "operator audit-evidence --category <category>",
    "unresolved": "operator audit-evidence --unresolved [--category <category>]",
}


def _severity_rank(severity: Any) -> int:
    return _SEVERITY_ORDER.get(str(severity), 99)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _record_clubs(row: dict[str, Any]) -> list[str]:
    clubs: set[str] = set()
    for key in ("club", "host_club", "participant_club", "candidate_host"):
        value = row.get(key)
        if isinstance(value, str) and value:
            clubs.add(value)
    for key in ("clubs", "participant_clubs", "candidate_hosts", "participant_teams"):
        for entry in _as_list(row.get(key)):
            if isinstance(entry, str) and entry:
                clubs.add(entry)
            elif isinstance(entry, dict) and entry.get("club"):
                clubs.add(str(entry["club"]))
    return sorted(clubs)


def _record(
    *,
    category: str,
    summary: str,
    detail: Any,
    checklist_item: int | None = None,
    finding_type: str | None = None,
    unresolved: bool | None = None,
    severity: str | None = None,
    tournament_id: Any = None,
    club: Any = None,
    clubs: list[str] | None = None,
    age_group: Any = None,
) -> dict[str, Any]:
    meta = EVIDENCE_CATEGORIES.get(category, {})
    resolved_clubs = {str(value) for value in clubs or [] if value}
    if club:
        resolved_clubs.add(str(club))
    return {
        "category": category,
        "finding_type": finding_type or category,
        "checklist_item": checklist_item if checklist_item is not None else meta.get("checklist_item"),
        "unresolved": bool(unresolved) if unresolved is not None else bool(meta.get("unresolved")),
        "severity": severity or meta.get("severity") or "info",
        "tournament_id": str(tournament_id) if tournament_id else None,
        "club": str(club) if club else None,
        "clubs": sorted(resolved_clubs),
        "age_group": str(age_group) if age_group else None,
        "summary": summary,
        "detail": detail,
    }


def _tournament_label(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("age_group") or "?"),
        str(row.get("date") or "?"),
        str(row.get("arena") or ""),
    ]
    return " ".join(part for part in parts if part)


def _extract_records(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn the assembled raw evidence into a flat list of queryable records."""
    records: list[dict[str, Any]] = []
    facts = raw.get("plan_audit_facts") or {}
    verify = raw.get("deterministic_verify_result") or {}

    for row in _as_list(facts.get("unresolved_participation_shortfalls")):
        if not isinstance(row, dict):
            continue
        records.append(
            _record(
                category="participation_shortfalls",
                finding_type=str(row.get("category") or "participation_under_target"),
                summary=(
                    f"{row.get('label') or row.get('club') or '?'} ({row.get('age_group') or '?'}): "
                    f"{row.get('actual')}/{row.get('target')} — {row.get('reason') or 'under target'}"
                ),
                detail=row,
                tournament_id=row.get("tournament_id") or row.get("tournament"),
                club=row.get("club"),
                clubs=_record_clubs(row),
                age_group=row.get("age_group"),
            )
        )

    for row in _as_list(facts.get("club_participation_fairness")):
        if not isinstance(row, dict):
            continue
        records.append(
            _record(
                category="club_participation_fairness",
                summary=(
                    f"{row.get('club') or '?'} ({row.get('age_group') or '?'}, {row.get('period') or 'season'}): "
                    f"target={row.get('target_share')} actual={row.get('actual_share')}"
                ),
                detail=row,
                club=row.get("club"),
                age_group=row.get("age_group"),
            )
        )

    for row in _as_list(verify.get("manual_participation_placements")):
        if not isinstance(row, dict):
            continue
        records.append(
            _record(
                category="manual_participation_placements",
                summary=f"manual participation placement: {row.get('club') or row.get('label') or row.get('team') or '?'}",
                detail=row,
                tournament_id=row.get("tournament_id") or row.get("tournament"),
                club=row.get("club"),
                clubs=_record_clubs(row),
                age_group=row.get("age_group"),
            )
        )

    for row in _as_list(verify.get("participation_deviations")):
        if not isinstance(row, dict):
            continue
        records.append(
            _record(
                category="participation_target_deviations",
                finding_type=str(row.get("avoidability") or "bounded_search_exhausted"),
                summary=(
                    f"{row.get('team') or row.get('label') or '?'} ({row.get('age_group') or '?'}, "
                    f"{row.get('scope') or 'season'}): {row.get('actual')}/{row.get('target')} "
                    f"{row.get('direction') or ''} — {row.get('avoidability') or 'bounded_search_exhausted'}"
                ),
                detail=row,
                club=row.get("club"),
                clubs=_record_clubs(row),
                age_group=row.get("age_group"),
            )
        )

    for row in _as_list(verify.get("unresolved_hosting_obligations")):
        if not isinstance(row, dict):
            continue
        records.append(
            _record(
                category="unresolved_hosting_obligations",
                summary=(
                    f"{row.get('club') or '?'} ({row.get('age_group') or '?'}) hosting obligation unresolved"
                    + (
                        f" ({len(_as_list(row.get('candidate_reallocation_slots')))} untried reallocation candidates)"
                        if row.get("candidate_reallocation_slots")
                        else ""
                    )
                ),
                detail=row,
                tournament_id=row.get("tournament_id"),
                club=row.get("club"),
                age_group=row.get("age_group"),
            )
        )

    for category in ("same_age_hosting_repairs", "cross_age_hosting_repairs"):
        for row in _as_list(facts.get(category)):
            if not isinstance(row, dict):
                continue
            records.append(
                _record(
                    category=category,
                    finding_type=str(row.get("status") or category),
                    unresolved=row.get("status") == "unresolved",
                    summary=(
                        f"{row.get('club') or '?'} ({row.get('age_group') or '?'}) "
                        f"repair={row.get('status') or 'attempted'}"
                    ),
                    detail=row,
                    tournament_id=row.get("tournament_id"),
                    club=row.get("club"),
                    age_group=row.get("age_group"),
                )
            )

    for row in _as_list(facts.get("unresolved_tournament_placements")):
        if not isinstance(row, dict):
            continue
        records.append(
            _record(
                category="unresolved_tournament_placements",
                finding_type=str(row.get("category") or "manual_tournament_placement"),
                summary=(
                    f"{_tournament_label(row)} could not be placed: {row.get('reason') or 'unknown'}"
                ),
                detail=row,
                tournament_id=row.get("tournament_id"),
                clubs=_record_clubs(row),
                age_group=row.get("age_group"),
            )
        )

    for row in _as_list(verify.get("unresolved_external_conflicts")):
        if not isinstance(row, dict):
            continue
        records.append(
            _record(
                category="unresolved_external_conflicts",
                summary=(
                    f"{row.get('host_club') or row.get('club') or '?'} ({row.get('age_group') or '?'}) "
                    f"external conflict: {row.get('reason') or 'unknown'}"
                ),
                detail=row,
                tournament_id=row.get("tournament_id"),
                club=row.get("host_club") or row.get("club"),
                clubs=_record_clubs(row),
                age_group=row.get("age_group"),
            )
        )

    for row in _as_list(verify.get("manual_external_conflict_placements")):
        if not isinstance(row, dict):
            continue
        records.append(
            _record(
                category="manual_external_conflict_placements",
                summary=f"manual external conflict: {row.get('host_club') or row.get('club') or '?'}",
                detail=row,
                tournament_id=row.get("tournament_id"),
                club=row.get("host_club") or row.get("club"),
                clubs=_record_clubs(row),
                age_group=row.get("age_group"),
            )
        )

    for row in _as_list(verify.get("movable_allocations_used")):
        if not isinstance(row, dict):
            continue
        event_label = row.get("calendar_event") or "host-controlled interval"
        records.append(
            _record(
                category="movable_allocations_used",
                summary=(
                    f"movable_busy placement: {row.get('host_club') or row.get('club') or '?'} "
                    f"on {row.get('date') or '?'} displaces '{event_label}' "
                    "(host confirmation required)"
                ),
                detail=row,
                tournament_id=row.get("tournament_id"),
                club=row.get("host_club") or row.get("club"),
                clubs=_record_clubs(row),
                age_group=row.get("age_group"),
            )
        )

    for row in _as_list(verify.get("calendar_interpretations_used")):
        if not isinstance(row, dict):
            continue
        records.append(
            _record(
                category="calendar_interpretations_used",
                summary=(
                    f"inferred movable_busy interpretation: {row.get('club') or '?'} "
                    f"{row.get('date') or '?'} '{row.get('calendar_event') or '?'}' "
                    "(host confirmation required)"
                ),
                detail=row,
                club=row.get("club"),
                clubs=_record_clubs(row),
            )
        )

    for row in _as_list(facts.get("duration_examples")):
        if not isinstance(row, dict):
            continue
        duration = row.get("duration_minutes")
        records.append(
            _record(
                category="tournament_duration",
                unresolved=duration is None,
                severity=None if duration is not None else "major",
                summary=(
                    f"{_tournament_label(row)}: duration={duration} min "
                    f"(rounds={row.get('round_count')}, games={row.get('game_count')})"
                ),
                detail=row,
                tournament_id=row.get("id"),
                age_group=row.get("age_group"),
            )
        )

    for row in _as_list(facts.get("blocking_byes")):
        if not isinstance(row, dict):
            continue
        records.append(
            _record(
                category="blocking_byes",
                summary=(
                    f"{_tournament_label(row)}: {row.get('pause_team_count') or 0} pause/bye team(s), "
                    f"required={row.get('required_team_count')}, actual={row.get('team_count')}"
                ),
                detail=row,
                tournament_id=row.get("id"),
                age_group=row.get("age_group"),
            )
        )

    for row in _as_list(facts.get("input_constrained_shape_examples")):
        if not isinstance(row, dict):
            continue
        records.append(
            _record(
                category="input_constrained_shape",
                summary=(
                    f"{_tournament_label(row)}: input-constrained shape "
                    f"(effective_team_count={row.get('effective_team_count')})"
                ),
                detail=row,
                tournament_id=row.get("id"),
                age_group=row.get("age_group"),
            )
        )

    for row in _as_list(facts.get("utilisation_examples")):
        if not isinstance(row, dict) or not row.get("underfilled"):
            continue
        records.append(
            _record(
                category="underfilled_tournaments",
                severity="minor",
                summary=(
                    f"{_tournament_label(row)}: {row.get('game_count')}/{row.get('full_capacity_game_count')} games"
                ),
                detail=row,
                tournament_id=row.get("id"),
                age_group=row.get("age_group"),
            )
        )

    for row in _as_list(facts.get("host_missing")):
        if not isinstance(row, dict):
            continue
        records.append(
            _record(
                category="host_participation",
                summary=(
                    f"{_tournament_label(row)}: host not among participants "
                    f"({', '.join(_as_list(row.get('participant_clubs'))) or 'none'})"
                ),
                detail=row,
                tournament_id=row.get("id"),
                clubs=_record_clubs(row),
                age_group=row.get("age_group"),
            )
        )

    for row in _as_list(facts.get("duplicate_team_days")):
        if not isinstance(row, dict):
            continue
        records.append(
            _record(
                category="duplicate_team_days",
                summary=f"{row.get('team')} on {row.get('date')}: {row.get('participations')} participations",
                detail=row,
            )
        )

    for row in _as_list(facts.get("club_count_over_two")):
        if not isinstance(row, dict):
            continue
        records.append(
            _record(
                category="same_club_participants",
                summary=f"{_tournament_label(row)}: {row.get('club_counts_over_two')}",
                detail=row,
                tournament_id=row.get("id"),
                clubs=sorted((row.get("club_counts_over_two") or {}).keys()),
                age_group=row.get("age_group"),
            )
        )

    for row in _as_list(facts.get("club_count_over_hard_max")):
        if not isinstance(row, dict):
            continue
        records.append(
            _record(
                category="same_club_participants_hard_max",
                severity="critical",
                summary=f"{_tournament_label(row)}: {row.get('club_counts_over_hard_max')}",
                detail=row,
                tournament_id=row.get("id"),
                clubs=sorted((row.get("club_counts_over_hard_max") or {}).keys()),
                age_group=row.get("age_group"),
            )
        )

    calendar = raw.get("calendar_evidence_summary") or {}
    for row in _as_list(calendar.get("per_source")):
        if not isinstance(row, dict):
            continue
        blocked = bool(row.get("blocked"))
        records.append(
            _record(
                category="calendar_sources",
                unresolved=blocked,
                severity=None if blocked else "info",
                summary=(
                    f"{row.get('name') or '?'} ({row.get('type') or '?'}): "
                    f"{row.get('event_count')} events"
                    + (f" — blocked: {row.get('block_reason')}" if blocked else "")
                ),
                detail=row,
            )
        )
    for club_name, status in (calendar.get("club_calendar_status") or {}).items():
        records.append(
            _record(
                category="calendar_sources",
                summary=f"{club_name}: calendar status {status}",
                detail={"club": club_name, "status": status},
                club=club_name,
            )
        )
    for warning in _as_list(calendar.get("event_expectation_warnings")):
        records.append(
            _record(
                category="calendar_sources",
                unresolved=True,
                severity="minor",
                summary=f"event expectation warning: {warning}",
                detail=warning,
            )
        )

    export = raw.get("export_consistency_summary") or {}
    for name, check in (export.get("checks") or {}).items():
        if not isinstance(check, dict):
            continue
        matches = check.get("matches_plan")
        records.append(
            _record(
                category="export_consistency",
                finding_type=f"export_{name}",
                unresolved=matches is False,
                severity="info" if matches else "major",
                summary=f"export {name}: matches_plan={matches}",
                detail=check,
            )
        )

    for violation in _as_list(verify.get("violations")):
        detail = violation if isinstance(violation, dict) else {"violation": violation}
        records.append(
            _record(
                category="verify_violations",
                finding_type=str(detail.get("code") or "violation"),
                summary=f"{detail.get('code') or 'violation'}: {detail.get('message') or detail}",
                detail=detail,
                tournament_id=detail.get("tournament_id"),
                club=detail.get("club"),
                age_group=detail.get("age_group"),
            )
        )

    for row in _as_list(raw.get("operator_waivers")):
        detail = row if isinstance(row, dict) else {"waiver": row}
        records.append(
            _record(
                category="operator_waivers",
                finding_type=str(detail.get("rule") or "operator_waiver"),
                summary=(
                    f"{detail.get('rule') or 'waiver'} for "
                    f"{(detail.get('team') or {}).get('label') if isinstance(detail.get('team'), dict) else detail.get('team')}"
                ),
                detail=detail,
                tournament_id=detail.get("tournament_id"),
                club=(detail.get("team") or {}).get("club") if isinstance(detail.get("team"), dict) else None,
                age_group=(detail.get("team") or {}).get("age_group") if isinstance(detail.get("team"), dict) else None,
            )
        )

    for row in _as_list(raw.get("operator_waived_violations")):
        detail = row if isinstance(row, dict) else {"violation": row}
        records.append(
            _record(
                category="waived_violations",
                finding_type=str(detail.get("code") or "waived_violation"),
                summary=f"waived: {detail.get('code') or detail}",
                detail=detail,
                tournament_id=detail.get("tournament_id"),
                club=detail.get("club"),
                age_group=detail.get("age_group"),
            )
        )

    approval = raw.get("approval_status")
    stale_approvals = approval.get("stale_approvals") if isinstance(approval, dict) else None
    for row in _as_list(stale_approvals):
        detail = row if isinstance(row, dict) else {"approval": row}
        records.append(
            _record(
                category="stale_approvals",
                summary=f"stale approval: {detail.get('tournament_id') or detail}",
                detail=detail,
                tournament_id=detail.get("tournament_id"),
                age_group=detail.get("age_group"),
            )
        )

    records.sort(
        key=lambda record: (
            _severity_rank(record.get("severity")),
            str(record.get("category")),
            str(record.get("summary")),
        )
    )
    return records


def _category_summaries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_category: dict[str, list[dict[str, Any]]] = {name: [] for name in EVIDENCE_CATEGORIES}
    for record in records:
        by_category.setdefault(record["category"], []).append(record)

    summaries: list[dict[str, Any]] = []
    for name, meta in EVIDENCE_CATEGORIES.items():
        rows = by_category.get(name, [])
        clubs = sorted({club for row in rows for club in (row.get("clubs") or [])})
        age_groups = sorted({row["age_group"] for row in rows if row.get("age_group")})
        summaries.append(
            {
                "category": name,
                "description": meta["description"],
                "checklist_item": meta["checklist_item"],
                "unresolved": bool(meta["unresolved"]),
                "count": len(rows),
                "unresolved_count": sum(1 for row in rows if row.get("unresolved")),
                "clubs_affected": len(clubs),
                "age_groups_affected": age_groups,
                "tournament_count": len({row["tournament_id"] for row in rows if row.get("tournament_id")}),
                "evidence_ref": f"operator audit-evidence --category {name}",
            }
        )
    return summaries


def build_evidence_index(raw: dict[str, Any]) -> dict[str, Any]:
    """Build the full (unbounded) evidence index from assembled raw evidence.

    The index keeps every record so selective queries can retrieve them; it is
    never placed in the default audit context. It is bound to the run/export
    fingerprint it was assembled from.
    """
    records = _extract_records(raw)
    return {
        "schema_version": 1,
        "run_id": raw.get("run_id"),
        "export_fingerprint": raw.get("export_fingerprint"),
        "source_fingerprints": raw.get("source_fingerprints") or {},
        "record_count": len(records),
        "categories": _category_summaries(records),
        "records": records,
    }


def _overview_example(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "severity": record.get("severity"),
        "unresolved": bool(record.get("unresolved")),
        "tournament_id": record.get("tournament_id"),
        "club": record.get("club"),
        "age_group": record.get("age_group"),
        "summary": record.get("summary"),
    }


def _club_selector_values(records: list[dict[str, Any]]) -> list[str]:
    clubs = {club for record in records for club in (record.get("clubs") or []) if club}
    return sorted(clubs)


def build_evidence_overview(index: dict[str, Any]) -> dict[str, Any]:
    """Build the bounded, discoverable overview placed in the audit context."""
    records = index.get("records") or []
    by_category: dict[str, list[dict[str, Any]]] = {name: [] for name in EVIDENCE_CATEGORIES}
    for record in records:
        by_category.setdefault(record["category"], []).append(record)

    categories: list[dict[str, Any]] = []
    for summary in index.get("categories") or []:
        category = summary["category"]
        examples = [_overview_example(row) for row in by_category.get(category, [])[:EVIDENCE_OVERVIEW_MAX_EXAMPLES]]
        categories.append({**summary, "examples": examples})

    tournament_ids = sorted({record["tournament_id"] for record in records if record.get("tournament_id")})
    clubs = _club_selector_values(records)
    age_groups = sorted({record["age_group"] for record in records if record.get("age_group")})

    return {
        "schema_version": 1,
        "run_id": index.get("run_id"),
        "export_fingerprint": index.get("export_fingerprint"),
        "source_fingerprints": index.get("source_fingerprints") or {},
        "record_count": index.get("record_count", 0),
        "categories": categories,
        "available_selectors": {
            "checklist_items": [int(item["item_id"]) for item in _checklist_item_ids()],
            "categories": list(EVIDENCE_CATEGORIES),
            "clubs": clubs,
            "club_count": len(clubs),
            "age_groups": age_groups,
            "tournament_ids": tournament_ids[:50],
            "tournament_id_count": len(tournament_ids),
            "unresolved_categories": [name for name, meta in EVIDENCE_CATEGORIES.items() if meta["unresolved"]],
        },
        "evidence_commands": dict(_SELECTOR_COMMANDS),
    }


def _checklist_item_ids() -> list[dict[str, Any]]:
    # Imported lazily to keep this module independent of audit_context at
    # import time (audit_context imports this module).
    from .audit_context import AUDIT_CHECKLIST

    return [dict(item) for item in AUDIT_CHECKLIST]


def build_overview_metrics(context: dict[str, Any], index: dict[str, Any]) -> dict[str, Any]:
    """Lightweight observability metrics for one bounded audit overview."""
    serialized = json.dumps(context, ensure_ascii=False, default=str).encode("utf-8")
    return {
        "overview_serialized_bytes": len(serialized),
        "overview_budget_bytes": AUDIT_CONTEXT_MAX_SERIALIZED_BYTES,
        "overview_budget_exceeded": len(serialized) > AUDIT_CONTEXT_MAX_SERIALIZED_BYTES,
        "index_record_count": index.get("record_count", 0),
        "populated_category_count": sum(1 for row in index.get("categories") or [] if row.get("count")),
        "query_max_records": EVIDENCE_QUERY_MAX_RECORDS,
    }


def _matches(
    record: dict[str, Any],
    *,
    item: int | None,
    tournament: str | None,
    club: str | None,
    age_group: str | None,
    category: str | None,
    unresolved: bool,
) -> bool:
    if item is not None and record.get("checklist_item") != item:
        return False
    if tournament:
        tournament_id = record.get("tournament_id")
        if not tournament_id or str(tournament_id) != str(tournament):
            return False
    if club:
        candidates = [str(value) for value in (record.get("clubs") or []) if value]
        if record.get("club"):
            candidates.append(str(record["club"]))
        if not any(clubs_represent_same_club(candidate, club) for candidate in candidates):
            return False
    if age_group and str(record.get("age_group") or "") != str(age_group):
        return False
    if category and category not in (record.get("category"), record.get("finding_type")):
        return False
    if unresolved and not record.get("unresolved"):
        return False
    return True


def query_evidence(
    index: dict[str, Any],
    *,
    item: int | None = None,
    tournament: str | None = None,
    club: str | None = None,
    age_group: str | None = None,
    category: str | None = None,
    unresolved: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return the detailed evidence records matching the given selectors.

    The response is bound to the same ``run_id``/``export_fingerprint`` as the
    overview that offered these selectors, so stale evidence from another
    run/export can never be silently mixed in. Matching is deterministic;
    results are ordered by severity then category/summary and capped at
    ``limit`` (default :data:`EVIDENCE_QUERY_MAX_RECORDS`) with an explicit
    ``truncated`` flag rather than an arbitrary character cut.
    """
    boundary = limit if isinstance(limit, int) and limit > 0 else EVIDENCE_QUERY_MAX_RECORDS
    matched = [
        record
        for record in index.get("records") or []
        if _matches(
            record,
            item=item,
            tournament=tournament,
            club=club,
            age_group=age_group,
            category=category,
            unresolved=unresolved,
        )
    ]
    page = matched[:boundary]
    response = {
        "schema_version": 1,
        "run_id": index.get("run_id"),
        "export_fingerprint": index.get("export_fingerprint"),
        "source_fingerprints": index.get("source_fingerprints") or {},
        "query": {
            "item": item,
            "tournament": tournament,
            "club": club,
            "age_group": age_group,
            "category": category,
            "unresolved": bool(unresolved),
            "limit": boundary,
        },
        "matched_record_count": len(matched),
        "returned_record_count": len(page),
        "truncated": len(matched) > len(page),
        "max_serialized_bytes": EVIDENCE_QUERY_MAX_SERIALIZED_BYTES,
        "records": page,
    }
    return response
