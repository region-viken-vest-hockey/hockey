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

from .canonical_baseline import build_canonical_baseline, change_cost
from .hosting_balance_repair import hosting_finding_id
from .local_repair_options import enumerate_local_repair_options
from .pareto import non_dominated_indices, representative_indices
from .participation_deviation_repair import participation_finding_id
from .participation_targets import INTRA_CLUB_DISTRIBUTION, search_evidence_from_acceptances
from .planning_contract import score_candidate, verify_candidate
from .quality_objectives import (
    QUALITY_OBJECTIVE_DIMENSIONS,
    compare_quality_scores,
    quality_objective_vector,
    with_unresolved_obligations_count,
)
from .season_state import (
    DEFAULT_SEASON_ROOT,
    canonical_state_revision,
    load_decisions,
    load_participation_acceptances,
    load_schedule,
    record_participation_acceptance,
    revoke_participation_acceptance,
)

SEASON_MAINTENANCE_SCHEMA_VERSION = 1

DEFAULT_DIMENSIONS: Tuple[str, ...] = ("participants", "host")

# The objective vector every maintenance option is measured on, oriented
# "lower is better" so one uniform dominance check applies. Defect and
# host-confirmation counts come from the same independent verifier that produced
# the findings; change cost is the number of tournaments whose placement/roster
# signature changed. An option is only on the front when no other option is at
# least as good everywhere and strictly better somewhere -- a verified option is
# not automatically a non-dominated one.
#
# The vector deliberately combines two families:
#   * hard/defect and change-cost dimensions owned by this module (a maintenance
#     repair must never buy a lower travel cost with a new hard violation);
#   * the shared Stage-3 soft-quality objectives (opponent diversity, turnaround,
#     same-club clustering, temporal coverage) plus travel, so a local repair is
#     compared on the same planner-independent quality facts Stage 3 uses
#     instead of only on the defect it was asked to fix.
MAINTENANCE_DEFECT_DIMENSIONS: Tuple[str, ...] = (
    "hard_violations",
    "unresolved_hosting_obligations",
    "hosting_balance_imbalances",
    "manual_placements",
    "unresolved_placement_obligations",
    "participation_deviations",
    "avoidable_participation_deviations",
    "host_confirmation_dependencies",
    "changed_tournament_count",
)

# Travel is a first-class operational consequence of moving a tournament to a
# different host/date, so it is measured both as a season total and as the worst
# single team. Computed by the canonical ``compute_team_travel_distances``.
TRAVEL_OBJECTIVE_DIMENSIONS: Tuple[str, ...] = (
    "total_travel_km",
    "max_team_travel_km",
)

PARETO_DIMENSIONS: Tuple[str, ...] = (
    MAINTENANCE_DEFECT_DIMENSIONS + QUALITY_OBJECTIVE_DIMENSIONS + TRAVEL_OBJECTIVE_DIMENSIONS
)

# A non-dominated front is still bounded before it is reported: one extreme per
# objective plus the lowest-cost remaining points, so a caller gets a small
# representative trade-off set rather than every legal mutation.
MAX_PARETO_REPRESENTATIVES = 6

# Categories are facts about what kind of finding this is, not a mandatory
# processing order: the harness may select any finding independently.
HARD_VIOLATION = "hard_violation"
HOSTING = "hosting"
PARTICIPATION = "participation"
MANUAL_PLACEMENT = "manual_placement"
MOVABLE_CAPACITY = "movable_capacity"
ROSTER_SHAPE = "roster_shape"

# Canonical search-coverage vocabulary shared by every finding family. A
# finding always carries ``search_coverage`` so a caller can tell apart
# "a verified option exists", "supported dimensions remain untried" and "the
# configured bounded search was run and found nothing" -- the last is
# deliberately *not* ``proven_infeasible`` (no bounded search proves that).
SEARCH_COVERAGE_OPTION_AVAILABLE = "option_available"
SEARCH_COVERAGE_INCOMPLETE = "search_incomplete"
SEARCH_COVERAGE_BOUNDED_EXHAUSTED = "bounded_search_exhausted"
SEARCH_COVERAGE_PROVEN_INFEASIBLE = "proven_infeasible"

# Supported repair/search dimensions per finding category. These describe the
# neighborhood a bounded search may widen into, not a scheduling rule.
SUPPORTED_DIMENSIONS_BY_CATEGORY: Dict[str, Tuple[str, ...]] = {
    HOSTING: ("participants", "host"),
    PARTICIPATION: ("participants",),
    MANUAL_PLACEMENT: ("participants", "host"),
    MOVABLE_CAPACITY: ("host",),
    HARD_VIOLATION: ("participants", "host"),
    ROSTER_SHAPE: (),
}


def supported_dimensions_for_finding(finding: Mapping[str, Any]) -> Tuple[str, ...]:
    """Canonical supported search dimensions for one finding's category."""
    category = str(finding.get("category") or "")
    return SUPPORTED_DIMENSIONS_BY_CATEGORY.get(category, DEFAULT_DIMENSIONS)


def cheap_search_coverage(finding: Mapping[str, Any]) -> Dict[str, Any]:
    """Conservative coverage for a finding whose bounded search has not run yet.

    The cheap listing path never runs the provider search, so every supported
    dimension is still untried: ``search_incomplete``, never a false
    ``bounded_search_exhausted``.
    """
    supported = list(supported_dimensions_for_finding(finding))
    return {
        "status": SEARCH_COVERAGE_INCOMPLETE,
        "supported": supported,
        "untried": list(supported),
        "attempted": [],
        "search_requested": False,
        "proven_infeasible": False,
    }


def derive_search_coverage(
    *,
    option_count: int,
    rejected_count: int,
    allow_search: bool,
    supported: Iterable[str],
) -> Dict[str, Any]:
    """Resolved coverage after a provider actually ran for one finding.

    ``bounded_search_exhausted`` means exactly "the configured bounded search
    ran and produced no verified option", never "no solution exists".
    ``rejected_count`` is carried so a caller can inspect the deterministic
    rejection evidence instead of treating the absence of options as proof.
    """
    supported_list = [str(item) for item in supported]
    if option_count > 0:
        status = SEARCH_COVERAGE_OPTION_AVAILABLE
    elif allow_search:
        status = SEARCH_COVERAGE_BOUNDED_EXHAUSTED
    else:
        status = SEARCH_COVERAGE_INCOMPLETE
    return {
        "status": status,
        "supported": supported_list,
        "untried": [] if allow_search or option_count > 0 else list(supported_list),
        "attempted": list(supported_list) if allow_search else [],
        "search_requested": bool(allow_search),
        "rejected_count": int(rejected_count),
        "proven_infeasible": False,
    }


class SeasonMaintenanceError(RuntimeError):
    """Raised when a maintenance request cannot be served safely."""


def _problem_from_schedule(schedule: Mapping[str, Any]) -> Dict[str, Any]:
    context = schedule.get("verification_context")
    problem = context.get("problem") if isinstance(context, Mapping) else None
    if not isinstance(problem, Mapping):
        raise SeasonMaintenanceError(
            "Canonical season carries no promoted verification-context problem; "
            "maintenance cannot reconstruct the planning contract without a re-promotion"
        )
    return dict(problem)


def load_context(
    season: str,
    *,
    root: str = DEFAULT_SEASON_ROOT,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Return ``(schedule, decisions, plan, problem)`` for a promoted season.

    The canonical baseline (locks + approval state) is rebuilt from the current
    canonical files and injected into the planning problem, so verification and
    repair always see the *current* locks/approvals rather than promotion-time
    state.
    """
    schedule = load_schedule(season, root=root)
    decisions = load_decisions(season, root=root)
    problem = _problem_from_schedule(schedule)
    problem["canonical_baseline"] = build_canonical_baseline(schedule, decisions)
    # A persisted operator acceptance is injected as verifier search evidence so
    # the same independent verifier that classifies every other deviation also
    # honours an explicit operator_accepted decision -- and stops honouring it
    # once the target changes or the deviation gets worse.
    problem["participation_search_evidence"] = search_evidence_from_acceptances(
        load_participation_acceptances(season, root=root)
    )
    return schedule, decisions, dict(schedule.get("plan") or {}), problem


def _acceptances_by_scope(
    problem: Mapping[str, Any],
) -> Dict[Tuple[str, str, str, str], Dict[str, Any]]:
    """Index the problem's operator acceptances by ``(club, label, age, scope)``."""
    out: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    evidence = problem.get("participation_search_evidence")
    if not isinstance(evidence, Mapping):
        return out
    for identity, entries in evidence.items():
        if not isinstance(identity, tuple) or len(identity) != 3:
            continue
        for entry in entries if isinstance(entries, (list, tuple)) else [entries]:
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("status") or "") != "operator_accepted":
                continue
            scope = str(entry.get("scope") or "")
            out[(str(identity[0]), str(identity[1]), str(identity[2]), scope)] = dict(entry)
    return out


def list_findings(season: str, *, root: str = DEFAULT_SEASON_ROOT) -> Dict[str, Any]:
    """Return stable, revision-bound actionable findings over canonical state."""
    schedule, decisions, plan, problem = load_context(season, root=root)
    verification = verify_candidate(plan, problem)
    revision = canonical_state_revision(schedule, decisions)
    findings = _findings(plan, problem, verification)
    counts: Dict[str, int] = {}
    for finding in findings:
        counts[finding["code"]] = counts.get(finding["code"], 0) + 1
    return {
        "schema_version": SEASON_MAINTENANCE_SCHEMA_VERSION,
        "season": season,
        "revision": revision,
        "candidate_fingerprint": _plan_fingerprint(plan),
        "verification_ok": bool(verification.get("ok")),
        "finding_count": len(findings),
        "counts_by_code": counts,
        "findings": findings,
    }


def repair_options(
    season: str,
    finding_id: str,
    *,
    root: str = DEFAULT_SEASON_ROOT,
    allow_search: bool = False,
) -> Dict[str, Any]:
    """Enumerate deterministic repair options for one selected finding."""
    schedule, decisions, plan, problem = load_context(season, root=root)
    revision = canonical_state_revision(schedule, decisions)
    findings = _findings(plan, problem, verify_candidate(plan, problem))
    finding = _require_finding(findings, finding_id)
    options, rejected, families = _options_for_finding(
        plan, problem, finding, allow_search=allow_search, dimensions=DEFAULT_DIMENSIONS
    )
    pareto = _annotate_pareto(plan, problem, options, finding, DEFAULT_DIMENSIONS)
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
    }


def search(
    season: str,
    finding_id: str,
    *,
    root: str = DEFAULT_SEASON_ROOT,
    dimensions: Iterable[str] = ("participants", "host"),
) -> Dict[str, Any]:
    """Run the bounded finding-directed search and return verified non-dominated options."""
    resolved_dimensions = tuple(sorted({str(d) for d in dimensions}))
    schedule, decisions, plan, problem = load_context(season, root=root)
    revision = canonical_state_revision(schedule, decisions)
    findings = _findings(plan, problem, verify_candidate(plan, problem))
    finding = _require_finding(findings, finding_id)
    options, rejected, families = _options_for_finding(
        plan, problem, finding, allow_search=True, dimensions=resolved_dimensions
    )
    pareto = _annotate_pareto(plan, problem, options, finding, resolved_dimensions)
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
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
) -> Dict[str, Any]:
    """Atomically apply one verified option to canonical state and return the delta.

    The option is reproduced from the current canonical revision and fully
    re-verified before any write. A stale revision (or an option that no longer
    enumerates) is rejected without touching canonical state.
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
    schedule, decisions, plan, problem = load_context(season, root=root)
    revision = canonical_state_revision(schedule, decisions)
    if expected_revision and expected_revision != revision:
        return _rejected_delta(
            season, revision, "stale_canonical_revision", expected_revision=expected_revision
        )
    findings = _findings(plan, problem, verify_candidate(plan, problem))
    if finding_id:
        candidates = [_require_finding(findings, finding_id)]
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
    before_verification = verify_candidate(plan, problem)
    verification = applied.get("verification") or verify_candidate(dict(result_candidate), dict(problem))
    if not verification.get("ok"):
        return _rejected_delta(
            season,
            revision,
            "verification_failed",
            option_id=option_id,
            verification=verification,
            delta=_metric_delta(plan, before_verification),
        )
    preview = _metric_delta(
        plan, before_verification, candidate=result_candidate, after_verification=verification, problem=problem
    )
    preview["changed_tournament_ids"] = _changed_tournament_ids(plan, result_candidate)
    preview["change_cost"] = change_cost(build_canonical_baseline(schedule, decisions), result_candidate)
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
        }

    from .season_state import apply_candidate

    updated_schedule, updated_decisions, cost = apply_candidate(
        season=season,
        candidate=result_candidate,
        root=root,
        problem=problem,
        actor=actor,
    )
    new_revision = canonical_state_revision(updated_schedule, updated_decisions)
    fresh_verification = verify_candidate(dict(updated_schedule.get("plan") or {}), problem)
    after_plan = dict(updated_schedule.get("plan") or {})
    delta = _metric_delta(
        plan, before_verification, candidate=after_plan, after_verification=fresh_verification, problem=problem
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
        "fresh_findings": list_findings(season, root=root),
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
    actor: Optional[str] = None,
    note: str = "",
) -> Dict[str, Any]:
    """Persist an explicit operator acceptance of one participation finding."""
    schedule, decisions, plan, problem = load_context(season, root=root)
    revision = canonical_state_revision(schedule, decisions)
    findings = _findings(plan, problem, verify_candidate(plan, problem))
    finding = _require_finding(findings, finding_id)
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
    actor: Optional[str] = None,
    note: str = "",
) -> Dict[str, Any]:
    """Revoke the active operator acceptance for one participation finding."""
    schedule, decisions, plan, problem = load_context(season, root=root)
    revision = canonical_state_revision(schedule, decisions)
    findings = _findings(plan, problem, verify_candidate(plan, problem))
    finding = _require_finding(findings, finding_id)
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


def plan_fingerprint(plan: Mapping[str, Any]) -> str:
    return _plan_fingerprint(plan)


def baseline_for_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    """Change-cost baseline for an unpromoted candidate (no approvals/locks).

    The reviewed candidate itself is the refinement baseline, so change cost
    measures movement away from what was reviewed rather than inventing a
    canonical-season baseline that does not apply before promotion.
    """
    return build_canonical_baseline({"plan": dict(plan)}, {})


def findings_for_plan(
    plan: Mapping[str, Any], problem: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """Stable actionable findings over any candidate plan + planning problem."""
    return _findings(plan, problem, verify_candidate(dict(plan), dict(problem)))


def repair_options_for_plan(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding_id: str,
    *,
    allow_search: bool = False,
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
) -> Dict[str, Any]:
    """Enumerate deterministic repair options for one finding on a bare plan."""
    resolved_dimensions = tuple(sorted({str(d) for d in dimensions}))
    findings = findings_for_plan(plan, problem)
    finding = _require_finding(findings, finding_id)
    options, rejected, families = _options_for_finding(
        plan, problem, finding, allow_search=allow_search, dimensions=resolved_dimensions
    )
    pareto = _annotate_pareto(plan, problem, options, finding, resolved_dimensions)
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


def apply_repair_to_plan(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    option_id: str,
    *,
    finding_id: Optional[str] = None,
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
    baseline: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Atomically reproduce and apply one verified option to a bare plan.

    Returns the mutated candidate plus the same before/after metric delta the
    canonical boundary returns; it never writes any canonical state.
    """
    resolved_dimensions = tuple(sorted({str(d) for d in dimensions}))
    findings = findings_for_plan(plan, problem)
    if finding_id:
        candidates = [_require_finding(findings, finding_id)]
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
    delta = _metric_delta(
        plan,
        before_verification,
        candidate=result_candidate,
        after_verification=verification,
        problem=problem,
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
        "delta": delta,
    }


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def _findings(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    verification: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    findings.extend(_hard_findings(plan, verification))
    findings.extend(_hosting_findings(problem, plan))
    findings.extend(_participation_findings(verification, problem))
    findings.extend(_manual_findings(plan, verification))
    findings.extend(_unplaced_findings(plan, problem))
    findings.extend(_movable_capacity_findings(problem, plan))
    findings.extend(_shape_findings(verification))
    findings.sort(key=lambda entry: (entry["category"], entry["finding_id"]))
    # Every finding carries a coverage view so a controller never has to infer
    # "untried dimensions remain" from the absence of options. The unplaced
    # provider already attached its authoritative per-obligation coverage; the
    # other families get the conservative cheap view here.
    for finding in findings:
        finding.setdefault("search_coverage", cheap_search_coverage(finding))
    return findings


def _hard_findings(plan: Mapping[str, Any], verification: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for index, violation in enumerate(verification.get("violations") or [], start=1):
        code = str(violation.get("code") or "hard_violation")
        tournament_ids = sorted(
            {str(item) for item in violation.get("tournament_ids") or [] if item}
        )
        tournament_id = str(violation.get("tournament_id") or "")
        if tournament_id and tournament_id not in tournament_ids:
            tournament_ids = sorted({tournament_id, *tournament_ids})
        # A stable finding id: prefer the exact tournament scope the finding
        # names, and only fall back to the violation index when the verifier
        # reports no tournament identity at all.
        if tournament_ids:
            finding_id = f"{code}:{'+'.join(tournament_ids)}"
        else:
            finding_id = f"{code}:{index}"
        entry: Dict[str, Any] = {
            "finding_id": finding_id,
            "code": code,
            "category": HARD_VIOLATION,
            "severity": "hard",
            "age_group": violation.get("age_group"),
            "tournament_id": tournament_ids[0] if tournament_ids else None,
            "message": violation.get("message") or code,
        }
        if tournament_ids:
            entry["tournament_ids"] = tournament_ids
        for key in ("team", "date"):
            if violation.get(key) is not None:
                entry[key] = violation[key]
        out.append(entry)
    return out


def _hosting_findings(problem: Mapping[str, Any], plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    from .hosting_balance_repair import hosting_deficit_rows

    out: List[Dict[str, Any]] = []
    for row in hosting_deficit_rows(problem, plan):
        age_group = str(row["age_group"])
        club = str(row["club"])
        out.append(
            {
                "finding_id": hosting_finding_id(age_group, club),
                "code": "unresolved_hosting_obligation" if row.get("coverage_unresolved") else "hosting_balance_imbalance",
                "category": HOSTING,
                "severity": "strong_goal",
                "age_group": age_group,
                "club": club,
                "target": int(row.get("target", 0)),
                "actual": int(row.get("actual", 0)),
                "deficit": int(row.get("deficit", 0)),
                "coverage_unresolved": bool(row.get("coverage_unresolved")),
                "message": (
                    f"{club} has a {age_group} hosting deficit of {int(row.get('deficit', 0))} "
                    f"(actual {int(row.get('actual', 0))} vs target {int(row.get('target', 0))})"
                ),
            }
        )
    return out


def _participation_findings(
    verification: Mapping[str, Any], problem: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    acceptances = _acceptances_by_scope(problem)
    for deviation in verification.get("participation_deviations") or []:
        if not _deviation_is_unresolved(deviation):
            # An aggregate-complete, uneven multi-team pool is intra-club
            # distribution, not an unresolved participation deficit -- it stays
            # visible as evidence/metrics but is not an actionable finding and
            # must not trigger repair search.
            continue
        club = str(deviation.get("club") or "")
        team = str(deviation.get("team") or "")
        scope = str(deviation.get("scope") or "")
        avoidability = str(deviation.get("avoidability") or "")
        finding: Dict[str, Any] = {
            "finding_id": participation_finding_id(club, team, scope),
            "code": "participation_deviation",
            "category": PARTICIPATION,
            "severity": "strong_goal",
            "age_group": deviation.get("age_group"),
            "club": club,
            "team": team,
            "scope": scope,
            "direction": deviation.get("direction"),
            "actual": int(deviation.get("actual", 0)),
            "target": deviation.get("target"),
            "deviation": int(deviation.get("deviation", 0)),
            "avoidability": avoidability,
            "proven_infeasible": avoidability == "proven_infeasible",
            "searchable": True,
            "message": (
                f"{team} ({club}, {deviation.get('age_group')}) is {deviation.get('direction')} "
                f"by {abs(int(deviation.get('deviation', 0)))} in {scope} "
                f"({avoidability or 'unclassified'})"
            ),
        }
        acceptance = acceptances.get((club, team, str(deviation.get("age_group") or ""), scope))
        if acceptance is not None:
            finding["acceptance_id"] = acceptance.get("id")
            if avoidability == "operator_accepted":
                finding["accepted"] = True
                finding["acceptance"] = dict(acceptance)
            else:
                # The persisted acceptance exists but no longer explains the
                # current deviation (target changed or deviation got worse), so
                # it is surfaced as stale rather than silently masking a finding.
                finding["accepted"] = False
                finding["acceptance_stale"] = True
                finding["acceptance"] = dict(acceptance)
        out.append(finding)
    return out


def _manual_findings(
    plan: Mapping[str, Any], verification: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for key, category in (
        ("manual_participation_placements", "participation"),
        ("manual_calendar_placements", "calendar"),
        ("manual_external_conflict_placements", "external_conflict"),
    ):
        for placement in verification.get(key) or []:
            tournament_id = str(placement.get("tournament_id") or "")
            if not tournament_id or tournament_id in seen:
                continue
            seen.add(tournament_id)
            out.append(
                {
                    "finding_id": f"manual_placement:{tournament_id}",
                    "code": "manual_placement",
                    "category": MANUAL_PLACEMENT,
                    "severity": "unresolved",
                    "age_group": placement.get("age_group"),
                    "tournament_id": tournament_id,
                    "host_club": placement.get("host_club"),
                    "manual_placement_kind": category,
                    "message": f"{tournament_id} requires manual placement ({category})",
                }
            )
    # An unresolved placement the planner marked on the tournament itself is a
    # plan-level fact verification cannot always rediscover: a manually chosen
    # slot may be calendar-free while still being an unconfirmed booking. Own
    # the same stable finding id as ``host_placement_repair`` so its repair
    # options are reachable without reconstructing the marker in a caller.
    from .host_placement_repair import is_manual_slot_failure

    for tournament in plan.get("tournaments") or []:
        tournament_id = str(tournament.get("id") or "")
        if not tournament_id or tournament_id in seen:
            continue
        if not is_manual_slot_failure(tournament):
            continue
        seen.add(tournament_id)
        out.append(
            {
                "finding_id": f"manual_placement:{tournament_id}",
                "code": "manual_placement",
                "category": MANUAL_PLACEMENT,
                "severity": "unresolved",
                "age_group": tournament.get("age_group"),
                "tournament_id": tournament_id,
                "host_club": tournament.get("host_club"),
                "manual_placement_kind": "calendar",
                "message": (
                    f"{tournament_id} is scheduled but still marked as an unresolved "
                    f"manual placement (calendar)"
                ),
            }
        )
    return out


def _unplaced_findings(
    plan: Mapping[str, Any], problem: Optional[Mapping[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Findings for genuine unplaced obligations (no tournament exists).

    A tournament the deterministic slot search could not place is not a
    scheduled tournament with a marker; it only exists as structured planning
    work in ``unresolved_tournament_placements``. Own the stable finding id the
    planner assigned it (``placement_findings``), never a synthetic tournament
    id, so the obligation stays visible/actionable without a fake placement.
    """
    out: List[Dict[str, Any]] = []
    for entry in plan.get("unresolved_tournament_placements") or []:
        if not isinstance(entry, Mapping):
            continue
        finding_id = str(entry.get("id") or "")
        if not finding_id:
            # Older/hand-built records without a canonical id: derive the same
            # stable identity shape from the finding's own scope rather than
            # hiding the obligation.
            finding_id = (
                f"unplaced_placement:{entry.get('age_group') or '?'}:"
                f"{entry.get('date') or '?'}"
            )
        from .unplaced_placement_repair import obligation_search_coverage

        coverage = obligation_search_coverage(plan, problem or {}, entry, allow_search=False)
        out.append(
            {
                "finding_id": finding_id,
                "code": "unplaced_tournament_placement",
                "category": MANUAL_PLACEMENT,
                "severity": "unresolved",
                "age_group": entry.get("age_group"),
                "date": entry.get("date"),
                "host_club": entry.get("responsible_host"),
                "responsible_host": entry.get("responsible_host"),
                "reason": entry.get("reason"),
                "search_attempted": bool(entry.get("search_attempted")),
                "bounded_repair_exhausted": bool(entry.get("bounded_repair_exhausted")),
                # The planner's own ``bounded_repair_exhausted`` flag describes
                # only the search it actually ran. The supported ladder is
                # broader, so report what this capability can still attempt
                # instead of letting that flag read as global infeasibility.
                "search_coverage": coverage,
                "message": (
                    f"{entry.get('age_group') or '?'} on {entry.get('date') or '?'} could not be "
                    f"placed automatically; responsible host {entry.get('responsible_host') or 'unknown'} "
                    "keeps the hosting obligation"
                ),
            }
        )
    return out


def _movable_capacity_findings(
    problem: Mapping[str, Any], plan: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """Expose host-controlled ice that is available to a blocked same-host placement.

    The finding is a fact (the responsible host controls movable ice on a
    candidate date), not a committed placement: it always carries
    ``requires_host_confirmation`` and the selected repair option still passes
    full verification. It is deliberately separate from the owning
    ``manual_placement`` finding so a caller can select the ice opportunity
    directly instead of reconstructing it from provider options.
    """
    from .movable_capacity_repair import movable_capacity_opportunities

    out: List[Dict[str, Any]] = []
    for opportunity in movable_capacity_opportunities(plan, problem):
        dates = opportunity.get("movable_dates") or []
        first = dates[0] if dates else {}
        out.append(
            {
                "finding_id": opportunity["finding_id"],
                "code": "movable_capacity_opportunity",
                "category": MOVABLE_CAPACITY,
                "severity": "opportunity",
                "age_group": opportunity.get("age_group"),
                "tournament_id": opportunity.get("tournament_id"),
                "host_club": opportunity.get("host_club"),
                "original_date": opportunity.get("original_date"),
                "movable_date_count": len(dates),
                "earliest_movable_date": first.get("date"),
                "requires_host_confirmation": True,
                "message": (
                    f"{opportunity['tournament_id']} can use host-controlled ice for "
                    f"{opportunity.get('host_club')} on {first.get('date')} "
                    f"(requires host confirmation)"
                ),
            }
        )
    return out


def _shape_findings(verification: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for index, shape in enumerate(verification.get("input_constrained_shapes") or [], start=1):
        age_group = str(shape.get("age_group") or "")
        out.append(
            {
                "finding_id": f"input_constrained_shape:{age_group}:{index}",
                "code": "input_constrained_shape",
                "category": ROSTER_SHAPE,
                "severity": "strong_goal",
                "age_group": age_group or None,
                "message": shape.get("message") or f"input-constrained tournament shape for {age_group}",
                "facts": dict(shape),
            }
        )
    return out


def _require_finding(findings: List[Dict[str, Any]], finding_id: str) -> Dict[str, Any]:
    finding = next((entry for entry in findings if entry["finding_id"] == finding_id), None)
    if finding is None:
        raise SeasonMaintenanceError(f"Unknown or stale finding id: {finding_id}")
    return finding


def _findings_for_option(findings: List[Dict[str, Any]], option_id: str) -> List[Dict[str, Any]]:
    # Preference-ordered scan: hosting/participation option ids encode their
    # finding scope, and hard-finding providers are cheap. A search-origin
    # option should normally be applied with an explicit finding id.
    selected = [
        finding
        for finding in findings
        if finding["category"]
        in (HOSTING, PARTICIPATION, HARD_VIOLATION, MANUAL_PLACEMENT, MOVABLE_CAPACITY)
    ]
    return selected or findings


def _infer_finding_id(
    option_id: str, findings: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Recover the owning finding from an option id when the caller omitted it.

    Hosting and participation option ids embed their stable finding id as a
    substring, so an apply can resolve the exact scope without re-running a
    bounded search for every finding in the season.
    """
    matches = [
        finding
        for finding in findings
        if (
            finding["category"] in (HOSTING, PARTICIPATION, MOVABLE_CAPACITY)
            or finding.get("code") == "unplaced_tournament_placement"
        )
        and finding["finding_id"] in option_id
    ]
    if not matches:
        return None
    return max(matches, key=lambda finding: len(finding["finding_id"]))


# ---------------------------------------------------------------------------
# Option enumeration / dispatch
# ---------------------------------------------------------------------------


def _options_for_finding(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding: Mapping[str, Any],
    *,
    allow_search: bool,
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    if finding.get("code") == "unplaced_tournament_placement":
        return _unplaced_options(plan, problem, finding, allow_search=allow_search)
    category = finding["category"]
    if category == HOSTING:
        options, rejected, families = _hosting_options(
            plan, problem, finding, allow_search=allow_search, dimensions=dimensions
        )
    elif category == PARTICIPATION:
        options, rejected, families = _participation_options(
            plan, problem, finding, allow_search=allow_search, dimensions=dimensions
        )
    elif category == MOVABLE_CAPACITY:
        options, rejected, families = _movable_capacity_options(plan, problem, finding)
    else:
        options, rejected, families = _hard_options(
            plan, problem, finding, allow_search=allow_search, dimensions=dimensions
        )
    # Record the resolved coverage on the finding the caller receives, unless
    # the owning provider already produced its own authoritative coverage
    # (the unplaced provider does; that branch returned above).
    finding["search_coverage"] = derive_search_coverage(
        option_count=len(options),
        rejected_count=len(rejected),
        allow_search=allow_search,
        supported=supported_dimensions_for_finding(finding),
    )
    return options, rejected, families


def _hosting_options(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding: Mapping[str, Any],
    *,
    allow_search: bool,
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    from .hosting_balance_repair import enumerate_hosting_balance_repairs

    repair_set = enumerate_hosting_balance_repairs(
        plan,
        problem,
        allow_search=allow_search,
        scope={"age_group": finding.get("age_group"), "club": finding.get("club")},
        dimensions=dimensions,
    )
    return _collect(repair_set, finding["finding_id"], family="hosting_balance")


def _unplaced_options(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding: Mapping[str, Any],
    *,
    allow_search: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Materialization options for one genuine unplaced obligation.

    The provider owns the whole repair ladder (same-date start time, same-host
    date, roster reselection, capacity release, coupled cross-age exchange).
    Its per-obligation coverage is folded back onto the finding so the caller
    can tell ``option_available``/``search_incomplete``/
    ``bounded_search_exhausted`` apart.
    """
    from .unplaced_placement_repair import enumerate_unplaced_placement_repairs

    repair_set = enumerate_unplaced_placement_repairs(
        plan,
        problem,
        finding_ids=[finding["finding_id"]],
        allow_search=allow_search,
    )
    options, rejected, _families = _collect(
        repair_set, finding["finding_id"], family="unplaced_placement"
    )
    coverage = dict((repair_set.get("coverage") or {}).get(finding["finding_id"]) or {})
    if coverage:
        finding["search_coverage"] = coverage
    families = {
        "unplaced_placement": {
            "option_count": len(options),
            "rejected_count": len(rejected),
            "search_coverage": coverage,
        }
    }
    return options, rejected, families


def _movable_capacity_options(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Expose the movable-capacity provider's verified options for one finding.

    The provider does its own bounded date/slot/roster enumeration, so the
    caller's ``dimensions`` do not narrow it further: its whole result already
    is the bounded same-host search for this obligation.
    """
    from .movable_capacity_repair import enumerate_movable_capacity_repairs

    repair_set = enumerate_movable_capacity_repairs(plan, problem)
    return _collect(
        repair_set,
        finding["finding_id"],
        family="movable_capacity",
        tournament_id=finding.get("tournament_id"),
    )


def _participation_options(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding: Mapping[str, Any],
    *,
    allow_search: bool,
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    from .participation_deviation_repair import enumerate_participation_deviation_repairs

    repair_set = enumerate_participation_deviation_repairs(
        plan,
        problem,
        allow_search=allow_search,
        scope={
            "team": finding.get("team"),
            "club": finding.get("club"),
            "scope": finding.get("scope"),
        },
        dimensions=dimensions,
    )
    return _collect(repair_set, finding["finding_id"], family="participation_deviation")


def _hard_options(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding: Mapping[str, Any],
    *,
    allow_search: bool,
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    from .search_neighborhood_repair import enumerate_search_neighborhood_repairs

    repair_set = enumerate_local_repair_options(plan, problem)
    options, rejected, families = _collect(
        repair_set,
        finding["finding_id"],
        family=None,
        tournament_id=finding.get("tournament_id"),
        tournament_ids=finding.get("tournament_ids"),
    )
    if allow_search:
        search_set = enumerate_search_neighborhood_repairs(
            plan,
            problem,
            scope={
                "age_group": finding.get("age_group"),
                "tournament_id": finding.get("tournament_id"),
            },
            dimensions=dimensions,
        )
        search_options, search_rejected, _ = _collect(
            search_set, finding["finding_id"], family="search_neighborhood", apply_finding_filter=False
        )
        options.extend(search_options)
        rejected.extend(search_rejected)
        families["search_neighborhood"] = {
            "option_count": len(search_options),
            "rejected_count": len(search_rejected),
        }
    return options, rejected, families


def _collect(
    repair_set: Mapping[str, Any],
    finding_id: str,
    *,
    family: Optional[str],
    apply_finding_filter: bool = True,
    tournament_id: Optional[str] = None,
    tournament_ids: Optional[Iterable[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    scope_ids = {str(item) for item in (tournament_ids or []) if item}
    if tournament_id:
        scope_ids.add(str(tournament_id))
    options: List[Dict[str, Any]] = []
    for option in repair_set.get("options") or []:
        if apply_finding_filter and option.get("finding_id") != finding_id:
            # A repair for the same tournament but a different finding family
            # (for example a movable-capacity opportunity for a tournament the
            # placement provider reports as manual) is still a legal repair for
            # this tournament, so keep it rather than hiding it.
            if not (
                scope_ids and str(option.get("tournament_id") or "") in scope_ids
            ):
                continue
        payload = dict(option)
        payload["family"] = family or option.get("family") or ""
        options.append(payload)
    rejected = [
        dict(entry)
        for entry in repair_set.get("rejected_candidates") or []
        if not apply_finding_filter or entry.get("finding_id") == finding_id
    ]
    families = dict(repair_set.get("families") or {})
    if family:
        families[family] = {
            "option_count": len(options),
            "rejected_count": len(rejected),
        }
    return options, rejected, families


def _escalation(options: List[Dict[str, Any]], rejected: List[Dict[str, Any]], finding: Mapping[str, Any]) -> Dict[str, Any]:
    if options:
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


def _annotate_pareto(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    options: List[Dict[str, Any]],
    finding: Mapping[str, Any],
    dimensions: Iterable[str],
) -> Dict[str, Any]:
    """Measure every option on the same objective vector and mark the front.

    Each option is reproduced against the current plan through its own atomic
    apply path and re-verified, so the vector describes the candidate that
    option would actually commit -- not a claim derived from the provider's
    self-reported effects. An option that no longer reproduces is left off the
    front instead of being reported as a verified trade-off.
    """
    measured: List[Tuple[int, Dict[str, float]]] = []
    before_score = with_unresolved_obligations_count(score_candidate(dict(plan), problem=dict(problem)))
    for index, option in enumerate(options):
        applied = _apply_option(plan, problem, option, finding, dimensions)
        candidate = applied.get("candidate") if applied.get("ok") else None
        if not isinstance(candidate, Mapping):
            option["objectives"] = None
            option["non_dominated"] = False
            continue
        verification = applied.get("verification") or verify_candidate(
            dict(candidate), dict(problem)
        )
        score = score_candidate(dict(candidate), problem=dict(problem))
        travel = _travel_metrics(candidate)
        vector = _objective_vector(
            candidate, verification, plan, score=score, travel=travel
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
    }


def _objective_vector(
    candidate: Mapping[str, Any],
    verification: Mapping[str, Any],
    before_plan: Mapping[str, Any],
    *,
    score: Mapping[str, Any],
    travel: Mapping[str, Any],
) -> Dict[str, float]:
    """Extract the uniformly "lower is better" maintenance objective vector.

    Combines the verifier-derived defect/change-cost dimensions owned here, the
    shared Stage-3 soft-quality vector (``quality_objectives``) and travel, so a
    single uniform dominance check and one bounded Pareto front cover both the
    hard and the soft consequences of a repair.
    """
    vector: Dict[str, float] = {
        "hard_violations": float(_count(verification, "violations")),
        "unresolved_hosting_obligations": float(
            _count(verification, "unresolved_hosting_obligations")
        ),
        "hosting_balance_imbalances": float(
            _count(verification, "hosting_balance_imbalances")
        ),
        "manual_placements": float(_manual_count(verification)),
        "unresolved_placement_obligations": float(
            len(candidate.get("unresolved_tournament_placements") or [])
        ),
        "participation_deviations": float(_unresolved_participation_deviation_count(verification)),
        "avoidable_participation_deviations": float(
            _unresolved_avoidability_count(verification, "avoidable")
        ),
        "host_confirmation_dependencies": float(
            _count(verification, "movable_allocations_used")
        ),
        "changed_tournament_count": float(
            len(_changed_tournament_ids(before_plan, candidate))
        ),
    }
    vector.update(quality_objective_vector(dict(score)))
    for dimension in TRAVEL_OBJECTIVE_DIMENSIONS:
        vector[dimension] = float(travel.get(dimension, 0.0))
    return vector


def _travel_metrics(plan: Mapping[str, Any]) -> Dict[str, Any]:
    """Return canonical season travel totals for a plan dict.

    Delegates to the one travel implementation (``compute_team_travel_distances``)
    rather than re-summing arena distances here. A plan that cannot be decoded
    (for example a synthetic candidate missing model fields) reports zero travel
    with ``available: false`` instead of failing the repair surface.
    """
    try:
        from .club_distances import compute_team_travel_distances
        from .serialization.season_plan import season_plan_from_dict

        season_plan = season_plan_from_dict(dict(plan))
        team_travel = compute_team_travel_distances(season_plan)
    except Exception:
        return {
            "total_travel_km": 0.0,
            "max_team_travel_km": 0.0,
            "available": False,
        }
    values = list(team_travel.values())
    return {
        "total_travel_km": float(sum(values)),
        "max_team_travel_km": float(max(values) if values else 0),
        "available": True,
    }


# ---------------------------------------------------------------------------
# Metric deltas
# ---------------------------------------------------------------------------


def _metric_delta(
    before_plan: Mapping[str, Any],
    before_verification: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any] | None = None,
    after_verification: Mapping[str, Any] | None = None,
    problem: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    after_plan = candidate if candidate is not None else before_plan
    after = after_verification or before_verification
    delta = {
        "hard_violations": _count(after, "violations") - _count(before_verification, "violations"),
        "hard_violations_before": _count(before_verification, "violations"),
        "hard_violations_after": _count(after, "violations"),
        "unresolved_hosting_obligations_before": _count(before_verification, "unresolved_hosting_obligations"),
        "unresolved_hosting_obligations_after": _count(after, "unresolved_hosting_obligations"),
        "hosting_balance_imbalances_before": _count(before_verification, "hosting_balance_imbalances"),
        "hosting_balance_imbalances_after": _count(after, "hosting_balance_imbalances"),
        "manual_placements_before": _manual_count(before_verification),
        "manual_placements_after": _manual_count(after),
        "unresolved_placement_obligations_before": len(
            before_plan.get("unresolved_tournament_placements") or []
        ),
        "unresolved_placement_obligations_after": len(
            after_plan.get("unresolved_tournament_placements") or []
        ),
        "participation_deviations_before": _count(before_verification, "participation_deviations"),
        "participation_deviations_after": _count(after, "participation_deviations"),
        "bounded_search_exhausted_before": _avoidability_count(before_verification, "bounded_search_exhausted"),
        "bounded_search_exhausted_after": _avoidability_count(after, "bounded_search_exhausted"),
        "avoidable_before": _avoidability_count(before_verification, "avoidable"),
        "avoidable_after": _avoidability_count(after, "avoidable"),
        "changed_tournament_count": len(_changed_tournament_ids(before_plan, after_plan)),
    }
    delta.update(
        _quality_delta(
            before_plan, after_plan, problem=dict(problem) if problem is not None else None
        )
    )
    return delta


def _quality_delta(
    before_plan: Mapping[str, Any],
    after_plan: Mapping[str, Any],
    *,
    problem: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    """Shared soft-quality + travel delta for one before/after plan pair.

    Uses the same Stage-3 quality comparison (``compare_quality_scores``) the
    promotion gate uses, so a maintenance action returns the exact
    improvement/regression evidence an operator would see from Stage 3 -- not a
    second, maintenance-only quality rule.
    """
    before_score = with_unresolved_obligations_count(
        score_candidate(dict(before_plan), problem=problem)
    )
    after_score = with_unresolved_obligations_count(
        score_candidate(dict(after_plan), problem=problem)
    )
    comparison = compare_quality_scores(before_score, after_score)
    before_travel = _travel_metrics(before_plan)
    after_travel = _travel_metrics(after_plan)
    return {
        "quality_metrics": comparison["metrics"],
        "quality_regressions": comparison["regressions"],
        "total_travel_km_before": before_travel["total_travel_km"],
        "total_travel_km_after": after_travel["total_travel_km"],
        "total_travel_km_delta": after_travel["total_travel_km"] - before_travel["total_travel_km"],
        "max_team_travel_km_before": before_travel["max_team_travel_km"],
        "max_team_travel_km_after": after_travel["max_team_travel_km"],
        "max_team_travel_km_delta": after_travel["max_team_travel_km"]
        - before_travel["max_team_travel_km"],
    }


def _rejected_delta(
    season: str,
    revision: str,
    reason: str,
    *,
    delta: Mapping[str, Any] | None = None,
    **extra: Any,
) -> Dict[str, Any]:
    return {
        "season": season,
        "ok": False,
        "reason": reason,
        "revision_before": revision,
        "revision_after": revision,
        "canonical_revision_unchanged": True,
        "delta": dict(delta or {}),
        **extra,
    }


def _count(verification: Mapping[str, Any], key: str) -> int:
    value = verification.get(key)
    return len(value) if isinstance(value, (list, tuple)) else 0


def _manual_count(verification: Mapping[str, Any]) -> int:
    # Participation entries flagged as pure intra-club distribution are not
    # unresolved manual work, so they must not inflate the manual-placement
    # defect dimension.
    participation = [
        item
        for item in (verification.get("manual_participation_placements") or [])
        if isinstance(item, Mapping) and item.get("counts_as_unresolved_shortfall", True)
    ]
    return len(participation) + sum(
        _count(verification, key)
        for key in (
            "manual_calendar_placements",
            "manual_external_conflict_placements",
        )
    )


def _avoidability_count(verification: Mapping[str, Any], avoidability: str) -> int:
    return sum(
        1
        for deviation in verification.get("participation_deviations") or []
        if str(deviation.get("avoidability") or "") == avoidability
    )


def _deviation_is_unresolved(deviation: Mapping[str, Any]) -> bool:
    """False for a pure intra-club label imbalance.

    An aggregate-complete pool split 5+3 is not an unresolved participation
    deficit, so it must not drive repair search or count as an equal-weight
    objective dimension.
    """
    return str(deviation.get("club_pool_classification") or "") != INTRA_CLUB_DISTRIBUTION


def _unresolved_participation_deviation_count(verification: Mapping[str, Any]) -> int:
    return sum(
        1
        for deviation in verification.get("participation_deviations") or []
        if _deviation_is_unresolved(deviation)
    )


def _unresolved_avoidability_count(verification: Mapping[str, Any], avoidability: str) -> int:
    return sum(
        1
        for deviation in verification.get("participation_deviations") or []
        if str(deviation.get("avoidability") or "") == avoidability
        and _deviation_is_unresolved(deviation)
    )


def _changed_tournament_ids(before: Mapping[str, Any], after: Mapping[str, Any]) -> List[str]:
    before_by_id = {str(t.get("id")): _signature(t) for t in before.get("tournaments", []) or []}
    after_by_id = {str(t.get("id")): _signature(t) for t in after.get("tournaments", []) or []}
    return [
        tournament_id
        for tournament_id in sorted(set(before_by_id) | set(after_by_id))
        if before_by_id.get(tournament_id) != after_by_id.get(tournament_id)
    ]


def _signature(tournament: Mapping[str, Any]) -> Any:
    teams = tuple(
        sorted(
            (str(team.get("club") or ""), str(team.get("label") or ""), str(team.get("age_group") or ""))
            for team in tournament.get("teams", []) or []
        )
    )
    return (
        tournament.get("date"),
        tournament.get("host_club"),
        tournament.get("arena"),
        tournament.get("start_time"),
        teams,
    )


def _plan_fingerprint(plan: Mapping[str, Any]) -> str:
    from .host_team_missing_repair import candidate_fingerprint

    return candidate_fingerprint(plan)


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
