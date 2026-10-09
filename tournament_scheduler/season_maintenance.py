"""Harness-neutral maintenance surface over a promoted canonical season.

Once a season is promoted (``season/<season>/schedule.json`` and
``decisions.json``), it is the operational baseline. This module exposes that
baseline to the *same* deterministic finding/repair/Pareto controller loop
Stage 3 already uses, so a localized defect does not require re-running Stage
1-4 merely to reach the repair providers:

    canonical season
      -> fresh revision-bound findings (``list_findings``)
      -> finding-directed repair options (``repair_options``)
      -> bounded finding-directed search (``search``)
      -> atomic, full-season-verified apply (``apply_repair``)
      -> fresh deterministic before/after metric delta

Every finding/option/delta is bound to the exact canonical revision it was
derived from. Findings come from a *fresh* :func:`verify_candidate` result --
never a snapshot promoted with the season -- so stale derived evidence cannot
masquerade as the current canonical truth. The module owns no scheduling rule
and no persistence: it composes the existing provider boundary
(``local_repair_options`` plus the hosting/participation providers) with the
canonical atomically-verified mutation boundary (``season_state.apply_candidate``).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .canonical_baseline import build_canonical_baseline
from .planning_contract import score_candidate, verify_candidate
from .quality_objectives import (
    with_unresolved_obligations_count,
)
from .request_constraints import (
    active_request_constraints,
)
from .finding_resolution import annotate_resolutions
from .rule_catalog import annotate_findings
from .season_state import (
    DEFAULT_SEASON_ROOT,
    canonical_state_revision,
    record_participation_acceptance,
    revoke_participation_acceptance,
)
from .maintenance_context import (
    load_maintenance_context,
)
from .findings_construction import (
    construct_findings,
    _require_finding,
)
from .search_coverage import (
    cheap_search_coverage,
)
from .repair_options import (
    DEFAULT_DIMENSIONS,
    SEASON_MAINTENANCE_SCHEMA_VERSION,
)
from .impact_analysis import (
    _plan_fingerprint,
    _plan_content_fingerprint,
)

# Owners of the decomposed maintenance families; re-exported here so
# callers keep the season_maintenance surface.
from .findings_construction import (
    BOOKING_FEASIBILITY,  # noqa: F401
    CLUB_DISTRIBUTION,  # noqa: F401
    HARD_VIOLATION,  # noqa: F401
    HOME_REPRESENTATION,  # noqa: F401
    HOSTING,  # noqa: F401
    MANUAL_PLACEMENT,  # noqa: F401
    MOVABLE_CAPACITY,  # noqa: F401
    PARTICIPATION,  # noqa: F401
    ROSTER_SHAPE,  # noqa: F401
    TEMPORAL_CLUSTERING,  # noqa: F401
    _acceptances_by_scope,  # noqa: F401
    _avoidability_count,  # noqa: F401
    _booking_feasibility_findings,  # noqa: F401
    _clustered_dates,  # noqa: F401
    _count,  # noqa: F401
    _deviation_is_unresolved,  # noqa: F401
    _hard_findings,  # noqa: F401
    _home_representation_findings,  # noqa: F401
    _hosting_findings,  # noqa: F401
    _intra_club_distribution_findings,  # noqa: F401
    _manual_count,  # noqa: F401
    _manual_findings,  # noqa: F401
    _movable_capacity_findings,  # noqa: F401
    _participation_findings,  # noqa: F401
    _shape_findings,  # noqa: F401
    _spacing_findings,  # noqa: F401
    _unplaced_findings,  # noqa: F401
    _unresolved_avoidability_count,  # noqa: F401
    _unresolved_participation_deviation_count,  # noqa: F401
    findings_for_plan,  # noqa: F401
)
from .impact_analysis import (
    plan_fingerprint,  # noqa: F401
)
from .maintenance_context import (
    SeasonMaintenanceError,  # noqa: F401
    project_canonical_overlays,  # noqa: F401
)
from .option_evaluation import (
    MAINTENANCE_DEFECT_DIMENSIONS,  # noqa: F401
    PARETO_DIMENSIONS,  # noqa: F401
    TRAVEL_OBJECTIVE_DIMENSIONS,  # noqa: F401
    _travel_metrics,  # noqa: F401
)
from .repair_application import (
    MAX_PARETO_REPRESENTATIVES,  # noqa: F401
    _annotate_pareto,  # noqa: F401
    _effective_search_coverage,  # noqa: F401
    _request_constraint_context,  # noqa: F401
    apply_repair,  # noqa: F401
    apply_repair_to_plan,  # noqa: F401
    baseline_for_plan,  # noqa: F401
    list_findings,  # noqa: F401
)
from .repair_options import (
    DATE_MOVING_FINDING_CODES,  # noqa: F401
    SUPPORTED_DIMENSIONS_BY_CATEGORY,  # noqa: F401
    _collect,  # noqa: F401
    _coupled_placement_options,  # noqa: F401
    _hard_options,  # noqa: F401
    _home_representation_options,  # noqa: F401
    _hosting_options,  # noqa: F401
    _intra_club_distribution_options,  # noqa: F401
    _movable_capacity_options,  # noqa: F401
    _option_is_applicable,  # noqa: F401
    _options_for_finding,  # noqa: F401
    _participation_options,  # noqa: F401
    _unplaced_options,  # noqa: F401
    supported_dimensions_for_finding,  # noqa: F401
)


# A non-dominated front is still bounded before it is reported: one extreme per
# objective plus the lowest-cost remaining points, so a caller gets a small
# representative trade-off set rather than every legal mutation.
load_context = load_maintenance_context


REQUEST_CONSTRAINT_OWNER = (
    "tournament_scheduler.request_constraints.request_constraint_violations"
)
HOSTING_COVERAGE_OWNER = "tournament_scheduler.hosting_coverage.hosting_coverage_matrix"
HOSTING_RESPONSIBILITY_OWNER = (
    "tournament_scheduler.hosting_responsibility.hosting_responsibility_facts"
)

_REQUEST_CONSTRAINT_RULES = (
    "request_team_unavailable",
    "request_minimum_gap",
    "request_opponent_avoidance",
)


def _audit_owner_evidence(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Tuple[str, ...], Dict[str, str]]:
    """Run the catalog rule owners the ordinary verifier does not cover.

    The season audit resolves every catalogued check against verifier codes and
    actionable findings. Request constraints, club x age-group hosting coverage
    and hosting responsibility are owned outside the ordinary verifier, so they
    are run here and their factual output is translated into that vocabulary.
    An owner is reported covered only after it ran on complete inputs; a
    missing input or a failed owner leaves its catalog rule ``incomplete`` with
    an actionable reason instead of a false green.
    """
    from .hosting_coverage import hosting_coverage_matrix
    from .hosting_responsibility import (
        RESPONSIBILITY_TRANSFER_CODE,
        hosting_responsibility_facts,
    )
    from .participation_withdrawals import eligible_hosting_teams
    from .request_constraints import request_constraint_violations

    covered: List[str] = []
    incomplete_reasons: Dict[str, str] = {}
    violations: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []

    try:
        violations.extend(
            dict(violation) for violation in request_constraint_violations(plan, decisions)
        )
        covered.append(REQUEST_CONSTRAINT_OWNER)
    except Exception as exc:  # defensive fail-closed boundary
        for rule_id in _REQUEST_CONSTRAINT_RULES:
            incomplete_reasons[rule_id] = f"request-constraint owner failed: {exc}"

    teams = eligible_hosting_teams(problem)
    if not teams:
        # Coverage/responsibility are derived from the registered roster; with
        # no roster evidence an empty result is not a clean season.
        incomplete_reasons["hosting_age_group_coverage"] = (
            "problem carries no registered-team evidence; hosting coverage cannot be evaluated"
        )
        incomplete_reasons["hosting_responsibility"] = (
            "problem carries no registered-team evidence; hosting responsibility cannot be evaluated"
        )
    else:
        try:
            for row in hosting_coverage_matrix(teams, plan.get("tournaments") or []):
                if not row.get("unresolved"):
                    continue
                age_group = str(row.get("age_group") or "")
                club = str(row.get("club") or "")
                findings.append(
                    {
                        # Distinct from the balance finding's ``hosting_balance:`` id
                        # so a coverage obligation is never silently discarded by a
                        # same-scope balance finding with a different code.
                        "finding_id": f"hosting_coverage:{age_group}:{club}",
                        "code": "unresolved_hosting_obligation",
                        "category": HOSTING,
                        "severity": "strong_goal",
                        "age_group": age_group,
                        "club": club,
                        "teams": int(row.get("teams", 0)),
                        "hosted": int(row.get("hosted", 0)),
                        "coverage_unresolved": True,
                        "message": (
                            f"{club} hosts no {age_group} tournament although it fields "
                            f"{int(row.get('teams', 0))} team(s)"
                        ),
                    }
                )
            covered.append(HOSTING_COVERAGE_OWNER)
        except Exception as exc:  # defensive fail-closed boundary
            incomplete_reasons["hosting_age_group_coverage"] = (
                f"hosting coverage owner failed: {exc}"
            )
        try:
            for row in hosting_responsibility_facts(problem, plan):
                excess = int(row.get("excess", 0))
                if excess <= 0:
                    continue
                age_group = str(row.get("age_group") or "")
                club = str(row.get("club") or "")
                findings.append(
                    {
                        "finding_id": f"hosting_responsibility:{age_group}:{club}",
                        "code": RESPONSIBILITY_TRANSFER_CODE,
                        "category": HOSTING,
                        "severity": "strong_goal",
                        "age_group": age_group,
                        "club": club,
                        "target": int(row.get("target", 0)),
                        "actual": int(row.get("actual", 0)),
                        "excess": excess,
                        "message": (
                            f"{club} hosts {int(row.get('actual', 0))} {age_group} "
                            f"tournament(s) against a target of {int(row.get('target', 0))}; "
                            "hosting responsibility was transferred to a club that does not owe it"
                        ),
                    }
                )
            covered.append(HOSTING_RESPONSIBILITY_OWNER)
        except Exception as exc:  # defensive fail-closed boundary
            incomplete_reasons["hosting_responsibility"] = (
                f"hosting responsibility owner failed: {exc}"
            )

    return violations, findings, tuple(covered), incomplete_reasons


def season_audit(season: str, *, root: str = DEFAULT_SEASON_ROOT) -> Dict[str, Any]:
    """Run the catalog-driven season-wide completion gate over canonical state.

    The audit is exhaustive by construction: it combines the ordinary
    ``verify_candidate`` result, the final minimum-size/game-integrity verifier
    and the canonical-lock verifier with the live finding inventory, resolves
    every catalogued hard/obligation check against that evidence and reports
    skipped checks as incomplete rather than as a pass. Reconciliation is read
    from the canonical lifecycle owner, so a stale or unreconciled season cannot
    report a green completion.
    """
    from .canonical_baseline import verify_canonical_locks
    from .final_verification import verify_final_candidate
    from .repair_adoption_guard import season_wide_audit
    from .season_state import season_lifecycle_report

    schedule, decisions, plan, problem = load_context(season, root=root)
    revision = canonical_state_revision(schedule, decisions)
    verification = verify_candidate(plan, problem)
    # Use the same accepted-booking exception owner as final verification and
    # findings before constructing the season-wide hard gate. The raw planning
    # verifier remains strict, but an exact accepted source interval is a
    # resolved fact rather than an unresolved hard violation.
    from .canonical_exception_policy import reclassify_accepted_exceptions

    accepted_booking = reclassify_accepted_exceptions(
        problem, plan, list(verification.get("violations") or [])
    )
    verification = dict(verification)
    verification["violations"] = accepted_booking["blocking_violations"]
    verification["booking_feasibility_warnings"] = accepted_booking["accepted_exceptions"]
    verification["ok"] = not verification["violations"]
    final = verify_final_candidate(dict(plan), dict(problem))
    locks = verify_canonical_locks(build_canonical_baseline(schedule, decisions), dict(plan))
    (
        owner_violations,
        owner_findings,
        owner_covered,
        owner_incomplete_reasons,
    ) = _audit_owner_evidence(plan, problem, decisions)
    merged_violations: List[Dict[str, Any]] = []
    seen_violations: set = set()
    # Include the ordinary verifier explicitly: ``verify_final_candidate``
    # delegates to it, but this must not depend on that implementation detail,
    # and a rule checked only by ``verify_candidate`` must stay visible. The
    # request-constraint owner's violations are appended here so they count as
    # the hard constraints they are, not as advisory reporting data.
    for violation in (
        list(verification.get("violations") or [])
        + list(final.get("violations") or [])
        + list(locks or [])
        + owner_violations
    ):
        key = (
            str(violation.get("code") or ""),
            str(violation.get("tournament_id") or ""),
            str(violation.get("team") or ""),
            str(violation.get("constraint_id") or ""),
            "|".join(str(item) for item in violation.get("tournament_ids") or []),
            "|".join(str(item) for item in violation.get("dates") or []),
        )
        if key in seen_violations:
            continue
        seen_violations.add(key)
        merged_violations.append(dict(violation))
    merged_verification = {
        **verification,
        "ok": (
            bool(verification.get("ok"))
            and bool(final.get("ok"))
            and not locks
            and not owner_violations
        ),
        "violations": merged_violations,
    }
    findings = construct_findings(plan, problem, verification, decisions=decisions)
    # The findings projection already translates coverage rows; append only the
    # owner findings it does not carry (structural coverage shortfalls and
    # responsibility transfers). Deduplicate by semantic identity
    # ``(code, age_group, club)`` -- not the finding id alone -- so a
    # same-scope balance finding with a different code cannot silently swallow
    # the coverage obligation, and vice versa.
    existing_finding_keys = {
        (
            str(finding.get("code") or ""),
            str(finding.get("age_group") or ""),
            str(finding.get("club") or ""),
        )
        for finding in findings
    }
    for finding in owner_findings:
        key = (
            str(finding.get("code") or ""),
            str(finding.get("age_group") or ""),
            str(finding.get("club") or ""),
        )
        if key in existing_finding_keys:
            continue
        existing_finding_keys.add(key)
        findings.append(finding)
    from .calendar_bookings import association_findings

    findings.extend(association_findings(problem=problem, plan=plan, decisions=decisions))
    annotate_findings(findings)
    annotate_resolutions(findings)
    for finding in findings:
        finding.setdefault("search_coverage", cheap_search_coverage(finding))
    score = with_unresolved_obligations_count(score_candidate(dict(plan), problem=dict(problem)))
    lifecycle = season_lifecycle_report(season, root=root)
    reconciliation = lifecycle.get("reconciliation") or None
    # Re-read the canonical revision after assembling the audit so a concurrent
    # canonical change cannot be reported as a fresh completion from a stale
    # in-memory snapshot. The plan handed to the audit is the first read; the
    # revision comparison detects drift.
    schedule_after, decisions_after, plan_after, _problem_after = load_context(
        season, root=root
    )
    revision_after = canonical_state_revision(schedule_after, decisions_after)
    audit = season_wide_audit(
        plan=plan,
        findings=findings,
        verification=merged_verification,
        score=score,
        reconciliation=reconciliation,
        expected_revision=revision,
        current_revision=revision_after,
        covered_verifier_owners=(
            "tournament_scheduler.final_verification.verify_final_candidate",
            "tournament_scheduler.canonical_baseline.verify_canonical_locks",
            *owner_covered,
        ),
        incomplete_reasons=owner_incomplete_reasons,
    )
    audit["revision_stable"] = revision == revision_after
    audit["content_fingerprint_after"] = _plan_content_fingerprint(plan_after)
    from .pipeline.fingerprints import stable_payload_sha256

    # Booking/confirmation/protection/acceptance metadata lives in decisions and
    # is part of the canonical revision; surface its identity explicitly so the
    # audit evidence names the full state it assessed, not only the placements.
    audit["decisions_fingerprint"] = stable_payload_sha256(decisions)
    audit["decisions_fingerprint_after"] = stable_payload_sha256(decisions_after)
    return {
        "schema_version": SEASON_MAINTENANCE_SCHEMA_VERSION,
        "season": season,
        "revision": revision,
        "revision_after": revision_after,
        "candidate_fingerprint": _plan_fingerprint(plan),
        "lifecycle": {
            "state": lifecycle.get("state"),
            "reconciliation": reconciliation,
        },
        "audit": audit,
    }


def repair_options(
    season: str,
    finding_id: str,
    *,
    root: str = DEFAULT_SEASON_ROOT,
    age_group: str | None = None,
    allow_search: bool = False,
    allow_manual_placement: bool = False,
    allow_host_confirmation: bool = False,
) -> Dict[str, Any]:
    """Enumerate deterministic repair options for one selected finding."""
    schedule, decisions, plan, problem = load_context(season, root=root)
    revision = canonical_state_revision(schedule, decisions)
    findings = construct_findings(plan, problem, verify_candidate(plan, problem), decisions=decisions)
    finding = _require_finding(findings, finding_id, age_group=age_group)
    options, rejected, families = _options_for_finding(
        plan, problem, finding, allow_search=allow_search, dimensions=DEFAULT_DIMENSIONS
    )
    constraints = active_request_constraints(decisions)
    pareto = _annotate_pareto(
        plan,
        problem,
        options,
        finding,
        DEFAULT_DIMENSIONS,
        active_constraints=constraints,
        allow_manual_placement=allow_manual_placement,
        allow_host_confirmation=allow_host_confirmation,
    )
    return {
        "season": season,
        "revision": revision,
        "candidate_fingerprint": _plan_fingerprint(plan),
        "finding": finding,
        "option_count": len(options),
        "options": options,
        "rejected_candidates": rejected,
        "families": families,
        "pareto": pareto,
        "escalation": _escalation(options, rejected, finding),
        "request_constraints": _request_constraint_context(plan, decisions),
    }


def search(
    season: str,
    finding_id: str,
    *,
    root: str = DEFAULT_SEASON_ROOT,
    age_group: str | None = None,
    dimensions: Iterable[str] = ("participants", "host"),
    allow_manual_placement: bool = False,
    allow_host_confirmation: bool = False,
) -> Dict[str, Any]:
    """Run the bounded finding-directed search and return verified non-dominated options."""
    resolved_dimensions = tuple(sorted({str(d) for d in dimensions}))
    schedule, decisions, plan, problem = load_context(season, root=root)
    revision = canonical_state_revision(schedule, decisions)
    findings = construct_findings(plan, problem, verify_candidate(plan, problem), decisions=decisions)
    finding = _require_finding(findings, finding_id, age_group=age_group)
    options, rejected, families = _options_for_finding(
        plan, problem, finding, allow_search=True, dimensions=resolved_dimensions
    )
    constraints = active_request_constraints(decisions)
    pareto = _annotate_pareto(
        plan,
        problem,
        options,
        finding,
        resolved_dimensions,
        active_constraints=constraints,
        allow_manual_placement=allow_manual_placement,
        allow_host_confirmation=allow_host_confirmation,
    )
    return {
        "season": season,
        "revision": revision,
        "candidate_fingerprint": _plan_fingerprint(plan),
        "finding": finding,
        "option_count": len(options),
        "options": options,
        "rejected_candidates": rejected,
        "families": families,
        "pareto": pareto,
        "escalation": _escalation(options, rejected, finding),
        "requested_dimensions": list(resolved_dimensions),
        "request_constraints": _request_constraint_context(plan, decisions),
    }


# The participation integration's ``operator_accepted`` state is not proven by
# the verifier -- it is an explicit, durable operator decision. These two entry
# points own that decision: acceptance never edits the schedule or the target,
# and it stops applying by itself once the target changes or the deviation gets
# worse (see ``participation_targets.evidence_covers_deviation``).
def accept_finding(
    season: str,
    finding_id: str,
    *,
    root: str = DEFAULT_SEASON_ROOT,
    age_group: str | None = None,
    actor: Optional[str] = None,
    note: str = "",
) -> Dict[str, Any]:
    """Persist an explicit operator acceptance of one participation finding."""
    schedule, decisions, plan, problem = load_context(season, root=root)
    revision = canonical_state_revision(schedule, decisions)
    findings = construct_findings(plan, problem, verify_candidate(plan, problem), decisions=decisions)
    finding = _require_finding(findings, finding_id, age_group=age_group)
    if finding["category"] != PARTICIPATION:
        raise SeasonMaintenanceError(
            f"Only participation findings can be accepted; {finding_id} is {finding['category']}"
        )
    record = record_participation_acceptance(
        season=season,
        club=str(finding["club"]),
        label=str(finding["team"]),
        age_group=str(finding.get("age_group") or ""),
        scope=str(finding["scope"]),
        direction=str(finding.get("direction") or ""),
        actual=int(finding.get("actual", 0)),
        target=int(finding.get("target") or 0),
        root=root,
        actor=actor,
        note=note,
    )
    return {
        "season": season,
        "ok": True,
        "finding": finding,
        "revision": revision,
        "acceptance": record,
        "fresh_findings": list_findings(season, root=root),
    }


def revoke_acceptance(
    season: str,
    finding_id: str,
    *,
    root: str = DEFAULT_SEASON_ROOT,
    age_group: str | None = None,
    actor: Optional[str] = None,
    note: str = "",
) -> Dict[str, Any]:
    """Revoke the active operator acceptance for one participation finding."""
    schedule, decisions, plan, problem = load_context(season, root=root)
    revision = canonical_state_revision(schedule, decisions)
    findings = construct_findings(plan, problem, verify_candidate(plan, problem), decisions=decisions)
    finding = _require_finding(findings, finding_id, age_group=age_group)
    if finding["category"] != PARTICIPATION:
        raise SeasonMaintenanceError(
            f"Only participation findings carry an acceptance; {finding_id} is {finding['category']}"
        )
    record = revoke_participation_acceptance(
        season=season,
        club=str(finding["club"]),
        label=str(finding["team"]),
        age_group=str(finding.get("age_group") or ""),
        scope=str(finding["scope"]),
        root=root,
        actor=actor,
        note=note,
    )
    return {
        "season": season,
        "ok": True,
        "finding": finding,
        "revision": revision,
        "revoked": record,
        "fresh_findings": list_findings(season, root=root),
    }


# ---------------------------------------------------------------------------
# Plan-level core (no canonical season required)
#
# The canonical-season entry points above are thin wrappers around the same
# facts/legality boundary: findings, options and atomic applies are pure
# functions of a candidate plan plus a planning problem. An unpromoted Stage
# 3/Stage 4 candidate that has not been promoted is refined through the exact
# same repository-owned providers, so a candidate-only rule set cannot drift
# away from the canonical one.
# ---------------------------------------------------------------------------


def repair_options_for_plan(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding_id: str,
    *,
    age_group: str | None = None,
    allow_search: bool = False,
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
    allow_manual_placement: bool = False,
    allow_host_confirmation: bool = False,
) -> Dict[str, Any]:
    """Enumerate deterministic repair options for one finding on a bare plan."""
    resolved_dimensions = tuple(sorted({str(d) for d in dimensions}))
    findings = findings_for_plan(plan, problem)
    finding = _require_finding(findings, finding_id, age_group=age_group)
    options, rejected, families = _options_for_finding(
        plan, problem, finding, allow_search=allow_search, dimensions=resolved_dimensions
    )
    pareto = _annotate_pareto(
        plan,
        problem,
        options,
        finding,
        resolved_dimensions,
        allow_manual_placement=allow_manual_placement,
        allow_host_confirmation=allow_host_confirmation,
    )
    return {
        "candidate_fingerprint": _plan_fingerprint(plan),
        "finding": finding,
        "option_count": len(options),
        "options": options,
        "rejected_candidates": rejected,
        "families": families,
        "pareto": pareto,
        "escalation": _escalation(options, rejected, finding),
    }


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def _findings(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    verification: Mapping[str, Any],
    *,
    decisions: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    findings.extend(_hard_findings(plan, verification))
    findings.extend(_hosting_findings(problem, plan))
    findings.extend(_participation_findings(verification, problem))
    findings.extend(_manual_findings(plan, verification))
    findings.extend(_unplaced_findings(plan, problem, decisions=decisions))
    findings.extend(_movable_capacity_findings(problem, plan))
    findings.extend(_shape_findings(verification))
    findings.extend(_booking_feasibility_findings(verification))
    findings.extend(_spacing_findings(problem, plan))
    findings.extend(_home_representation_findings(problem, plan))
    findings.extend(_intra_club_distribution_findings(verification))
    findings.sort(key=lambda entry: (entry["category"], entry["finding_id"]))
    # Attach the stable catalog rule ID to every finding whose code is a
    # registered semantic, so the controller payload carries identity instead
    # of prose categories alone.
    annotate_findings(findings)
    annotate_resolutions(findings)
    # Every finding carries a coverage view so a controller never has to infer
    # "untried dimensions remain" from the absence of options. The unplaced
    # provider already attached its authoritative per-obligation coverage; the
    # other families get the conservative cheap view here.
    for finding in findings:
        finding.setdefault("search_coverage", cheap_search_coverage(finding))
    return findings


def _escalation(options: List[Dict[str, Any]], rejected: List[Dict[str, Any]], finding: Mapping[str, Any]) -> Dict[str, Any]:
    if any(_option_is_applicable(option) for option in options):
        return {"needed": False, "reason": "legal_option_available"}
    if finding["category"] == HOSTING:
        return {
            "needed": True,
            "reason": "no_direct_rehost",
            "next": "request bounded search for this finding",
        }
    if finding["category"] == PARTICIPATION:
        avoidability = finding.get("avoidability") or "unclassified"
        return {
            "needed": True,
            "reason": "no_search_improvement_yet",
            "avoidability": avoidability,
            "proven_infeasible": bool(finding.get("proven_infeasible")),
            "note": (
                "bounded_search_exhausted is not proof of infeasibility; another targeted "
                "search may be requested"
            ),
        }
    if finding["category"] == MOVABLE_CAPACITY:
        return {
            "needed": True,
            "reason": "no_verified_movable_capacity_repair",
            "next": "inspect rejected_candidates for date/roster reasons",
        }
    if finding["category"] == HOME_REPRESENTATION:
        return {
            "needed": True,
            "reason": "no_verified_sibling_swap",
            "next": (
                "inspect rejected_candidates; simple rotations and coupled "
                "home + away swaps were enumerated before treating the skew as unavoidable"
            ),
        }
    if finding["category"] == CLUB_DISTRIBUTION:
        return {
            "needed": True,
            "reason": "no_verified_sibling_substitution",
            "next": (
                "inspect rejected_candidates; away-then-home substitutions and the "
                "bounded coupled sibling-only neighborhood were enumerated before the "
                "imbalance was treated as unavoidable"
            ),
        }
    if finding["category"] == TEMPORAL_CLUSTERING:
        return {
            "needed": True,
            "reason": "no_verified_coupled_placement_repair",
            "next": (
                "inspect rejected_candidates; cross-age placement exchanges and same-age "
                "roster rotations were enumerated before treating the cluster as unavoidable"
            ),
        }
    if finding.get("code") == "unplaced_tournament_placement":
        coverage = finding.get("search_coverage") or {}
        return {
            "needed": True,
            "reason": coverage.get("status") or "no_verified_materialization",
            "untried_dimensions": list(coverage.get("untried") or []),
            "next": (
                "request bounded search for the untried dimensions"
                if coverage.get("status") == "search_incomplete"
                else "inspect rejected_candidates for the deterministic rejection reasons"
            ),
        }
    return {"needed": True, "reason": "no_cheap_local_option"}


# ---------------------------------------------------------------------------
# Pareto / non-dominated alternative set
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Metric deltas
# ---------------------------------------------------------------------------


