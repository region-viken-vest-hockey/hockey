"""Reserved guest-slot lifecycle."""

from __future__ import annotations

from datetime import date as _date
from typing import Any, Mapping

from tournament_scheduler.canonical_baseline import approval_fingerprint, resolve_approval
from tournament_scheduler.canonical_state import (
    canonical_state_revision,
    schedule_fingerprint,
)
from tournament_scheduler.guest_slots import (
    DEFAULT_GUEST_AGE_GROUPS,
    GUEST_SLOT_FILLED,
    GUEST_SLOT_OPEN,
    GUEST_SLOT_RELEASED,
    active_guest_slot_count,
    capacity_places,
    guest_slot_records,
    guest_slot_summary,
    new_guest_slot,
    rvv_team_count,
    rvv_teams,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.plan_derived_state import reconcile_plan_derived_state
from tournament_scheduler.planning_contract import verify_candidate

from .shared import (
    _operator_identity,
    _now_iso,
    _append_decision_history,
    _reconcile_decisions,
    _resolve_plan_problem,
    _regenerate_tournament_games,
)

def _configured_capacity(problem: Mapping[str, Any] | None, age_group: str) -> int | None:
    """Return the configured participant+guest place capacity for an age group."""

    parallel = ((problem or {}).get("parallel_games") or {}).get(age_group)
    if isinstance(parallel, int) and parallel > 0:
        return parallel * 2
    return None


def guest_slot_report(service, season: str) -> dict[str, Any]:
    """Read-only lifecycle status of every reserved guest place."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    tournaments: list[dict[str, Any]] = []
    for tournament in schedule["plan"].get("tournaments", []) or []:
        records = guest_slot_records(tournament)
        if not records:
            continue
        tournaments.append(
            {
                "tournament_id": str(tournament.get("id") or ""),
                "date": tournament.get("date"),
                "age_group": tournament.get("age_group"),
                "host_club": tournament.get("host_club"),
                "rvv_team_count": rvv_team_count(tournament),
                **guest_slot_summary(tournament),
            }
        )
    return {
        "season": season,
        "revision": schedule.get("revision"),
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "tournament_count": len(tournaments),
        "reserved_total": sum(int(entry["reserved"]) for entry in tournaments),
        "open_total": sum(int(entry["open"]) for entry in tournaments),
        "filled_total": sum(int(entry["filled"]) for entry in tournaments),
        "tournaments": tournaments,
    }


def guest_slot_candidates(
    service,
    *,
    season: str,
    age_groups: list[str] | tuple[str, ...] | None = None,
    max_per_tournament: int = 1,
    problem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic candidate facts for a policy-level reservation request.

    This never chooses *which* tournaments receive a reservation; it exposes
    the legal alternatives with the facts (free places, existing
    reservations, half, approval state, displaceable participants) and a
    deterministic spread-aware ranking so the controller can make and
    record the contextual choice.
    """

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
    allowed = {str(group) for group in (age_groups or DEFAULT_GUEST_AGE_GROUPS)}
    records = decisions.get("decisions", {}) or {}
    from tournament_scheduler import planning_half

    start = schedule["plan"].get("start_date")
    end = schedule["plan"].get("end_date")
    split = None
    if start and end:
        try:
            split = planning_half.christmas_split_date(
                _date.fromisoformat(str(start)), _date.fromisoformat(str(end))
            )
        except ValueError:
            split = None

    candidates: list[dict[str, Any]] = []
    for tournament in schedule["plan"].get("tournaments", []) or []:
        age_group = str(tournament.get("age_group") or "")
        if age_group not in allowed or tournament.get("cancelled"):
            continue
        tournament_id = str(tournament.get("id") or "")
        capacity = _configured_capacity(resolved_problem, age_group)
        used = capacity_places(tournament)
        free = (capacity - used) if capacity is not None else None
        active = active_guest_slot_count(tournament)
        approval = resolve_approval(records.get(tournament_id), tournament)
        locked = bool(approval.get("placement_locked") or approval.get("participants_locked"))
        displaceable = service._displaceable_team_options(tournament)
        has_room = locked is False and (
            free is None
            or free > 0
            or (active < max_per_tournament and bool(displaceable))
        )
        half = None
        try:
            tournament_date = _date.fromisoformat(str(tournament.get("date")))
            if split is not None:
                half = planning_half.tournament_half(tournament_date, split)
        except (TypeError, ValueError):
            tournament_date = None
        candidates.append(
            {
                "tournament_id": tournament_id,
                "date": tournament.get("date"),
                "age_group": age_group,
                "host_club": tournament.get("host_club"),
                "half": half,
                "configured_capacity": capacity,
                "current_places": used,
                "free_places": free,
                "active_reservations": active,
                "at_max_per_tournament": active >= max_per_tournament,
                "reservation_requires_participant_change": bool(
                    locked is False and free is not None and free <= 0 and displaceable
                ),
                "placement_locked": bool(approval.get("placement_locked")),
                "participants_locked": bool(approval.get("participants_locked")),
                "approval_status": approval.get("status"),
                "legal": has_room,
                "displaceable_team_options": displaceable,
            }
        )

    # Deterministic, spread-aware ranking: reservations that need no
    # participant change come first, then a half that is currently short of
    # reservations, then earlier dates, then the stable tournament id.
    half_totals: dict[str, int] = {}
    for candidate in candidates:
        key = str(candidate.get("half") or "unknown")
        half_totals[key] = half_totals.get(key, 0) + int(candidate.get("active_reservations") or 0)

    def _rank(candidate: dict[str, Any]) -> tuple:
        half_key = str(candidate.get("half") or "unknown")
        free = candidate.get("free_places")
        needs_change = bool(candidate.get("reservation_requires_participant_change"))
        return (
            0 if candidate["legal"] else 1,
            1 if needs_change else 0,
            half_totals.get(half_key, 0),
            str(candidate.get("date") or ""),
            str(candidate.get("tournament_id") or ""),
        )

    ranked = sorted(candidates, key=_rank)
    for index, candidate in enumerate(ranked, start=1):
        candidate["rank"] = index

    return {
        "season": season,
        "revision": schedule.get("revision"),
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "age_groups": sorted(allowed),
        "max_per_tournament": max_per_tournament,
        "candidates": ranked,
        "legal_candidates": [c for c in ranked if c["legal"]],
    }


def _displaceable_team_options(tournament: Mapping[str, Any]) -> list[dict[str, Any]]:
    """RVV participants that a reservation may displace without breaking host representation."""

    rvv = rvv_teams(tournament)
    host_club = str(tournament.get("host_club") or "")
    host_club_teams = [team for team in rvv if str(team.get("club") or "") == host_club]
    protect_host = len(host_club_teams) == 1
    options: list[dict[str, Any]] = []
    for team in rvv:
        club = str(team.get("club") or "")
        if protect_host and club == host_club:
            continue
        options.append(
            {
                "label": str(team.get("label") or ""),
                "club": club,
                "age_group": str(team.get("age_group") or ""),
            }
        )
    return options


def reserve_guest_slot(
    service,
    *,
    season: str,
    tournament_id: str,
    count: int = 1,
    displaced_teams: list[str] | None = None,
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Reserve one or more guest places on one canonical tournament.

    Spare capacity is reserved without removing a real team. When the
    tournament is already full, the caller must name the participant(s) to
    displace from the deterministic ``guest_slot_candidates`` facts; a
    participant is never dropped arbitrarily, and host representation must
    survive (enforced by the independent verifier).
    """

    if int(count) < 1:
        raise SeasonStateError("Refusing guest reservation: count must be at least 1")
    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
    plan = dict(schedule["plan"])
    tournaments = [dict(t) for t in plan.get("tournaments", [])]
    target = next((t for t in tournaments if str(t.get("id")) == tournament_id), None)
    if target is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    if target.get("cancelled"):
        raise SeasonStateError(f"Tournament {tournament_id} is cancelled and cannot receive a reservation")
    record = decisions.get("decisions", {}).get(tournament_id, {})
    approval = resolve_approval(record, target)
    if approval.get("placement_locked") or approval.get("participants_locked"):
        raise SeasonStateError(
            f"Tournament {tournament_id} has an active approval/lock and cannot be mutated; "
            "unapprove it explicitly first"
        )

    age_group = str(target.get("age_group") or "")
    capacity = _configured_capacity(resolved_problem, age_group)
    used = capacity_places(target)
    free = (capacity - used) if capacity is not None else None
    requested = int(count)
    needed_displacements = 0
    if free is not None:
        needed_displacements = max(0, requested - max(0, free))
    displaced = [str(label) for label in (displaced_teams or [])]
    if needed_displacements > len(displaced):
        raise SeasonStateError(
            f"Refusing guest reservation on {tournament_id}: the tournament has "
            f"{max(0, free or 0)} free place(s) and no arbitrary participant is dropped; "
            f"request guest_slot_candidates and name {needed_displacements} displaced team(s)"
        )

    rvv_labels = {str(team.get("label") or "") for team in rvv_teams(target)}
    host_club = str(target.get("host_club") or "")
    host_rvv = [team for team in rvv_teams(target) if str(team.get("club") or "") == host_club]
    for label in displaced:
        if label not in rvv_labels:
            raise SeasonStateError(
                f"Refusing guest reservation on {tournament_id}: {label!r} is not a participant"
            )
        if len(host_rvv) == 1 and str(host_rvv[0].get("label")) == label:
            raise SeasonStateError(
                f"Refusing guest reservation on {tournament_id}: {label!r} is the host club's "
                "only participating team and host representation must survive"
            )

    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    records = [dict(item) for item in (target.get("guest_slots") or []) if isinstance(item, Mapping)]
    used_ids = {str(item.get("id") or "") for item in records}
    created: list[dict[str, Any]] = []
    for _ in range(requested):
        index = 1
        while f"guest:{tournament_id}:{index}" in used_ids:
            index += 1
        slot = new_guest_slot(
            reserved_by=resolved_actor,
            reserved_at=now,
            note=note,
            slot_id=f"guest:{tournament_id}:{index}",
        )
        used_ids.add(str(slot["id"]))
        created.append(slot)
    target["guest_slots"] = records + created
    if displaced:
        target["teams"] = [
            team
            for team in target.get("teams", [])
            if team.get("guest") or str(team.get("label") or "") not in set(displaced)
        ]
    target["reserved_guest_slots"] = active_guest_slot_count(target)
    _regenerate_tournament_games(target, resolved_problem)
    plan["tournaments"] = tournaments

    result = (
        verify_candidate(plan, resolved_problem)
        if resolved_problem
        else verify_candidate(plan)
    )
    if not result.get("ok", True):
        messages = "; ".join(
            str(v.get("message") or v.get("code")) for v in result.get("violations", [])
        )
        raise SeasonStateError(
            f"Refusing canonical guest reservation: candidate fails hard verification: {messages}"
        )
    reconcile_plan_derived_state(plan, result, problem=resolved_problem)
    if not dry_run:
        service._assert_request_constraints_satisfied(
            plan, decisions, action="guest reservation"
        )

    fingerprint = schedule_fingerprint(plan)
    details = {
        "slots": created,
        "displaced_teams": displaced,
        "age_group": age_group,
        "configured_capacity": capacity,
        "places_before": used,
        "before_fingerprint": str(schedule.get("revision") or ""),
        "after_fingerprint": fingerprint,
        "verification_ok": True,
    }
    updated_schedule = {
        **schedule,
        "updated_at": now,
        "revision": fingerprint,
        "fingerprint": fingerprint,
        "plan": plan,
    }
    if dry_run:
        updated_schedule["dry_run"] = True
        updated_schedule["guest_reservation_preview"] = {
            "tournament_id": tournament_id,
            **details,
        }
        return updated_schedule

    updated_decisions = {
        **decisions,
        "updated_at": now,
        "schedule_fingerprint": fingerprint,
        "decisions": _reconcile_decisions(decisions.get("decisions", {}), plan, now=now),
    }
    _append_decision_history(
        updated_decisions,
        event="reserve_guest_slot",
        tournament_id=tournament_id,
        actor=resolved_actor,
        now=now,
        tournament_fingerprint=approval_fingerprint(target),
        note=note,
        details=details,
    )
    committed = service._commit(
        snapshot.with_schedule(updated_schedule).with_decisions(updated_decisions)
    )
    return committed.schedule


def fill_guest_slot(
    service,
    *,
    season: str,
    tournament_id: str,
    slot_id: str | None,
    external_team: dict[str, Any],
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Accept an external team into a reserved place and regenerate games."""

    if not isinstance(external_team, Mapping) or not str(external_team.get("label") or ""):
        raise SeasonStateError("Refusing guest fill: an external team label is required")
    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
    plan = dict(schedule["plan"])
    tournaments = [dict(t) for t in plan.get("tournaments", [])]
    target = next((t for t in tournaments if str(t.get("id")) == tournament_id), None)
    if target is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")

    records = [dict(item) for item in (target.get("guest_slots") or []) if isinstance(item, Mapping)]
    chosen: dict[str, Any] | None = None
    for item in records:
        if str(item.get("status") or GUEST_SLOT_OPEN) != GUEST_SLOT_OPEN:
            continue
        if slot_id is not None and str(item.get("id") or "") != slot_id:
            continue
        chosen = item
        break
    if chosen is None:
        raise SeasonStateError(
            f"Refusing guest fill on {tournament_id}: no open reservation matches {slot_id!r}"
        )
    label = str(external_team.get("label") or "")
    if any(str(team.get("label") or "") == label for team in target.get("teams", [])):
        raise SeasonStateError(f"Refusing guest fill on {tournament_id}: {label!r} already participates")

    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    external = {
        "club": str(external_team.get("club") or ""),
        "label": label,
        "age_group": str(external_team.get("age_group") or target.get("age_group") or ""),
    }
    chosen["status"] = GUEST_SLOT_FILLED
    chosen["external_team"] = external
    chosen["filled_at"] = now
    chosen["filled_by"] = resolved_actor
    target["guest_slots"] = records
    target.setdefault("teams", []).append({**external, "guest": True})
    target["reserved_guest_slots"] = active_guest_slot_count(target)
    _regenerate_tournament_games(target, resolved_problem)
    plan["tournaments"] = tournaments

    result = (
        verify_candidate(plan, resolved_problem)
        if resolved_problem
        else verify_candidate(plan)
    )
    if not result.get("ok", True):
        messages = "; ".join(
            str(v.get("message") or v.get("code")) for v in result.get("violations", [])
        )
        raise SeasonStateError(
            f"Refusing canonical guest fill: candidate fails hard verification: {messages}"
        )
    reconcile_plan_derived_state(plan, result, problem=resolved_problem)
    service._assert_request_constraints_satisfied(plan, decisions, action="guest fill")

    fingerprint = schedule_fingerprint(plan)
    updated_schedule = {
        **schedule,
        "updated_at": now,
        "revision": fingerprint,
        "fingerprint": fingerprint,
        "plan": plan,
    }
    updated_decisions = {
        **decisions,
        "updated_at": now,
        "schedule_fingerprint": fingerprint,
        "decisions": _reconcile_decisions(decisions.get("decisions", {}), plan, now=now),
    }
    _append_decision_history(
        updated_decisions,
        event="fill_guest_slot",
        tournament_id=tournament_id,
        actor=resolved_actor,
        now=now,
        tournament_fingerprint=approval_fingerprint(target),
        note=note,
        details={
            "slot_id": chosen.get("id"),
            "external_team": external,
            "before_fingerprint": str(schedule.get("revision") or ""),
            "after_fingerprint": fingerprint,
            "verification_ok": True,
        },
    )
    committed = service._commit(
        snapshot.with_schedule(updated_schedule).with_decisions(updated_decisions)
    )
    return committed.schedule


def release_guest_slot(
    service,
    *,
    season: str,
    tournament_id: str,
    slot_id: str | None = None,
    replacement_team: dict[str, Any] | None = None,
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Release a reservation, optionally filling it with a real RVV team.

    Releasing an open reservation on a tournament that then falls below its
    verified shape is refused unless ``replacement_team`` supplies a legal
    RVV participant, so a released place never silently becomes an
    underfilled tournament.
    """

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
    plan = dict(schedule["plan"])
    tournaments = [dict(t) for t in plan.get("tournaments", [])]
    target = next((t for t in tournaments if str(t.get("id")) == tournament_id), None)
    if target is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")

    records = [dict(item) for item in (target.get("guest_slots") or []) if isinstance(item, Mapping)]
    chosen: dict[str, Any] | None = None
    for item in records:
        if str(item.get("status") or GUEST_SLOT_OPEN) not in (GUEST_SLOT_OPEN, GUEST_SLOT_FILLED):
            continue
        if slot_id is not None and str(item.get("id") or "") != slot_id:
            continue
        chosen = item
        break
    if chosen is None:
        raise SeasonStateError(
            f"Refusing guest release on {tournament_id}: no active reservation matches {slot_id!r}"
        )

    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    removed_guest = None
    if str(chosen.get("status")) == GUEST_SLOT_FILLED:
        external = chosen.get("external_team") or {}
        guest_label = str(external.get("label") or "")
        if guest_label:
            target["teams"] = [
                team
                for team in target.get("teams", [])
                if not (team.get("guest") and str(team.get("label") or "") == guest_label)
            ]
            removed_guest = guest_label
    if isinstance(replacement_team, Mapping) and str(replacement_team.get("label") or ""):
        label = str(replacement_team.get("label") or "")
        if any(str(team.get("label") or "") == label for team in target.get("teams", [])):
            raise SeasonStateError(
                f"Refusing guest release on {tournament_id}: replacement {label!r} already participates"
            )
        target.setdefault("teams", []).append(
            {
                "club": str(replacement_team.get("club") or ""),
                "label": label,
                "age_group": str(replacement_team.get("age_group") or target.get("age_group") or ""),
            }
        )

    chosen["status"] = GUEST_SLOT_RELEASED
    chosen["released_at"] = now
    chosen["released_by"] = resolved_actor
    chosen["release_reason"] = note or ""
    target["guest_slots"] = records
    target["reserved_guest_slots"] = active_guest_slot_count(target)
    _regenerate_tournament_games(target, resolved_problem)
    plan["tournaments"] = tournaments

    result = (
        verify_candidate(plan, resolved_problem)
        if resolved_problem
        else verify_candidate(plan)
    )
    if not result.get("ok", True):
        messages = "; ".join(
            str(v.get("message") or v.get("code")) for v in result.get("violations", [])
        )
        raise SeasonStateError(
            f"Refusing canonical guest release: candidate fails hard verification: {messages}"
        )
    reconcile_plan_derived_state(plan, result, problem=resolved_problem)
    if not dry_run:
        service._assert_request_constraints_satisfied(
            plan, decisions, action="guest release"
        )

    fingerprint = schedule_fingerprint(plan)
    details = {
        "slot_id": chosen.get("id"),
        "removed_guest": removed_guest,
        "replacement_team": dict(replacement_team) if isinstance(replacement_team, Mapping) else None,
        "before_fingerprint": str(schedule.get("revision") or ""),
        "after_fingerprint": fingerprint,
        "verification_ok": True,
    }
    updated_schedule = {
        **schedule,
        "updated_at": now,
        "revision": fingerprint,
        "fingerprint": fingerprint,
        "plan": plan,
    }
    if dry_run:
        updated_schedule["dry_run"] = True
        updated_schedule["guest_release_preview"] = {"tournament_id": tournament_id, **details}
        return updated_schedule

    updated_decisions = {
        **decisions,
        "updated_at": now,
        "schedule_fingerprint": fingerprint,
        "decisions": _reconcile_decisions(decisions.get("decisions", {}), plan, now=now),
    }
    _append_decision_history(
        updated_decisions,
        event="release_guest_slot",
        tournament_id=tournament_id,
        actor=resolved_actor,
        now=now,
        tournament_fingerprint=approval_fingerprint(target),
        note=note,
        details=details,
    )
    committed = service._commit(
        snapshot.with_schedule(updated_schedule).with_decisions(updated_decisions)
    )
    return committed.schedule
