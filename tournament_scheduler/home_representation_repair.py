"""Bounded same-club sibling swaps for intra-club home representation.

Hosting coverage/balance already guarantees a multi-team club hosts its share
of a club x age-group. It does not guarantee that the *same* sibling team is
not representing the club at nearly every one of those home tournaments.
:mod:`tournament_scheduler.home_representation` owns the accounting; this
provider owns the smallest real repair for a skewed pool.

Two deterministic, planner-neutral move families are enumerated:

* a **simple sibling rotation** -- replace an over-represented sibling with an
  under-represented one at one home tournament (the gaining team gets one extra
  season participation, the losing team one fewer);
* a **coupled home + away swap** -- the same home rotation, paired with the
  reverse rotation at an away tournament in the same half of the season where
  the under-represented team already plays, so both siblings keep their exact
  half-season participation counts while the home split is rebalanced. This is
  the move the issue describes for the Ringerike U12 case.

A bounded greedy sequence of those moves is also exposed as a single option when
one swap does not fully balance the pool. Every option is hard-verified by the
independent verifier, must strictly reduce the pool's material home skew, and
carries the complete changed-team consequence set (spacing, coverage, opponent
repetition, travel and the affected club-pool participation). Acceptance uses
the family's narrower material set: a *swapped* sibling may not gain a
``<7``-day double, worsen a participation shortfall/hard maximum or materially
worsen season coverage, and the affected club pool may not deepen or
materially increase its total travel. Opponent-repetition and extra 7-13-day
gap changes stay visible as quality evidence and in the objective vector,
because changing which sibling represents the club necessarily changes opponent
identities for everyone in the tournament; they never force an individual
team's rule to be relaxed.

The provider is deliberately planner-neutral: it reads plain
``candidate``/``problem`` dicts, never changes a tournament's host/date/arena or
hosting responsibility, and commits nothing. The verified candidate is applied
through the ordinary revision-bound ``apply_repair``/``apply_repair_option``
boundary.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

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
    HALVES,
    SEASON_SCOPE,
    ParticipationEvaluation,
    club_pool_participation_regressions,
    club_pool_sibling_spread,
    evaluate_participation,
)
from .planning_contract import _parse_date, score_candidate, verify_candidate
from .quality_objectives import compare_quality_scores, with_unresolved_obligations_count
from .team_schedule_quality import compare_changed_team_schedule_consequence

HOME_REPRESENTATION_FINDING_PREFIX = "home_representation"

# Bounds so the family stays a localized rebalance, not a whole-season search.
DEFAULT_MAX_OPTIONS_PER_FINDING = 4
DEFAULT_MAX_HOME_TOURNAMENTS = 10
DEFAULT_MAX_AWAY_TOURNAMENTS = 10
DEFAULT_MAX_GREEDY_STEPS = 8
# Ceiling on how many distinct swap units a single finding may verify. The move
# generator is ordered by the most-skewed sibling pair first, so the budget
# cuts the long tail of equivalent low-value swaps rather than the promising
# repairs.
DEFAULT_MAX_MOVES_EVALUATED = 24

# Tier-2 bounded search: only runs when the tier-1 simple rotations, coupled
# home+away pairs and greedy plan produced no *acceptable* option. It reuses
# the same move-unit generator to build two-substitution coupled plans and
# bounded 3+ sibling cycles, so multi-tournament sibling rotations stay
# available when a direct pairwise move is insufficient -- without turning the
# family into a whole-season search.
DEFAULT_MAX_TIER2_UNITS = 16
DEFAULT_MAX_UNITS_PER_LABEL_PAIR = 2
DEFAULT_MAX_TIER2_PAIR_EVALUATED = 40
DEFAULT_MAX_TIER2_CYCLE_EVALUATED = 24

# Material consequences this family refuses to accept. A sibling swap is
# deliberately *narrower* than a placement/roster repair: it must not create a
# new same-weekend (<7-day) double, worsen a swapped team's participation
# shortfall or cross a permanent hard maximum, or materially worsen a swapped
# team's season coverage. Softer preferences (an extra 7-13 day gap, one more
# meeting with a familiar opponent) are reported as quality evidence and left
# to the shared objective vector instead of hard-blocking the repair, because
# changing *which* sibling represents the club necessarily changes opponent
# identities for everyone in the tournament. Travel is evaluated per affected
# club pool (a swap that merely redistributes the same total travel between
# siblings is not a regression).
MATERIAL_SWAP_REGRESSION_CODES = frozenset(
    {
        "participation_shortfall_worsened",
        "participation_hard_max_exceeded",
        "more_gaps_under_7_days",
        "temporal_coverage_materially_worse",
    }
)
MATERIAL_CLUB_TRAVEL_INCREASE_KM = 50
MATERIAL_CLUB_TRAVEL_INCREASE_RATIO = 0.25


def home_representation_finding_id(age_group: str, club: str) -> str:
    """Stable finding identity shared by findings and option enumeration."""
    return f"{HOME_REPRESENTATION_FINDING_PREFIX}:{age_group}:{club}"


def enumerate_home_representation_repairs(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str = "",
    scope: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return verified sibling-swap options for every skewed multi-team pool."""
    verification = verify_candidate(dict(candidate), dict(problem))
    fingerprint = candidate_fingerprint(candidate)
    rows = home_representation_rows(
        (problem or {}).get("teams") or [],
        (candidate or {}).get("tournaments") or [],
    )
    scope = scope or {}
    scope_age = str(scope.get("age_group") or "")
    scope_club = str(scope.get("club") or "")
    skewed = [
        row
        for row in rows
        if not row.get("balanced")
        and (not scope_age or row.get("age_group") == scope_age)
        and (not scope_club or row.get("club") == scope_club)
    ]
    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    seen_swaps: set = set()
    for row in skewed:
        finding_options, finding_rejections = _finding_options(
            candidate, problem, row, fingerprint, verification
        )
        for option in finding_options:
            signature = _swap_signature(option.arguments.get("swaps") or [])
            if signature in seen_swaps:
                continue
            seen_swaps.add(signature)
            options.append(option)
        rejected.extend(finding_rejections)
    options.sort(key=_option_rank)
    if len(options) > DEFAULT_MAX_OPTIONS_PER_FINDING * max(1, len(skewed)):
        options = options[: DEFAULT_MAX_OPTIONS_PER_FINDING * max(1, len(skewed))]
    return {
        "run_id": run_id,
        "candidate_fingerprint": fingerprint,
        "verification": dict(verification),
        "applicable": bool(skewed),
        "applicable_findings": [
            home_representation_finding_id(str(row["age_group"]), str(row["club"]))
            for row in skewed
        ],
        "options": [option.to_dict() for option in options],
        "rejected_candidates": rejected,
    }


def apply_home_representation_repair_option(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    option_id: str,
    expected_fingerprint: str,
    run_id: str = "",
    arguments: Optional[Mapping[str, Any]] = None,
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
    swaps = list((arguments or {}).get("swaps") or [])
    if not swaps:
        return {"ok": False, "reason": "unknown_or_stale_option", "before_fingerprint": before}
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
    if problem:
        consequences = changed_team_consequences(
            candidate, trial, problem, swapped_identities=_swapped_identities(swaps)
        )
        if not consequences["consequence_acceptable"]:
            return {
                "ok": False,
                "reason": "team_schedule_regression",
                "before_fingerprint": before,
                "consequences": consequences,
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


def _finding_options(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    row: Mapping[str, Any],
    fingerprint: str,
    before_verification: Mapping[str, Any],
) -> Tuple[List[RepairOption], List[Dict[str, Any]]]:
    club = str(row["club"])
    age_group = str(row["age_group"])
    finding_id = home_representation_finding_id(age_group, club)
    rejected: List[Dict[str, Any]] = []
    options: List[RepairOption] = []

    # A single swap unit (simple rotation or coupled home+away pair) is the
    # smallest reviewable repair; keep the best few visible so an operator can
    # pick the one with the least collateral change.
    evaluated = 0
    for swaps in _candidate_moves(candidate, problem, row):
        if evaluated >= DEFAULT_MAX_MOVES_EVALUATED:
            break
        evaluated += 1
        trial = _apply_swaps(candidate, problem, swaps)
        if trial is None:
            continue
        option, reason = _evaluate_move(
            candidate,
            trial,
            problem,
            row,
            finding_id,
            fingerprint,
            swaps,
            before_verification,
        )
        if option is not None:
            options.append(option)
        elif reason is not None:
            rejected.append(reason)

    # The coupled greedy sequence is the repair the issue describes: chain the
    # lowest-impact moves until the pool is balanced (or no legal move remains)
    # and expose the whole plan as one option.
    greedy = _greedy_option(candidate, problem, row, finding_id, fingerprint)
    if greedy is not None:
        options.append(greedy)

    # Tier 2: only widen into the bounded coupled/cycle neighborhood when
    # nothing acceptable was found among the cheap tier-1 moves.
    if not any(option.effects.get("consequence_acceptable") for option in options):
        tier2_options, tier2_rejections = _tier2_options(
            candidate, problem, row, finding_id, fingerprint, before_verification
        )
        options.extend(tier2_options)
        rejected.extend(tier2_rejections)

    if not options:
        rejected.append(
            {
                "finding_id": finding_id,
                "club": club,
                "age_group": age_group,
                "reason": "no_verified_sibling_swap",
                "home_appearances": dict(row.get("home_appearances") or {}),
            }
        )
    return options, rejected


def _evaluate_move(
    before: Mapping[str, Any],
    trial: Mapping[str, Any],
    problem: Mapping[str, Any],
    row: Mapping[str, Any],
    finding_id: str,
    fingerprint: str,
    swaps: Sequence[Mapping[str, Any]],
    before_verification: Mapping[str, Any],
) -> Tuple[Optional[RepairOption], Optional[Dict[str, Any]]]:
    club = str(row["club"])
    age_group = str(row["age_group"])
    base = {
        "finding_id": finding_id,
        "club": club,
        "age_group": age_group,
        "swaps": [
            {
                "tournament_id": str(swap.get("tournament_id") or ""),
                "remove": swap.get("remove"),
                "add": swap.get("add"),
            }
            for swap in swaps
        ],
    }
    verification = verify_candidate(dict(trial), dict(problem))
    if not verification.get("ok"):
        return None, {**base, "reason": _primary_violation_reason(verification), "violations": _codes(verification)}
    after_row = _pool_row(problem, trial, club, age_group)
    if after_row is None:
        return None, {**base, "reason": "pool_missing_after_swap"}
    if int(after_row.get("material_spread") or 0) >= int(row.get("material_spread") or 0):
        return None, {**base, "reason": "no_home_representation_improvement"}
    consequences = changed_team_consequences(
        before, trial, problem, swapped_identities=_swapped_identities(swaps)
    )
    changed_ids = consequences["changed_tournament_ids"]
    option_id = (
        f"{fingerprint[:12]}:{HOME_REPRESENTATION_FINDING_PREFIX}:{age_group}:{club}:"
        f"home_swap:{_swap_tag(swaps)}"
    )
    effects: Dict[str, Any] = {
        "home_spread_before": int(row.get("spread") or 0),
        "home_spread_after": int(after_row.get("spread") or 0),
        "material_spread_before": int(row.get("material_spread") or 0),
        "material_spread_after": int(after_row.get("material_spread") or 0),
        "home_appearances_before": dict(row.get("home_appearances") or {}),
        "home_appearances_after": dict(after_row.get("home_appearances") or {}),
        "changed_tournament_ids": changed_ids,
        "changed_tournament_count": len(changed_ids),
        "participant_swaps": len(swaps),
        "quality_regression_count": _quality_regression_count(before, trial, problem),
        "consequence_acceptable": bool(consequences["consequence_acceptable"]),
        "material_team_regressions": consequences["material_team_regressions"],
        "club_pool_regressions": consequences["club_pool_regressions"],
        "affected_teams": consequences["affected_teams"],
    }
    return (
        RepairOption(
            option_id=option_id,
            finding_id=finding_id,
            action="sibling_swaps",
            tournament_id=str(swaps[0].get("tournament_id") or ""),
            arguments={"swaps": [dict(swap) for swap in swaps]},
            hard_feasible=True,
            effects=effects,
            evidence={
                "verification_ok": True,
                "home_representation_evidence": after_row.get("evidence"),
                "host_responsibility_unchanged": True,
            },
        ),
        None,
    )


def _greedy_option(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    row: Mapping[str, Any],
    finding_id: str,
    fingerprint: str,
) -> Optional[RepairOption]:
    club = str(row["club"])
    age_group = str(row["age_group"])
    work = copy.deepcopy(dict(candidate))
    applied: List[Dict[str, Any]] = []
    for _step in range(DEFAULT_MAX_GREEDY_STEPS):
        current_row = _pool_row(problem, work, club, age_group)
        if current_row is None or int(current_row.get("material_spread") or 0) == 0:
            break
        chosen: Optional[List[Dict[str, Any]]] = None
        chosen_trial: Optional[Dict[str, Any]] = None
        best_rank: Optional[Tuple[int, int, int]] = None
        evaluated = 0
        for swaps in _candidate_moves(work, problem, current_row):
            if evaluated >= DEFAULT_MAX_MOVES_EVALUATED:
                break
            evaluated += 1
            trial = _apply_swaps(work, problem, swaps)
            if trial is None:
                continue
            verification = verify_candidate(dict(trial), dict(problem))
            if not verification.get("ok"):
                continue
            after_row = _pool_row(problem, trial, club, age_group)
            if after_row is None:
                continue
            improvement = int(current_row.get("material_spread") or 0) - int(
                after_row.get("material_spread") or 0
            )
            if improvement <= 0:
                continue
            # Prefer a move whose shared quality vector does not regress at all
            # (the Ringerike repair has such a plan); fall back to the largest
            # home-skew improvement among any remaining legal moves.
            consequences = changed_team_consequences(
                work, trial, problem, swapped_identities=_swapped_identities(swaps)
            )
            if not consequences["consequence_acceptable"]:
                continue
            regressions = _quality_regression_count(work, trial, problem)
            rank = (regressions, -improvement, len(swaps))
            if best_rank is None or rank < best_rank:
                best_rank = rank
                chosen = swaps
                chosen_trial = trial
        if chosen is None or chosen_trial is None:
            break
        work = chosen_trial
        applied.extend(chosen)
    if not applied:
        return None
    verification = verify_candidate(dict(work), dict(problem))
    if not verification.get("ok"):
        return None
    final_row = _pool_row(problem, work, club, age_group)
    if final_row is None or int(final_row.get("material_spread") or 0) >= int(
        row.get("material_spread") or 0
    ):
        return None
    consequences = changed_team_consequences(
        candidate, work, problem, swapped_identities=_swapped_identities(applied)
    )
    changed_ids = consequences["changed_tournament_ids"]
    option_id = (
        f"{fingerprint[:12]}:{HOME_REPRESENTATION_FINDING_PREFIX}:{age_group}:{club}:"
        f"home_plan:{_swap_tag(applied)}"
    )
    return RepairOption(
        option_id=option_id,
        finding_id=finding_id,
        action="sibling_swaps",
        tournament_id=str(applied[0].get("tournament_id") or ""),
        arguments={"swaps": [dict(swap) for swap in applied]},
        hard_feasible=True,
        effects={
            "home_spread_before": int(row.get("spread") or 0),
            "home_spread_after": int(final_row.get("spread") or 0),
            "material_spread_before": int(row.get("material_spread") or 0),
            "material_spread_after": int(final_row.get("material_spread") or 0),
            "home_appearances_before": dict(row.get("home_appearances") or {}),
            "home_appearances_after": dict(final_row.get("home_appearances") or {}),
            "changed_tournament_ids": changed_ids,
            "changed_tournament_count": len(changed_ids),
            "participant_swaps": len(applied),
            "coupled_plan": True,
            "quality_regression_count": _quality_regression_count(candidate, work, problem),
            "consequence_acceptable": bool(consequences["consequence_acceptable"]),
            "material_team_regressions": consequences["material_team_regressions"],
            "club_pool_regressions": consequences["club_pool_regressions"],
            "affected_teams": consequences["affected_teams"],
        },
        evidence={
            "verification_ok": True,
            "home_representation_evidence": final_row.get("evidence"),
            "host_responsibility_unchanged": True,
        },
    )


def _diverse_units(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    row: Mapping[str, Any],
    limit: int,
    *,
    per_label_pair: int = DEFAULT_MAX_UNITS_PER_LABEL_PAIR,
) -> List[List[Dict[str, Any]]]:
    """Bound ``_candidate_moves`` to a *diverse* sample across (over, under) pairs.

    ``_candidate_moves`` is ordered most-skewed-pair-first and can enumerate
    many home/away combinations for a single (over, under) label pair before
    ever reaching the next one. A pool that needs two *independent* label
    pairs rebalanced in the same candidate (for example four sibling teams at
    3/1/1/3, which needs both a 3->1 move and the other 3->1 move to reach an
    even split) would otherwise never see the second pair inside a truncated
    prefix. Capping per label pair instead keeps the tier-2 pairwise search
    bounded while still covering every distinct over/under pair.
    """
    grouped: Dict[Tuple[str, str], List[List[Dict[str, Any]]]] = {}
    for swaps in _candidate_moves(plan, problem, row):
        key = (str(swaps[0]["remove"]["label"]), str(swaps[0]["add"]["label"]))
        bucket = grouped.setdefault(key, [])
        if len(bucket) < per_label_pair:
            bucket.append(swaps)
    units: List[List[Dict[str, Any]]] = []
    for key in sorted(grouped):
        units.extend(grouped[key])
    return units[:limit]


def _tier2_options(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    row: Mapping[str, Any],
    finding_id: str,
    fingerprint: str,
    before_verification: Mapping[str, Any],
) -> Tuple[List[RepairOption], List[Dict[str, Any]]]:
    """Bounded coupled-pair and 3+ sibling cycle search for one skewed pool.

    Reuses the same swap-unit generator and evaluation as tier 1; only the
    combination strategy differs, so acceptance stays governed by the single
    ``_evaluate_move`` gate (strict material-spread improvement, hard
    verification, and the family's consequence policy).
    """
    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    units = _diverse_units(candidate, problem, row, DEFAULT_MAX_TIER2_UNITS)

    evaluated = 0
    for first_index in range(len(units)):
        for second_index in range(first_index + 1, len(units)):
            if evaluated >= DEFAULT_MAX_TIER2_PAIR_EVALUATED:
                break
            evaluated += 1
            combined = list(units[first_index]) + list(units[second_index])
            trial = _apply_swaps(candidate, problem, combined)
            if trial is None:
                continue
            option, reason = _evaluate_move(
                candidate,
                trial,
                problem,
                row,
                finding_id,
                fingerprint,
                combined,
                before_verification,
            )
            if option is not None:
                options.append(option)
            elif reason is not None:
                rejected.append({**reason, "coupled_pair": True})
        if evaluated >= DEFAULT_MAX_TIER2_PAIR_EVALUATED:
            break

    evaluated = 0
    for swaps in _cycle_moves(candidate, problem, row):
        if evaluated >= DEFAULT_MAX_TIER2_CYCLE_EVALUATED:
            break
        evaluated += 1
        trial = _apply_swaps(candidate, problem, swaps)
        if trial is None:
            continue
        option, reason = _evaluate_move(
            candidate,
            trial,
            problem,
            row,
            finding_id,
            fingerprint,
            swaps,
            before_verification,
        )
        if option is not None:
            options.append(option)
        elif reason is not None:
            rejected.append({**reason, "sibling_cycle": True})

    return options, rejected


# ---------------------------------------------------------------------------
# Move generation
# ---------------------------------------------------------------------------


def _candidate_moves(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    row: Mapping[str, Any],
) -> Iterable[List[Dict[str, Any]]]:
    """Yield deterministic single-swap units that can raise an under-sibling.

    Coupled home + away swaps are yielded before simple rotations because they
    keep both siblings' half-season participation counts unchanged, which is the
    lower-impact repair whenever a compensating away tournament exists.
    """
    club = str(row["club"])
    age_group = str(row["age_group"])
    appearances = dict(row.get("home_appearances") or {})
    if len(appearances) < 2:
        return
    home_tournaments = _home_tournaments(plan, club, age_group)
    away_tournaments = _away_tournaments(plan, club, age_group)

    over_labels = sorted(
        (label for label in appearances if appearances[label] > row.get("min_appearances", 0)),
        key=lambda label: (-appearances[label], label),
    )
    under_labels = sorted(
        (label for label in appearances if appearances[label] < row.get("max_appearances", 0)),
        key=lambda label: (appearances[label], label),
    )
    for over in over_labels:
        for under in under_labels:
            if over == under:
                continue
            home_targets = [
                tournament
                for tournament in home_tournaments
                if _participates(tournament, club, age_group, over)
                and not _participates(tournament, club, age_group, under)
                and not _plays_on_date(plan, club, age_group, under, tournament)
            ][:DEFAULT_MAX_HOME_TOURNAMENTS]
            for home in home_targets:
                home_swap = _swap(home, over, under, club, age_group)
                coupled = 0
                for away in away_tournaments:
                    if _participates(away, club, age_group, over):
                        continue
                    if not _participates(away, club, age_group, under):
                        continue
                    if _plays_on_date(plan, club, age_group, over, away):
                        continue
                    if not _same_half(problem, home, away):
                        continue
                    if coupled >= DEFAULT_MAX_AWAY_TOURNAMENTS:
                        break
                    coupled += 1
                    yield [home_swap, _swap(away, under, over, club, age_group)]
                yield [home_swap]


def _cycle_moves(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    row: Mapping[str, Any],
) -> Iterable[List[Dict[str, Any]]]:
    """Yield bounded 3-sibling participation-preserving home-representation cycles.

    ``home: A -> B``, ``away: B -> C``, ``away: C -> A`` -- every sibling's own
    participation count is unchanged (A trades one home appearance for one away
    appearance, B and C's own totals are each net zero), only the home split
    moves. Used when no direct pairwise move between the two most-skewed
    siblings exists but a third sibling can act as the compensating buffer.
    """
    club = str(row["club"])
    age_group = str(row["age_group"])
    appearances = dict(row.get("home_appearances") or {})
    labels = list(appearances)
    if len(labels) < 3:
        return
    home_tournaments = _home_tournaments(plan, club, age_group)
    away_tournaments = _away_tournaments(plan, club, age_group)

    over_labels = sorted(
        (label for label in appearances if appearances[label] > row.get("min_appearances", 0)),
        key=lambda label: (-appearances[label], label),
    )
    under_labels = sorted(
        (label for label in appearances if appearances[label] < row.get("max_appearances", 0)),
        key=lambda label: (appearances[label], label),
    )
    for over in over_labels:
        for under in under_labels:
            if over == under:
                continue
            homes = [
                tournament
                for tournament in home_tournaments
                if _participates(tournament, club, age_group, over)
                and not _participates(tournament, club, age_group, under)
                and not _plays_on_date(plan, club, age_group, under, tournament)
            ][:DEFAULT_MAX_HOME_TOURNAMENTS]
            for home in homes:
                home_swap = _swap(home, over, under, club, age_group)
                for buffer_label in labels:
                    if buffer_label in (over, under):
                        continue
                    seconds = [
                        tournament
                        for tournament in away_tournaments
                        if _participates(tournament, club, age_group, under)
                        and not _participates(tournament, club, age_group, buffer_label)
                        and not _plays_on_date(plan, club, age_group, buffer_label, tournament)
                        and _same_half(problem, home, tournament)
                    ]
                    for second in seconds:
                        second_swap = _swap(second, under, buffer_label, club, age_group)
                        thirds = [
                            tournament
                            for tournament in away_tournaments
                            if str(tournament.get("id") or "") != str(second.get("id") or "")
                            and _participates(tournament, club, age_group, buffer_label)
                            and not _participates(tournament, club, age_group, over)
                            and not _plays_on_date(plan, club, age_group, over, tournament)
                            and _same_half(problem, home, tournament)
                        ]
                        for third in thirds:
                            third_swap = _swap(third, buffer_label, over, club, age_group)
                            yield [home_swap, second_swap, third_swap]


def _home_tournaments(
    plan: Mapping[str, Any], club: str, age_group: str
) -> List[Mapping[str, Any]]:
    return [
        tournament
        for tournament in plan.get("tournaments", []) or []
        if not tournament.get("cancelled")
        and str(tournament.get("age_group") or "") == age_group
        and clubs_represent_same_club(str(tournament.get("host_club") or ""), club)
    ]


def _away_tournaments(
    plan: Mapping[str, Any], club: str, age_group: str
) -> List[Mapping[str, Any]]:
    return [
        tournament
        for tournament in plan.get("tournaments", []) or []
        if not tournament.get("cancelled")
        and str(tournament.get("age_group") or "") == age_group
        and not clubs_represent_same_club(str(tournament.get("host_club") or ""), club)
    ]


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
    t_date = _parse_date(tournament.get("date"))
    if t_date is None:
        return False
    for other in plan.get("tournaments", []) or []:
        if str(other.get("id")) == str(tournament.get("id")):
            continue
        if _parse_date(other.get("date")) != t_date:
            continue
        if _participates(other, club, age_group, label):
            return True
    return False


def _same_half(problem: Mapping[str, Any], left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    from tournament_scheduler import planning_half

    split = _split_date(problem)
    left_half = planning_half.tournament_half(_parse_date(left.get("date")), split)
    right_half = planning_half.tournament_half(_parse_date(right.get("date")), split)
    return left_half == right_half and left_half in ("before_christmas", "after_christmas")


def _swap(
    tournament: Mapping[str, Any], remove: str, add: str, club: str, age_group: str
) -> Dict[str, Any]:
    return {
        "tournament_id": str(tournament.get("id") or ""),
        "remove": {"club": club, "label": remove, "age_group": age_group},
        "add": {"club": club, "label": add, "age_group": age_group},
    }


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
        parts.append(
            f"{swap.get('tournament_id')}-{remove.get('label')}-{add.get('label')}"
        )
    return _slug(tuple(parts))


# ---------------------------------------------------------------------------
# Mutation / consequences
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
    if tournament is None:
        return False
    remove_identity = _identity(swap.get("remove") or {})
    add_identity = _identity(swap.get("add") or {})
    teams = [team for team in tournament.get("teams", []) or [] if _identity(team) != remove_identity]
    if not any(_identity(team) == remove_identity for team in tournament.get("teams", []) or []):
        return False
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


def _pool_row(
    problem: Mapping[str, Any], plan: Mapping[str, Any], club: str, age_group: str
) -> Optional[Dict[str, Any]]:
    for row in home_representation_rows(
        problem.get("teams") or [], plan.get("tournaments") or []
    ):
        if row.get("club") == club and row.get("age_group") == age_group:
            return row
    return None


def changed_team_consequences(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    swapped_identities: Iterable[Tuple[str, str, str]] | None = None,
) -> Dict[str, Any]:
    """Complete changed-team consequence set for a sibling-swap candidate.

    Every team whose tournament membership changed is evaluated with the
    canonical per-team policy (``compare_changed_team_schedule_consequence``),
    using ``retained`` for a coupled swap that leaves the team's participation
    count intact and ``added``/``removed`` for a simple rotation. The affected
    club x age-group player pools are re-evaluated with the canonical
    club-pool classification so a swap that deepens the club's aggregate
    deficit is a material regression even when every individual team looks fine.

    ``consequence_acceptable`` uses the family's narrower material set (see
    ``MATERIAL_SWAP_REGRESSION_CODES``): the *swapped* teams may not gain a
    <7-day double, worsen a participation shortfall/hard maximum or materially
    worsen season coverage, and the affected club pool may not deepen and may
    not materially increase its total travel. Opponent-diversity and extra
    7-13-day-gap changes stay in ``material_team_regressions`` as evidence and
    feed the shared objective vector, but do not hard-block a sibling swap.

    A swapped sibling's own ``participation_shortfall_worsened`` is exempt from
    that material set -- see :func:`_sibling_redistribution_exempt` -- when the
    club-pool aggregate is unchanged and the sibling distribution spread is
    equal or better; a genuinely deepened club-pool shortfall is still caught
    by ``club_pool_regressions`` regardless.
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
    before_eval = evaluate_participation(before, problem)
    after_eval = evaluate_participation(after, problem)
    pool_regressions = club_pool_participation_regressions(
        before_eval, after_eval, club_age_pairs=club_age_pairs
    )
    swapped = {tuple(identity) for identity in (swapped_identities or [])}
    strict_material: List[Dict[str, Any]] = []
    for key, analysis in team_consequences.items():
        identity = tuple(key.split("|"))
        if swapped and identity not in swapped:
            continue
        for regression in analysis.get("material_regressions") or []:
            code = regression.get("code")
            if code == "participation_shortfall_worsened" and _sibling_redistribution_exempt(
                identity, before_eval, after_eval
            ):
                continue
            if code in MATERIAL_SWAP_REGRESSION_CODES:
                strict_material.append(
                    {
                        "team": analysis["team"],
                        "membership_role": analysis.get("membership_role"),
                        "regression": regression,
                    }
                )
    club_travel_regressions = _club_pool_travel_regressions(
        before, after, problem, club_age_pairs
    )
    acceptable = not strict_material and not pool_regressions and not club_travel_regressions
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
        "material_team_regressions": [
            {
                "team": analysis["team"],
                "membership_role": analysis.get("membership_role"),
                "regressions": analysis.get("material_regressions") or [],
            }
            for analysis in team_consequences.values()
            if analysis.get("material_regressions")
        ],
        "club_pool_regressions": pool_regressions,
        "strict_material_regressions": strict_material,
        "club_pool_travel_regressions": club_travel_regressions,
        "consequence_acceptable": acceptable,
    }


def _swapped_identities(swaps: Sequence[Mapping[str, Any]]) -> List[Tuple[str, str, str]]:
    identities: List[Tuple[str, str, str]] = []
    for swap in swaps:
        for side in ("remove", "add"):
            identity = _identity(swap.get(side) or {})
            if identity not in identities:
                identities.append(identity)
    return identities


def _club_pool_for(
    evaluation: ParticipationEvaluation, club: str, age_group: str, scope: str
) -> Optional[Mapping[str, Any]]:
    for pool in evaluation.club_pools:
        if (
            str(pool.get("club") or "") == club
            and str(pool.get("age_group") or "") == age_group
            and str(pool.get("scope") or "") == scope
        ):
            return pool
    return None


def _sibling_redistribution_exempt(
    identity: Tuple[str, str, str],
    before_eval: ParticipationEvaluation,
    after_eval: ParticipationEvaluation,
) -> bool:
    """Whether a swapped sibling's own ``participation_shortfall_worsened`` is exempt.

    ``compare_changed_team_schedule_consequence`` flags a "removed" team's own
    per-team shortfall growing as material -- correct for a roster repair that
    drops a team's participation outright, but too conservative for a
    same-club/same-age sibling swap that merely moves *which* label represents
    the club: the losing sibling's individual target miss is exactly offset by
    the gaining sibling's improvement, so the club-pool aggregate the operator
    actually cares about is unaffected.

    This narrow exception applies -- at every scope (season and each
    before/after-Christmas half) the swap actually touched -- only when all of
    the following hold, using the canonical club-pool classification/spread
    instead of a second sibling-balance model:

    * the club x age-group pool has more than one registered team (a
      single-team club's own shortfall is never redistributable and stays
      material, matching ``SINGLE_TEAM_DEVIATION`` semantics);
    * the pool's aggregate actual participation is exactly unchanged;
    * the pool's per-sibling spread (:func:`club_pool_sibling_spread`) did not
      increase -- the redistribution must be equal-or-better, never a materially
      worse sibling split.

    A genuine deepened club-pool shortfall is still caught independently by
    ``club_pool_participation_regressions`` in the caller, and a hard-maximum
    breach is a separate, never-exempt regression code.
    """
    club, _, age_group = identity
    checked = False
    for scope in (SEASON_SCOPE,) + HALVES:
        before_pool = _club_pool_for(before_eval, club, age_group, scope)
        after_pool = _club_pool_for(after_eval, club, age_group, scope)
        if before_pool is None or after_pool is None:
            continue
        checked = True
        if int(before_pool.get("registered_team_count") or 0) < 2:
            return False
        if int(before_pool.get("club_pool_actual") or 0) != int(after_pool.get("club_pool_actual") or 0):
            return False
        if club_pool_sibling_spread(after_pool) > club_pool_sibling_spread(before_pool):
            return False
    return checked


def _club_pool_travel_regressions(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    problem: Mapping[str, Any],
    club_age_pairs: Iterable[Tuple[str, str]],
) -> List[Dict[str, Any]]:
    """A club x age pool whose combined season travel materially increased.

    A sibling swap redistributes travel between the two sibling teams; the
    club pool's total is the right object, so an offsetting redistribution is
    not a regression.
    """
    from .team_schedule_quality import team_schedule_profile

    out: List[Dict[str, Any]] = []
    for club, age_group in sorted(set(club_age_pairs)):
        if not club or not age_group:
            continue
        identities = {
            _identity(team)
            for team in problem.get("teams", []) or []
            if str(team.get("club") or "") == club
            and str(team.get("age_group") or "") == age_group
        }
        before_km = sum(
            int(team_schedule_profile(before, identity, problem=problem).get("travel_km") or 0)
            for identity in identities
        )
        after_km = sum(
            int(team_schedule_profile(after, identity, problem=problem).get("travel_km") or 0)
            for identity in identities
        )
        delta = after_km - before_km
        ratio = (delta / before_km) if before_km > 0 else None
        if delta >= MATERIAL_CLUB_TRAVEL_INCREASE_KM and (
            before_km == 0
            or ratio is not None
            and ratio >= MATERIAL_CLUB_TRAVEL_INCREASE_RATIO
        ):
            out.append(
                {
                    "club": club,
                    "age_group": age_group,
                    "travel_km_before": before_km,
                    "travel_km_after": after_km,
                    "delta_km": delta,
                    "relative_increase": ratio,
                }
            )
    return out


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


def _option_rank(option: RepairOption) -> Tuple[int, int, int, int, str]:
    effects = option.effects or {}
    rejected = 0 if effects.get("consequence_acceptable", True) else 1
    quality = int(effects.get("quality_regression_count", 0) or 0)
    improvement = int(effects.get("material_spread_before", 0) or 0) - int(
        effects.get("material_spread_after", 0) or 0
    )
    return (
        rejected,
        quality,
        -improvement,
        int(effects.get("participant_swaps", 0) or 0),
        option.option_id,
    )


__all__ = [
    "HOME_REPRESENTATION_FINDING_PREFIX",
    "apply_home_representation_repair_option",
    "changed_team_consequences",
    "enumerate_home_representation_repairs",
    "home_representation_finding_id",
]
