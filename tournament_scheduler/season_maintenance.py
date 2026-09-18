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
from .participation_deviation_repair import participation_finding_id
from .participation_targets import search_evidence_from_acceptances
from .planning_contract import verify_candidate
from .season_state import (
    DEFAULT_SEASON_ROOT,
    load_decisions,
    load_participation_acceptances,
    load_schedule,
    record_participation_acceptance,
    revoke_participation_acceptance,
)

SEASON_MAINTENANCE_SCHEMA_VERSION = 1

DEFAULT_DIMENSIONS: Tuple[str, ...] = ("participants", "host")

# Categories are facts about what kind of finding this is, not a mandatory
# processing order: the harness may select any finding independently.
HARD_VIOLATION = "hard_violation"
HOSTING = "hosting"
PARTICIPATION = "participation"
MANUAL_PLACEMENT = "manual_placement"
ROSTER_SHAPE = "roster_shape"


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
    schedule, _decisions, plan, problem = load_context(season, root=root)
    verification = verify_candidate(plan, problem)
    revision = str(schedule.get("revision") or schedule.get("fingerprint") or "")
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
    schedule, _decisions, plan, problem = load_context(season, root=root)
    revision = str(schedule.get("revision") or schedule.get("fingerprint") or "")
    findings = _findings(plan, problem, verify_candidate(plan, problem))
    finding = _require_finding(findings, finding_id)
    options, rejected, families = _options_for_finding(
        plan, problem, finding, allow_search=allow_search, dimensions=DEFAULT_DIMENSIONS
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
    schedule, _decisions, plan, problem = load_context(season, root=root)
    revision = str(schedule.get("revision") or schedule.get("fingerprint") or "")
    findings = _findings(plan, problem, verify_candidate(plan, problem))
    finding = _require_finding(findings, finding_id)
    options, rejected, families = _options_for_finding(
        plan, problem, finding, allow_search=True, dimensions=resolved_dimensions
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
    schedule, decisions, plan, problem = load_context(season, root=root)
    revision = str(schedule.get("revision") or schedule.get("fingerprint") or "")
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
    preview = _metric_delta(plan, before_verification, candidate=result_candidate, after_verification=verification)
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

    updated_schedule, _updated_decisions, cost = apply_candidate(
        season=season,
        candidate=result_candidate,
        root=root,
        problem=problem,
        actor=actor,
    )
    new_revision = str(updated_schedule.get("revision") or updated_schedule.get("fingerprint") or "")
    fresh_verification = verify_candidate(dict(updated_schedule.get("plan") or {}), problem)
    after_plan = dict(updated_schedule.get("plan") or {})
    delta = _metric_delta(plan, before_verification, candidate=after_plan, after_verification=fresh_verification)
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
    schedule, _decisions, plan, problem = load_context(season, root=root)
    revision = str(schedule.get("revision") or schedule.get("fingerprint") or "")
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
    schedule, _decisions, plan, problem = load_context(season, root=root)
    revision = str(schedule.get("revision") or schedule.get("fingerprint") or "")
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
    findings.extend(_manual_findings(verification))
    findings.extend(_shape_findings(verification))
    findings.sort(key=lambda entry: (entry["category"], entry["finding_id"]))
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


def _manual_findings(verification: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for key, category in (
        ("manual_participation_placements", "participation"),
        ("manual_calendar_placements", "calendar"),
        ("manual_external_conflict_placements", "external_conflict"),
    ):
        for placement in verification.get(key) or []:
            tournament_id = str(placement.get("tournament_id") or "")
            if not tournament_id:
                continue
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
        if finding["category"] in (HOSTING, PARTICIPATION, HARD_VIOLATION)
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
        if finding["category"] in (HOSTING, PARTICIPATION)
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
    category = finding["category"]
    if category == HOSTING:
        return _hosting_options(plan, problem, finding, allow_search=allow_search, dimensions=dimensions)
    if category == PARTICIPATION:
        return _participation_options(plan, problem, finding, allow_search=allow_search, dimensions=dimensions)
    return _hard_options(plan, problem, finding, allow_search=allow_search, dimensions=dimensions)


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
    return {"needed": True, "reason": "no_cheap_local_option"}


# ---------------------------------------------------------------------------
# Metric deltas
# ---------------------------------------------------------------------------


def _metric_delta(
    before_plan: Mapping[str, Any],
    before_verification: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any] | None = None,
    after_verification: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    after_plan = candidate if candidate is not None else before_plan
    after = after_verification or before_verification
    return {
        "hard_violations": _count(after, "violations") - _count(before_verification, "violations"),
        "hard_violations_before": _count(before_verification, "violations"),
        "hard_violations_after": _count(after, "violations"),
        "unresolved_hosting_obligations_before": _count(before_verification, "unresolved_hosting_obligations"),
        "unresolved_hosting_obligations_after": _count(after, "unresolved_hosting_obligations"),
        "hosting_balance_imbalances_before": _count(before_verification, "hosting_balance_imbalances"),
        "hosting_balance_imbalances_after": _count(after, "hosting_balance_imbalances"),
        "manual_placements_before": _manual_count(before_verification),
        "manual_placements_after": _manual_count(after),
        "participation_deviations_before": _count(before_verification, "participation_deviations"),
        "participation_deviations_after": _count(after, "participation_deviations"),
        "bounded_search_exhausted_before": _avoidability_count(before_verification, "bounded_search_exhausted"),
        "bounded_search_exhausted_after": _avoidability_count(after, "bounded_search_exhausted"),
        "avoidable_before": _avoidability_count(before_verification, "avoidable"),
        "avoidable_after": _avoidability_count(after, "avoidable"),
        "changed_tournament_count": len(_changed_tournament_ids(before_plan, after_plan)),
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
    return sum(
        _count(verification, key)
        for key in (
            "manual_participation_placements",
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
    from .local_repair_options import apply_local_repair_option

    return apply_local_repair_option(
        plan, problem, option_id=option_id, expected_fingerprint=fingerprint
    )
