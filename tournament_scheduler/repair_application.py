"""Atomic repair application and adoption for maintenance.

This module owns the atomic application of repair options to canonical state
and plan-level candidates, including adoption checking, scoping, and
mutability guarantees.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .application.canonical_season.scoped_mutation import (
    authorization_history_details,
    authorize_bounded_repair,
)
from .canonical_baseline import build_canonical_baseline, change_cost
from .finding_construction import construct_findings
from .host_team_missing_repair import candidate_fingerprint
from .local_repair_options import apply_local_repair_option
from .planning_contract import verify_candidate
from .repair_adoption_guard import (
    RepairPassLedger,
    adoption_history_summary,
    evaluate_adoption,
)
from .season_state import (
    apply_candidate,
    canonical_state_revision,
    load_decisions,
    load_participation_acceptances,
    load_schedule,
)
from .season_maintenance import (
    _require_finding,
    DEFAULT_SEASON_ROOT,
    _infer_finding_id,
    _findings_for_option,
    findings_for_plan,
    check_operational_acceptability,
    required_opt_in_flags,
    _metric_delta,
    _changed_team_ids,
    _changed_tournament_ids,
    _plan_fingerprint,
)


def apply_repair(
    season: str,
    option_id: str,
    expected_revision: str,
    *,
    root: str = DEFAULT_SEASON_ROOT,
    actor: Optional[str] = None,
    dry_run: bool = False,
    finding_id: Optional[str] = None,
    age_group: str | None = None,
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
    allow_manual_placement: bool = False,
    allow_host_confirmation: bool = False,
    accept_regressions: Any = (),
    regression_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Atomically apply one verified option to canonical state and return the delta.

    The option is reproduced from the current canonical revision and fully
    re-verified before any write. A stale revision (or an option that no longer
    enumerates) is rejected without touching canonical state. The option's
    provider self-report is never trusted for operational acceptability: the
    apply boundary independently re-checks that the candidate does not newly
    introduce fixed-busy/manual placement work or host-confirmation
    dependencies unless the operator explicitly opted in.
    """
    from .maintenance_context import load_maintenance_context

    resolved_dimensions = tuple(sorted({str(dimension) for dimension in dimensions}))
    # A bounded search result is reproduced from the dimensions that produced
    # it, not from whatever the caller happened to pass to ``apply-repair``.
    # Prefer the option's own stable dimension tag so applying a returned
    # search option does not require the harness to replay its flags.
    from .host_team_missing_repair import search_dimensions_from_option_id

    recovered_dimensions = search_dimensions_from_option_id(option_id)
    if recovered_dimensions is not None:
        resolved_dimensions = recovered_dimensions
    schedule, decisions, plan, problem = load_maintenance_context(season, root=root)
    revision = canonical_state_revision(schedule, decisions)
    if expected_revision and expected_revision != revision:
        return _rejected_delta(
            season, revision, "stale_canonical_revision", expected_revision=expected_revision
        )
    findings = construct_findings(plan, problem, verify_candidate(plan, problem), decisions=decisions)
    if finding_id:
        candidates = [_require_finding(findings, finding_id, age_group=age_group)]
    else:
        inferred = _infer_finding_id(option_id, findings)
        candidates = [inferred] if inferred is not None else _findings_for_option(findings, option_id)
    match = None
    resolved_finding = None
    for finding in candidates:
        options, _rejected, _families = _options_for_finding(
            plan, problem, finding, allow_search=True, dimensions=resolved_dimensions
        )
        found = next((entry for entry in options if entry["option_id"] == option_id), None)
        if found is not None:
            match, resolved_finding = found, finding
            break
    if match is None or resolved_finding is None:
        return _rejected_delta(
            season, revision, "unknown_or_stale_option", option_id=option_id
        )

    applied = _apply_option(plan, problem, match, resolved_finding, resolved_dimensions)
    if not applied.get("ok"):
        return _rejected_delta(
            season,
            revision,
            str(applied.get("reason") or "repair_rejected"),
            option_id=option_id,
            verification=applied.get("verification"),
        )
    result_candidate = applied["candidate"]
    constraint_comparison = compare_constraint_violations(
        plan, result_candidate, active_request_constraints(decisions)
    )
    if constraint_comparison["regressions"]:
        return _rejected_delta(
            season,
            revision,
            "request_constraint_violation",
            option_id=option_id,
            request_constraint_violations=constraint_comparison["regressions"],
        )
    before_verification = verify_candidate(plan, problem)
    verification = applied.get("verification") or verify_candidate(dict(result_candidate), dict(problem))
    if not verification.get("ok"):
        return _rejected_delta(
            season,
            revision,
            "verification_failed",
            option_id=option_id,
            verification=verification,
            delta=_metric_delta(plan, before_verification, decisions=decisions),
        )
    acceptability = check_operational_acceptability(
        plan,
        before_verification,
        result_candidate,
        verification,
        allow_manual_placement=allow_manual_placement,
        allow_host_confirmation=allow_host_confirmation,
    )
    if not acceptability["ok"]:
        return _rejected_delta(
            season,
            revision,
            "operational_acceptability_regression",
            option_id=option_id,
            verification=verification,
            operational_acceptability=acceptability,
            required_opt_in_flags=required_opt_in_flags(acceptability),
        )
    preview = _metric_delta(
        plan, before_verification, candidate=result_candidate, after_verification=verification,
        problem=problem, decisions=decisions
    )
    preview["changed_tournament_ids"] = _changed_tournament_ids(plan, result_candidate)
    preview["change_cost"] = change_cost(build_canonical_baseline(schedule, decisions), result_candidate)

    # Cross-rule adoption guard: a repair that fixes its triggering finding but
    # introduces a material regression elsewhere (more back-to-back pairs, a
    # participation guardrail regression, concentrated opponent exposure,
    # materially worse travel/temporal coverage) or revisits an already-adopted
    # assignment is refused unless the operator explicitly accepts the exact
    # regression code with a reason. This is deliberately measured on the whole
    # season, not only on the directly affected squads.
    pass_ledger = RepairPassLedger.from_history(decisions.get("history") or [])
    before_findings = construct_findings(plan, problem, before_verification, decisions=decisions)
    candidate_findings = construct_findings(result_candidate, problem, verification, decisions=decisions)
    pass_baseline = decisions.get("season_baseline")
    try:
        adoption = evaluate_adoption(
            plan,
            result_candidate,
            problem=problem,
            before_findings=before_findings,
            candidate_findings=candidate_findings,
            pass_baseline=pass_baseline if isinstance(pass_baseline, Mapping) else None,
            ledger=pass_ledger,
            accept_regressions=accept_regressions,
            regression_reason=regression_reason,
            trigger_rule=str(resolved_finding.get("rule_id") or resolved_finding.get("code") or ""),
            finding_id=str(resolved_finding.get("finding_id") or finding_id or ""),
            option_id=option_id,
            priority_improvement_tier=_priority_improvement_tier(preview),
        )
    except ValueError as exc:
        return _rejected_delta(
            season,
            revision,
            "invalid_regression_override",
            option_id=option_id,
            error=str(exc),
        )
    if not adoption["adoptable"]:
        reason = (
            "cross_rule_adoption_cycle"
            if adoption["cycle_detected"]
            else "cross_rule_adoption_regression"
        )
        return _rejected_delta(
            season,
            revision,
            reason,
            option_id=option_id,
            verification=verification,
            adoption=adoption,
            delta=preview,
        )
    preview["adoption"] = adoption
    if dry_run:
        return {
            "season": season,
            "ok": True,
            "dry_run": True,
            "revision_before": revision,
            "revision_after": revision,
            "option_id": option_id,
            "finding": resolved_finding,
            "delta": preview,
            "operational_acceptability": acceptability,
            "adoption": adoption,
        }

    from .season_state import apply_candidate

    # Re-derive the allowed affected ids and the exact after-state by
    # reproducing the bounded repair from the current canonical plan. The
    # caller cannot widen the scope or substitute a different candidate.
    scoped_authorization = authorize_bounded_repair(
        schedule=schedule,
        decisions=decisions,
        candidate=result_candidate,
        option_id=option_id,
        finding_id=finding_id,
        dimensions=resolved_dimensions,
        allow_manual_placement=allow_manual_placement,
        allow_host_confirmation=allow_host_confirmation,
    )
    changed_tournament_ids = list(scoped_authorization.affected_tournament_ids)
    updated_schedule, updated_decisions, cost = apply_candidate(
        season=season,
        candidate=result_candidate,
        root=root,
        problem=problem,
        actor=actor,
        allow_manual_placement=allow_manual_placement,
        allow_host_confirmation=allow_host_confirmation,
        operation="targeted_repair",
        _scoped_authorization=scoped_authorization,
        _history_event={
            "event": "repair_option_applied",
            "tournament_id": str(changed_tournament_ids[0] if changed_tournament_ids else ""),
            "note": f"Applied repair option {option_id}",
            "details": {
                "option_id": option_id,
                "finding_id": finding_id,
                "changed_tournament_ids": changed_tournament_ids,
                "adoption": adoption_history_summary(
                    adoption,
                    affected_tournament_ids=changed_tournament_ids,
                    affected_team_ids=_changed_team_ids(plan, result_candidate),
                    revision=revision,
                    decision_id=option_id,
                    baseline_revision=str(
                        (adoption.get("baseline_comparison") or {}).get("baseline_revision") or ""
                    ),
                    baseline_fingerprint=str(
                        pass_ledger.baseline_fingerprint or adoption["before_fingerprint"]
                    ),
                    note=f"Applied repair option {option_id}",
                ),
                **authorization_history_details(scoped_authorization),
            },
        },
    )
    new_revision = canonical_state_revision(updated_schedule, updated_decisions)
    fresh_verification = verify_candidate(dict(updated_schedule.get("plan") or {}), problem)
    after_plan = dict(updated_schedule.get("plan") or {})
    delta = _metric_delta(
        plan, before_verification, candidate=after_plan, after_verification=fresh_verification,
        problem=problem, decisions=decisions
    )
    delta["changed_tournament_ids"] = _changed_tournament_ids(plan, after_plan)
    delta["change_cost"] = cost
    return {
        "season": season,
        "ok": True,
        "dry_run": False,
        "option_id": option_id,
        "finding": resolved_finding,
        "revision_before": revision,
        "revision_after": new_revision,
        "delta": delta,
        "operational_acceptability": acceptability,
        "adoption": adoption,
        "fresh_findings": list_findings(season, root=root),
    }


def apply_repair_to_plan(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    option_id: str,
    *,
    finding_id: Optional[str] = None,
    age_group: str | None = None,
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
    baseline: Optional[Mapping[str, Any]] = None,
    allow_manual_placement: bool = False,
    allow_host_confirmation: bool = False,
    decisions: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Apply a repair option to a candidate plan and return the delta.

    This is the plan-level analog of ``apply_repair``: it takes a candidate
    plan and planning problem (neither of which need to be promoted canonical
    state) and applies a verified option to produce a delta that can be used
    to evaluate the option's impact on the plan. The option is reproduced from
    the plan's own verification and fully re-verified before any change to the
    plan. A stale option (or an option that no longer enumerates) is rejected
    without touching the plan. The option's provider self-report is never
    trusted for operational acceptability: the apply boundary independently
    re-checks that the candidate does not newly introduce fixed-busy/manual
    placement work or host-confirmation dependencies unless the operator
    explicitly opted in.
    """
    resolved_dimensions = tuple(sorted({str(d) for d in dimensions}))
    findings = findings_for_plan(plan, problem)
    if finding_id:
        candidates = [_require_finding(findings, finding_id, age_group=age_group)]
    else:
        inferred = _infer_finding_id(option_id, findings)
        candidates = [inferred] if inferred is not None else _findings_for_option(findings, option_id)
    match = None
    resolved_finding = None
    for finding in candidates:
        options, _rejected, _families = _options_for_finding(
            plan, problem, finding, allow_search=True, dimensions=resolved_dimensions
        )
        found = next((entry for entry in options if entry["option_id"] == option_id), None)
        if found is not None:
            match, resolved_finding = found, finding
            break
    if match is None or resolved_finding is None:
        return {"ok": False, "reason": "unknown_or_stale_option", "option_id": option_id}

    before_verification = verify_candidate(dict(plan), dict(problem))
    applied = _apply_option(plan, problem, match, resolved_finding, resolved_dimensions)
    if not applied.get("ok"):
        return {
            "ok": False,
            "reason": str(applied.get("reason") or "repair_rejected"),
            "option_id": option_id,
            "verification": applied.get("verification"),
        }
    result_candidate = applied["candidate"]
    verification = applied.get("verification") or verify_candidate(
        dict(result_candidate), dict(problem)
    )
    if not verification.get("ok"):
        return {
            "ok": False,
            "reason": "verification_failed",
            "option_id": option_id,
            "verification": verification,
        }
    acceptability = check_operational_acceptability(
        plan,
        before_verification,
        result_candidate,
        verification,
        allow_manual_placement=allow_manual_placement,
        allow_host_confirmation=allow_host_confirmation,
    )
    if not acceptability["ok"]:
        return {
            "ok": False,
            "reason": "operational_acceptability_regression",
            "option_id": option_id,
            "verification": verification,
            "operational_acceptability": acceptability,
            "required_opt_in_flags": required_opt_in_flags(acceptability),
        }
    delta = _metric_delta(
        plan,
        before_verification,
        candidate=result_candidate,
        after_verification=verification,
        problem=problem,
        decisions=decisions,
    )
    delta["changed_tournament_ids"] = _changed_tournament_ids(plan, result_candidate)
    delta["change_cost"] = change_cost(
        baseline if baseline is not None else baseline_for_plan(plan), result_candidate
    )
    return {
        "ok": True,
        "option_id": option_id,
        "finding": resolved_finding,
        "family": match.get("family"),
        "candidate": result_candidate,
        "verification": verification,
        "operational_acceptability": acceptability,
        "delta": delta,
    }


def _apply_option(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    option: Mapping[str, Any],
    finding: Mapping[str, Any],
    dimensions: Iterable[str],
) -> Dict[str, Any]:
    """Dispatch one selected option to its owning provider's atomic apply path."""
    from .host_team_missing_repair import candidate_fingerprint

    option_id = str(option["option_id"])
    fingerprint = candidate_fingerprint(plan)
    family = str(option.get("family") or "")
    arguments = option.get("arguments") or {}
    option_dimensions = tuple(arguments.get("dimensions") or dimensions)

    scope = {
        "age_group": finding.get("age_group"),
        "club": finding.get("club"),
        "team": finding.get("team"),
        "tournament_id": finding.get("tournament_id"),
    }
    if family == "hosting_balance":
        from .hosting_balance_repair import apply_hosting_balance_repair_option

        return apply_hosting_balance_repair_option(
            plan,
            problem,
            option_id=option_id,
            expected_fingerprint=fingerprint,
            scope=scope,
            dimensions=option_dimensions,
        )
    if family == "participation_deviation":
        from .participation_deviation_repair import apply_participation_deviation_repair_option

        return apply_participation_deviation_repair_option(
            plan,
            problem,
            option_id=option_id,
            expected_fingerprint=fingerprint,
            scope=scope,
            dimensions=option_dimensions,
        )
    if family == "search_neighborhood":
        from .search_neighborhood_repair import apply_search_neighborhood_repair_option

        return apply_search_neighborhood_repair_option(
            plan,
            problem,
            option_id=option_id,
            expected_fingerprint=fingerprint,
            scope=scope,
            dimensions=option_dimensions,
        )
    if family == "movable_capacity":
        from .movable_capacity_repair import apply_movable_capacity_repair_option

        return apply_movable_capacity_repair_option(
            plan, problem, option_id=option_id, expected_fingerprint=fingerprint
        )
    if family == "home_representation":
        from .home_representation_repair import apply_home_representation_repair_option

        return apply_home_representation_repair_option(
            plan,
            problem,
            option_id=option_id,
            expected_fingerprint=fingerprint,
            arguments=dict(arguments) or None,
        )
    if family == "intra_club_distribution":
        from .intra_club_distribution_repair import (
            apply_intra_club_distribution_repair_option,
        )

        return apply_intra_club_distribution_repair_option(
            plan,
            problem,
            option_id=option_id,
            expected_fingerprint=fingerprint,
            arguments=dict(arguments) or None,
            scope=scope,
        )
    if family == "coupled_placement":
        from .coupled_placement_repair import apply_coupled_placement_repair_option

        return apply_coupled_placement_repair_option(
            plan,
            problem,
            option_id=option_id,
            expected_fingerpoint=fingerprint,
            arguments=dict(arguments) or None,
        )
    if family == "unplaced_placement":
        from .unplaced_placement_repair import apply_unplaced_placement_repair_option

        return apply_unplaced_placement_repair_option(
            plan,
            problem,
            option_id=option_id,
            expected_fingerprint=fingerprint,
            # The option's own self-contained mutation plan is passed through
            # so annotating/applying it does not replay the whole bounded
            # coupled search for every option.
            arguments=dict(option.get("arguments") or {}) or None,
        )
    from .local_repair_options import apply_local_repair_option

    return apply_local_repair_option(
        plan, problem, option_id=option_id, expected_fingerprint=fingerprint
    )