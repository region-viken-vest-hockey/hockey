"""Participant swap mutations."""

from __future__ import annotations

import copy
from typing import Any

from tournament_scheduler.season.baseline_overlay import resolve_approval
from tournament_scheduler.season.state import (
    canonical_state_revision,
    schedule_fingerprint,
)
from tournament_scheduler.season.change_protections import (
    build_swap_protections,
    protection_violations,
)
from tournament_scheduler.season.request_constraints import (
    compare_request_constraint_violations,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.planning.derived_state import reconcile_plan_derived_state

from .single_operation_gate import refuse_on_verification, verify_single_operation_candidate
from .swap_plan import _apply_swap_to_plan
from .shared import (
    _operator_identity,
    _now_iso,
    _resolve_plan_problem,
)

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
    expected_revision: str | None = None,
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

    from tournament_scheduler.participation.team_schedule_quality import (
        RegressionAcceptanceError,
        evaluate_regression_acceptances,
        parse_regression_acceptances,
        regression_acceptance_refusals,
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

    from tournament_scheduler.season.baseline_overlay import (
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

    result = verify_single_operation_candidate(schedule.get("plan") or {}, plan, resolved_problem)
    refusal = refuse_on_verification(
        result,
        dry_run,
        season,
        schedule,
        "canonical participant swap",
        "swap",
        {"tournament_a_id": tournament_a_id, "tournament_b_id": tournament_b_id},
    )
    if refusal is not None:
        return refusal

    if resolved_problem:
        from tournament_scheduler.hosting.responsibility import (
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
    constraint_comparison = compare_request_constraint_violations(
        schedule.get("plan") or {}, plan, decisions
    )
    candidate_revision = schedule_fingerprint(plan)
    cost = change_cost(baseline, plan)

    from tournament_scheduler.participation.team_schedule_quality import (
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
    consequence_acceptable = bool(regression_acceptance["acceptable"])
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
        "request_constraint_violations": constraint_comparison["candidate_violations"],
        "request_constraint_regressions": constraint_comparison["regressions"],
        "request_constraint_unchanged_violations": constraint_comparison["unchanged"],
        "request_constraint_acceptable": constraint_comparison["acceptable"],
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
            "swap": {**details, "verification_result": result},
        }

    if existing_protection_violations:
        messages = "; ".join(
            str(item.get("message")) for item in existing_protection_violations
        )
        raise SeasonStateError(
            "Refusing canonical participant swap: it would undo an accepted change: "
            + messages
        )
    if constraint_comparison["regressions"]:
        messages = "; ".join(
            str(item.get("message")) for item in constraint_comparison["regressions"]
        )
        raise SeasonStateError(
            "Refusing canonical participant swap: it violates an active request constraint: "
            + messages
        )

    if not consequence_acceptable:
        raise SeasonStateError(
            "Refusing canonical participant swap: it "
            + "; ".join(regression_acceptance_refusals(regression_acceptance))
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
    # Called directly so the reviewed revision reaches the commit compare-and-swap;
    # the service method is a pure forwarder that cannot carry it.
    from .candidates import apply_candidate

    updated_schedule, updated_decisions, applied_cost = apply_candidate(
        service,
        season=season,
        candidate=plan,
        problem=resolved_problem,
        actor=actor,
        operation="targeted_mutation",
        _expected_revision=expected_revision,
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
