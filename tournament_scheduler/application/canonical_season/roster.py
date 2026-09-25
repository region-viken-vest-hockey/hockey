"""Participant swap mutations."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from tournament_scheduler.canonical_baseline import approval_fingerprint, resolve_approval
from tournament_scheduler.canonical_state import (
    canonical_state_revision,
    schedule_fingerprint,
)
from tournament_scheduler.change_protections import (
    build_swap_protections,
    protection_violations,
)
from tournament_scheduler.request_constraints import (
    request_constraint_violations,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.plan_derived_state import reconcile_plan_derived_state
from tournament_scheduler.planning_contract import verify_candidate

from .shared import (
    _operator_identity,
    _now_iso,
    _resolve_plan_problem,
    _regenerate_tournament_games,
)

def _apply_swap_to_plan(
    plan: dict[str, Any],
    *,
    tournament_a_id: str,
    team_a_label: str,
    tournament_b_id: str,
    team_b_label: str,
    problem: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply one same-age participant swap to an in-memory plan.

    Single owner of roster-swap mutation semantics, shared by the direct
    ``swap_participants`` boundary and atomic batch maintenance: tournament
    existence/cancellation validation, the same-age restriction, participant
    lookup (guests are rejected), duplicate/no-op checks, the actual exchange
    and regeneration of both tournaments' games. Lock, protection, constraint,
    hard-verification, hosting-responsibility and consequence gates stay at the
    caller's boundary.
    """

    if tournament_a_id == tournament_b_id:
        raise SeasonStateError("Participant swap requires two different tournaments")

    tournaments = plan.get("tournaments", []) or []
    by_id = {
        str(tournament.get("id") or ""): tournament
        for tournament in tournaments
        if tournament.get("id")
    }
    tournament_a = by_id.get(tournament_a_id)
    tournament_b = by_id.get(tournament_b_id)
    if tournament_a is None:
        raise SeasonStateError(
            f"Unknown tournament id in canonical schedule: {tournament_a_id}"
        )
    if tournament_b is None:
        raise SeasonStateError(
            f"Unknown tournament id in canonical schedule: {tournament_b_id}"
        )
    for tournament_id, tournament in (
        (tournament_a_id, tournament_a),
        (tournament_b_id, tournament_b),
    ):
        if tournament.get("cancelled"):
            raise SeasonStateError(
                f"Tournament {tournament_id} is cancelled and cannot participate in a roster swap"
            )

    age_group_a = str(tournament_a.get("age_group") or "")
    age_group_b = str(tournament_b.get("age_group") or "")
    if not age_group_a or age_group_a != age_group_b:
        raise SeasonStateError(
            "Participant swap requires tournaments in the same age group; "
            f"got {age_group_a or '<missing>'} and {age_group_b or '<missing>'}"
        )

    def locate_team(
        tournament: dict[str, Any],
        *,
        tournament_id: str,
        label: str,
    ) -> tuple[int, dict[str, Any]]:
        matches = [
            (index, team)
            for index, team in enumerate(tournament.get("teams", []) or [])
            if str(team.get("label") or "") == label
        ]
        if not matches:
            raise SeasonStateError(
                f"Team {label!r} is not a participant in tournament {tournament_id}"
            )
        if len(matches) > 1:
            raise SeasonStateError(
                f"Team label {label!r} is ambiguous in tournament {tournament_id}"
            )
        index, team = matches[0]
        if bool(team.get("guest", False)):
            raise SeasonStateError(
                f"Team {label!r} in tournament {tournament_id} is a guest participant; "
                "use the guest-slot lifecycle instead"
            )
        return index, team

    index_a, team_a = locate_team(
        tournament_a, tournament_id=tournament_a_id, label=team_a_label
    )
    index_b, team_b = locate_team(
        tournament_b, tournament_id=tournament_b_id, label=team_b_label
    )

    def team_identity(team: Mapping[str, Any], fallback_age_group: str) -> tuple[str, str, str]:
        return (
            str(team.get("club") or ""),
            str(team.get("label") or ""),
            str(team.get("age_group") or fallback_age_group),
        )

    identity_a = team_identity(team_a, age_group_a)
    identity_b = team_identity(team_b, age_group_b)
    if identity_a == identity_b:
        raise SeasonStateError("Participant swap would be a no-op")

    for index, existing in enumerate(tournament_a.get("teams", []) or []):
        if index != index_a and team_identity(existing, age_group_a) == identity_b:
            raise SeasonStateError(
                f"Cannot swap {team_b_label!r} into {tournament_a_id}: "
                "that team already participates there"
            )
    for index, existing in enumerate(tournament_b.get("teams", []) or []):
        if index != index_b and team_identity(existing, age_group_b) == identity_a:
            raise SeasonStateError(
                f"Cannot swap {team_a_label!r} into {tournament_b_id}: "
                "that team already participates there"
            )

    before_fingerprint_a = approval_fingerprint(tournament_a)
    before_fingerprint_b = approval_fingerprint(tournament_b)
    original_team_a = copy.deepcopy(team_a)
    original_team_b = copy.deepcopy(team_b)
    tournament_a["teams"][index_a] = copy.deepcopy(team_b)
    tournament_b["teams"][index_b] = copy.deepcopy(team_a)
    _regenerate_tournament_games(tournament_a, problem)
    _regenerate_tournament_games(tournament_b, problem)
    return {
        "tournament_a": tournament_a,
        "tournament_b": tournament_b,
        "team_a": original_team_a,
        "team_b": original_team_b,
        "index_a": index_a,
        "index_b": index_b,
        "identity_a": identity_a,
        "identity_b": identity_b,
        "age_group": age_group_a,
        "before_fingerprint_a": before_fingerprint_a,
        "before_fingerprint_b": before_fingerprint_b,
    }


def swap_participants(
    service,
    *,
    season: str,
    tournament_a_id: str,
    team_a_label: str,
    tournament_b_id: str,
    team_b_label: str,
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
    request_id: str | None = None,
    accept_regressions: list[Any] | None = None,
    accept_regression_reason: str | None = None,
) -> dict[str, Any]:
    """Swap one RVV participant between two same-age canonical tournaments.

    This is a narrow operator mutation for an already-promoted season. It
    deliberately keeps both placements/hosts fixed, regenerates the games
    for both rosters, and validates the whole season through the same
    canonical lock, guest-slot, hard-verification and hosting-responsibility
    gates used by apply_candidate.

    Material regressions of either affected team refuse the swap unless the
    operator explicitly accepts that exact team/regression with a reason.
    """

    if tournament_a_id == tournament_b_id:
        raise SeasonStateError("Participant swap requires two different tournaments")

    from tournament_scheduler.team_schedule_quality import (
        RegressionAcceptanceError,
        evaluate_regression_acceptances,
        parse_regression_acceptances,
    )

    try:
        regression_acceptances = parse_regression_acceptances(
            accept_regressions, accept_regression_reason
        )
    except RegressionAcceptanceError as exc:
        raise SeasonStateError(f"Refusing canonical participant swap: {exc}") from exc

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
    plan = copy.deepcopy(schedule["plan"])

    by_id = {
        str(tournament.get("id") or ""): tournament
        for tournament in plan.get("tournaments", []) or []
        if tournament.get("id")
    }
    for tournament_id in (tournament_a_id, tournament_b_id):
        tournament = by_id.get(tournament_id)
        if tournament is None:
            raise SeasonStateError(
                f"Unknown tournament id in canonical schedule: {tournament_id}"
            )
        if tournament.get("cancelled"):
            raise SeasonStateError(
                f"Tournament {tournament_id} is cancelled and cannot participate in a roster swap"
            )
        resolved = resolve_approval(
            decisions.get("decisions", {}).get(tournament_id, {}),
            tournament,
        )
        if resolved["participants_locked"]:
            raise SeasonStateError(
                f"Tournament {tournament_id} has an active participant lock; "
                "unapprove it explicitly first"
            )

    swap_result = _apply_swap_to_plan(
        plan,
        tournament_a_id=tournament_a_id,
        team_a_label=team_a_label,
        tournament_b_id=tournament_b_id,
        team_b_label=team_b_label,
        problem=resolved_problem,
    )
    tournament_a = swap_result["tournament_a"]
    tournament_b = swap_result["tournament_b"]
    team_a = swap_result["team_a"]
    team_b = swap_result["team_b"]
    identity_a = swap_result["identity_a"]
    identity_b = swap_result["identity_b"]
    age_group_a = swap_result["age_group"]
    before_fingerprint_a = swap_result["before_fingerprint_a"]
    before_fingerprint_b = swap_result["before_fingerprint_b"]

    from tournament_scheduler.canonical_baseline import (
        build_canonical_baseline,
        change_cost,
        verify_canonical_locks,
    )

    baseline = build_canonical_baseline(schedule, decisions)
    lock_violations = verify_canonical_locks(baseline, plan)
    if lock_violations:
        messages = "; ".join(str(v.get("message")) for v in lock_violations)
        raise SeasonStateError(
            f"Refusing canonical participant swap: candidate violates canonical locks: {messages}"
        )

    result = (
        verify_candidate(plan, resolved_problem)
        if resolved_problem
        else verify_candidate(plan)
    )
    if not result.get("ok", True):
        messages = "; ".join(
            str(v.get("message") or v.get("code"))
            for v in result.get("violations", [])
        )
        raise SeasonStateError(
            f"Refusing canonical participant swap: candidate fails hard verification: {messages}"
        )

    if resolved_problem:
        from tournament_scheduler.hosting_responsibility import (
            unexplained_responsibility_transfers,
        )

        transfers = unexplained_responsibility_transfers(
            schedule.get("plan"),
            plan,
            resolved_problem,
        )
        if transfers:
            messages = "; ".join(str(entry.get("message")) for entry in transfers)
            raise SeasonStateError(
                "Refusing canonical participant swap: candidate transfers hosting "
                f"responsibility: {messages}"
            )

    reconcile_plan_derived_state(plan, result, problem=resolved_problem)
    existing_protection_violations = protection_violations(plan, decisions)
    constraint_violations = request_constraint_violations(plan, decisions)
    candidate_revision = schedule_fingerprint(plan)
    cost = change_cost(baseline, plan)

    from tournament_scheduler.team_schedule_quality import (
        compare_team_schedule_consequence,
    )

    team_consequences = {
        "team_a": compare_team_schedule_consequence(
            schedule.get("plan") or {},
            plan,
            identity_a,
            problem=resolved_problem,
        ),
        "team_b": compare_team_schedule_consequence(
            schedule.get("plan") or {},
            plan,
            identity_b,
            problem=resolved_problem,
        ),
    }
    regression_acceptance = evaluate_regression_acceptances(
        team_consequences, regression_acceptances
    )
    consequence_acceptable = not regression_acceptance["unaccepted_regressions"]
    protection_request_id = str(request_id or "")
    protection_created_at = _now_iso()
    new_protections = build_swap_protections(
        team_a=team_a,
        tournament_a_id=tournament_a_id,
        team_b=team_b,
        tournament_b_id=tournament_b_id,
        request_id=protection_request_id,
        actor=_operator_identity(actor),
        note=note,
        created_at=protection_created_at,
        source_revision=canonical_state_revision(schedule, decisions),
    )
    details = {
        "tournament_a_id": tournament_a_id,
        "tournament_b_id": tournament_b_id,
        "age_group": age_group_a,
        "team_a": {
            "club": identity_a[0],
            "label": identity_a[1],
        },
        "team_b": {
            "club": identity_b[0],
            "label": identity_b[1],
        },
        "tournament_a_date": tournament_a.get("date"),
        "tournament_b_date": tournament_b.get("date"),
        "before_fingerprint_a": before_fingerprint_a,
        "before_fingerprint_b": before_fingerprint_b,
        "candidate_revision": candidate_revision,
        "team_consequences": team_consequences,
        "consequence_acceptable": consequence_acceptable,
        "regression_acceptance": regression_acceptance,
        "existing_change_protection_violations": existing_protection_violations,
        "change_protection_acceptable": not existing_protection_violations,
        "request_constraint_violations": constraint_violations,
        "request_constraint_acceptable": not constraint_violations,
        "protections_to_add": new_protections,
        "request_id": protection_request_id,
    }

    if dry_run:
        return {
            "season": season,
            "dry_run": True,
            "current_revision": schedule.get("revision"),
            "candidate_revision": candidate_revision,
            "verification_result": result,
            "change_cost": cost,
            "swap": details,
        }

    if existing_protection_violations:
        messages = "; ".join(
            str(item.get("message")) for item in existing_protection_violations
        )
        raise SeasonStateError(
            "Refusing canonical participant swap: it would undo an accepted change: "
            + messages
        )
    if constraint_violations:
        messages = "; ".join(
            str(item.get("message")) for item in constraint_violations
        )
        raise SeasonStateError(
            "Refusing canonical participant swap: it violates an active request constraint: "
            + messages
        )

    if not consequence_acceptable:
        regressions = [
            f"{item['consequence']}:{item['code']}"
            for item in regression_acceptance["unaccepted_regressions"]
        ]
        raise SeasonStateError(
            "Refusing canonical participant swap: it materially worsens an affected "
            "team's schedule: " + ", ".join(regressions)
        )
    if regression_acceptance["unmatched_acceptances"]:
        raise SeasonStateError(
            "Refusing canonical participant swap: regression acceptance(s) match no "
            "material regression: "
            + ", ".join(
                f"{item['team']}={item['code']}"
                for item in regression_acceptance["unmatched_acceptances"]
            )
        )

    from .scoped_mutation import authorize_participant_swap

    scoped_authorization = authorize_participant_swap(
        schedule=schedule,
        decisions=decisions,
        candidate=plan,
        tournament_a_id=tournament_a_id,
        team_a_label=team_a_label,
        tournament_b_id=tournament_b_id,
        team_b_label=team_b_label,
    )
    updated_schedule, updated_decisions, applied_cost = service.apply_candidate(
        season=season,
        candidate=plan,
        problem=resolved_problem,
        actor=actor,
        operation="targeted_mutation",
        _scoped_authorization=scoped_authorization,
        _new_change_protections=new_protections,
        _history_event={
            "event": "participant_swap",
            "tournament_id": tournament_a_id,
            "previous_fingerprint": before_fingerprint_a,
            "note": note,
            "details": details,
        },
    )
    return {
        "season": season,
        "dry_run": False,
        "revision": updated_schedule.get("revision"),
        "canonical_state_revision": canonical_state_revision(
            updated_schedule,
            updated_decisions,
        ),
        "verification_result": result,
        "change_cost": applied_cost,
        "swap": details,
    }
