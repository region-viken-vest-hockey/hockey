"""Approval, change-protection and participation-acceptance lifecycle."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from tournament_scheduler.canonical_baseline import approval_fingerprint, resolve_approval
from tournament_scheduler.canonical_state import (
    CHANGE_PROTECTIONS_KEY,
    PARTICIPATION_ACCEPTANCES_KEY,
    canonical_state_revision,
    participation_acceptance_id,
)
from tournament_scheduler.change_protections import (
    ACTIVE as CHANGE_PROTECTION_ACTIVE,
    RELEASED as CHANGE_PROTECTION_RELEASED,
    active_change_protections,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.participation_targets import OPERATOR_ACCEPTED
from tournament_scheduler.planning_contract import verify_candidate

from .shared import (
    APPROVED_STATUS,
    PENDING_REVIEW_STATUS,
    _operator_identity,
    _now_iso,
    _append_decision_history,
    _attributable_blockers,
)

def _acceptance_record_id(record: Mapping[str, Any]) -> str:
    """Return the canonical acceptance id, migrating a legacy record on read.

    A legacy record omitted ``age_group`` from its id. Recomputing from the
    stored fields lets revocation match it before any write migrates the file.
    """

    club = str(record.get("club") or "")
    label = str(record.get("label") or "")
    age_group = str(record.get("age_group") or "")
    scope = str(record.get("scope") or "")
    if club and label and age_group and scope:
        return participation_acceptance_id(club, label, age_group, scope)
    return str(record.get("id") or "")


def change_protection_report(
    service,
    season: str,
    *,
    include_released: bool = False,
) -> dict[str, Any]:
    """Return durable accepted-change guards for a promoted season."""

    snapshot = service.load(season)
    records = [
        dict(record)
        for record in snapshot.decisions.get(CHANGE_PROTECTIONS_KEY, []) or []
        if isinstance(record, Mapping)
        and (
            include_released
            or str(record.get("status") or CHANGE_PROTECTION_ACTIVE)
            == CHANGE_PROTECTION_ACTIVE
        )
    ]
    return {
        "season": season,
        "canonical_state_revision": canonical_state_revision(
            snapshot.schedule, snapshot.decisions
        ),
        "active_count": sum(
            1
            for record in records
            if str(record.get("status") or CHANGE_PROTECTION_ACTIVE)
            == CHANGE_PROTECTION_ACTIVE
        ),
        "protections": records,
    }


def release_change_protections(
    service,
    *,
    season: str,
    protection_ids: list[str] | None = None,
    request_id: str | None = None,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Release accepted-change guards for an explicitly superseding request."""

    wanted_ids = {str(value) for value in (protection_ids or []) if str(value)}
    wanted_request = str(request_id or "")
    if not wanted_ids and not wanted_request:
        raise SeasonStateError(
            "Refusing protection release: provide --protection-id and/or --request-id"
        )

    snapshot = service.load(season)
    decisions = copy.deepcopy(snapshot.decisions)
    records = decisions.get(CHANGE_PROTECTIONS_KEY, []) or []
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    released: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("status") or CHANGE_PROTECTION_ACTIVE) != CHANGE_PROTECTION_ACTIVE:
            continue
        matches_id = str(record.get("id") or "") in wanted_ids
        matches_request = bool(wanted_request) and str(record.get("request_id") or "") == wanted_request
        if not (matches_id or matches_request):
            continue
        record["status"] = CHANGE_PROTECTION_RELEASED
        record["released_at"] = now
        record["released_by"] = resolved_actor
        record["release_reason"] = note or ""
        released.append(str(record.get("id") or ""))

    if not released:
        raise SeasonStateError("No active change protections matched the release request")

    decisions["updated_at"] = now
    _append_decision_history(
        decisions,
        event="release_change_protection",
        tournament_id="",
        actor=resolved_actor,
        now=now,
        note=note,
        details={
            "released_protection_ids": released,
            "request_id": wanted_request,
        },
    )
    committed = service._commit(snapshot.with_decisions(decisions))
    return {
        "season": season,
        "canonical_state_revision": canonical_state_revision(
            committed.schedule, committed.decisions
        ),
        "released_protection_ids": released,
        "active_count": len(active_change_protections(committed.decisions)),
    }


def approve_tournament(
    service,
    *,
    season: str,
    tournament_id: str,
    actor: str | None = None,
    note: str = "",
    placement_locked: bool = True,
    participants_locked: bool = False,
    problem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Approve/lock one tournament after canonical hard verification."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule["plan"]
    tournament = next(
        (t for t in plan.get("tournaments", []) if str(t.get("id")) == tournament_id), None
    )
    if tournament is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")

    verification = verify_candidate(plan, problem) if problem else verify_candidate(plan)
    hard_blockers, unresolved_blockers = _attributable_blockers(verification, tournament_id)
    blockers = hard_blockers + unresolved_blockers
    if blockers:
        messages = "; ".join(
            str(blocker.get("message") or blocker.get("code")) for blocker in blockers
        )
        raise SeasonStateError(
            f"Refusing to approve {tournament_id}: tournament fails canonical verification: {messages}"
        )

    approved_at = _now_iso()
    resolved_actor = _operator_identity(actor)
    tournament_fingerprint = approval_fingerprint(tournament)
    previous = dict(decisions["decisions"].get(tournament_id, {}))
    record = dict(previous)
    record.update(
        {
            "status": APPROVED_STATUS,
            "placement_locked": bool(placement_locked),
            "participants_locked": bool(participants_locked),
            "approved_fingerprint": tournament_fingerprint,
            "approved_at": approved_at,
            "approved_by": resolved_actor,
            "note": note,
        }
    )
    record.pop("stale_at", None)
    record.pop("stale_reason", None)
    record.pop("unapproved_at", None)
    record.pop("unapproved_by", None)
    updated = dict(decisions)
    updated["decisions"] = dict(updated.get("decisions", {}))
    updated["decisions"][tournament_id] = record
    updated["updated_at"] = approved_at
    _append_decision_history(
        updated,
        event="approve",
        tournament_id=tournament_id,
        actor=resolved_actor,
        now=approved_at,
        tournament_fingerprint=tournament_fingerprint,
        previous_fingerprint=previous.get("approved_fingerprint"),
        note=note,
    )
    return service._commit(snapshot.with_decisions(updated)).decisions


def unapprove_tournament(
    service,
    *,
    season: str,
    tournament_id: str,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Explicitly revoke approval and all locks for one canonical tournament."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule["plan"]
    tournament = next(
        (t for t in plan.get("tournaments", []) if str(t.get("id")) == tournament_id), None
    )
    if tournament is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    existing = decisions["decisions"].get(tournament_id)
    if existing is None:
        raise SeasonStateError(f"Unknown tournament id in decisions state: {tournament_id}")

    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    previous_fingerprint = existing.get("approved_fingerprint")
    record = dict(existing)
    record.update(
        {
            "status": PENDING_REVIEW_STATUS,
            "placement_locked": False,
            "participants_locked": False,
            "approved_fingerprint": None,
            "approved_at": None,
            "approved_by": None,
            "note": note or existing.get("note") or "",
            "unapproved_at": now,
            "unapproved_by": resolved_actor,
        }
    )
    record.pop("stale_at", None)
    record.pop("stale_reason", None)
    updated = dict(decisions)
    updated["decisions"] = dict(updated.get("decisions", {}))
    updated["decisions"][tournament_id] = record
    updated["updated_at"] = now
    _append_decision_history(
        updated,
        event="unapprove",
        tournament_id=tournament_id,
        actor=resolved_actor,
        now=now,
        previous_fingerprint=previous_fingerprint,
        note=note,
    )
    return service._commit(snapshot.with_decisions(updated)).decisions


def load_participation_acceptances(service, season: str) -> list[dict[str, Any]]:
    """Return the active (non-revoked) operator participation acceptances."""

    decisions = service.load(season).decisions
    records = decisions.get(PARTICIPATION_ACCEPTANCES_KEY) or []
    if not isinstance(records, list):
        return []
    return [
        {**record, "id": _acceptance_record_id(record)}
        for record in records
        if isinstance(record, dict) and not record.get("revoked_at")
    ]


def record_participation_acceptance(
    service,
    *,
    season: str,
    club: str,
    label: str,
    age_group: str,
    scope: str,
    direction: str,
    actual: int,
    target: int,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Persist an explicit operator acceptance of one participation deviation."""

    if not club or not label or not scope:
        raise SeasonStateError(
            "Refusing participation acceptance: club, team label and scope are required"
        )
    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    acceptance_id = participation_acceptance_id(club, label, age_group, scope)
    record = {
        "id": acceptance_id,
        "club": club,
        "label": label,
        "age_group": age_group,
        "scope": scope,
        "direction": direction,
        "target": int(target),
        "actual": int(actual),
        "accepted_deviation": int(actual) - int(target),
        "status": OPERATOR_ACCEPTED,
        "accepted_at": now,
        "accepted_by": resolved_actor,
        "note": note or "",
        "schedule_fingerprint": schedule.get("fingerprint"),
    }
    existing = decisions.get(PARTICIPATION_ACCEPTANCES_KEY) or []
    if not isinstance(existing, list):
        existing = []
    kept = [
        entry
        for entry in existing
        if not isinstance(entry, dict)
        or _acceptance_record_id(entry) != acceptance_id
    ]
    kept.append(record)
    updated = {**decisions, PARTICIPATION_ACCEPTANCES_KEY: kept, "updated_at": now}
    service._commit(snapshot.with_decisions(updated))
    return record


def revoke_participation_acceptance(
    service,
    *,
    season: str,
    club: str,
    label: str,
    age_group: str,
    scope: str,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Revoke one active participation acceptance, preserving its audit trail."""

    snapshot = service.load(season)
    decisions = snapshot.decisions
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    acceptance_id = participation_acceptance_id(club, label, age_group, scope)
    records = decisions.get(PARTICIPATION_ACCEPTANCES_KEY) or []
    if not isinstance(records, list):
        records = []
    updated_records: list[dict[str, Any]] = []
    revoked: dict[str, Any] | None = None
    for record in records:
        if (
            isinstance(record, dict)
            and _acceptance_record_id(record) == acceptance_id
            and not record.get("revoked_at")
        ):
            revoked = {
                **record,
                "id": acceptance_id,
                "revoked_at": now,
                "revoked_by": resolved_actor,
                "revoke_note": note or "",
            }
            updated_records.append(revoked)
        else:
            updated_records.append(record)
    if revoked is None:
        raise SeasonStateError(f"No active participation acceptance for {acceptance_id!r}")
    updated = {**decisions, PARTICIPATION_ACCEPTANCES_KEY: updated_records, "updated_at": now}
    service._commit(snapshot.with_decisions(updated))
    return revoked


def approval_report(service, season: str) -> dict[str, Any]:
    """Read-only approval/lock status for every canonical tournament."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    records = decisions.get("decisions", {}) or {}
    tournaments: list[dict[str, Any]] = []
    stale_approvals: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for tournament in schedule["plan"].get("tournaments", []) or []:
        tournament_id = str(tournament.get("id") or "")
        if not tournament_id:
            continue
        seen_ids.add(tournament_id)
        resolved = resolve_approval(records.get(tournament_id), tournament)
        entry = {
            "tournament_id": tournament_id,
            "status": resolved["status"],
            "stale": resolved["stale"],
            "placement_locked": resolved["placement_locked"],
            "participants_locked": resolved["participants_locked"],
            "approved_fingerprint": resolved["approved_fingerprint"],
            "current_fingerprint": resolved["current_fingerprint"],
            "approved_at": resolved["approved_at"],
            "approved_by": resolved["approved_by"],
            "note": resolved["note"],
            "stale_reason": resolved.get("stale_reason"),
        }
        tournaments.append(entry)
        if resolved["stale"]:
            stale_approvals.append(
                {
                    "code": "stale_approval",
                    "tournament_id": tournament_id,
                    "approved_fingerprint": resolved["approved_fingerprint"],
                    "current_fingerprint": resolved["current_fingerprint"],
                    "stale_reason": resolved.get("stale_reason"),
                }
            )
    orphaned = [
        {
            "code": "orphaned_approval",
            "tournament_id": tournament_id,
            "approved_fingerprint": (record or {}).get("approved_fingerprint"),
        }
        for tournament_id, record in records.items()
        if tournament_id not in seen_ids and (record or {}).get("approved_fingerprint")
    ]
    counts = {
        "total": len(tournaments),
        "approved": sum(1 for entry in tournaments if entry["status"] == APPROVED_STATUS),
        "stale": len(stale_approvals),
        "orphaned": len(orphaned),
        "locked": sum(
            1
            for entry in tournaments
            if entry["placement_locked"] or entry["participants_locked"]
        ),
        "pending_review": sum(
            1 for entry in tournaments if entry["status"] == PENDING_REVIEW_STATUS
        ),
    }
    return {
        "season": season,
        "schedule_fingerprint": decisions.get("schedule_fingerprint"),
        "revision": schedule.get("revision"),
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "counts": counts,
        "tournaments": tournaments,
        "stale_approvals": stale_approvals,
        "orphaned_approvals": orphaned,
    }
