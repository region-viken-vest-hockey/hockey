"""Read-only tournament-move discovery over the shared consequence preview.

The operator names a bounded set of tournaments. For each one, discovery looks
for other dates on the tournament's own host where that host's ice is verified
free at the tournament's own start time and duration. Every such slot is then
handed to the shared move preview, which owns validity, approvals, booking and
fairness judgments. Discovery never writes, never ranks and never reimplements
those gates.

Only verified free ice counts. Club-controlled, movable and unknown capacity are
not proposed, because each would need a host or operator decision that this
slice does not model.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from tournament_scheduler.booking.candidate_weekend_availability import (
    AVAILABILITY_FREE,
    classify_candidate_availability,
)
from tournament_scheduler.booking.candidate_weekends import season_weekend_dates
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.planning.contract import external_calendar_conflict, movable_calendar_opportunity
from tournament_scheduler.season.baseline_overlay import resolve_approval
from tournament_scheduler.season.state import canonical_state_revision

from .consequence_preview import preview_canonical_operation
from .shared import _resolve_plan_problem

MAX_SCOPE_TOURNAMENTS = 10
MAX_EVALUATED_CANDIDATES = 100
RANKING_POLICY = "none: deterministic order by (tournament_id, date, start_time, arena)"


def search_tournament_moves(
    service,
    *,
    season: str,
    tournament_ids: list[str],
) -> dict[str, Any]:
    """Return every evaluated same-host free-ice move in the scope, unranked, with shared verdicts."""

    scope = sorted({str(item) for item in tournament_ids if str(item)})
    if not 1 <= len(scope) <= MAX_SCOPE_TOURNAMENTS:
        raise ValueError(
            f"Move discovery needs 1 to {MAX_SCOPE_TOURNAMENTS} distinct tournament ids; got {len(scope)}"
        )

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    revision = canonical_state_revision(schedule, decisions)
    problem = _resolve_plan_problem(schedule, None, decisions)
    if problem is None:
        raise SeasonStateError(
            "Move discovery needs a promoted verification context to read host ice; none is present"
        )
    by_id = {
        str(tournament.get("id") or ""): tournament
        for tournament in (schedule.get("plan") or {}).get("tournaments", []) or []
    }

    excluded: list[dict[str, str]] = []
    eligible: list[dict[str, Any]] = []
    for tournament_id in scope:
        tournament = by_id.get(tournament_id)
        if tournament is None:
            raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
        if tournament.get("cancelled"):
            excluded.append({"tournament_id": tournament_id, "reason": "cancelled"})
            continue
        resolved = resolve_approval(
            decisions.get("decisions", {}).get(tournament_id, {}),
            tournament,
        )
        # A move changes placement, so only a placement lock excludes it. A
        # participant-only lock does not, because the roster stays the same.
        if resolved["placement_locked"]:
            excluded.append({"tournament_id": tournament_id, "reason": "placement_locked"})
            continue
        # A zero or missing duration would make the free-ice check vacuous, so
        # the tournament is excluded rather than searched with a guessed length.
        if _duration_minutes(problem, str(tournament.get("age_group") or "")) <= 0:
            excluded.append({"tournament_id": tournament_id, "reason": "no_duration_configured"})
            continue
        eligible.append(tournament)

    slots = [
        (tournament, day.isoformat())
        for tournament in eligible
        for day in _free_ice_dates(problem, tournament)
    ]
    slots.sort(key=lambda item: (
        str(item[0]["id"]), item[1], str(item[0].get("start_time") or ""), str(item[0].get("arena") or ""),
    ))

    candidates = [
        _candidate(service, season, revision, tournament, day_iso)
        for tournament, day_iso in slots[:MAX_EVALUATED_CANDIDATES]
    ]

    after = service.load(season)
    if canonical_state_revision(after.schedule, after.decisions) != revision:
        raise RuntimeError("Move candidate discovery changed canonical state; discovery must never write")

    if not eligible:
        empty_reason = "no_eligible_tournaments"
    elif not slots:
        empty_reason = "no_available_ice"
    else:
        empty_reason = None
    return {
        "season": season,
        "canonical_state_revision": revision,
        "scope": scope,
        "excluded_tournaments": excluded,
        "limits": {
            "max_scope_tournaments": MAX_SCOPE_TOURNAMENTS,
            "max_evaluated_candidates": MAX_EVALUATED_CANDIDATES,
        },
        "truncated": len(slots) > MAX_EVALUATED_CANDIDATES,
        # True only when every verified free slot in scope was evaluated. A zero
        # safe count is exhaustive only when this is True.
        "complete": len(slots) <= MAX_EVALUATED_CANDIDATES,
        "candidate_count": len(candidates),
        "safe_candidate_count": sum(1 for item in candidates if item["safe_to_apply"]),
        "empty_reason": empty_reason,
        "ranking_policy": RANKING_POLICY,
        "candidates": candidates,
        "apply_command": "season move requires an explicit request id and is never invoked by this discovery",
    }


def _free_ice_dates(problem: Mapping[str, Any], tournament: Mapping[str, Any]) -> list[date]:
    """Weekend dates where the tournament's own host has verified free ice at its own slot."""

    host = str(tournament.get("host_club") or "")
    start_time = str(tournament.get("start_time") or "")
    duration = _duration_minutes(problem, str(tournament.get("age_group") or ""))
    busy = problem.get("club_busy_intervals") or {}
    calendar_known = (problem.get("club_calendar_status") or {}).get(host) == "known"
    current = str(tournament.get("date") or "")

    free: list[date] = []
    for day in season_weekend_dates(problem):
        if day.isoformat() == current:
            continue
        if external_calendar_conflict(busy, host, day, start_time, duration):
            continue
        movable = movable_calendar_opportunity(busy, host, day, start_time, duration)
        availability, _, _, _ = classify_candidate_availability(
            calendar_known, movable, host, day, start_time, duration
        )
        if availability == AVAILABILITY_FREE:
            free.append(day)
    return free


def _duration_minutes(problem: Mapping[str, Any], age_group: str) -> int:
    configured = (problem.get("ice_time_minutes") or {}).get(age_group)
    if configured is None:
        configured = (problem.get("round_length_minutes") or {}).get(age_group)
    return int(configured or 0)


def _candidate(service, season, revision, tournament, day_iso) -> dict[str, Any]:
    preview = preview_canonical_operation(
        service,
        operation="move",
        season=season,
        expected_revision=revision,
        tournament_id=str(tournament["id"]),
        date=day_iso,
    )
    verdict = preview["verdict"]
    return {
        "tournament_id": str(tournament["id"]),
        "date": day_iso,
        "start_time": str(tournament.get("start_time") or ""),
        "arena": str(tournament.get("arena") or ""),
        "host_club": str(tournament.get("host_club") or ""),
        "age_group": str(tournament.get("age_group") or ""),
        "availability": AVAILABILITY_FREE,
        "verdict": verdict,
        "safe_to_apply": bool(verdict["applicable"]),
        "blockers": list(verdict["blockers"]),
        "candidate_fingerprint": preview.get("candidate_fingerprint"),
        "canonical_preview": {key: value for key, value in preview.items() if key != "details"},
    }


__all__ = ["MAX_EVALUATED_CANDIDATES", "MAX_SCOPE_TOURNAMENTS", "search_tournament_moves"]
