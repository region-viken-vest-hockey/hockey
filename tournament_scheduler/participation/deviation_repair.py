"""Bounded repair options for participation strong-goal deviations.

``participation_targets`` / ``planning.contract.verify_candidate`` own the
strong-goal model: a per-team/scope target is a goal with bounded, evidenced
relaxation, and every material deviation carries an ``avoidability``
classification (``avoidable``, ``proven_infeasible``, ``bounded_search_exhausted``,
``operator_accepted``). This provider turns one selected deviation into a
bounded, verified participant search over its age group and exposes only
candidates that keep full verification valid *and* reduce the selected
deviation.

The combinatorial search is not implemented here: it is served by the generic
CP-SAT maintenance path (``stage3.maintenance_problem`` /
``stage3.cpsat_maintenance``, ADR 0008). This module only (1) projects the
selected deviation into a participants-only ``MaintenanceProblem``, (2)
independently re-verifies the returned patch, and (3) classifies the outcome.
Placement (host/date/arena/start time) is never moved by this family.

It deliberately preserves the classification semantics: a
``bounded_search_exhausted`` result is reported as a bounded-search outcome, not
as proof of infeasibility, and an ``avoidable`` deviation is never made worse.
The provider is planner-neutral (plain ``problem``/``candidate`` dicts) and
never decides on its own.
"""

from __future__ import annotations

from tournament_scheduler.repair.capability import COVERAGE_BOUNDED_EXHAUSTED, COVERAGE_PROVEN_INFEASIBLE
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from tournament_scheduler.repair.host_team_missing import RepairOption, candidate_fingerprint, search_dimension_tag
from tournament_scheduler.planning.contract import verify_candidate
from tournament_scheduler.repair.neighborhood_diff import changed_tournaments
from tournament_scheduler.stage3.cpsat_maintenance import solve_maintenance_problem
from tournament_scheduler.stage3.maintenance_problem import (
    DEFAULT_SEARCH_BUDGET_SECONDS,
    DIMENSION_PARTICIPANTS,
    MaintenanceSolveResult,
    build_participant_maintenance_problem,
)

PARTICIPATION_FINDING_PREFIX = "participation_deviation"
# This family only ever re-staffs rosters, whatever dimensions a caller asks for.
_SEARCH_DIMENSIONS: Tuple[str, ...] = (DIMENSION_PARTICIPANTS,)


def participation_finding_id(club: str, team: str, age_group: str, scope: str) -> str:
    return f"{PARTICIPATION_FINDING_PREFIX}:{club}:{team}:{age_group}:{scope}"


def legacy_participation_finding_id(club: str, team: str, scope: str) -> str:
    return f"{PARTICIPATION_FINDING_PREFIX}:{club}:{team}:{scope}"


def participation_deviations(candidate: Mapping[str, Any], problem: Mapping[str, Any]) -> List[Dict[str, Any]]:
    verification = verify_candidate(dict(candidate), dict(problem))
    return list(verification.get("participation_deviations") or [])


def _matches(deviation: Mapping[str, Any], scope: Mapping[str, Any]) -> bool:
    for key in ("team", "club", "age_group", "scope"):
        expected = scope.get(key)
        if expected and str(deviation.get(key) or "") != str(expected):
            return False
    return True


def enumerate_participation_deviation_repairs(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str = "",
    allow_search: bool = False,
    scope: Mapping[str, Any] | None = None,
    search_budget_seconds: float = DEFAULT_SEARCH_BUDGET_SECONDS,
    seed: int = 0,
    dimensions: Iterable[str] = ("participants",),
) -> Dict[str, Any]:
    """Enumerate verified participant-only repairs for the selected deviations.

    *dimensions* is accepted for caller compatibility and recorded, but this
    family only ever searches ``participants``: a request for host/date/slot
    movement is reported on each option as ``unserved_dimensions`` instead of
    silently widening a promoted season's placement.
    """
    verification = verify_candidate(dict(candidate), dict(problem))
    fingerprint = candidate_fingerprint(candidate)
    deviations = list(verification.get("participation_deviations") or [])
    resolved_scope = scope or {}
    targets = [deviation for deviation in deviations if _matches(deviation, resolved_scope)]
    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    results: Dict[str, Any] = {}
    applicable_findings = [_finding_id(deviation) for deviation in targets]
    if not targets:
        return _result(
            run_id, fingerprint, verification, options, rejected, results, applicable_findings, applicable=False
        )
    if not allow_search:
        for deviation in targets:
            rejected.append(
                {
                    "finding_id": _finding_id(deviation),
                    "reason": "targeted_search_not_requested",
                    **_classification(deviation),
                }
            )
        return _result(
            run_id, fingerprint, verification, options, rejected, results, applicable_findings, applicable=True
        )

    requested = {str(dim) for dim in dimensions}
    unserved = sorted(requested - set(_SEARCH_DIMENSIONS))
    before_index = _deviation_index(verification)
    solves: Dict[str, MaintenanceSolveResult] = {}
    for deviation in targets:
        finding_id = _finding_id(deviation)
        age_group = str(deviation.get("age_group") or "")
        key = _deviation_key(deviation)
        if age_group not in solves:
            maintenance = build_participant_maintenance_problem(
                candidate,
                problem,
                age_groups=[age_group],
                search_budget_seconds=search_budget_seconds,
                seed=seed,
            )
            solves[age_group] = solve_maintenance_problem(maintenance, candidate, problem)
        solved = solves[age_group]
        if not solved.has_candidate or solved.candidate is None:
            rejected.append(
                {
                    "finding_id": finding_id,
                    "reason": f"maintenance_search_{solved.status}",
                    "maintenance_status": solved.status,
                    "solver_status": solved.diagnostics.get("solver_status"),
                    **_classification(deviation),
                }
            )
            continue
        outcome = solved.candidate
        outcome_verification = verify_candidate(dict(outcome), dict(problem))
        if not outcome_verification.get("ok"):
            rejected.append(
                {
                    "finding_id": finding_id,
                    "reason": "search_candidate_not_hard_valid",
                    "violation_codes": sorted({str(v.get("code")) for v in outcome_verification.get("violations", [])}),
                }
            )
            continue
        after_index = _deviation_index(outcome_verification)
        if not _improves(before_index.get(key), after_index.get(key)):
            rejected.append(
                {"finding_id": finding_id, "reason": "no_deviation_improvement", **_classification(deviation)}
            )
            continue
        changed = changed_tournaments(
            candidate, outcome, [str(t.get("id")) for t in candidate.get("tournaments", []) or []]
        )
        # Bind both the exact maintenance request and its produced candidate.
        # Apply re-runs the bounded solve, but a wall-clock-bound solver is not
        # guaranteed to reproduce the same incumbent: a different result must
        # therefore get a different id and be rejected as stale.
        result_fingerprint = str(solved.candidate_fingerprint)
        option_id = (
            f"{fingerprint[:12]}:{PARTICIPATION_FINDING_PREFIX}:"
            f"{deviation.get('club')}:{deviation.get('team')}:{deviation.get('age_group')}:{deviation.get('scope')}:"
            f"search:{int(seed)}:{search_dimension_tag(_SEARCH_DIMENSIONS)}:"
            f"{solved.problem_fingerprint}:{result_fingerprint}"
        )
        options.append(
            RepairOption(
                option_id=option_id,
                finding_id=finding_id,
                action="search",
                tournament_id="",
                arguments={
                    "engine": "cp_sat",
                    "seed": int(seed),
                    "search_budget_seconds": float(search_budget_seconds),
                    "dimensions": list(_SEARCH_DIMENSIONS),
                    "unserved_dimensions": unserved,
                    "maintenance_problem_fingerprint": solved.problem_fingerprint,
                    "baseline_fingerprint": solved.baseline_fingerprint,
                    "patched_tournament_ids": [str(patch["tournament_id"]) for patch in solved.patches],
                },
                hard_feasible=True,
                effects=_effects(before_index.get(key), after_index.get(key), changed, deviation),
                evidence={
                    "verification_ok": True,
                    "avoidability": deviation.get("avoidability"),
                    "result_fingerprint": result_fingerprint,
                    "solver_status": solved.diagnostics.get("solver_status"),
                },
            )
        )
        results[option_id] = outcome
    return _result(run_id, fingerprint, verification, options, rejected, results, applicable_findings, applicable=True)


def _finding_id(deviation: Mapping[str, Any]) -> str:
    return participation_finding_id(
        str(deviation.get("club") or ""),
        str(deviation.get("team") or ""),
        str(deviation.get("age_group") or ""),
        str(deviation.get("scope") or ""),
    )


def apply_participation_deviation_repair_option(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    option_id: str,
    expected_fingerprint: str,
    run_id: str = "",
    scope: Mapping[str, Any] | None = None,
    dimensions: Iterable[str] = ("participants",),
    search_budget_seconds: float | None = None,
    seed: int | None = None,
) -> Dict[str, Any]:
    before = candidate_fingerprint(candidate)
    if before != expected_fingerprint:
        return {
            "ok": False,
            "reason": "stale_candidate_fingerprint",
            "before_fingerprint": before,
            "expected_fingerprint": expected_fingerprint,
        }
    replay_budget = DEFAULT_SEARCH_BUDGET_SECONDS if search_budget_seconds is None else float(search_budget_seconds)
    replay_seed = 0 if seed is None else int(seed)
    repair_set = enumerate_participation_deviation_repairs(
        candidate,
        problem,
        run_id=run_id,
        allow_search=True,
        scope=scope,
        dimensions=dimensions,
        search_budget_seconds=replay_budget,
        seed=replay_seed,
    )
    option = next((entry for entry in repair_set["options"] if entry["option_id"] == option_id), None)
    if option is None:
        return {"ok": False, "reason": "unknown_or_stale_option", "before_fingerprint": before}
    result = repair_set["result_candidates"].get(option_id)
    if result is None:
        return {"ok": False, "reason": "search_result_unavailable", "before_fingerprint": before}
    verification = verify_candidate(dict(result), dict(problem))
    if not verification.get("ok"):
        return {
            "ok": False,
            "reason": "verification_failed",
            "before_fingerprint": before,
            "verification": verification,
        }
    return {
        "ok": True,
        "candidate": result,
        "option_id": option_id,
        "before_fingerprint": before,
        "after_fingerprint": candidate_fingerprint(result),
        "verification": verification,
        "effects": option.get("effects", {}),
    }


def _classification(deviation: Mapping[str, Any]) -> Dict[str, Any]:
    """Carry the deviation's own classification into a rejection entry.

    A bounded search that finds no improvement is a statement about the search
    budget, never proof of infeasibility: only a deviation the target model
    already classified ``proven_infeasible`` reports ``proven_infeasible`` here.
    """
    avoidability = str(deviation.get("avoidability") or "")
    payload: Dict[str, Any] = {
        "avoidability": avoidability,
        "proven_infeasible": avoidability == COVERAGE_PROVEN_INFEASIBLE,
    }
    if avoidability == COVERAGE_BOUNDED_EXHAUSTED:
        payload["note"] = (
            "bounded_search_exhausted is not proof of infeasibility; another targeted search may be requested"
        )
    return payload


def _deviation_key(deviation: Mapping[str, Any]) -> Tuple[str, str, str]:
    return (
        str(deviation.get("team") or ""),
        str(deviation.get("scope") or ""),
        str(deviation.get("club") or ""),
    )


def _deviation_index(verification: Mapping[str, Any]) -> Dict[Tuple[str, str, str], Mapping[str, Any]]:
    return {_deviation_key(deviation): deviation for deviation in verification.get("participation_deviations") or []}


def _improves(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> bool:
    if before is None:
        return False
    if after is None:
        # The verifier no longer reports the deviation: it was resolved.
        return True
    return abs(int(after.get("deviation", 0))) < abs(int(before.get("deviation", 0)))


def _effects(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    changed: Iterable[str],
    deviation: Mapping[str, Any],
) -> Dict[str, Any]:
    changed_ids = sorted({str(item) for item in changed})
    return {
        "deviation_before": int((before or {}).get("deviation", 0)),
        "deviation_after": int((after or {}).get("deviation", 0)),
        "avoidability": deviation.get("avoidability"),
        "changed_tournament_ids": changed_ids,
        "changed_tournament_count": len(changed_ids),
    }


def _result(
    run_id: str,
    fingerprint: str,
    verification: Mapping[str, Any],
    options: List[RepairOption],
    rejected: List[Dict[str, Any]],
    result_candidates: Dict[str, Any],
    applicable_findings: List[str],
    *,
    applicable: bool,
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "candidate_fingerprint": fingerprint,
        "verification": dict(verification),
        "applicable": applicable,
        "applicable_findings": applicable_findings,
        "options": [option.to_dict() for option in options],
        "rejected_candidates": rejected,
        "result_candidates": result_candidates,
    }
