"""Reviewed-candidate promotion and generic candidate application."""

from __future__ import annotations

import os
from typing import Any, Mapping

from tournament_scheduler.canonical_baseline import approval_fingerprint
from tournament_scheduler.canonical_state import (
    CHANGE_PROTECTIONS_KEY,
    PARTICIPATION_WITHDRAWALS_KEY,
    schedule_fingerprint,
)
from tournament_scheduler.change_protections import (
    ACTIVE as CHANGE_PROTECTION_ACTIVE,
    RELEASED as CHANGE_PROTECTION_RELEASED,
    append_change_protections,
    protection_violations,
)
from tournament_scheduler.participation_withdrawals import (
    ACTIVE as WITHDRAWAL_ACTIVE,
    RELEASED as WITHDRAWAL_RELEASED,
    append_withdrawal_records,
)
from tournament_scheduler.request_constraints import (
    request_constraint_violations,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    DECISIONS_SCHEMA_VERSION,
    SEASON_STATE_SCHEMA_VERSION,
    CanonicalSeasonSnapshot,
    SeasonStateError,
    season_id_from_plan,
)
from tournament_scheduler.operational_acceptability import (
    check_operational_acceptability,
    required_opt_in_flags,
)
from tournament_scheduler.plan_derived_state import reconcile_plan_derived_state
from tournament_scheduler.planning_contract import extract_candidate, verify_candidate
from tournament_scheduler.published_baseline import (
    assert_season_allows_global_regeneration,
    is_published_sealed,
)
from tournament_scheduler.serialization.season_plan import SEASON_PLAN_SCHEMA_VERSION
from .scoped_mutation import (
    ScopedMutationAuthorization,
    permitted_history_event_for_authorization,
    validate_scoped_mutation_authorization,
    validate_scoped_mutation_completion,
)
from .shared import (
    PENDING_REVIEW_STATUS,
    _operator_identity,
    _now_iso,
    _append_decision_history,
    _reconcile_decisions,
    _guest_reservation_signature,
)


def _with_released_records(
    decisions: Mapping[str, Any],
    *,
    withdrawal_ids: list[str] | None,
    protection_ids: list[str] | None,
    actor: str | None,
    now: str,
    note: str,
) -> dict[str, Any]:
    """Return decisions with the named withdrawal/protection records released.

    Used by the atomic reversal path so a restoration can release its own
    removal guards in the same commit that adds the participants back. The
    released records are new mappings; the input decisions are never mutated.
    """

    wanted_withdrawals = {str(item) for item in (withdrawal_ids or []) if str(item)}
    wanted_protections = {str(item) for item in (protection_ids or []) if str(item)}
    if not wanted_withdrawals and not wanted_protections:
        return dict(decisions)

    resolved_actor = actor or os.environ.get("RVV_OPERATOR") or os.environ.get("USER") or "operator"
    working: dict[str, Any] = dict(decisions)
    if wanted_withdrawals:
        records: list[Any] = []
        for record in decisions.get(PARTICIPATION_WITHDRAWALS_KEY, []) or []:
            if (
                isinstance(record, Mapping)
                and str(record.get("id") or "") in wanted_withdrawals
                and str(record.get("status") or WITHDRAWAL_ACTIVE) == WITHDRAWAL_ACTIVE
            ):
                record = {
                    **record,
                    "status": WITHDRAWAL_RELEASED,
                    "released_at": now,
                    "released_by": resolved_actor,
                    "release_reason": note or "",
                }
            records.append(record)
        working[PARTICIPATION_WITHDRAWALS_KEY] = records
    if wanted_protections:
        protections: list[Any] = []
        for record in decisions.get(CHANGE_PROTECTIONS_KEY, []) or []:
            if (
                isinstance(record, Mapping)
                and str(record.get("id") or "") in wanted_protections
                and str(record.get("status") or CHANGE_PROTECTION_ACTIVE)
                == CHANGE_PROTECTION_ACTIVE
            ):
                record = {
                    **record,
                    "status": CHANGE_PROTECTION_RELEASED,
                    "released_at": now,
                    "released_by": resolved_actor,
                    "release_reason": note or "",
                }
            protections.append(record)
        working[CHANGE_PROTECTIONS_KEY] = protections
    return working


def _record_promotion_trace(
    work_dir: str | os.PathLike[str],
    *,
    run_id: str | None,
    season: str,
    actor: str | None,
    schedule: dict[str, Any],
    candidate_fingerprint: str | None,
    export_fingerprint: str | None,
) -> None:
    """Best-effort controller-trace event for a canonical promotion.

    Promotion is the deliberate handoff from an audited review export to the
    operational baseline. Recording it in the same append-only trace as the
    convergence decisions lets an analyst link the exact reviewed candidate to
    the season revision that became canonical, without a second evidence file.
    """
    try:
        from ...pipeline.controller_trace import EVENT_PROMOTION, ControllerTrace

        ControllerTrace(work_dir, run_id).emit(
            EVENT_PROMOTION,
            season=season,
            actor=_operator_identity(actor),
            schedule_revision=str(schedule.get("revision") or ""),
            schedule_fingerprint=str(schedule.get("fingerprint") or ""),
            candidate_fingerprint=str(candidate_fingerprint or ""),
            export_fingerprint=str(export_fingerprint or ""),
        )
    except Exception:
        # Observability must never fail or roll back a committed promotion.
        pass


def _initial_decisions(plan_dict: dict[str, Any]) -> dict[str, Any]:
    records: dict[str, Any] = {}
    for tournament in plan_dict.get("tournaments", []):
        tournament_id = str(tournament.get("id") or "")
        if not tournament_id:
            continue
        records[tournament_id] = {
            "status": PENDING_REVIEW_STATUS,
            "placement_locked": False,
            "participants_locked": False,
            "approved_fingerprint": None,
            "approved_at": None,
            "approved_by": None,
            "note": "",
        }
    return records


def promote(
    service,
    *,
    work_dir: str | os.PathLike[str] = ".pipeline",
    season: str | None = None,
    actor: str | None = None,
    force: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Promote the reviewed Stage 4 candidate into canonical season state."""

    from tournament_scheduler.pipeline.state import PipelineState, StageName
    from tournament_scheduler.pipeline.verification_context import (
        VerificationContextError,
        resolve_promotion_verification_context,
    )

    state = PipelineState(work_dir)
    checkpoint = state.read_stage(StageName.PLANNING)
    if not checkpoint:
        raise SeasonStateError("No Stage 3 planning checkpoint found to promote")
    candidate = extract_candidate(checkpoint)
    try:
        bound_context = resolve_promotion_verification_context(
            work_dir=str(work_dir), candidate=candidate
        )
    except VerificationContextError as exc:
        raise SeasonStateError(f"Refusing promotion: {exc}") from exc
    result = verify_candidate(candidate, bound_context["problem"])
    if not result.get("ok", True):
        messages = "; ".join(
            str(v.get("message") or v.get("code")) for v in result.get("violations", [])
        )
        raise SeasonStateError(
            f"Refusing promotion: selected candidate fails hard verification: {messages}"
        )

    reviewed_plan = bound_context.get("reviewed_plan")
    plan_dict = dict(reviewed_plan) if isinstance(reviewed_plan, dict) else dict(candidate)
    plan_dict["schema_version"] = SEASON_PLAN_SCHEMA_VERSION
    resolved_season = season or season_id_from_plan(plan_dict)
    # Promotion replaces the whole canonical season. A sealed published season
    # may only be replaced after an explicit ``season reopen-planning``; even
    # ``--force`` must not silently bypass the published operational baseline.
    if service.store.decisions_path(resolved_season).exists():
        existing = service.load(resolved_season)
        assert_season_allows_global_regeneration(
            existing.decisions, operation="season promote"
        )
    if (
        service.store.schedule_path(resolved_season).exists()
        or service.store.decisions_path(resolved_season).exists()
    ) and not force:
        raise SeasonStateError(
            f"Canonical season state already exists for {resolved_season}; "
            "use --force only for deliberate replacement"
        )

    now = _now_iso()
    fingerprint = schedule_fingerprint(plan_dict)
    schedule_payload = {
        "schema_version": SEASON_STATE_SCHEMA_VERSION,
        "season": resolved_season,
        "created_at": now,
        "updated_at": now,
        "revision": fingerprint,
        "fingerprint": fingerprint,
        "plan_schema_version": SEASON_PLAN_SCHEMA_VERSION,
        "plan": plan_dict,
        "verification_context": dict(bound_context["context"]),
        "promoted_from": {
            "work_dir": str(work_dir),
            "run_id": bound_context["run_id"],
            "stage3_fingerprint": fingerprint,
            "stage4_export_fingerprint": bound_context["export_fingerprint"],
            "stage4_export_dir": bound_context.get("export_dir"),
            "verification_context_schema_version": bound_context["context"].get("schema_version"),
            "verification_context_problem_fingerprint": bound_context.get("problem_fingerprint"),
            "verification_context_candidate_fingerprint": bound_context["candidate_fingerprint"],
            "verification_context_verified_ok": True,
            "public_export_context_fingerprint": bound_context.get("public_export_context_fingerprint"),
        },
    }
    decisions_payload = {
        "schema_version": DECISIONS_SCHEMA_VERSION,
        "season": resolved_season,
        "created_at": now,
        "updated_at": now,
        "schedule_fingerprint": fingerprint,
        "actor": _operator_identity(actor),
        "decisions": _initial_decisions(plan_dict),
    }
    snapshot = CanonicalSeasonSnapshot(
        season=resolved_season,
        schedule=schedule_payload,
        decisions=decisions_payload,
        export_context=bound_context.get("public_export_context"),
    )
    committed = service._commit(snapshot, require_absent=not force)
    _record_promotion_trace(
        work_dir,
        run_id=bound_context.get("run_id"),
        season=resolved_season,
        actor=actor,
        schedule=committed.schedule,
        candidate_fingerprint=bound_context.get("candidate_fingerprint"),
        export_fingerprint=bound_context.get("export_fingerprint"),
    )
    return committed.schedule, committed.decisions


def apply_candidate(
    service,
    *,
    season: str,
    candidate: dict[str, Any],
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    change_weights: dict[str, float] | None = None,
    allow_guest_slot_changes: bool = False,
    _history_event: Mapping[str, Any] | None = None,
    _new_change_protections: list[dict[str, Any]] | None = None,
    _new_participation_withdrawals: list[dict[str, Any]] | None = None,
    _release_participation_withdrawal_ids: list[str] | None = None,
    _release_change_protection_ids: list[str] | None = None,
    allow_manual_placement: bool = False,
    allow_host_confirmation: bool = False,
    operation: str = "global_regeneration",
    _scoped_authorization: ScopedMutationAuthorization | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Apply a verified replan candidate to canonical season state.

    A published_sealed season refuses every caller that cannot present a
    service-issued, evidence-derived scoped authorization. ``operation`` is only
    narration: neither it nor a caller-built contract/scope is authorization.
    """

    from tournament_scheduler.canonical_baseline import (
        build_canonical_baseline,
        change_cost,
        verify_canonical_locks,
    )

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    now = _now_iso()
    # Releases are part of the same atomic decision write: apply them up front so
    # the protection/constraint gates and the persisted decisions both see the
    # post-release state (a restoration must not trip its own removal guard).
    working_decisions = _with_released_records(
        decisions,
        withdrawal_ids=_release_participation_withdrawal_ids,
        protection_ids=_release_change_protection_ids,
        actor=actor,
        now=now,
        note=(
            str(_history_event.get("note"))
            if isinstance(_history_event, Mapping) and _history_event.get("note")
            else operation
        ),
    )
    baseline = build_canonical_baseline(schedule, decisions)
    normalized_candidate = extract_candidate(candidate)
    scoped_validation: dict[str, Any] | None = None
    if is_published_sealed(decisions) and not isinstance(
        _scoped_authorization, ScopedMutationAuthorization
    ):
        # A mode string, a caller-built contract or an arbitrary id list is
        # never authorization: a whole-season candidate can be packaged as a
        # wide "scoped" contract. A token is not authorization either -- only a
        # typed operation reproduced from current canonical state is.
        assert_season_allows_global_regeneration(decisions, operation=operation)
    if _scoped_authorization is not None:
        if isinstance(_scoped_authorization, ScopedMutationAuthorization):
            permitted_event = permitted_history_event_for_authorization(_scoped_authorization)
            if not _history_event or str(_history_event.get("event") or "") != str(permitted_event):
                raise SeasonStateError(
                    f"Refusing {operation}: scoped mutation must carry its permitted durable "
                    f"history event {permitted_event!r}"
                )
        scoped_validation = validate_scoped_mutation_authorization(
            schedule=schedule,
            decisions=decisions,
            candidate=normalized_candidate,
            authorization=_scoped_authorization,
            operation=operation,
        )

    # A reservation is durable canonical state: a replan/apply must not
    # silently drop or rewrite one. Filling and releasing are the only
    # deliberate transitions, and both go through their own operations.
    if not allow_guest_slot_changes:
        before_reservations = _guest_reservation_signature(schedule.get("plan") or {})
        after_reservations = _guest_reservation_signature(normalized_candidate)
        if before_reservations != after_reservations:
            changed = sorted(
                set(before_reservations) | set(after_reservations)
            )
            raise SeasonStateError(
                "Refusing canonical apply: it would change reserved guest slots on "
                f"{changed}; use reserve/fill/release explicitly"
            )
    accepted_change_violations = protection_violations(normalized_candidate, working_decisions)
    if accepted_change_violations:
        messages = "; ".join(
            str(item.get("message")) for item in accepted_change_violations
        )
        raise SeasonStateError(
            "Refusing canonical apply: it would undo an accepted change: " + messages
        )
    active_constraint_violations = request_constraint_violations(
        normalized_candidate, working_decisions
    )
    if active_constraint_violations:
        messages = "; ".join(
            str(item.get("message")) for item in active_constraint_violations
        )
        raise SeasonStateError(
            "Refusing canonical apply: it violates an active request constraint: " + messages
        )

    lock_violations = verify_canonical_locks(baseline, normalized_candidate)
    if lock_violations:
        messages = "; ".join(str(v.get("message")) for v in lock_violations)
        raise SeasonStateError(
            f"Refusing canonical apply: candidate violates canonical locks: {messages}"
        )

    verification_problem = (
        scoped_validation.get("problem")
        if isinstance(scoped_validation, Mapping)
        else problem
    )
    result = (
        verify_candidate(normalized_candidate, verification_problem)
        if verification_problem
        else verify_candidate(normalized_candidate)
    )
    if not result.get("ok", True):
        messages = "; ".join(
            str(v.get("message") or v.get("code")) for v in result.get("violations", [])
        )
        raise SeasonStateError(
            f"Refusing canonical apply: candidate fails hard verification: {messages}"
        )

    before_plan = schedule.get("plan") or {}
    before_verification = (
        verify_candidate(dict(before_plan), verification_problem)
        if verification_problem
        else verify_candidate(dict(before_plan))
    )
    operational_acceptability = check_operational_acceptability(
        before_plan,
        before_verification,
        normalized_candidate,
        result,
        allow_manual_placement=allow_manual_placement,
        allow_host_confirmation=allow_host_confirmation,
    )
    if not operational_acceptability["ok"]:
        messages = "; ".join(
            str(item.get("message")) for item in operational_acceptability["regressions"]
        )
        flags = ", ".join(required_opt_in_flags(operational_acceptability))
        raise SeasonStateError(
            "Refusing canonical apply: candidate newly introduces operational placement "
            f"work: {messages}"
            + (f"; pass {flags} only for an explicit provisional placement" if flags else "")
        )

    if verification_problem:
        from tournament_scheduler.hosting_responsibility import (
            unexplained_responsibility_transfers,
        )

        transfers = unexplained_responsibility_transfers(
            schedule.get("plan"), normalized_candidate, verification_problem
        )
        if transfers:
            messages = "; ".join(str(entry.get("message")) for entry in transfers)
            raise SeasonStateError(
                f"Refusing canonical apply: candidate transfers hosting responsibility: {messages}"
            )

    plan = dict(normalized_candidate)
    plan.pop("source", None)
    plan["schema_version"] = SEASON_PLAN_SCHEMA_VERSION
    plan.setdefault("start_date", schedule["plan"].get("start_date"))
    plan.setdefault("end_date", schedule["plan"].get("end_date"))
    reconcile_plan_derived_state(plan, result, problem=verification_problem)
    if _scoped_authorization is not None:
        # Derived-state reconciliation must not change anything the reproduced
        # typed operation did not produce, and the final after-state must still
        # match the durable history the authorization will record.
        validate_scoped_mutation_completion(
            schedule=schedule,
            decisions=decisions,
            plan=plan,
            reproduced=scoped_validation,
            authorization=_scoped_authorization,
            operation=operation,
        )

    now = _now_iso()
    fingerprint = schedule_fingerprint(plan)
    updated_schedule = {
        **schedule,
        "updated_at": now,
        "revision": fingerprint,
        "fingerprint": fingerprint,
        "plan_schema_version": SEASON_PLAN_SCHEMA_VERSION,
        "plan": plan,
        "applied_from": {
            "previous_revision": schedule.get("revision"),
            "actor": _operator_identity(actor),
            "operational_acceptability": operational_acceptability,
        },
    }
    updated_decisions = {
        **working_decisions,
        "updated_at": now,
        "schedule_fingerprint": fingerprint,
        "decisions": _reconcile_decisions(working_decisions.get("decisions", {}), plan, now=now),
    }
    if _new_change_protections:
        append_change_protections(updated_decisions, _new_change_protections)
    if _new_participation_withdrawals:
        append_withdrawal_records(updated_decisions, _new_participation_withdrawals)
    if _history_event:
        history_tournament_id = str(_history_event.get("tournament_id") or "")
        history_tournament = next(
            (
                tournament
                for tournament in plan.get("tournaments", []) or []
                if str(tournament.get("id") or "") == history_tournament_id
            ),
            None,
        )
        history_details = (
            dict(_history_event.get("details") or {})
            if isinstance(_history_event.get("details"), Mapping)
            else {}
        )
        if _scoped_authorization is not None:
            # The replayable record must be the authorization the apply boundary
            # validated, so a caller cannot record a wider after-state than the
            # candidate it actually committed.
            if isinstance(scoped_validation, Mapping):
                history_details.update(dict(scoped_validation.get("history_details") or {}))
        history_event_name = str(_history_event.get("event") or "apply_candidate")
        if isinstance(scoped_validation, Mapping):
            history_event_name = str(scoped_validation.get("history_event") or history_event_name)
        _append_decision_history(
            updated_decisions,
            event=history_event_name,
            tournament_id=history_tournament_id,
            actor=actor,
            now=now,
            tournament_fingerprint=(
                approval_fingerprint(history_tournament)
                if history_tournament is not None
                else None
            ),
            previous_fingerprint=(
                str(_history_event.get("previous_fingerprint"))
                if _history_event.get("previous_fingerprint")
                else None
            ),
            note=str(_history_event.get("note") or ""),
            details=history_details or None,
        )
    cost = change_cost(baseline, plan, weights=change_weights)
    updated_snapshot = snapshot.with_schedule(updated_schedule).with_decisions(updated_decisions)
    service._assert_published_sealed_reconciliation(updated_snapshot, action=operation)
    committed = service._commit(updated_snapshot)
    return committed.schedule, committed.decisions, cost
