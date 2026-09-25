"""Atomic scoped batch maintenance over one in-memory candidate."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from tournament_scheduler.canonical_state import (
    canonical_state_revision,
    compute_canonical_state_revision,
    schedule_fingerprint,
)
from tournament_scheduler.change_protections import (
    append_change_protections,
    build_move_protections,
    build_net_roster_protections,
    protection_violations,
)
from tournament_scheduler.request_constraints import (
    request_constraint_violations,
)
from tournament_scheduler.guest_slots import (
    active_guest_slots,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.operational_acceptability import (
    check_operational_acceptability,
)
from tournament_scheduler.plan_derived_state import reconcile_plan_derived_state
from tournament_scheduler.planning_contract import verify_candidate

from .shared import (
    _operator_identity,
    _now_iso,
    _append_decision_history,
    _reconcile_decisions,
    _resolve_plan_problem,
    _guest_reservation_signature,
    _placement_snapshot,
)
from .placements import _apply_move_to_plan
from .roster import _apply_swap_to_plan

_BATCH_MOVE_FIELDS: tuple[str, ...] = ("date", "arena", "host_club", "start_time")


def _apply_cancel_to_plan(
    plan: dict[str, Any],
    *,
    tournament_id: str,
    reason: str = "",
) -> dict[str, Any]:
    """Mark one tournament cancelled in an in-memory plan.

    Cancellation is represented by the first-class ``cancelled`` flag the
    verifier already honours. The full canonical gates decide whether a batch
    containing a cancellation is acceptable; this helper only applies the
    domain fact.
    """

    tournaments = plan.get("tournaments", []) or []
    target = next((t for t in tournaments if str(t.get("id")) == tournament_id), None)
    if target is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    if target.get("cancelled"):
        raise SeasonStateError(f"Tournament {tournament_id} is already cancelled")
    if active_guest_slots(target):
        raise SeasonStateError(
            f"Cannot cancel {tournament_id}: it still has active guest reservations; "
            "release them explicitly before cancelling"
        )
    target["cancelled"] = True
    if reason:
        target["cancellation_reason"] = reason
    else:
        target.pop("cancellation_reason", None)
    return {
        "tournament_id": tournament_id,
        "reason": reason,
        "original_date": target.get("date"),
        "age_group": target.get("age_group"),
    }


def _normalize_batch_operation(raw: Any) -> dict[str, Any]:
    """Validate and normalize one atomic-batch operation payload."""

    if not isinstance(raw, Mapping):
        raise SeasonStateError("Each batch operation must be a JSON object")
    op = str(raw.get("op") or raw.get("operation") or raw.get("action") or "").strip().lower()
    if op in ("move", "placement_move"):
        tournament_id = str(raw.get("tournament_id") or raw.get("id") or "").strip()
        if not tournament_id:
            raise SeasonStateError("A batch move operation requires tournament_id")
        operation = {
            "op": "move",
            "tournament_id": tournament_id,
            "date": raw.get("date"),
            "arena": raw.get("arena"),
            "host_club": raw.get("host_club"),
            "start_time": raw.get("start_time"),
            "allow_cross_half": bool(raw.get("allow_cross_half", False)),
        }
        if not any(operation[field] is not None for field in _BATCH_MOVE_FIELDS):
            raise SeasonStateError(
                f"A batch move for {tournament_id} requires at least one placement field"
            )
        return operation
    if op in ("swap", "swap_participants", "participant_swap"):
        resolved = {
            "tournament_a": str(raw.get("tournament_a") or raw.get("tournament_a_id") or "").strip(),
            "team_a": str(raw.get("team_a") or raw.get("team_a_label") or "").strip(),
            "tournament_b": str(raw.get("tournament_b") or raw.get("tournament_b_id") or "").strip(),
            "team_b": str(raw.get("team_b") or raw.get("team_b_label") or "").strip(),
        }
        missing = sorted(key for key, value in resolved.items() if not value)
        if missing:
            raise SeasonStateError(
                "A batch swap_participants operation requires " + ", ".join(missing)
            )
        return {"op": "swap_participants", **resolved}
    if op in ("cancel", "cancel_tournament"):
        tournament_id = str(raw.get("tournament_id") or raw.get("id") or "").strip()
        if not tournament_id:
            raise SeasonStateError("A batch cancel operation requires tournament_id")
        return {
            "op": "cancel",
            "tournament_id": tournament_id,
            "reason": str(raw.get("reason") or raw.get("note") or ""),
        }
    raise SeasonStateError(f"Unsupported batch operation {op!r}")


def _batch_operation_tournament_ids(operation: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the tournament ids one normalized batch operation references."""

    op = operation.get("op")
    if op == "move":
        return (str(operation.get("tournament_id") or ""),)
    if op == "swap_participants":
        return (
            str(operation.get("tournament_a") or ""),
            str(operation.get("tournament_b") or ""),
        )
    if op == "cancel":
        return (str(operation.get("tournament_id") or ""),)
    return ()


def batch_maintenance(
    service,
    *,
    season: str,
    operations: list[Mapping[str, Any]],
    scope: list[str] | None = None,
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
    request_id: str | None = None,
    allow_manual_placement: bool = False,
    allow_host_confirmation: bool = False,
    accept_regressions: list[Any] | None = None,
    accept_regression_reason: str | None = None,
) -> dict[str, Any]:
    """Compose several canonical mutations against one in-memory candidate.

    Request constraints are hard: every single ``move`` / ``swap`` / generic
    ``apply`` commit must leave the whole season satisfying every active
    constraint. When one operator request records several independent
    violations, no single mutation can satisfy the whole active set. This
    boundary applies an explicitly scoped set of operations to one in-memory
    copy of the current canonical plan, runs the complete authoritative
    gates once on the final candidate, and commits exactly once.

    The caller declares the affected tournament ids. Every operation must
    reference only in-scope tournaments, and any tournament that changes
    outside the declared scope refuses the whole batch. No canonical state
    is written until every gate passes; an invalid final candidate (or a
    batch that leaves any active request constraint violated) is refused
    without a partial write. It is generic: nothing here is specific to one
    real-world request or calendar.
    """

    if not operations:
        raise SeasonStateError("Refusing canonical batch: at least one operation is required")
    resolved_request_id = str(request_id or "").strip()
    if not resolved_request_id:
        raise SeasonStateError(
            "Refusing canonical batch: a stable --request-id is required so the batch is auditable"
        )
    from tournament_scheduler.team_schedule_quality import (
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
        raise SeasonStateError(f"Refusing canonical batch: {exc}") from exc
    scope_ids = {str(item).strip() for item in (scope or []) if str(item).strip()}
    if not scope_ids:
        raise SeasonStateError(
            "Refusing canonical batch: declare the affected tournament ids with --scope"
        )

    normalized_operations = [
        _normalize_batch_operation(operation) for operation in operations
    ]
    referenced_ids = sorted(
        {
            tournament_id
            for operation in normalized_operations
            for tournament_id in _batch_operation_tournament_ids(operation)
        }
    )
    referenced_outside_scope = [
        tournament_id for tournament_id in referenced_ids if tournament_id not in scope_ids
    ]

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
    before_plan = schedule.get("plan") or {}
    before_fingerprint = str(schedule.get("revision") or schedule.get("fingerprint") or "")
    before_canonical_revision = canonical_state_revision(schedule, decisions)
    before_tournaments = {
        str(tournament.get("id") or ""): tournament
        for tournament in before_plan.get("tournaments", []) or []
        if tournament.get("id")
    }
    unknown_scope_ids = sorted(scope_ids - set(before_tournaments))
    if unknown_scope_ids:
        raise SeasonStateError(
            "Refusing canonical batch: scope names unknown tournament ids: "
            + ", ".join(unknown_scope_ids)
        )

    candidate_plan = copy.deepcopy(before_plan)
    now = _now_iso()
    resolved_actor = _operator_identity(actor)

    applied_moves: list[dict[str, Any]] = []
    applied_swaps: list[dict[str, Any]] = []
    applied_cancellations: list[dict[str, Any]] = []
    new_protections: list[dict[str, Any]] = []

    for operation in normalized_operations:
        op = operation["op"]
        if op == "move":
            result = _apply_move_to_plan(
                candidate_plan,
                tournament_id=operation["tournament_id"],
                date=operation.get("date"),
                arena=operation.get("arena"),
                host_club=operation.get("host_club"),
                start_time=operation.get("start_time"),
                problem=resolved_problem,
                allow_cross_half=bool(operation.get("allow_cross_half", False)),
            )
            applied_moves.append(
                {
                    "tournament_id": operation["tournament_id"],
                    "changed": bool(result["changed"]),
                    "old_placement": result["original_placement"],
                    "new_placement": _placement_snapshot(result["tournament"]),
                }
            )
        elif op == "swap_participants":
            result = _apply_swap_to_plan(
                candidate_plan,
                tournament_a_id=operation["tournament_a"],
                team_a_label=operation["team_a"],
                tournament_b_id=operation["tournament_b"],
                team_b_label=operation["team_b"],
                problem=resolved_problem,
            )
            applied_swaps.append(
                {
                    "tournament_a_id": operation["tournament_a"],
                    "tournament_b_id": operation["tournament_b"],
                    "team_a": {
                        key: result["team_a"].get(key)
                        for key in ("club", "label", "age_group")
                    },
                    "team_b": {
                        key: result["team_b"].get(key)
                        for key in ("club", "label", "age_group")
                    },
                    "identity_a": result["identity_a"],
                    "identity_b": result["identity_b"],
                    "age_group": result["age_group"],
                }
            )
        elif op == "cancel":
            applied_cancellations.append(
                _apply_cancel_to_plan(
                    candidate_plan,
                    tournament_id=operation["tournament_id"],
                    reason=str(operation.get("reason") or ""),
                )
            )
        else:  # pragma: no cover - normalization already rejects unknown operators
            raise SeasonStateError(f"Unsupported batch operation {op!r}")

    candidate_tournaments = {
        str(tournament.get("id") or ""): tournament
        for tournament in candidate_plan.get("tournaments", []) or []
        if tournament.get("id")
    }

    # Roster protections, like placement protections below, are derived from
    # the original -> final membership, so chained swaps protect only their
    # net result (no transient or undone assignment is protected).
    if applied_swaps:
        new_protections.extend(
            build_net_roster_protections(
                before_plan=before_plan,
                after_plan=candidate_plan,
                tournament_ids=scope_ids,
                request_id=resolved_request_id,
                actor=resolved_actor,
                note=note,
                created_at=now,
                source_revision=before_canonical_revision,
            )
        )

    # Placement protections are derived from the final pre-batch -> final
    # difference, so a repeated move of one tournament protects the result.
    for tournament_id in sorted(scope_ids):
        before_tournament = before_tournaments.get(tournament_id)
        after_tournament = candidate_tournaments.get(tournament_id)
        if before_tournament is None or after_tournament is None:
            continue
        before_placement = _placement_snapshot(before_tournament)
        after_placement = _placement_snapshot(after_tournament)
        changed_fields = {
            field: after_placement.get(field)
            for field in ("date", "arena", "host_club", "start_time")
            if before_placement.get(field) != after_placement.get(field)
        }
        if changed_fields:
            new_protections.extend(
                build_move_protections(
                    tournament_id=tournament_id,
                    changed_fields=changed_fields,
                    request_id=resolved_request_id,
                    actor=resolved_actor,
                    note=note,
                    created_at=now,
                    source_revision=before_canonical_revision,
                )
            )

    from tournament_scheduler.canonical_baseline import (
        build_canonical_baseline,
        change_cost,
        verify_canonical_locks,
    )

    baseline = build_canonical_baseline(schedule, decisions)
    verification_result = (
        verify_candidate(candidate_plan, resolved_problem)
        if resolved_problem
        else verify_candidate(candidate_plan)
    )
    before_verification = (
        verify_candidate(dict(before_plan), resolved_problem)
        if resolved_problem
        else verify_candidate(dict(before_plan))
    )
    operational_acceptability = check_operational_acceptability(
        before_plan,
        before_verification,
        candidate_plan,
        verification_result,
        allow_manual_placement=allow_manual_placement,
        allow_host_confirmation=allow_host_confirmation,
    )
    lock_violations = verify_canonical_locks(baseline, candidate_plan)
    existing_protection_violations = protection_violations(candidate_plan, decisions)
    constraint_violations = request_constraint_violations(candidate_plan, decisions)
    before_guest_signature = _guest_reservation_signature(before_plan)
    after_guest_signature = _guest_reservation_signature(candidate_plan)
    guest_integrity_ok = before_guest_signature == after_guest_signature

    hosting_transfers: list[dict[str, Any]] = []
    if resolved_problem:
        from tournament_scheduler.hosting_responsibility import (
            unexplained_responsibility_transfers,
        )

        hosting_transfers = unexplained_responsibility_transfers(
            before_plan, candidate_plan, resolved_problem
        )

    changed_ids = sorted(
        tournament_id
        for tournament_id, tournament in candidate_tournaments.items()
        if before_tournaments.get(tournament_id) != tournament
    )
    missing_ids = sorted(set(before_tournaments) - set(candidate_tournaments))
    changed_ids = sorted(set(changed_ids) | set(missing_ids))
    changed_outside_scope = [
        tournament_id for tournament_id in changed_ids if tournament_id not in scope_ids
    ]

    team_consequences: dict[str, Any] = {}
    if applied_swaps:
        from tournament_scheduler.team_schedule_quality import (
            compare_team_schedule_consequence,
        )

        for swap in applied_swaps:
            for tournament_key, team_key, identity in (
                ("tournament_a_id", "team_a", swap["identity_a"]),
                ("tournament_b_id", "team_b", swap["identity_b"]),
            ):
                key = f"{swap[tournament_key]}:{swap[team_key]['label']}"
                team_consequences[key] = compare_team_schedule_consequence(
                    before_plan,
                    candidate_plan,
                    identity,
                    problem=resolved_problem,
                )
    regression_acceptance = evaluate_regression_acceptances(
        team_consequences, regression_acceptances
    )
    consequence_acceptable = bool(regression_acceptance["acceptable"])

    reconcile_plan_derived_state(
        candidate_plan, verification_result, problem=resolved_problem
    )
    candidate_fingerprint = schedule_fingerprint(candidate_plan)
    candidate_cost = change_cost(baseline, candidate_plan)

    refusal_reasons: list[str] = []
    if referenced_outside_scope:
        refusal_reasons.append(
            "operations reference tournaments outside the declared scope: "
            + ", ".join(referenced_outside_scope)
        )
    if changed_outside_scope:
        refusal_reasons.append(
            "tournaments outside the declared scope changed: "
            + ", ".join(changed_outside_scope)
        )
    if not verification_result.get("ok", True):
        refusal_reasons.append("final candidate fails hard verification")
    if not operational_acceptability["ok"]:
        refusal_reasons.append(
            "final candidate newly introduces operational placement work"
        )
    if lock_violations:
        refusal_reasons.append("final candidate violates an approval/lock")
    if existing_protection_violations:
        refusal_reasons.append(
            "final candidate would undo an accepted change protection"
        )
    if not guest_integrity_ok:
        refusal_reasons.append("final candidate would change reserved guest slots")
    if hosting_transfers:
        refusal_reasons.append("final candidate transfers hosting responsibility")
    if constraint_violations:
        refusal_reasons.append(
            f"final candidate leaves {len(constraint_violations)} active "
            "request-constraint violation(s)"
        )
    refusal_reasons.extend(
        f"final candidate {reason}" if reason.startswith("materially") else reason
        for reason in regression_acceptance_refusals(regression_acceptance)
    )

    updated_schedule = {
        **schedule,
        "updated_at": now,
        "revision": candidate_fingerprint,
        "fingerprint": candidate_fingerprint,
        "plan": candidate_plan,
    }
    updated_decisions = {
        **decisions,
        "updated_at": now,
        "schedule_fingerprint": candidate_fingerprint,
        "decisions": _reconcile_decisions(decisions.get("decisions", {}), candidate_plan, now=now),
    }
    append_change_protections(updated_decisions, new_protections)

    report: dict[str, Any] = {
        "season": season,
        "dry_run": bool(dry_run),
        "request_id": resolved_request_id,
        "declared_scope": sorted(scope_ids),
        "operations": normalized_operations,
        "referenced_tournament_ids": referenced_ids,
        "referenced_outside_scope": referenced_outside_scope,
        "changed_tournament_ids": changed_ids,
        "changed_outside_scope": changed_outside_scope,
        "moves": applied_moves,
        "swaps": applied_swaps,
        "cancellations": applied_cancellations,
        "before_schedule_revision": before_fingerprint,
        "before_canonical_revision": before_canonical_revision,
        "candidate_schedule_revision": candidate_fingerprint,
        "verification_result": verification_result,
        "hard_verification_ok": bool(verification_result.get("ok", True)),
        "operational_acceptability": operational_acceptability,
        "operational_acceptable": bool(operational_acceptability.get("ok")),
        "lock_violations": lock_violations,
        "change_protection_violations": existing_protection_violations,
        "change_protection_acceptable": not existing_protection_violations,
        "guest_reservation_integrity": {
            "ok": guest_integrity_ok,
            "before": before_guest_signature,
            "after": after_guest_signature,
        },
        "hosting_responsibility_transfers": hosting_transfers,
        "hosting_responsibility_ok": not hosting_transfers,
        "request_constraint_violations": constraint_violations,
        "remaining_request_constraint_violations": constraint_violations,
        "request_constraint_acceptable": not constraint_violations,
        "team_consequences": team_consequences,
        "consequence_acceptable": consequence_acceptable,
        "regression_acceptance": regression_acceptance,
        "change_cost": candidate_cost,
        "protections_to_add": new_protections,
        "refused": bool(refusal_reasons),
        "refusal_reasons": refusal_reasons,
    }

    if dry_run:
        report["committed"] = False
        preview_schedule = {
            **schedule,
            "revision": candidate_fingerprint,
            "fingerprint": candidate_fingerprint,
            "plan": candidate_plan,
        }
        report["candidate_canonical_revision"] = compute_canonical_state_revision(
            preview_schedule, updated_decisions
        )
        return report

    if refusal_reasons:
        messages = "; ".join(refusal_reasons)
        if constraint_violations:
            messages += ": " + "; ".join(
                str(item.get("message")) for item in constraint_violations
            )
        raise SeasonStateError(f"Refusing canonical batch: {messages}")

    _append_decision_history(
        updated_decisions,
        event="batch_maintenance",
        tournament_id="",
        actor=actor,
        now=now,
        note=note,
        details={
            "request_id": resolved_request_id,
            "declared_scope": sorted(scope_ids),
            "changed_tournament_ids": changed_ids,
            "operations": normalized_operations,
            "before_schedule_revision": before_fingerprint,
            "after_schedule_revision": candidate_fingerprint,
            "before_canonical_revision": before_canonical_revision,
            "moves": applied_moves,
            "swaps": applied_swaps,
            "cancellations": applied_cancellations,
            "protections_added": [
                str(protection.get("id") or "") for protection in new_protections
            ],
            "verification_ok": bool(verification_result.get("ok", True)),
            "operational_acceptable": bool(operational_acceptability.get("ok")),
            "accepted_regressions": regression_acceptance["accepted_regressions"],
        },
    )
    updated_snapshot = snapshot.with_schedule(updated_schedule).with_decisions(updated_decisions)
    service._assert_published_sealed_reconciliation(updated_snapshot, action="batch")
    committed = service._commit(updated_snapshot)
    report["committed"] = True
    report["revision"] = committed.schedule.get("revision")
    report["canonical_state_revision"] = canonical_state_revision(
        committed.schedule, committed.decisions
    )
    return report
