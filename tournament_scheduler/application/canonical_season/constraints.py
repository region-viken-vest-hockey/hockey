"""Request constraint and calendar-exception (holiday/banned date) lifecycle."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from tournament_scheduler.canonical_state import (
    BANNED_DATES_KEY,
    HOLIDAY_DATE_EXCEPTIONS_KEY,
    REQUEST_CONSTRAINTS_KEY,
    canonical_state_revision,
)
from tournament_scheduler.canonical_banned_dates import (
    ACTIVE as BANNED_DATE_ACTIVE,
    RELEASED as BANNED_DATE_RELEASED,
    BannedDateError,
    active_banned_date_records,
    append_banned_dates,
    banned_date_report as build_banned_date_report,
    validate_and_normalize as normalize_banned_date,
)
from tournament_scheduler.canonical_holiday_exceptions import (
    ACTIVE as HOLIDAY_EXCEPTION_ACTIVE,
    RELEASED as HOLIDAY_EXCEPTION_RELEASED,
    HolidayDateExceptionError,
    active_exception_records,
    append_exceptions as append_holiday_exceptions,
    exception_report as build_holiday_exception_report,
    validate_and_normalize as normalize_holiday_exception,
)
from tournament_scheduler.request_constraints import (
    ACTIVE as REQUEST_CONSTRAINT_ACTIVE,
    RELEASED as REQUEST_CONSTRAINT_RELEASED,
    RequestConstraintError,
    active_request_constraints,
    append_request_constraints,
    request_constraint_records,
    request_constraint_report as build_request_constraint_report,
    request_constraint_violations,
    validate_and_normalize as normalize_request_constraint,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)

from .shared import (
    _operator_identity,
    _now_iso,
    _append_decision_history,
    _resolve_plan_problem,
)

def request_constraint_report(
    service,
    season: str,
    *,
    include_released: bool = False,
) -> dict[str, Any]:
    """Read-only lifecycle + derived satisfaction of request constraints."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    constraints = build_request_constraint_report(
        schedule.get("plan") or {},
        decisions,
        include_released=include_released,
    )
    return {
        "season": season,
        "revision": schedule.get("revision"),
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "active_count": sum(
            1 for item in constraints if item.get("status") == REQUEST_CONSTRAINT_ACTIVE
        ),
        "unsatisfied_count": sum(
            1
            for item in constraints
            if item.get("status") == REQUEST_CONSTRAINT_ACTIVE and not item.get("satisfied")
        ),
        "constraints": constraints,
    }


def add_request_constraint(
    service,
    *,
    season: str,
    type: str,
    request_id: str,
    teams: list[Mapping[str, Any]] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_days: int | None = None,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Persist one validated typed request constraint (decision-only write).

    Recording a real club/operator request may intentionally leave the
    current schedule non-conforming. This write is therefore always allowed
    when the definition itself is valid: it persists the constraint, reports
    its current structured violation(s), and advances the canonical-state
    revision so every previously generated repair/search option is stale.
    The schedule is not touched.
    """

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule.get("plan") or {}
    try:
        normalized = normalize_request_constraint(
            {
                "type": type,
                "request_id": request_id,
                "teams": [dict(team) for team in (teams or [])],
                "date_from": date_from,
                "date_to": date_to,
                "min_days": min_days,
            },
            plan,
        )
    except RequestConstraintError as exc:
        raise SeasonStateError(str(exc)) from exc

    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    record = {
        **normalized,
        "status": REQUEST_CONSTRAINT_ACTIVE,
        "created_at": now,
        "created_by": resolved_actor,
        "note": note or "",
        "source_revision": canonical_state_revision(schedule, decisions),
    }
    existing = request_constraint_records(decisions, include_released=True)
    stored = next(
        (item for item in existing if str(item.get("id") or "") == record["id"]),
        None,
    )
    if stored is not None:
        # Idempotent retry: return the already-stored constraint with its
        # current derived status instead of creating a conflicting copy.
        is_active = str(stored.get("status") or REQUEST_CONSTRAINT_ACTIVE) == REQUEST_CONSTRAINT_ACTIVE
        violations = (
            [
                violation
                for violation in request_constraint_violations(plan, decisions)
                if str(violation.get("constraint_id") or "") == record["id"]
            ]
            if is_active
            else []
        )
        return {
            "season": season,
            "created": False,
            "constraint": {
                **stored,
                "satisfied": (not violations) if is_active else None,
                "violations": violations,
            },
            "canonical_state_revision": canonical_state_revision(schedule, decisions),
        }
    append_request_constraints(decisions, [record])

    _append_decision_history(
        decisions,
        event="add_request_constraint",
        tournament_id="",
        actor=resolved_actor,
        now=now,
        note=note,
        details={
            "constraint_id": record["id"],
            "type": record["type"],
            "request_id": record["request_id"],
            "teams": record["teams"],
            "date_from": record.get("date_from"),
            "date_to": record.get("date_to"),
            "min_days": record.get("min_days"),
        },
    )
    updated = {**decisions, "updated_at": now}
    committed = service._commit(snapshot.with_decisions(updated))
    violations = [
        violation
        for violation in request_constraint_violations(
            committed.schedule.get("plan") or {}, committed.decisions
        )
        if str(violation.get("constraint_id") or "") == record["id"]
    ]
    return {
        "season": season,
        "created": True,
        "constraint": {
            **record,
            "satisfied": not violations,
            "violations": violations,
        },
        "canonical_state_revision": canonical_state_revision(
            committed.schedule, committed.decisions
        ),
    }


def release_request_constraints(
    service,
    *,
    season: str,
    constraint_ids: list[str] | None = None,
    request_id: str | None = None,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Explicitly release/supersede request constraints with audit history."""

    wanted_ids = {str(value) for value in (constraint_ids or []) if str(value)}
    wanted_request = str(request_id or "")
    if not wanted_ids and not wanted_request:
        raise SeasonStateError(
            "Refusing constraint release: provide --constraint-id and/or --request-id"
        )

    snapshot = service.load(season)
    decisions = copy.deepcopy(snapshot.decisions)
    records = decisions.get(REQUEST_CONSTRAINTS_KEY, []) or []
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    released: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("status") or REQUEST_CONSTRAINT_ACTIVE) != REQUEST_CONSTRAINT_ACTIVE:
            continue
        matches_id = str(record.get("id") or "") in wanted_ids
        matches_request = bool(wanted_request) and str(record.get("request_id") or "") == wanted_request
        if not (matches_id or matches_request):
            continue
        record["status"] = REQUEST_CONSTRAINT_RELEASED
        record["released_at"] = now
        record["released_by"] = resolved_actor
        record["release_reason"] = note or ""
        released.append(str(record.get("id") or ""))

    if not released:
        raise SeasonStateError("No active request constraints matched the release request")

    decisions["updated_at"] = now
    _append_decision_history(
        decisions,
        event="release_request_constraint",
        tournament_id="",
        actor=resolved_actor,
        now=now,
        note=note,
        details={"released_constraint_ids": released, "request_id": wanted_request},
    )
    committed = service._commit(snapshot.with_decisions(decisions))
    return {
        "season": season,
        "canonical_state_revision": canonical_state_revision(
            committed.schedule, committed.decisions
        ),
        "released_constraint_ids": released,
        "active_count": len(active_request_constraints(committed.decisions)),
    }


def holiday_date_exception_report(
    service,
    season: str,
    *,
    include_released: bool = False,
) -> dict[str, Any]:
    """Read-only lifecycle + evidence for holiday-policy exceptions."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule.get("plan") or {}
    problem = _resolve_plan_problem(schedule, None, decisions)
    entries = build_holiday_exception_report(
        plan,
        decisions,
        problem=problem,
        include_released=include_released,
    )
    active = [entry for entry in entries if entry.get("status") == HOLIDAY_EXCEPTION_ACTIVE]
    return {
        "season": season,
        "revision": schedule.get("revision"),
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "active_count": len(active),
        "holiday_date_exceptions": entries,
    }


def allow_holiday_date(
    service,
    *,
    season: str,
    date: str,
    reason: str,
    actor: str | None = None,
) -> dict[str, Any]:
    """Persist one derived-holiday-policy exception (decision-only write)."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule.get("plan") or {}
    problem = _resolve_plan_problem(schedule, None, None)
    try:
        normalized = normalize_holiday_exception({"date": date, "reason": reason})
    except HolidayDateExceptionError as exc:
        raise SeasonStateError(str(exc)) from exc

    existing = active_exception_records(decisions)
    stored = next(
        (
            record
            for record in existing
            if str(record.get("date") or "") == normalized["date"]
        ),
        None,
    )
    if stored is not None:
        entry = next(
            entry
            for entry in build_holiday_exception_report(
                plan, decisions, problem=_resolve_plan_problem(schedule, None, decisions)
            )
            if str(entry.get("id") or "") == str(stored.get("id") or "")
        )
        return {
            "season": season,
            "created": False,
            "holiday_date_exception": entry,
            "canonical_state_revision": canonical_state_revision(schedule, decisions),
        }

    derived_entries = build_holiday_exception_report(
        plan,
        {**decisions, HOLIDAY_DATE_EXCEPTIONS_KEY: [normalized]},
        problem=problem,
    )
    projected_entry = next(
        (entry for entry in derived_entries if str(entry.get("id") or "") == normalized["id"]),
        None,
    )
    if not projected_entry or not projected_entry.get("derived_holiday_policy_reason"):
        raise SeasonStateError(
            f"Refusing holiday-date exception: {normalized['date']} is not excluded by the derived holiday policy"
        )

    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    record = {
        **normalized,
        "status": HOLIDAY_EXCEPTION_ACTIVE,
        "created_at": now,
        "created_by": resolved_actor,
        "source_revision": canonical_state_revision(schedule, decisions),
        "derived_holiday_policy_reason": projected_entry.get("derived_holiday_policy_reason"),
    }
    updated = dict(decisions)
    append_holiday_exceptions(updated, [record])
    _append_decision_history(
        updated,
        event="allow_holiday_date",
        tournament_id="",
        actor=resolved_actor,
        now=now,
        note=reason,
        details={
            "holiday_date_exception_id": record["id"],
            "date": record["date"],
            "reason": record["reason"],
        },
    )
    updated["updated_at"] = now
    committed = service._commit(snapshot.with_decisions(updated))
    entry = next(
        entry
        for entry in build_holiday_exception_report(
            committed.schedule.get("plan") or {},
            committed.decisions,
            problem=_resolve_plan_problem(committed.schedule, None, committed.decisions),
        )
        if str(entry.get("id") or "") == record["id"]
    )
    return {
        "season": season,
        "created": True,
        "holiday_date_exception": entry,
        "canonical_state_revision": canonical_state_revision(committed.schedule, committed.decisions),
    }


def disallow_holiday_dates(
    service,
    *,
    season: str,
    dates: list[str] | None = None,
    exception_ids: list[str] | None = None,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Release one or more active holiday-policy exceptions."""

    wanted_ids = {str(value) for value in (exception_ids or []) if str(value)}
    wanted_dates = {str(value) for value in (dates or []) if str(value)}
    if not wanted_ids and not wanted_dates:
        raise SeasonStateError("Refusing holiday-date exception release: provide --date and/or --exception-id")

    snapshot = service.load(season)
    decisions = copy.deepcopy(snapshot.decisions)
    records = decisions.get(HOLIDAY_DATE_EXCEPTIONS_KEY, []) or []
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    released: list[str] = []
    released_dates: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("status") or HOLIDAY_EXCEPTION_ACTIVE) != HOLIDAY_EXCEPTION_ACTIVE:
            continue
        if str(record.get("id") or "") not in wanted_ids and str(record.get("date") or "") not in wanted_dates:
            continue
        record["status"] = HOLIDAY_EXCEPTION_RELEASED
        record["released_at"] = now
        record["released_by"] = resolved_actor
        record["release_reason"] = note or ""
        released.append(str(record.get("id") or ""))
        released_dates.append(str(record.get("date") or ""))

    if not released:
        raise SeasonStateError("No active holiday-date exceptions matched the release request")

    decisions["updated_at"] = now
    _append_decision_history(
        decisions,
        event="disallow_holiday_date",
        tournament_id="",
        actor=resolved_actor,
        now=now,
        note=note,
        details={
            "released_holiday_date_exception_ids": released,
            "released_dates": released_dates,
        },
    )
    committed = service._commit(snapshot.with_decisions(decisions))
    return {
        "season": season,
        "canonical_state_revision": canonical_state_revision(committed.schedule, committed.decisions),
        "released_holiday_date_exception_ids": released,
        "released_dates": sorted(set(released_dates)),
        "active_count": len(active_exception_records(committed.decisions)),
    }


def banned_date_report(
    service,
    season: str,
    *,
    include_released: bool = False,
) -> dict[str, Any]:
    """Read-only lifecycle + derived satisfaction of operator banned dates."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule.get("plan") or {}
    entries = build_banned_date_report(plan, decisions, include_released=include_released)
    active = [entry for entry in entries if entry.get("status") == BANNED_DATE_ACTIVE]
    return {
        "season": season,
        "revision": schedule.get("revision"),
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "active_count": len(active),
        "unsatisfied_count": sum(1 for entry in active if not entry.get("satisfied")),
        "affected_tournament_ids": sorted(
            {
                tournament_id
                for entry in active
                for tournament_id in entry.get("affected_tournament_ids") or []
            }
        ),
        "banned_dates": entries,
    }


def add_banned_date(
    service,
    *,
    season: str,
    date: str,
    request_id: str,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Persist one global operator date ban (policy/decision-only write).

    Recording a real-world date restriction is allowed even when the
    current schedule still has tournaments on that date, exactly like
    ``season add-constraint``: the ban is persisted, its current structured
    violation(s) are reported, and the canonical-state revision advances so
    every stale maintenance/search/batch option is invalidated. The
    schedule itself is not touched -- repair is a separate operation.
    """

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule.get("plan") or {}
    try:
        normalized = normalize_banned_date(
            {"date": date, "request_id": request_id, "note": note}
        )
    except BannedDateError as exc:
        raise SeasonStateError(str(exc)) from exc

    existing = active_banned_date_records(decisions)
    stored = next(
        (
            record
            for record in existing
            if str(record.get("date") or "") == normalized["date"]
        ),
        None,
    )
    if stored is not None:
        entry = next(
            entry
            for entry in build_banned_date_report(plan, decisions)
            if str(entry.get("id") or "") == str(stored.get("id") or "")
        )
        return {
            "season": season,
            "created": False,
            "banned_date": entry,
            "canonical_state_revision": canonical_state_revision(schedule, decisions),
        }

    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    record = {
        **normalized,
        "status": BANNED_DATE_ACTIVE,
        "created_at": now,
        "created_by": resolved_actor,
        "source_revision": canonical_state_revision(schedule, decisions),
    }
    updated = dict(decisions)
    append_banned_dates(updated, [record])
    _append_decision_history(
        updated,
        event="ban_date",
        tournament_id="",
        actor=resolved_actor,
        now=now,
        note=note,
        details={
            "banned_date_id": record["id"],
            "date": record["date"],
            "request_id": record["request_id"],
        },
    )
    updated["updated_at"] = now
    committed = service._commit(snapshot.with_decisions(updated))
    entry = next(
        entry
        for entry in build_banned_date_report(
            committed.schedule.get("plan") or {}, committed.decisions
        )
        if str(entry.get("id") or "") == record["id"]
    )
    return {
        "season": season,
        "created": True,
        "banned_date": entry,
        "canonical_state_revision": canonical_state_revision(
            committed.schedule, committed.decisions
        ),
    }


def release_banned_dates(
    service,
    *,
    season: str,
    date_ids: list[str] | None = None,
    dates: list[str] | None = None,
    request_id: str | None = None,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Explicitly remove one or more active banned dates with audit history."""

    wanted_ids = {str(value) for value in (date_ids or []) if str(value)}
    wanted_dates = {str(value) for value in (dates or []) if str(value)}
    wanted_request = str(request_id or "")
    if not wanted_ids and not wanted_dates and not wanted_request:
        raise SeasonStateError(
            "Refusing ban release: provide --date, --ban-id and/or --request-id"
        )

    snapshot = service.load(season)
    decisions = copy.deepcopy(snapshot.decisions)
    records = decisions.get(BANNED_DATES_KEY, []) or []
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    released: list[str] = []
    released_dates: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("status") or BANNED_DATE_ACTIVE) != BANNED_DATE_ACTIVE:
            continue
        matches_id = str(record.get("id") or "") in wanted_ids
        matches_date = str(record.get("date") or "") in wanted_dates
        matches_request = bool(wanted_request) and str(record.get("request_id") or "") == wanted_request
        if not (matches_id or matches_date or matches_request):
            continue
        record["status"] = BANNED_DATE_RELEASED
        record["released_at"] = now
        record["released_by"] = resolved_actor
        record["release_reason"] = note or ""
        released.append(str(record.get("id") or ""))
        released_dates.append(str(record.get("date") or ""))

    if not released:
        raise SeasonStateError("No active banned dates matched the release request")

    decisions["updated_at"] = now
    _append_decision_history(
        decisions,
        event="unban_date",
        tournament_id="",
        actor=resolved_actor,
        now=now,
        note=note,
        details={
            "released_banned_date_ids": released,
            "released_dates": released_dates,
            "request_id": wanted_request,
        },
    )
    committed = service._commit(snapshot.with_decisions(decisions))
    return {
        "season": season,
        "canonical_state_revision": canonical_state_revision(
            committed.schedule, committed.decisions
        ),
        "released_banned_date_ids": released,
        "released_dates": sorted(set(released_dates)),
        "active_count": len(active_banned_date_records(committed.decisions)),
    }
