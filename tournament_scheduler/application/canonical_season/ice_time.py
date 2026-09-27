"""Canonical per-tournament ice-time override use case.

This is the application-layer owner for ``season set-ice-time-minutes`` /
``season clear-ice-time-minutes`` / ``season ice-time-overrides``. It records a
host-confirmed duration decision without editing the schedule or the age-group
configuration, and it validates the value against the format/governing floors so
an override can never silently make a tournament physically impossible.

Every verification/booking consumer resolves the duration through
:func:`~tournament_scheduler.canonical_ice_time_overrides.project_overrides_into_problem`,
which :func:`~tournament_scheduler.application.canonical_season.shared._resolve_plan_problem`
and :func:`~tournament_scheduler.season_maintenance.project_canonical_overlays`
both apply.
"""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.canonical_ice_time_overrides import (
    ACTIVE,
    RELEASED,
    IceTimeOverrideError,
    active_override_records,
    override_for_tournament,
    override_records,
    validate_and_normalize,
)
from tournament_scheduler.canonical_state import canonical_state_revision
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.occupancy import (
    governing_minimum_ice_time_minutes,
    minimum_playing_requirement_minutes,
)

from .shared import (
    _append_decision_history,
    _now_iso,
    _operator_identity,
    _resolve_plan_problem,
)


def _tournament_round_count(tournament: Mapping[str, Any]) -> int:
    rounds: list[int] = []
    for game in tournament.get("games") or []:
        if isinstance(game, Mapping):
            try:
                rounds.append(int(game.get("round_number") or 0))
            except (TypeError, ValueError):
                pass
    return max(rounds, default=0)


def _minimum_override_minutes(
    tournament: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
) -> int:
    """Return the format/governing floor an override may not go below."""

    age_group = str(tournament.get("age_group") or "")
    round_length = ((problem or {}).get("round_length_minutes") or {}).get(age_group)
    format_minimum = minimum_playing_requirement_minutes(
        round_length if isinstance(round_length, int) else None,
        _tournament_round_count(tournament),
    )
    governing = governing_minimum_ice_time_minutes(age_group) or 0
    return max(format_minimum, governing)


def _find_tournament(plan: Mapping[str, Any], tournament_id: str) -> dict[str, Any] | None:
    return next(
        (
            tournament
            for tournament in plan.get("tournaments", []) or []
            if str(tournament.get("id") or "") == tournament_id
        ),
        None,
    )


def set_ice_time_minutes(
    service,
    *,
    season: str,
    tournament_id: str,
    minutes: int,
    request_id: str,
    actor: str | None = None,
    note: str = "",
    reference: str = "",
    expected_revision: str | None = None,
    problem: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Record one host-confirmed per-tournament ice-time override.

    The write is decision-only: it never moves a tournament or changes the
    age-group configuration. It advances the canonical revision and invalidates
    stale duration-dependent verification/booking evidence. Repeating the same
    decision for the same tournament is idempotent; a genuinely new decision
    supersedes the previous active override while retaining it for audit.
    """

    try:
        normalized = validate_and_normalize(
            {
                "tournament_id": tournament_id,
                "minutes": minutes,
                "request_id": request_id,
                "note": note,
                "reference": reference,
            }
        )
    except IceTimeOverrideError as exc:
        raise SeasonStateError(str(exc)) from exc
    if not (str(note).strip() or str(reference).strip()):
        raise SeasonStateError(
            "An ice-time override requires a traceable host-confirmation reference (--reference) "
            "or rationale (--note)"
        )

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    current_revision = canonical_state_revision(schedule, decisions)
    if expected_revision and expected_revision != current_revision:
        raise SeasonStateError(
            f"Stale canonical revision: expected {expected_revision}, current is {current_revision}"
        )
    plan = schedule["plan"]
    tournament = _find_tournament(plan, tournament_id)
    if tournament is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    if tournament.get("cancelled"):
        raise SeasonStateError(
            f"Tournament {tournament_id} is cancelled and cannot carry an ice-time override"
        )

    resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
    floor = _minimum_override_minutes(tournament, resolved_problem)
    if floor and int(normalized["minutes"]) < floor:
        raise SeasonStateError(
            f"Refusing ice-time override for {tournament_id}: {normalized['minutes']} minutes is below "
            f"the required minimum {floor} minutes for its actual round plan and governing booking floor"
        )

    age_group = str(tournament.get("age_group") or "")
    default_minutes = ((resolved_problem or {}).get("ice_time_minutes") or {}).get(age_group)
    existing = override_for_tournament(decisions, tournament_id)
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    if existing is not None and int(existing.get("minutes") or 0) == int(normalized["minutes"]):
        return {
            "season": season,
            "dry_run": bool(dry_run),
            "changed": False,
            "idempotent": True,
            "tournament_id": tournament_id,
            "override": existing,
            "canonical_state_revision": current_revision,
        }

    record = {
        **normalized,
        "status": ACTIVE,
        "age_group": age_group,
        "default_minutes": int(default_minutes) if isinstance(default_minutes, int) else None,
        "minimum_minutes": floor or None,
        "created_at": now,
        "created_by": resolved_actor,
    }
    updated = dict(decisions)
    prior = [
        dict(row)
        for row in updated.get("ice_time_minutes_overrides") or []
        if isinstance(row, Mapping)
    ]
    if existing is not None:
        existing_id = str(existing.get("id") or "")
        for row in prior:
            if str(row.get("id") or "") == existing_id:
                row["status"] = RELEASED
                row["released_at"] = now
                row["released_by"] = resolved_actor
                row["release_reason"] = f"superseded by {record['id']}"
        record["supersedes"] = existing_id
    prior.append(record)
    updated["ice_time_minutes_overrides"] = prior
    updated["updated_at"] = now
    _append_decision_history(
        updated,
        event="set_ice_time_minutes",
        tournament_id=tournament_id,
        actor=resolved_actor,
        now=now,
        note=note,
        details={
            "override_id": record["id"],
            "minutes": record["minutes"],
            "default_minutes": record.get("default_minutes"),
            "minimum_minutes": record.get("minimum_minutes"),
            "request_id": record["request_id"],
            "reference": record.get("reference") or "",
            "supersedes": record.get("supersedes") or "",
        },
    )
    result: dict[str, Any] = {
        "season": season,
        "dry_run": bool(dry_run),
        "changed": True,
        "idempotent": False,
        "tournament_id": tournament_id,
        "override": record,
        "previous_override": existing,
        "canonical_state_revision": current_revision,
    }
    if dry_run:
        return result
    committed = service._commit(snapshot.with_decisions(updated))
    result["canonical_state_revision"] = canonical_state_revision(committed.schedule, committed.decisions)
    return result


def clear_ice_time_minutes(
    service,
    *,
    season: str,
    tournament_id: str,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Release the active override for one tournament, restoring the age default."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    current_revision = canonical_state_revision(schedule, decisions)
    if _find_tournament(schedule["plan"], tournament_id) is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    existing = override_for_tournament(decisions, tournament_id)
    if existing is None:
        return {
            "season": season,
            "dry_run": bool(dry_run),
            "changed": False,
            "tournament_id": tournament_id,
            "released": None,
            "canonical_state_revision": current_revision,
        }
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    updated = dict(decisions)
    existing_id = str(existing.get("id") or "")
    released: dict[str, Any] | None = None
    records = [
        dict(row)
        for row in updated.get("ice_time_minutes_overrides") or []
        if isinstance(row, Mapping)
    ]
    for row in records:
        if str(row.get("id") or "") == existing_id:
            row["status"] = RELEASED
            row["released_at"] = now
            row["released_by"] = resolved_actor
            row["release_reason"] = note or "released by operator"
            released = row
    updated["ice_time_minutes_overrides"] = records
    updated["updated_at"] = now
    _append_decision_history(
        updated,
        event="clear_ice_time_minutes",
        tournament_id=tournament_id,
        actor=resolved_actor,
        now=now,
        note=note,
        details={"override_id": existing_id},
    )
    result: dict[str, Any] = {
        "season": season,
        "dry_run": bool(dry_run),
        "changed": True,
        "tournament_id": tournament_id,
        "released": released,
        "canonical_state_revision": current_revision,
    }
    if dry_run:
        return result
    committed = service._commit(snapshot.with_decisions(updated))
    result["canonical_state_revision"] = canonical_state_revision(committed.schedule, committed.decisions)
    return result


def ice_time_override_report(
    service,
    *,
    season: str,
    problem: dict[str, Any] | None = None,
    include_released: bool = False,
) -> dict[str, Any]:
    """Return the durable overrides with the age-group default they replace."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule["plan"]
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
    ice = (resolved_problem or {}).get("ice_time_minutes") or {}
    rows: list[dict[str, Any]] = []
    for record in override_records(decisions, include_released=include_released):
        tournament_id = str(record.get("tournament_id") or "")
        tournament = _find_tournament(plan, tournament_id)
        age_group = str((tournament or {}).get("age_group") or record.get("age_group") or "")
        try:
            default_minutes = int(ice.get(age_group) or 0) or None
        except (TypeError, ValueError):
            default_minutes = None
        rows.append(
            {
                **record,
                "age_group": age_group,
                "default_minutes": default_minutes,
                "tournament_present": tournament is not None,
            }
        )
    return {
        "season": season,
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "active_count": len(active_override_records(decisions)),
        "overrides": rows,
    }


__all__ = [
    "clear_ice_time_minutes",
    "ice_time_override_report",
    "set_ice_time_minutes",
]
