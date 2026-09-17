"""Bounded local-search repair provider for localized hard findings.

This is the solver-neighborhood family of the harness-directed repair
architecture: the deterministic small families in ``host_team_missing_repair``,
``underfilled_roster_repair`` and ``host_placement_repair`` enumerate the
cheapest local mutations first, and this provider is the next step when none of
them verifies.

It runs the generic Stage 3 local search over an explicit *neighborhood* --
every tournament in the age group(s) touched by a hard finding -- and freezes
every other tournament. The search therefore stays a bounded candidate provider
instead of a season-wide policy owner: it may only re-pair participants and
reassign the host to a club already represented by the tournament's own teams
inside the affected age group, and every produced candidate is re-checked by
the full independent verifier before it is exposed as an option.

The harness still chooses whether to apply the verified result; this module
never adopts a candidate on its own and never weakens a hard rule. When no seed
produces a strict hard-improvement, the seed-by-seed rejection evidence is
returned instead of silently falling back to an opaque whole-season retry.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .application.decisions import DecisionContext
from .host_team_missing_repair import (
    RepairOption,
    _codes,
    _identity,
    candidate_fingerprint,
)
from .planning_contract import verify_candidate
from .stage3_optimizer import optimize_candidate

SEARCH_ENGINE = "local_search"
# Bounded budget: a neighborhood search is a last-resort provider, not the
# primary planner. Two deterministic seeds and a short search are enough to
# expose whether a coupled participant/host repair exists.
_DEFAULT_ITERATIONS = 1200
_DEFAULT_SEEDS: Tuple[int, ...] = (0, 1)
_MAX_OPTIONS = 5

# The bounded search changes *which teams* fill each tournament and *which
# represented club hosts* it. Only hard findings those two dimensions can
# actually repair are in scope; a `date_outside_window`/`unregistered_team`
# finding is not a "search harder" problem, so the provider stays silent for
# it and the ordinary decision context is used instead.
SEARCHABLE_VIOLATION_CODES = frozenset(
    {
        "host_team_missing",
        "excluded_host_club_used",
        "club_hard_max_exceeded",
        "duplicate_participation_same_date",
        "duplicate_team_in_tournament",
        "arena_interval_conflict",
    }
)


def findings_are_locally_searchable(codes: Iterable[str]) -> bool:
    """Whether a bounded participant/host neighborhood search is applicable.

    True only when there is at least one hard finding and every hard finding is
    one the search's two dimensions can repair; a mixed finding set means the
    search alone cannot make the candidate hard-valid, so the ordinary
    controller context (not a solver pass) owns it.
    """
    resolved = {str(code) for code in codes}
    return bool(resolved) and resolved <= SEARCHABLE_VIOLATION_CODES


@dataclass(frozen=True)
class _Neighborhood:
    age_groups: Tuple[str, ...]
    tournament_ids: Tuple[str, ...]
    frozen_tournament_ids: Tuple[str, ...]


def enumerate_search_neighborhood_repairs(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str = "",
    iterations: int = _DEFAULT_ITERATIONS,
    seeds: Sequence[int] = _DEFAULT_SEEDS,
) -> Dict[str, Any]:
    """Run a bounded neighborhood search and expose verified results.

    Returns ``options`` (one per independent verified improvement, deduplicated
    by resulting fingerprint), ``rejected_candidates`` with a per-seed reason,
    and the ``repair_neighborhood`` facts so the controller can see exactly
    which tournaments were allowed to change.
    """
    verification = verify_candidate(dict(candidate), dict(problem))
    findings = list(verification.get("violations", []) or [])
    fingerprint = candidate_fingerprint(candidate)
    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    result_candidates: Dict[str, Any] = {}

    codes = {str(v.get("code")) for v in findings}
    if not findings_are_locally_searchable(codes):
        return _result(
            run_id,
            fingerprint,
            verification,
            None,
            options,
            rejected,
            result_candidates,
            iterations,
            seeds,
            applicable=False,
        )

    neighborhood = _build_neighborhood(candidate, findings)
    if not neighborhood.tournament_ids:
        rejected.append(
            {
                "reason": "no_neighborhood_from_findings",
                "hard_violation_codes": sorted({str(v.get("code")) for v in findings}),
            }
        )
        return _result(run_id, fingerprint, verification, neighborhood, options, rejected, result_candidates, iterations, seeds)

    before_codes = Counter(_codes(verification))
    seen_after: set[str] = set()
    for seed in seeds:
        search_result = optimize_candidate(
            dict(candidate),
            dict(problem),
            iterations=max(1, int(iterations)),
            seed=int(seed),
            move_hosts=True,
            frozen_tournament_ids=list(neighborhood.frozen_tournament_ids),
        )
        after = verify_candidate(dict(search_result), dict(problem))
        after_codes = Counter(_codes(after))
        if not after.get("ok"):
            rejected.append(
                {
                    "reason": "search_candidate_not_hard_valid",
                    "seed": int(seed),
                    "remaining_violation_codes": sorted(after_codes),
                }
            )
            continue
        if sum(after_codes.values()) >= sum(before_codes.values()):
            rejected.append(
                {
                    "reason": "no_hard_improvement",
                    "seed": int(seed),
                    "hard_violations_before": sum(before_codes.values()),
                    "hard_violations_after": sum(after_codes.values()),
                }
            )
            continue
        after_fingerprint = candidate_fingerprint(search_result)
        if after_fingerprint in seen_after:
            rejected.append({"reason": "duplicate_candidate", "seed": int(seed)})
            continue
        seen_after.add(after_fingerprint)
        option_id = (
            f"{fingerprint[:12]}:search_neighborhood:{int(seed)}:"
            f"{_neighborhood_tag(neighborhood.tournament_ids)}"
        )
        options.append(
            RepairOption(
                option_id=option_id,
                finding_id="search_neighborhood",
                action="search_neighborhood",
                tournament_id="",
                arguments={
                    "engine": SEARCH_ENGINE,
                    "seed": int(seed),
                    "iterations": max(1, int(iterations)),
                    "move_hosts": True,
                    "neighborhood_tournament_ids": list(neighborhood.tournament_ids),
                    "frozen_tournament_ids": list(neighborhood.frozen_tournament_ids),
                },
                hard_feasible=True,
                effects=_effects(
                    candidate,
                    search_result,
                    verification,
                    after,
                    neighborhood,
                ),
                evidence={
                    "verification_ok": True,
                    "result_fingerprint": after_fingerprint,
                    "engine": SEARCH_ENGINE,
                    "seed": int(seed),
                },
            )
        )
        result_candidates[option_id] = search_result
        if len(options) >= _MAX_OPTIONS:
            break

    return _result(
        run_id,
        fingerprint,
        verification,
        neighborhood,
        options,
        rejected,
        result_candidates,
        iterations,
        seeds,
    )


def build_search_neighborhood_decision_context(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str,
    candidate_ref: Optional[str] = None,
) -> DecisionContext:
    repair_set = enumerate_search_neighborhood_repairs(candidate, problem, run_id=run_id)
    legal_ids = [option["option_id"] for option in repair_set["options"]]
    verification = repair_set["verification"]
    return DecisionContext(
        run_id=run_id,
        capability="search_neighborhood_repair",
        stage="stage3",
        objective=(
            "Choose one repository-verified result of a bounded neighborhood search "
            "for a localized hard finding, or escalate if the bounded search found none."
        ),
        facts={
            "candidate_fingerprint": repair_set["candidate_fingerprint"],
            "applicable": repair_set["applicable"],
            "repair_options": repair_set["options"],
            "rejected_candidates": repair_set["rejected_candidates"],
            "repair_neighborhood": repair_set["repair_neighborhood"],
            "search_summary": repair_set["search_summary"],
        },
        baseline_hard_violations=tuple(
            f"{violation.get('code')}: {violation.get('message')}"
            for violation in verification.get("violations", [])
        ),
        warnings=()
        if legal_ids
        else (
            "Bounded neighborhood search found no hard-valid improvement; "
            "rejected_candidates records every seed and neighborhood tried.",
        ),
        candidate_ref=candidate_ref,
        available_actions=("apply_repair_option", "optimize_plan", "request_operator")
        if legal_ids
        else ("optimize_plan", "request_operator"),
        action_parameters={
            "apply_repair_option": {
                "option_id": {
                    "type": "string",
                    "enum": legal_ids,
                    "description": "Repository-verified neighborhood-search result id to apply.",
                },
                "candidate_fingerprint": {
                    "type": "string",
                    "enum": [repair_set["candidate_fingerprint"]],
                },
            }
        }
        if legal_ids
        else {},
    )


def apply_search_neighborhood_repair_option(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    option_id: str,
    expected_fingerprint: str,
    run_id: str = "",
) -> Dict[str, Any]:
    """Atomically replace the candidate with a selected verified search result.

    The search is deterministic for a given (candidate, problem, seed), so the
    option is reproduced and re-verified here rather than trusting a cached
    copy across process boundaries.
    """
    before = candidate_fingerprint(candidate)
    if before != expected_fingerprint:
        return {
            "ok": False,
            "reason": "stale_candidate_fingerprint",
            "before_fingerprint": before,
            "expected_fingerprint": expected_fingerprint,
        }
    repair_set = enumerate_search_neighborhood_repairs(candidate, problem, run_id=run_id)
    option = next(
        (entry for entry in repair_set["options"] if entry["option_id"] == option_id), None
    )
    if option is None:
        return {"ok": False, "reason": "unknown_or_stale_option", "before_fingerprint": before}
    result_candidate = repair_set["result_candidates"].get(option_id)
    if result_candidate is None:
        return {
            "ok": False,
            "reason": "search_result_unavailable",
            "before_fingerprint": before,
        }
    verification = verify_candidate(dict(result_candidate), dict(problem))
    if not verification.get("ok"):
        return {
            "ok": False,
            "reason": "verification_failed",
            "before_fingerprint": before,
            "verification": verification,
        }
    return {
        "ok": True,
        "candidate": result_candidate,
        "option_id": option_id,
        "before_fingerprint": before,
        "after_fingerprint": candidate_fingerprint(result_candidate),
        "verification": verification,
        "effects": option.get("effects", {}),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _result(
    run_id: str,
    fingerprint: str,
    verification: Mapping[str, Any],
    neighborhood: Optional[_Neighborhood],
    options: List[RepairOption],
    rejected: List[Dict[str, Any]],
    result_candidates: Dict[str, Any],
    iterations: int,
    seeds: Sequence[int],
    *,
    applicable: bool = True,
) -> Dict[str, Any]:
    if neighborhood is None:
        repair_neighborhood: Dict[str, Any] = {
            "age_groups": [],
            "tournament_ids": [],
            "frozen_tournament_ids": [],
        }
    else:
        repair_neighborhood = {
            "age_groups": list(neighborhood.age_groups),
            "tournament_ids": list(neighborhood.tournament_ids),
            "frozen_tournament_ids": list(neighborhood.frozen_tournament_ids),
        }
    return {
        "run_id": run_id,
        "candidate_fingerprint": fingerprint,
        "verification": dict(verification),
        "applicable": applicable,
        "repair_neighborhood": repair_neighborhood,
        "options": [option.to_dict() for option in options],
        "rejected_candidates": rejected,
        "result_candidates": result_candidates,
        "search_summary": {
            "engine": SEARCH_ENGINE,
            "iterations": max(1, int(iterations)),
            "seeds": [int(seed) for seed in seeds],
            "seeds_with_verified_improvement": len(options),
            "options_returned": len(options),
        },
    }


def _neighborhood_tag(tournament_ids: Sequence[str]) -> str:
    payload = json.dumps(list(tournament_ids), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


def _build_neighborhood(
    candidate: Mapping[str, Any], findings: Sequence[Mapping[str, Any]]
) -> _Neighborhood:
    tournaments = [t for t in candidate.get("tournaments", []) if not t.get("cancelled")]
    by_id = {str(t.get("id")): t for t in tournaments}
    age_groups: set[str] = set()
    for finding in findings:
        if finding.get("age_group"):
            age_groups.add(str(finding.get("age_group")))
        tournament_id = finding.get("tournament_id")
        if tournament_id is None:
            continue
        tournament = by_id.get(str(tournament_id))
        if tournament and tournament.get("age_group"):
            age_groups.add(str(tournament.get("age_group")))
    if age_groups:
        neighborhood_ids = {
            str(t.get("id")) for t in tournaments if str(t.get("age_group")) in age_groups
        }
    else:
        # Findings without a resolvable age group: fall back to the exact
        # tournaments named, which is the smallest possible neighborhood.
        neighborhood_ids = {
            str(f.get("tournament_id"))
            for f in findings
            if f.get("tournament_id") is not None
        }
    all_ids = {str(t.get("id")) for t in tournaments}
    frozen_ids = all_ids - neighborhood_ids
    return _Neighborhood(
        age_groups=tuple(sorted(age_groups)),
        tournament_ids=tuple(sorted(neighborhood_ids)),
        frozen_tournament_ids=tuple(sorted(frozen_ids)),
    )


def _effects(
    before_candidate: Mapping[str, Any],
    after_candidate: Mapping[str, Any],
    before_verification: Mapping[str, Any],
    after_verification: Mapping[str, Any],
    neighborhood: _Neighborhood,
) -> Dict[str, Any]:
    before_codes = Counter(_codes(before_verification))
    after_codes = Counter(_codes(after_verification))
    deltas: Dict[str, int] = {}
    for code in set(before_codes) | set(after_codes):
        delta = after_codes.get(code, 0) - before_codes.get(code, 0)
        if delta:
            deltas[code] = delta
    changed = _changed_tournaments(
        before_candidate, after_candidate, set(neighborhood.tournament_ids)
    )
    source = after_candidate.get("source") or {}
    objective_before = source.get("objective_before")
    objective_after = source.get("objective_after")
    effects: Dict[str, Any] = {
        "hard_violations": sum(after_codes.values()) - sum(before_codes.values()),
        "hard_violation_delta_by_code": deltas,
        "changed_tournament_ids": changed,
        "changed_tournament_count": len(changed),
    }
    if isinstance(objective_before, (int, float)) and isinstance(objective_after, (int, float)):
        effects["weighted_objective_before"] = float(objective_before)
        effects["weighted_objective_after"] = float(objective_after)
        effects["weighted_objective_delta"] = float(objective_after) - float(objective_before)
    return effects


def _changed_tournaments(
    before_candidate: Mapping[str, Any],
    after_candidate: Mapping[str, Any],
    scope_ids: Iterable[str],
) -> List[str]:
    scope = {str(item) for item in scope_ids}
    before_by_id = {
        str(t.get("id")): t for t in before_candidate.get("tournaments", [])
    }
    after_by_id = {str(t.get("id")): t for t in after_candidate.get("tournaments", [])}
    changed: List[str] = []
    for tournament_id in sorted(scope):
        before = before_by_id.get(tournament_id)
        after = after_by_id.get(tournament_id)
        if before is None or after is None:
            changed.append(tournament_id)
            continue
        if _tournament_signature(before) != _tournament_signature(after):
            changed.append(tournament_id)
    return changed


def _tournament_signature(tournament: Mapping[str, Any]) -> Tuple[Any, ...]:
    teams = tuple(sorted(_identity(team) for team in tournament.get("teams", []) or []))
    return (
        tournament.get("date"),
        tournament.get("host_club"),
        tournament.get("arena"),
        tournament.get("start_time"),
        teams,
    )
