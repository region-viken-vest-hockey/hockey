"""Bounded same-club sibling substitutions for intra-club participation spread.

``participation_targets`` owns the club x age-group x scope player-pool
classification. When a multi-team pool has *enough aggregate* participation but
the nominal sibling labels are uneven, the classification is
``intra_club_distribution``: a pure redistribution opportunity, not a missing
participation opportunity. :mod:`tournament_scheduler.home_representation_repair`
owns the analogous *home-appearance* spread; this module owns the participation
spread semantic separately (they are independent quality layers).

The cheapest real repair is a same-club, same-age substitution inside one
existing tournament in the finding's scope: replace an over-represented sibling
with an under-represented sibling. The tournament's id/date/arena/start
time/host/age group are untouched, games are regenerated through the canonical
generator, the club-pool aggregate participation count is preserved, and guest
reservations are never consumed or released.

Away tournaments (the club is not the host) are enumerated before home
tournaments, because changing which sibling shows up at a home tournament can
only ever move the already-repaired ``home_representation`` objective. A home
substitution is returned only when it does not worsen the affected pool's home
representation.

When a direct substitution is blocked by a duplicate-day or a schedule
consequence, a bounded coupled sibling-only search pairs two substitutions so
the combined candidate can still be legal. Dates/hosts/arenas/slots are never
moved to solve label distribution.

Every option is hard-verified by the independent verifier, must strictly reduce
the selected scope's sibling spread, must preserve the club-pool aggregate, may
not deepen any genuine club-pool shortfall, may not worsen home representation,
may not materially regress an affected team's spacing/coverage/opponent
repetition/travel, and may not change hosting responsibility or guest
reservations. The provider is planner-neutral (plain ``candidate``/``problem``
dicts) and commits nothing: the verified candidate is applied through the
ordinary revision-bound ``apply_repair`` boundary.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .guest_slots import guest_slot_summary
from .home_representation import home_representation_rows
from .host_representation import clubs_represent_same_club
from .host_team_missing_repair import (
    RepairOption,
    _codes,
    _find_tournament,
    _identity,
    _participation_counts,
    _primary_violation_reason,
    _regenerate_games,
    _slug,
    _split_date,
    candidate_fingerprint,
)
from .participation_targets import (
    INTRA_CLUB_DISTRIBUTION,
    club_pool_participation_regressions,
    evaluate_participation,
)
from .planning_contract import (
    _parse_date,
    _participation_search_evidence,
    score_candidate,
    verify_candidate,
)
from .quality_objectives import compare_quality_scores, with_unresolved_obligations_count
from .team_schedule_quality import compare_changed_team_schedule_consequence

INTRA_CLUB_DISTRIBUTION_FINDING_PREFIX = "intra_club_participation_distribution"

# Bounds so the family stays a localized rebalance, not a whole-season search.
DEFAULT_MAX_OPTIONS_PER_FINDING = 4
DEFAULT_MAX_DIRECT_EVALUATED = 32
DEFAULT_MAX_COUPLED_UNITS = 16
DEFAULT_MAX_COUPLED_EVALUATED = 64

# Material consequences this family refuses to accept. Unlike the narrower
# home-representation swap policy, an intra-club distribution repair is
# explicitly required not to materially worsen an affected team's opponent
# repetition or travel either, so those canonical material codes are included.
MATERIAL_SUBSTITUTION_REGRESSION_CODES = frozenset(
    {
        "participation_shortfall_worsened",
        "participation_hard_max_exceeded",
        "more_gaps_under_7_days",
        "more_gaps_under_14_days",
        "temporal_coverage_materially_worse",
        "more_repeated_opponent_excess",
        "travel_materially_worse",
    }
)


def intra_club_distribution_finding_id(club: str, age_group: str, scope: str) -> str:
    """Stable finding identity shared by findings and option enumeration."""
    return f"{INTRA_CLUB_DISTRIBUTION_FINDING_PREFIX}:{club}:{age_group}:{scope}"


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------


def enumerate_intra_club_distribution_repairs(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str = "",
    allow_search: bool = False,
    scope: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return verified sibling-substitution options for intra-club pools.

    ``allow_search`` widens the enumeration into the bounded coupled
    sibling-only neighborhood once the cheap direct substitutions are known;
    it never relaxes the acceptance gates.
    """
    verification = verify_candidate(dict(candidate), dict(problem))
    fingerprint = candidate_fingerprint(candidate)
    pools = [
        pool
        for pool in verification.get("participation_club_pools") or []
        if str(pool.get("classification") or "") == INTRA_CLUB_DISTRIBUTION
    ]
    resolved_scope = scope or {}
    scope_club = str(resolved_scope.get("club") or "")
    scope_age = str(resolved_scope.get("age_group") or "")
    scope_name = str(resolved_scope.get("scope") or "")
    targeted = [
        pool
        for pool in pools
        if (not scope_club or str(pool.get("club") or "") == scope_club)
        and (not scope_age or str(pool.get("age_group") or "") == scope_age)
        and (not scope_name or str(pool.get("scope") or "") == scope_name)
    ]
    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    seen_signatures: set = set()
    for pool in targeted:
        pool_options, pool_rejections = _pool_options(
            candidate,
            problem,
            pool,
            fingerprint,
            verification,
            allow_search=allow_search,
        )
        for option in pool_options:
            signature = _swap_signature(option.arguments.get("swaps") or [])
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            options.append(option)
        rejected.extend(pool_rejections)
    options.sort(key=_option_rank)
    applicable_findings = [
        intra_club_distribution_finding_id(
            str(pool.get("club") or ""),
            str(pool.get("age_group") or ""),
            str(pool.get("scope") or ""),
        )
        for pool in targeted
    ]
    return {
        "run_id": run_id,
        "candidate_fingerprint": fingerprint,
        "verification": dict(verification),
        "applicable": bool(targeted),
        "applicable_findings": applicable_findings,
        "options": [option.to_dict() for option in options],
        "rejected_candidates": rejected,
    }


def apply_intra_club_distribution_repair_option(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    option_id: str,
    expected_fingerprint: str,
    run_id: str = "",
    arguments: Mapping[str, Any] | None = None,
    scope: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Atomically replay one option's declared swaps and re-verify the season."""
    before = candidate_fingerprint(candidate)
    if before != expected_fingerprint:
        return {
            "ok": False,
            "reason": "stale_candidate_fingerprint",
            "before_fingerprint": before,
            "expected_fingerprint": expected_fingerprint,
        }
    arguments = dict(arguments or {})
    swaps = list(arguments.get("swaps") or [])
    if not swaps:
        return {"ok": False, "reason": "unknown_or_stale_option", "before_fingerprint": before}
    club = str(arguments.get("club") or (scope or {}).get("club") or "")
    age_group = str(arguments.get("age_group") or (scope or {}).get("age_group") or "")
    pool_scope = str(arguments.get("scope") or (scope or {}).get("scope") or "")
    trial = copy.deepcopy(dict(candidate))
    for swap in swaps:
        if not _apply_swap(trial, problem, swap):
            return {
                "ok": False,
                "reason": "invalid_or_stale_swap",
                "before_fingerprint": before,
                "swap": dict(swap),
            }
    verification = verify_candidate(dict(trial), dict(problem))
    if not verification.get("ok"):
        return {
            "ok": False,
            "reason": _primary_violation_reason(verification),
            "before_fingerprint": before,
            "violations": _codes(verification),
            "verification": verification,
        }
    before_eval = evaluate_participation(
        dict(candidate), dict(problem), search_evidence=_participation_search_evidence(problem)
    )
    after_eval = evaluate_participation(
        dict(trial), dict(problem), search_evidence=_participation_search_evidence(problem)
    )
    before_pool = _pool_for(before_eval.club_pools, club, age_group, pool_scope)
    after_pool = _pool_for(after_eval.club_pools, club, age_group, pool_scope)
    if before_pool is None or after_pool is None:
        return {
            "ok": False,
            "reason": "unknown_or_stale_option",
            "before_fingerprint": before,
        }
    if _spread(after_pool) >= _spread(before_pool):
        return {
            "ok": False,
            "reason": "no_distribution_improvement",
            "before_fingerprint": before,
            "distribution_before": _distribution(before_pool),
            "distribution_after": _distribution(after_pool),
        }
    if int(after_pool.get("club_pool_actual") or 0) != int(before_pool.get("club_pool_actual") or 0):
        return {
            "ok": False,
            "reason": "club_pool_aggregate_changed",
            "before_fingerprint": before,
            "club_pool_actual_before": before_pool.get("club_pool_actual"),
            "club_pool_actual_after": after_pool.get("club_pool_actual"),
        }
    consequences = _changed_team_consequences(
        candidate, trial, problem, swapped_identities=_swapped_identities(swaps)
    )
    if not consequences["consequence_acceptable"]:
        return {
            "ok": False,
            "reason": "team_schedule_regression",
            "before_fingerprint": before,
            "consequences": consequences,
        }
    if not _guest_reservations_preserved(candidate, trial):
        return {
            "ok": False,
            "reason": "guest_reservations_changed",
            "before_fingerprint": before,
        }
    return {
        "ok": True,
        "candidate": trial,
        "option_id": option_id,
        "before_fingerprint": before,
        "after_fingerprint": candidate_fingerprint(trial),
        "verification": verification,
    }


# ---------------------------------------------------------------------------
# Option enumeration
# ---------------------------------------------------------------------------


def _pool_options(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    pool: Mapping[str, Any],
    fingerprint: str,
    before_verification: Mapping[str, Any],
    *,
    allow_search: bool,
) -> Tuple[List[RepairOption], List[Dict[str, Any]]]:
    club = str(pool.get("club") or "")
    age_group = str(pool.get("age_group") or "")
    pool_scope = str(pool.get("scope") or "")
    finding_id = intra_club_distribution_finding_id(club, age_group, pool_scope)
    distribution = _distribution(pool)
    over_labels, under_labels = _over_under(distribution)
    rejected: List[Dict[str, Any]] = []
    options: List[RepairOption] = []

    if not over_labels or not under_labels:
        rejected.append(
            {
                "finding_id": finding_id,
                "club": club,
                "age_group": age_group,
                "scope": pool_scope,
                "reason": "no_over_under_sibling_pair",
                "distribution": distribution,
            }
        )
        return options, rejected

    units = list(
        _move_units(
            candidate,
            problem,
            club,
            age_group,
            pool_scope,
            over_labels,
            under_labels,
            distribution,
        )
    )
    evaluated = 0
    for unit in units:
        if evaluated >= DEFAULT_MAX_DIRECT_EVALUATED:
            break
        evaluated += 1
        if unit.get("blocked_reason"):
            rejected.append(
                {
                    "finding_id": finding_id,
                    "club": club,
                    "age_group": age_group,
                    "scope": pool_scope,
                    "reason": unit["blocked_reason"],
                    "tournament_id": unit.get("tournament_id"),
                    "swap": dict(unit["swap"]),
                }
            )
            continue
        trial = _apply_swaps(candidate, problem, [unit["swap"]])
        if trial is None:
            continue
        option, reason = _evaluate_move(
            candidate,
            trial,
            problem,
            pool,
            finding_id,
            fingerprint,
            [unit["swap"]],
            before_verification,
        )
        if option is not None:
            options.append(option)
        elif reason is not None:
            rejected.append(reason)

    if allow_search:
        coupled, coupled_rejections = _coupled_options(
            candidate,
            problem,
            pool,
            finding_id,
            fingerprint,
            units,
            before_verification,
        )
        options.extend(coupled)
        rejected.extend(coupled_rejections)

    if not options:
        rejected.append(
            {
                "finding_id": finding_id,
                "club": club,
                "age_group": age_group,
                "scope": pool_scope,
                "reason": "no_verified_sibling_substitution",
                "distribution": distribution,
                "bounded_search_run": bool(allow_search),
            }
        )
    options.sort(key=_option_rank)
    return options[:DEFAULT_MAX_OPTIONS_PER_FINDING], rejected


def _evaluate_move(
    before: Mapping[str, Any],
    trial: Mapping[str, Any],
    problem: Mapping[str, Any],
    pool: Mapping[str, Any],
    finding_id: str,
    fingerprint: str,
    swaps: Sequence[Mapping[str, Any]],
    before_verification: Mapping[str, Any],
) -> Tuple[Optional[RepairOption], Optional[Dict[str, Any]]]:
    club = str(pool.get("club") or "")
    age_group = str(pool.get("age_group") or "")
    pool_scope = str(pool.get("scope") or "")
    base = {
        "finding_id": finding_id,
        "club": club,
        "age_group": age_group,
        "scope": pool_scope,
        "tournament_id": str(swaps[0].get("tournament_id") or "") if swaps else "",
        "swaps": _swap_evidence(swaps),
    }
    verification = verify_candidate(dict(trial), dict(problem))
    if not verification.get("ok"):
        return None, {
            **base,
            "reason": _primary_violation_reason(verification),
            "violations": _codes(verification),
        }
    after_pool = _pool_for(
        verification.get("participation_club_pools") or [], club, age_group, pool_scope
    )
    if after_pool is None:
        return None, {**base, "reason": "pool_missing_after_substitution"}
    if _spread(after_pool) >= _spread(pool):
        return None, {
            **base,
            "reason": "no_distribution_improvement",
            "spread_before": _spread(pool),
            "spread_after": _spread(after_pool),
        }
    if int(after_pool.get("club_pool_actual") or 0) != int(pool.get("club_pool_actual") or 0):
        return None, {**base, "reason": "club_pool_aggregate_changed"}
    consequences = _changed_team_consequences(
        before, trial, problem, swapped_identities=_swapped_identities(swaps)
    )
    if not consequences["consequence_acceptable"]:
        return None, {
            **base,
            "reason": _consequence_reason(consequences),
            "consequences": consequences,
        }
    changed_ids = consequences["changed_tournament_ids"]
    home_involved = any(
        _is_home(_find_tournament(before, str(swap.get("tournament_id") or "")) or {}, club)
        for swap in swaps
    )
    option_id = (
        f"{fingerprint[:12]}:{INTRA_CLUB_DISTRIBUTION_FINDING_PREFIX}:{club}:{age_group}:"
        f"{pool_scope}:swap:{_swap_tag(swaps)}"
    )
    effects: Dict[str, Any] = {
        "scope": pool_scope,
        "distribution_before": _distribution(pool),
        "distribution_after": _distribution(after_pool),
        "spread_before": _spread(pool),
        "spread_after": _spread(after_pool),
        "club_pool_target": pool.get("club_pool_target"),
        "club_pool_actual_before": pool.get("club_pool_actual"),
        "club_pool_actual_after": after_pool.get("club_pool_actual"),
        "classification_before": pool.get("classification"),
        "classification_after": after_pool.get("classification"),
        "changed_tournament_ids": changed_ids,
        "changed_tournament_count": len(changed_ids),
        "participant_swaps": len(swaps),
        "moves_home_representation": home_involved,
        "home_representation_regressions": consequences["home_representation_regressions"],
        "material_team_regressions": consequences["material_team_regressions"],
        "club_pool_regressions": consequences["club_pool_regressions"],
        "affected_teams": consequences["affected_teams"],
        "consequence_acceptable": bool(consequences["consequence_acceptable"]),
        "quality_regression_count": _quality_regression_count(before, trial, problem),
    }
    arguments = {
        "swaps": [dict(swap) for swap in swaps],
        "club": club,
        "age_group": age_group,
        "scope": pool_scope,
    }
    return (
        RepairOption(
            option_id=option_id,
            finding_id=finding_id,
            action="sibling_substitution",
            tournament_id=str(swaps[0].get("tournament_id") or ""),
            arguments=arguments,
            hard_feasible=True,
            effects=effects,
            evidence={
                "verification_ok": True,
                "guest_reservations_unchanged": True,
                "host_responsibility_unchanged": True,
                "forbidden_dimension_moved": False,
            },
        ),
        None,
    )


def _coupled_options(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    pool: Mapping[str, Any],
    finding_id: str,
    fingerprint: str,
    units: Sequence[Mapping[str, Any]],
    before_verification: Mapping[str, Any],
) -> Tuple[List[RepairOption], List[Dict[str, Any]]]:
    """Bounded pair search over sibling-only substitutions.

    A single substitution can be blocked by a duplicate-day or a schedule
    consequence. Pairing two substitutions (possibly including the blocked
    move) can still produce a legal, aggregate-preserving candidate. The
    neighborhood is bounded and only participates when the cheap direct pass
    is insufficient; no date/host/arena/slot field is ever moved.
    """
    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    eligible = [unit for unit in units if unit.get("swap")][:DEFAULT_MAX_COUPLED_UNITS]
    if len(eligible) < 2:
        return options, rejected
    evaluated = 0
    for first_index in range(len(eligible)):
        for second_index in range(first_index + 1, len(eligible)):
            if evaluated >= DEFAULT_MAX_COUPLED_EVALUATED:
                break
            first = eligible[first_index]
            second = eligible[second_index]
            if first.get("blocked_reason") and second.get("blocked_reason"):
                # Two moves that are both individually blocked are not a
                # plausible coupling; keep the budget for one-blocked pairs.
                continue
            evaluated += 1
            swaps = [first["swap"], second["swap"]]
            trial = _apply_swaps(candidate, problem, swaps)
            if trial is None:
                continue
            option, reason = _evaluate_move(
                candidate,
                trial,
                problem,
                pool,
                finding_id,
                fingerprint,
                swaps,
                before_verification,
            )
            if option is not None:
                options.append(option)
            elif reason is not None:
                rejected.append({**reason, "coupled": True})
        if evaluated >= DEFAULT_MAX_COUPLED_EVALUATED:
            break
    return options, rejected


def _move_units(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    club: str,
    age_group: str,
    pool_scope: str,
    over_labels: Sequence[str],
    under_labels: Sequence[str],
    distribution: Mapping[str, Mapping[str, int]],
) -> Iterable[Dict[str, Any]]:
    """Yield deterministic single-substitution units, away tournaments first."""
    tournaments = _scope_tournaments(candidate, problem, club, age_group, pool_scope)
    ordered_tournaments = sorted(
        tournaments,
        key=lambda tournament: (
            0 if not _is_home(tournament, club) else 1,
            str(tournament.get("date") or ""),
            str(tournament.get("id") or ""),
        ),
    )
    for over in over_labels:
        for under in under_labels:
            if over == under:
                continue
            gap = int(distribution[over]["actual"]) - int(distribution[under]["actual"])
            if gap <= 0:
                continue
            for tournament in ordered_tournaments:
                if not _participates(tournament, club, age_group, over):
                    continue
                if _participates(tournament, club, age_group, under):
                    continue
                swap = _swap(tournament, over, under, club, age_group)
                blocked = None
                if _plays_on_date(candidate, club, age_group, under, tournament):
                    blocked = "duplicate_day_conflict"
                yield {
                    "tournament_id": str(tournament.get("id") or ""),
                    "over": over,
                    "under": under,
                    "gap": gap,
                    "away": not _is_home(tournament, club),
                    "swap": swap,
                    "blocked_reason": blocked,
                }


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------


def _apply_swaps(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    swaps: Sequence[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    trial = copy.deepcopy(dict(candidate))
    for swap in swaps:
        if not _apply_swap(trial, problem, swap):
            return None
    return trial


def _apply_swap(
    trial: Dict[str, Any], problem: Mapping[str, Any], swap: Mapping[str, Any]
) -> bool:
    tournament = _find_tournament(trial, str(swap.get("tournament_id") or ""))
    if tournament is None or tournament.get("cancelled"):
        return False
    remove_identity = _identity(swap.get("remove") or {})
    add_identity = _identity(swap.get("add") or {})
    teams = [team for team in tournament.get("teams", []) or []]
    if not any(_identity(team) == remove_identity for team in teams):
        return False
    teams = [team for team in teams if _identity(team) != remove_identity]
    if not any(_identity(team) == add_identity for team in teams):
        teams.append(_registered_team(problem, swap.get("add") or {}))
    tournament["teams"] = teams
    _regenerate_games(tournament, problem)
    return True


def _registered_team(problem: Mapping[str, Any], reference: Mapping[str, Any]) -> Dict[str, Any]:
    identity = _identity(reference)
    for team in problem.get("teams", []) or []:
        if _identity(team) == identity:
            return dict(team)
    return dict(reference)


def _swap(tournament: Mapping[str, Any], remove: str, add: str, club: str, age_group: str) -> Dict[str, Any]:
    return {
        "tournament_id": str(tournament.get("id") or ""),
        "remove": {"club": club, "label": remove, "age_group": age_group},
        "add": {"club": club, "label": add, "age_group": age_group},
    }


# ---------------------------------------------------------------------------
# Consequences
# ---------------------------------------------------------------------------


def _changed_team_consequences(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    swapped_identities: Iterable[Tuple[str, str, str]] | None = None,
) -> Dict[str, Any]:
    """Complete changed-team consequence set for a sibling substitution.

    Every team whose tournament membership changed is evaluated with the
    canonical membership-aware per-team policy, the affected club x age-group
    player pools are re-evaluated with the canonical club-pool classification,
    and the affected home-representation pools are re-measured. A candidate is
    acceptable only when no affected team has a canonical material regression,
    no genuine club-pool shortfall deepens, no home-representation pool worsens
    and no guest reservation changed.
    """
    changed_ids = _changed_tournament_ids(before, after)
    identities: set = set()
    club_age_pairs: set = set()
    for tournament_id in changed_ids:
        for plan in (before, after):
            tournament = _find_tournament(plan, tournament_id)
            if tournament is None:
                continue
            if tournament.get("age_group"):
                for club in {
                    str(team.get("club") or "")
                    for team in tournament.get("teams", []) or []
                    if team.get("club")
                }:
                    club_age_pairs.add((club, str(tournament.get("age_group") or "")))
            for team in tournament.get("teams", []) or []:
                identities.add(_identity(team))
    before_counts = _participation_counts(before)
    after_counts = _participation_counts(after)
    team_consequences: Dict[str, Any] = {}
    for identity in sorted(identities):
        before_count = before_counts.get(identity, 0)
        after_count = after_counts.get(identity, 0)
        if after_count > before_count:
            role = "added"
        elif after_count < before_count:
            role = "removed"
        else:
            role = "retained"
        team_consequences[f"{identity[0]}|{identity[1]}|{identity[2]}"] = {
            "team": {"club": identity[0], "label": identity[1], "age_group": identity[2]},
            **compare_changed_team_schedule_consequence(
                before, after, identity, problem=problem, membership_role=role
            ),
        }
    before_eval = evaluate_participation(
        before, problem, search_evidence=_participation_search_evidence(problem)
    )
    after_eval = evaluate_participation(
        after, problem, search_evidence=_participation_search_evidence(problem)
    )
    pool_regressions = club_pool_participation_regressions(
        before_eval, after_eval, club_age_pairs=club_age_pairs
    )
    home_regressions = _home_representation_regressions(before, after, problem, club_age_pairs)
    material_team_regressions = [
        {
            "team": analysis["team"],
            "membership_role": analysis.get("membership_role"),
            "regressions": analysis.get("material_regressions") or [],
        }
        for analysis in team_consequences.values()
        if analysis.get("material_regressions")
    ]
    strict_material: List[Dict[str, Any]] = []
    for analysis in team_consequences.values():
        for regression in analysis.get("material_regressions") or []:
            if regression.get("code") in MATERIAL_SUBSTITUTION_REGRESSION_CODES:
                strict_material.append(
                    {
                        "team": analysis["team"],
                        "membership_role": analysis.get("membership_role"),
                        "regression": regression,
                    }
                )
    acceptable = (
        not strict_material and not pool_regressions and not home_regressions
    )
    return {
        "changed_tournament_ids": changed_ids,
        "affected_teams": [
            {
                "club": key.split("|")[0],
                "label": key.split("|")[1],
                "age_group": key.split("|")[2],
                "membership_role": analysis.get("membership_role"),
            }
            for key, analysis in sorted(team_consequences.items())
        ],
        "team_consequences": team_consequences,
        "material_team_regressions": material_team_regressions,
        "strict_material_regressions": strict_material,
        "club_pool_regressions": pool_regressions,
        "home_representation_regressions": home_regressions,
        "consequence_acceptable": acceptable,
    }


def _home_representation_regressions(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    problem: Mapping[str, Any],
    club_age_pairs: Iterable[Tuple[str, str]],
) -> List[Dict[str, Any]]:
    """Affected club x age home-representation pools whose spread got worse."""
    before_rows = {
        (str(row.get("club") or ""), str(row.get("age_group") or "")): row
        for row in home_representation_rows(
            (problem or {}).get("teams") or [], (before or {}).get("tournaments") or []
        )
    }
    after_rows = {
        (str(row.get("club") or ""), str(row.get("age_group") or "")): row
        for row in home_representation_rows(
            (problem or {}).get("teams") or [], (after or {}).get("tournaments") or []
        )
    }
    out: List[Dict[str, Any]] = []
    for pair in sorted(set(club_age_pairs)):
        prior = before_rows.get(pair)
        current = after_rows.get(pair)
        if prior is None or current is None:
            continue
        if int(current.get("material_spread") or 0) > int(prior.get("material_spread") or 0) or int(
            current.get("spread") or 0
        ) > int(prior.get("spread") or 0):
            out.append(
                {
                    "club": pair[0],
                    "age_group": pair[1],
                    "spread_before": int(prior.get("spread") or 0),
                    "spread_after": int(current.get("spread") or 0),
                    "material_spread_before": int(prior.get("material_spread") or 0),
                    "material_spread_after": int(current.get("material_spread") or 0),
                }
            )
    return out


def _guest_reservations_preserved(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    before_summaries = {
        str(tournament.get("id") or ""): guest_slot_summary(tournament)
        for tournament in before.get("tournaments", []) or []
    }
    after_summaries = {
        str(tournament.get("id") or ""): guest_slot_summary(tournament)
        for tournament in after.get("tournaments", []) or []
    }
    return before_summaries == after_summaries


def _consequence_reason(consequences: Mapping[str, Any]) -> str:
    if consequences.get("home_representation_regressions"):
        return "home_representation_regression"
    if consequences.get("club_pool_regressions"):
        return "club_pool_shortfall_regression"
    if consequences.get("strict_material_regressions"):
        return "team_schedule_regression"
    return "team_schedule_regression"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _distribution(pool: Mapping[str, Any]) -> Dict[str, Dict[str, int]]:
    return {
        str(row.get("team") or ""): {
            "actual": int(row.get("actual") or 0),
            "target": int(row.get("target") or 0),
        }
        for row in pool.get("team_distribution") or []
    }


def _spread(pool: Mapping[str, Any]) -> int:
    actuals = [int(row.get("actual") or 0) for row in pool.get("team_distribution") or []]
    return max(actuals) - min(actuals) if actuals else 0


def _over_under(
    distribution: Mapping[str, Mapping[str, int]]
) -> Tuple[List[str], List[str]]:
    over = sorted(
        (label for label, row in distribution.items() if row["actual"] > row["target"]),
        key=lambda label: (-distribution[label]["actual"], label),
    )
    under = sorted(
        (label for label, row in distribution.items() if row["actual"] < row["target"]),
        key=lambda label: (distribution[label]["actual"], label),
    )
    return over, under


def _pool_for(
    pools: Iterable[Mapping[str, Any]], club: str, age_group: str, scope: str
) -> Optional[Mapping[str, Any]]:
    for pool in pools:
        if (
            str(pool.get("club") or "") == club
            and str(pool.get("age_group") or "") == age_group
            and str(pool.get("scope") or "") == scope
        ):
            return pool
    return None


def _scope_tournaments(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    club: str,
    age_group: str,
    pool_scope: str,
) -> List[Mapping[str, Any]]:
    return [
        tournament
        for tournament in candidate.get("tournaments", []) or []
        if not tournament.get("cancelled")
        and str(tournament.get("age_group") or "") == age_group
        and _in_scope(problem, tournament, pool_scope)
    ]


def _in_scope(
    problem: Mapping[str, Any], tournament: Mapping[str, Any], pool_scope: str
) -> bool:
    if pool_scope in ("before_christmas", "after_christmas"):
        from tournament_scheduler import planning_half

        half = planning_half.tournament_half(
            _parse_date(tournament.get("date")), _split_date(problem)
        )
        return half == pool_scope
    return True


def _is_home(tournament: Mapping[str, Any], club: str) -> bool:
    return clubs_represent_same_club(str(tournament.get("host_club") or ""), club)


def _participates(
    tournament: Mapping[str, Any], club: str, age_group: str, label: str
) -> bool:
    for team in tournament.get("teams", []) or []:
        if (
            str(team.get("label") or "") == label
            and str(team.get("age_group") or "") == age_group
            and clubs_represent_same_club(str(team.get("club") or ""), club)
        ):
            return True
    return False


def _plays_on_date(
    plan: Mapping[str, Any], club: str, age_group: str, label: str, tournament: Mapping[str, Any]
) -> bool:
    tournament_date = _parse_date(tournament.get("date"))
    if tournament_date is None:
        return False
    for other in plan.get("tournaments", []) or []:
        if str(other.get("id")) == str(tournament.get("id")):
            continue
        if _parse_date(other.get("date")) != tournament_date:
            continue
        if _participates(other, club, age_group, label):
            return True
    return False


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
            (
                str(team.get("club") or ""),
                str(team.get("label") or ""),
                str(team.get("age_group") or ""),
            )
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


def _swapped_identities(swaps: Sequence[Mapping[str, Any]]) -> List[Tuple[str, str, str]]:
    identities: List[Tuple[str, str, str]] = []
    for swap in swaps:
        for side in ("remove", "add"):
            identity = _identity(swap.get(side) or {})
            if identity not in identities:
                identities.append(identity)
    return identities


def _swap_evidence(swaps: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "tournament_id": str(swap.get("tournament_id") or ""),
            "remove": swap.get("remove"),
            "add": swap.get("add"),
        }
        for swap in swaps
    ]


def _swap_signature(swaps: Sequence[Mapping[str, Any]]) -> Tuple[Any, ...]:
    return tuple(
        (
            str(swap.get("tournament_id") or ""),
            _identity(swap.get("remove") or {}),
            _identity(swap.get("add") or {}),
        )
        for swap in swaps
    )


def _swap_tag(swaps: Sequence[Mapping[str, Any]]) -> str:
    parts: List[str] = []
    for swap in swaps:
        remove = swap.get("remove") or {}
        add = swap.get("add") or {}
        parts.append(f"{swap.get('tournament_id')}-{remove.get('label')}-{add.get('label')}")
    return _slug(tuple(parts))


def _quality_regression_count(
    before: Mapping[str, Any], after: Mapping[str, Any], problem: Mapping[str, Any]
) -> int:
    before_score = with_unresolved_obligations_count(
        score_candidate(dict(before), problem=dict(problem))
    )
    after_score = with_unresolved_obligations_count(
        score_candidate(dict(after), problem=dict(problem))
    )
    return len(compare_quality_scores(before_score, after_score)["regressions"])


def _option_rank(option: RepairOption) -> Tuple[int, int, int, int, int, str]:
    effects = option.effects or {}
    rejected = 0 if effects.get("consequence_acceptable", True) else 1
    # Away substitutions are preferred so the already-repaired
    # ``home_representation`` objective is naturally preserved.
    moves_home = 1 if effects.get("moves_home_representation") else 0
    quality = int(effects.get("quality_regression_count", 0) or 0)
    improvement = int(effects.get("spread_before", 0) or 0) - int(
        effects.get("spread_after", 0) or 0
    )
    return (
        rejected,
        moves_home,
        quality,
        -improvement,
        int(effects.get("participant_swaps", 0) or 0),
        option.option_id,
    )


__all__ = [
    "INTRA_CLUB_DISTRIBUTION_FINDING_PREFIX",
    "apply_intra_club_distribution_repair_option",
    "enumerate_intra_club_distribution_repairs",
    "intra_club_distribution_finding_id",
]
