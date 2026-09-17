"""Bounded repair options for participation strong-goal deviations.

``participation_targets`` / ``planning_contract.verify_candidate`` own the
strong-goal model: a per-team/scope target is a goal with bounded, evidenced
relaxation, and every material deviation carries an ``avoidability``
classification (``avoidable``, ``proven_infeasible``, ``bounded_search_exhausted``,
``operator_accepted``). This provider turns one selected deviation into a
bounded, verified search over its age group -- participant swaps plus any
requested host/date/slot dimensions, with every unrelated tournament frozen --
and exposes only candidates that keep full verification valid *and* reduce the
selected deviation.

It deliberately preserves the classification semantics: a
``bounded_search_exhausted`` result is reported as a bounded-search outcome, not
as proof of infeasibility, and an ``avoidable`` deviation is never made worse.
The provider is planner-neutral (plain ``problem``/``candidate`` dicts) and
never decides on its own.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .host_team_missing_repair import RepairOption, candidate_fingerprint
from .planning_contract import verify_candidate
from .stage3_optimizer import optimize_candidate

PARTICIPATION_FINDING_PREFIX = "participation_deviation"
_DEFAULT_ITERATIONS = 800
_DEFAULT_SEEDS: Tuple[int, ...] = (0, 1)
_MAX_OPTIONS = 3


def participation_finding_id(club: str, team: str, scope: str) -> str:
    return f"{PARTICIPATION_FINDING_PREFIX}:{club}:{team}:{scope}"


def participation_deviations(
    candidate: Mapping[str, Any], problem: Mapping[str, Any]
) -> List[Dict[str, Any]]:
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
    iterations: int = _DEFAULT_ITERATIONS,
    seeds: Sequence[int] = _DEFAULT_SEEDS,
    dimensions: Iterable[str] = ("participants",),
) -> Dict[str, Any]:
    verification = verify_candidate(dict(candidate), dict(problem))
    fingerprint = candidate_fingerprint(candidate)
    deviations = list(verification.get("participation_deviations") or [])
    resolved_scope = scope or {}
    targets = [deviation for deviation in deviations if _matches(deviation, resolved_scope)]
    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    results: Dict[str, Any] = {}
    applicable_findings = [
        participation_finding_id(
            str(deviation.get("club") or ""),
            str(deviation.get("team") or ""),
            str(deviation.get("scope") or ""),
        )
        for deviation in targets
    ]
    if not targets:
        return _result(run_id, fingerprint, verification, options, rejected, results, applicable_findings, applicable=False)
    if not allow_search:
        for deviation in targets:
            rejected.append(
                {
                    "finding_id": participation_finding_id(
                        str(deviation.get("club") or ""),
                        str(deviation.get("team") or ""),
                        str(deviation.get("scope") or ""),
                    ),
                    "reason": "targeted_search_not_requested",
                    **_classification(deviation),
                }
            )
        return _result(run_id, fingerprint, verification, options, rejected, results, applicable_findings, applicable=True)

    dims = {str(dim) for dim in dimensions}
    age_groups = sorted({str(deviation.get("age_group") or "") for deviation in targets if deviation.get("age_group")})
    frozen = [
        str(tournament.get("id"))
        for tournament in candidate.get("tournaments", []) or []
        if str(tournament.get("age_group") or "") not in age_groups
    ]
    before_index = _deviation_index(verification)
    for deviation in targets:
        finding_id = participation_finding_id(
            str(deviation.get("club") or ""),
            str(deviation.get("team") or ""),
            str(deviation.get("scope") or ""),
        )
        key = _deviation_key(deviation)
        seen: set[str] = set()
        for seed in seeds:
            outcome = optimize_candidate(
                dict(candidate),
                dict(problem),
                iterations=max(1, int(iterations)),
                seed=int(seed),
                move_hosts="host" in dims,
                move_dates="date" in dims,
                move_slots="slot" in dims,
                frozen_tournament_ids=frozen,
            )
            outcome_verification = verify_candidate(dict(outcome), dict(problem))
            if not outcome_verification.get("ok"):
                rejected.append(
                    {
                        "finding_id": finding_id,
                        "reason": "search_candidate_not_hard_valid",
                        "seed": int(seed),
                        "violation_codes": sorted(
                            {str(v.get("code")) for v in outcome_verification.get("violations", [])}
                        ),
                    }
                )
                continue
            after_index = _deviation_index(outcome_verification)
            if not _improves(before_index.get(key), after_index.get(key)):
                rejected.append(
                    {
                        "finding_id": finding_id,
                        "reason": "no_deviation_improvement",
                        "seed": int(seed),
                        **_classification(deviation),
                    }
                )
                continue
            after_fp = candidate_fingerprint(outcome)
            if after_fp in seen:
                rejected.append({"finding_id": finding_id, "reason": "duplicate_candidate", "seed": int(seed)})
                continue
            seen.add(after_fp)
            changed = _changed_ids(candidate, outcome)
            option_id = (
                f"{fingerprint[:12]}:{PARTICIPATION_FINDING_PREFIX}:"
                f"{deviation.get('club')}:{deviation.get('team')}:{deviation.get('scope')}:"
                f"search:{int(seed)}:{after_fp[:10]}"
            )
            options.append(
                RepairOption(
                    option_id=option_id,
                    finding_id=finding_id,
                    action="search",
                    tournament_id="",
                    arguments={
                        "seed": int(seed),
                        "iterations": max(1, int(iterations)),
                        "dimensions": sorted(dims),
                        "frozen_tournament_ids": frozen,
                    },
                    hard_feasible=True,
                    effects=_effects(before_index.get(key), after_index.get(key), changed, deviation),
                    evidence={
                        "verification_ok": True,
                        "avoidability": deviation.get("avoidability"),
                        "result_fingerprint": after_fp,
                    },
                )
            )
            results[option_id] = outcome
            if len(options) >= _MAX_OPTIONS:
                break
        if not any(option.finding_id == finding_id for option in options):
            rejected.append(
                {
                    "finding_id": finding_id,
                    "reason": "bounded_search_found_no_improvement",
                    **_classification(deviation),
                }
            )
    return _result(run_id, fingerprint, verification, options, rejected, results, applicable_findings, applicable=True)


def apply_participation_deviation_repair_option(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    option_id: str,
    expected_fingerprint: str,
    run_id: str = "",
    scope: Mapping[str, Any] | None = None,
    dimensions: Iterable[str] = ("participants",),
) -> Dict[str, Any]:
    before = candidate_fingerprint(candidate)
    if before != expected_fingerprint:
        return {
            "ok": False,
            "reason": "stale_candidate_fingerprint",
            "before_fingerprint": before,
            "expected_fingerprint": expected_fingerprint,
        }
    repair_set = enumerate_participation_deviation_repairs(
        candidate, problem, run_id=run_id, allow_search=True, scope=scope, dimensions=dimensions
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
        "proven_infeasible": avoidability == "proven_infeasible",
    }
    if avoidability == "bounded_search_exhausted":
        payload["note"] = (
            "bounded_search_exhausted is not proof of infeasibility; another "
            "targeted search may be requested"
        )
    return payload


def _deviation_key(deviation: Mapping[str, Any]) -> Tuple[str, str, str]:
    return (
        str(deviation.get("team") or ""),
        str(deviation.get("scope") or ""),
        str(deviation.get("club") or ""),
    )


def _deviation_index(verification: Mapping[str, Any]) -> Dict[Tuple[str, str, str], Mapping[str, Any]]:
    return {
        _deviation_key(deviation): deviation
        for deviation in verification.get("participation_deviations") or []
    }


def _improves(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> bool:
    if before is None or after is None:
        return False
    return abs(int(after.get("deviation", 0))) < abs(int(before.get("deviation", 0)))


def _changed_ids(before: Mapping[str, Any], after: Mapping[str, Any]) -> List[str]:
    before_by_id = {str(t.get("id")): _signature(t) for t in before.get("tournaments", [])}
    after_by_id = {str(t.get("id")): _signature(t) for t in after.get("tournaments", [])}
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
    return (tournament.get("date"), tournament.get("host_club"), tournament.get("arena"), tournament.get("start_time"), teams)


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
