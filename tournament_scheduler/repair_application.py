"""Atomic repair application and adoption for maintenance.

This module owns the atomic application of repair options to canonical state
and plan-level candidates, including adoption checking, scoping, and
mutability guarantees.
"""

from __future__ import annotations

from .application.canonical_season.scoped_mutation import authorization_history_details
from .application.canonical_season.scoped_mutation import authorize_bounded_repair
from .canonical_baseline import build_canonical_baseline
from .canonical_baseline import change_cost
from .finding_resolution import annotate_resolutions
from .finding_resolution import resolution_summary
from .findings_construction import _findings_for_option
from .findings_construction import _infer_finding_id
from .findings_construction import _require_finding
from .findings_construction import construct_findings
from .findings_construction import findings_for_plan
from .host_team_missing_repair import candidate_fingerprint
from .impact_analysis import _changed_team_ids
from .impact_analysis import _changed_tournament_ids
from .impact_analysis import _plan_fingerprint
from .impact_analysis import _priority_improvement_tier
from .local_repair_options import apply_local_repair_option
from .maintenance_context import load_maintenance_context
from .operational_acceptability import check_operational_acceptability
from .operational_acceptability import required_opt_in_flags
from .option_evaluation import (
    PARETO_DIMENSIONS,
    _metric_delta,
    _objective_vector,
    _rejected_delta,
    _travel_metrics,
)
from .pareto import non_dominated_indices, representative_indices
from .planning_contract import score_candidate
from .quality_objectives import compare_quality_scores, with_unresolved_obligations_count
from .repair_options import _option_is_applicable
from .search_coverage import (
    SEARCH_COVERAGE_BOUNDED_EXHAUSTED,
    SEARCH_COVERAGE_INCOMPLETE,
    SEARCH_COVERAGE_OPTION_AVAILABLE,
)
from .planning_contract import verify_candidate
from .repair_adoption_guard import RepairPassLedger
from .repair_adoption_guard import adoption_history_summary
from .repair_adoption_guard import evaluate_adoption
from .repair_options import DEFAULT_DIMENSIONS
from .repair_options import SEASON_MAINTENANCE_SCHEMA_VERSION
from .repair_options import _options_for_finding
from .request_constraints import active_request_constraints
from .request_constraints import compare_constraint_violations
from .request_constraints import request_constraint_report
from .rule_catalog import annotate_findings
from .season_state import DEFAULT_SEASON_ROOT
from .season_state import apply_candidate
from .season_state import canonical_state_revision
from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Tuple
from typing import Mapping
from typing import Optional


def list_findings(season: str, *, root: str = DEFAULT_SEASON_ROOT) -> Dict[str, Any]:
    """Return stable, revision-bound actionable findings over canonical state."""
    schedule, decisions, plan, problem = load_maintenance_context(season, root=root)
    verification = verify_candidate(plan, problem)
    revision = canonical_state_revision(schedule, decisions)
    # ADR 0005: a source-confirmed accepted booking below the governing planning
    # floor is a durable follow-up finding at the audit boundary, not a
    # new-placement hard violation. Use the same revision-bound classification
    # the export/preflight path applies so findings/audit and export agree.
    from .final_verification import _reclassify_accepted_booking_floor

    accepted_floor_findings, remaining_violations = _reclassify_accepted_booking_floor(
        problem, plan, list(verification.get("violations") or [])
    )
    verification = dict(verification)
    verification["violations"] = remaining_violations
    verification["ok"] = not remaining_violations
    verification["booking_feasibility_warnings"] = accepted_floor_findings
    findings = construct_findings(plan, problem, verification, decisions=decisions)
    from .calendar_bookings import association_findings

    findings.extend(association_findings(problem=problem, plan=plan, decisions=decisions))
    annotate_findings(findings)
    annotate_resolutions(findings)
    baseline = decisions.get("season_baseline") or None
    from .season_baseline import compare_findings_to_baseline

    baseline_comparison = compare_findings_to_baseline(baseline, findings)
    # Hard verification is never baselineable: a hard failure must never let a
    # comparison claim the season is safe to advance/accept, regardless of how
    # the non-hard findings compare.
    baseline_comparison["hard_verification_ok"] = bool(verification.get("ok"))
    if not verification.get("ok"):
        baseline_comparison["ok_to_advance"] = False
    counts: Dict[str, int] = {}
    counts_by_rule_id: Dict[str, int] = {}
    for finding in findings:
        counts[finding["code"]] = counts.get(finding["code"], 0) + 1
        rule_id = finding.get("rule_id")
        if rule_id:
            counts_by_rule_id[str(rule_id)] = counts_by_rule_id.get(str(rule_id), 0) + 1
    return {
        "schema_version": SEASON_MAINTENANCE_SCHEMA_VERSION,
        "season": season,
        "revision": revision,
        "candidate_fingerprint": _plan_fingerprint(plan),
        "verification_ok": bool(verification.get("ok")),
        "finding_count": len(findings),
        "counts_by_code": counts,
        "counts_by_rule_id": counts_by_rule_id,
        "findings": findings,
        "resolution_summary": resolution_summary(findings),
        "baseline_comparison": baseline_comparison,
        "baseline": {
            "active": bool(baseline),
            "created_at": baseline.get("created_at") if isinstance(baseline, Mapping) else None,
            "note": baseline.get("note") if isinstance(baseline, Mapping) else None,
        },
        "request_constraints": _request_constraint_context(plan, decisions),
    }


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
        return _rejected_delta(season, revision, "unknown_or_stale_option", option_id=option_id)

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


def baseline_for_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    """Change-cost baseline for an unpromoted candidate (no approvals/locks).

    The reviewed candidate itself is the refinement baseline, so change cost
    measures movement away from what was reviewed rather than inventing a
    canonical-season baseline that does not apply before promotion.
    """
    return build_canonical_baseline({"plan": dict(plan)}, {})


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
    """Atomically reproduce and apply one verified option to a bare plan.

    Returns the mutated candidate plus the same before/after metric delta the
    canonical boundary returns; it never writes any canonical state. The same
    operational-acceptability boundary the canonical apply uses is applied
    here, so an unpromoted candidate cannot admit a repair the promoted path
    would reject.
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


def _request_constraint_context(
    plan: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return the active request constraints plus their derived current status."""

    constraints = request_constraint_report(plan, decisions)
    return {
        "active_count": sum(1 for item in constraints if item.get("status") == "active"),
        "unsatisfied_count": sum(
            1
            for item in constraints
            if item.get("status") == "active" and not item.get("satisfied")
        ),
        "constraints": constraints,
    }


def _apply_option(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    option: Mapping[str, Any],
    finding: Mapping[str, Any],
    dimensions: Iterable[str],
) -> Dict[str, Any]:
    """Dispatch one selected option to its owning provider's atomic apply path."""

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
            expected_fingerprint=fingerprint,
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

    return apply_local_repair_option(
        plan, problem, option_id=option_id, expected_fingerprint=fingerprint
    )


# A non-dominated front is still bounded before it is reported: one extreme per
# objective plus the lowest-cost remaining points, so a caller gets a small
# representative trade-off set rather than every legal mutation.
MAX_PARETO_REPRESENTATIVES = 6


def _effective_search_coverage(
    finding: Mapping[str, Any], options: Iterable[Mapping[str, Any]]
) -> Dict[str, Any]:
    """Reclassify raw provider coverage using effectively applicable options."""

    coverage = dict(finding.get("search_coverage") or {})
    option_list = list(options)
    applicable_count = sum(1 for option in option_list if _option_is_applicable(option))
    raw_option_count = len(option_list)
    coverage["applicable_option_count"] = applicable_count
    coverage["non_applicable_option_count"] = raw_option_count - applicable_count
    if applicable_count:
        coverage["status"] = SEARCH_COVERAGE_OPTION_AVAILABLE
    elif coverage.get("search_requested"):
        coverage["status"] = SEARCH_COVERAGE_BOUNDED_EXHAUSTED
        coverage["proven_infeasible"] = False
    elif coverage.get("status") == SEARCH_COVERAGE_OPTION_AVAILABLE:
        coverage["status"] = SEARCH_COVERAGE_INCOMPLETE
    return coverage


def _annotate_pareto(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    options: List[Dict[str, Any]],
    finding: Mapping[str, Any],
    dimensions: Iterable[str],
    *,
    active_constraints: Iterable[Mapping[str, Any]] = (),
    allow_manual_placement: bool = False,
    allow_host_confirmation: bool = False,
) -> Dict[str, Any]:
    """Measure every option on the same objective vector and mark the front.

    Each option is reproduced against the current plan through its own atomic
    apply path and re-verified, so the vector describes the candidate that
    option would actually commit -- not a claim derived from the provider's
    self-reported effects. An option that no longer reproduces is left off the
    front instead of being reported as a verified trade-off.

    An option that newly introduces operational work (fixed-busy/manual
    placement, or a host-confirmation dependency) is classified as requiring an
    explicit operator opt-in and kept off the auto-applicable Pareto front.
    Pareto scoring alone is not protection here: an option that fixes one defect
    while introducing a manual placement can stay non-dominated, so this is a
    hard filter rather than a weight.
    """
    constraints = list(active_constraints)
    measured: List[Tuple[int, Dict[str, float]]] = []
    before_score = with_unresolved_obligations_count(score_candidate(dict(plan), problem=dict(problem)))
    before_verification = verify_candidate(dict(plan), dict(problem))
    for index, option in enumerate(options):
        applied = _apply_option(plan, problem, option, finding, dimensions)
        candidate = applied.get("candidate") if applied.get("ok") else None
        if not isinstance(candidate, Mapping):
            option["objectives"] = None
            option["non_dominated"] = False
            continue
        # Active request constraints are hard maintenance requirements, not
        # soft weights: a candidate that introduces or worsens one is reported
        # as rejected evidence instead of being offered as a Pareto trade-off,
        # and the canonical apply boundary re-checks the full active set
        # regardless. Unchanged pre-existing violations stay visible in the
        # option evidence but do not reject an otherwise valid repair.
        constraint_comparison = compare_constraint_violations(
            plan, candidate, constraints
        )
        option["request_constraint_violations"] = constraint_comparison["candidate_violations"]
        option["request_constraint_regressions"] = constraint_comparison["regressions"]
        option["request_constraint_unchanged_violations"] = constraint_comparison["unchanged"]
        option["request_constraint_acceptable"] = constraint_comparison["acceptable"]
        if constraint_comparison["regressions"]:
            option["objectives"] = None
            option["non_dominated"] = False
            continue
        verification = applied.get("verification") or verify_candidate(
            dict(candidate), dict(problem)
        )
        acceptability = check_operational_acceptability(
            plan,
            before_verification,
            candidate,
            verification,
            allow_manual_placement=allow_manual_placement,
            allow_host_confirmation=allow_host_confirmation,
        )
        option["operational_acceptable"] = acceptability["ok"]
        option["operational_regressions"] = acceptability["regressions"]
        option["operational_work_added"] = acceptability["added_by_category"]
        option["requires_operational_opt_in"] = required_opt_in_flags(acceptability)
        if not acceptability["ok"]:
            option["objectives"] = None
            option["non_dominated"] = False
            continue
        # A coupled placement + roster repair may be hard-valid and
        # operationally acceptable yet still materially regress an affected
        # team's own schedule. That is a deterministic rejection owned by the
        # consequence policy, not a Pareto trade-off: keep it off the
        # auto-applicable front and let the apply boundary refuse it.  Use the
        # same complete predicate escalation uses, so raw rejected option
        # objects can never hide the need for operator action.
        if not _option_is_applicable(option):
            if (option.get("effects") or {}).get("consequence_acceptable") is False:
                option["consequence_acceptable"] = False
            option["objectives"] = None
            option["non_dominated"] = False
            continue
        score = score_candidate(dict(candidate), problem=dict(problem))
        travel = _travel_metrics(candidate)
        vector = _objective_vector(
            candidate, verification, plan, score=score, travel=travel,
            active_constraints=constraints,
        )
        option["objectives"] = vector
        # The same Stage-3 quality comparison Stage 3 uses to gate promotion,
        # measured against the current canonical plan, so the harness sees the
        # soft side effects of a repair without reconstructing them.
        option["quality_vs_current"] = compare_quality_scores(
            before_score, with_unresolved_obligations_count(score)
        )
        option["travel"] = travel
        measured.append((index, vector))

    finding["search_coverage"] = _effective_search_coverage(finding, options)

    vectors = [vector for _index, vector in measured]
    front = non_dominated_indices(vectors)
    front_option_indices = sorted(measured[local][0] for local in front)
    front_set = set(front_option_indices)
    for index, option in enumerate(options):
        option["non_dominated"] = index in front_set

    front_vectors = [measured[local][1] for local in front]
    representative = representative_indices(front_vectors, MAX_PARETO_REPRESENTATIVES)
    return {
        "dimensions": list(PARETO_DIMENSIONS),
        "non_dominated_option_ids": [options[index]["option_id"] for index in front_option_indices],
        "representative_option_ids": [
            options[front_option_indices[local]]["option_id"] for local in representative
        ],
        "front_size": len(front_option_indices),
        "measured_option_count": len(measured),
        "request_constraint_rejected_option_ids": [
            option["option_id"]
            for option in options
            if option.get("request_constraint_acceptable") is False
        ],
        "operational_rejected_option_ids": [
            option["option_id"]
            for option in options
            if option.get("operational_acceptable") is False
        ],
        "consequence_rejected_option_ids": [
            option["option_id"]
            for option in options
            if (option.get("effects") or {}).get("consequence_acceptable") is False
        ],
        "allow_manual_placement": bool(allow_manual_placement),
        "allow_host_confirmation": bool(allow_host_confirmation),
    }
