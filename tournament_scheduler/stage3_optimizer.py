"""Generic Stage 3 v2 candidate optimizer (issue #257, scope item 5).

This is the "generic solver" side of the target architecture in issue #257:
a harness-neutral, non-hockey-specific local-search *repair* pass over an
existing candidate plan. It does not depend on ``SeasonPlanner`` internals
and makes no LLM calls, so :func:`verify_candidate` and :func:`score_candidate`
remain the sole authority on whether its output is acceptable.

Design (matches the "generic repair/optimization pass" described in the
issue, which reassigned teams within a fixed schedule and struck a strictly
better balance of opponent repetition, turnaround spacing and same-club
clustering than the baseline planner):

- The tournament *skeleton* — dates, arenas, hosts, and how many teams each
  tournament holds — is taken as given from the input candidate. This
  optimizer does not invent tournament slots; it only decides which teams
  fill them.
- Within each age group, teams are swapped one-for-one between tournaments
  via simulated annealing, so every team's total participation count and
  every tournament's roster size stay exactly as in the input (the hard
  requirements :func:`verify_candidate` checks for these are preserved by
  construction, not re-derived here).
- After the search settles, games for any changed tournament are
  regenerated with the existing generic round-robin generator
  (:func:`tournament_scheduler.game_generation.generate_round_robin_games`),
  keeping the same host-club-plays-at-home convention Stage 3 already uses.

Exposed as an explicit CLI command (``rvv-miniputt plan optimize``, see
``cli/plan_command.py``) — never invoked implicitly — so it stays strictly
opt-in per issue #257's "behind a feature flag or explicit command"
requirement.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from .game_generation import generate_round_robin_games
from .models import Team
from .planning_contract import CANDIDATE_SCHEMA_VERSION, _parse_date, _team_identity

TeamIdentity = Tuple[str, str, str]

DEFAULT_WEIGHTS: Dict[str, float] = {
    "pair_repeat": 3.0,
    "same_club_pairing": 1.0,
    "same_club_cluster": 2.0,
    "gap_under_7": 5.0,
    "gap_under_14": 1.0,
}


@dataclass
class _Slot:
    """Mutable per-tournament view used during the search."""

    tournament: Dict[str, Any]
    date: date
    age_group: str
    host_club: Optional[str]
    parallel_games: int
    team_ids: List[TeamIdentity] = field(default_factory=list)
    changed: bool = False


def _infer_parallel_games(tournament: Dict[str, Any], problem: Optional[Dict[str, Any]]) -> int:
    if problem:
        pg = (problem.get("parallel_games") or {}).get(tournament.get("age_group"))
        if isinstance(pg, int) and pg > 0:
            return pg
    games = tournament.get("games") or []
    if games:
        return max(g.get("parallel_slot", 0) for g in games) + 1
    return 1


def _build_slots(
    candidate: Dict[str, Any], problem: Optional[Dict[str, Any]]
) -> Tuple[List[_Slot], List[Dict[str, Any]]]:
    """Split a candidate's tournaments into optimizable slots vs. untouched (cancelled) ones."""
    slots: List[_Slot] = []
    untouched: List[Dict[str, Any]] = []
    for tournament in candidate.get("tournaments", []):
        if tournament.get("cancelled"):
            untouched.append(tournament)
            continue
        t_date = _parse_date(tournament.get("date"))
        if t_date is None:
            untouched.append(tournament)
            continue
        slots.append(
            _Slot(
                tournament=tournament,
                date=t_date,
                age_group=tournament.get("age_group", ""),
                host_club=tournament.get("host_club"),
                parallel_games=_infer_parallel_games(tournament, problem),
                team_ids=[_team_identity(t) for t in tournament.get("teams", [])],
            )
        )
    return slots, untouched


def _pair_counts(slots: List[_Slot]) -> Dict[Tuple[TeamIdentity, TeamIdentity], int]:
    counts: Dict[Tuple[TeamIdentity, TeamIdentity], int] = {}
    for slot in slots:
        ids = slot.team_ids
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pair = tuple(sorted((ids[i], ids[j])))
                counts[pair] = counts.get(pair, 0) + 1
    return counts


def _resolve_weights(
    base_weights: Dict[str, float],
    per_age_group: Optional[Dict[str, Dict[str, float]]],
    age_group: str,
) -> Dict[str, float]:
    resolved = dict(base_weights)
    if per_age_group and age_group in per_age_group:
        resolved.update(per_age_group[age_group])
    return resolved


def _objective(
    slots: List[_Slot],
    base_weights: Dict[str, float],
    per_age_group: Optional[Dict[str, Dict[str, float]]] = None,
) -> float:
    total = 0.0

    # Pairs, clustering and gaps are only ever computed *within* a single age
    # group (teams from different age groups never share a tournament or a
    # participation target), so each metric can be weighted per age group by
    # attributing it to the age group its slot(s) belong to.
    pair_counts = _pair_counts(slots)
    slots_by_age_group: Dict[str, List[_Slot]] = {}
    for slot in slots:
        slots_by_age_group.setdefault(slot.age_group, []).append(slot)

    age_group_by_team: Dict[TeamIdentity, str] = {
        identity: slot.age_group for slot in slots for identity in slot.team_ids
    }

    for (a, b), count in pair_counts.items():
        age_group = age_group_by_team.get(a, "")
        weights = _resolve_weights(base_weights, per_age_group, age_group)
        if count > 1:
            total += weights["pair_repeat"] * (count - 1) ** 2
        if a[0] == b[0]:
            total += weights["same_club_pairing"] * count

    for slot in slots:
        weights = _resolve_weights(base_weights, per_age_group, slot.age_group)
        club_counts: Dict[str, int] = {}
        for identity in slot.team_ids:
            club_counts[identity[0]] = club_counts.get(identity[0], 0) + 1
        for club_count in club_counts.values():
            if club_count > 1:
                total += weights["same_club_cluster"] * (club_count - 1) ** 2

    dates_by_team: Dict[TeamIdentity, List[date]] = {}
    for slot in slots:
        for identity in slot.team_ids:
            dates_by_team.setdefault(identity, []).append(slot.date)
    for identity, dates in dates_by_team.items():
        weights = _resolve_weights(base_weights, per_age_group, age_group_by_team.get(identity, ""))
        dates.sort()
        for prev, nxt in zip(dates, dates[1:]):
            gap = (nxt - prev).days
            if gap < 7:
                total += weights["gap_under_7"]
            elif gap < 14:
                total += weights["gap_under_14"]

    return total


def _candidate_swaps(
    slots: List[_Slot], rng: random.Random
) -> Optional[Tuple[int, int, int, int]]:
    """Pick a random pair of (slot_index, team_position) to swap.

    Returns ``None`` when no swap is possible (e.g. a single-tournament age
    group). Only considers swaps between two *different* tournaments in the
    same age group, since participation targets and roster sizes are only
    meaningful to preserve within an age group.
    """
    by_age_group: Dict[str, List[int]] = {}
    for index, slot in enumerate(slots):
        if slot.team_ids:
            by_age_group.setdefault(slot.age_group, []).append(index)

    candidates = [indices for indices in by_age_group.values() if len(indices) >= 2]
    if not candidates:
        return None
    indices = rng.choice(candidates)
    slot_a, slot_b = rng.sample(indices, 2)
    pos_a = rng.randrange(len(slots[slot_a].team_ids))
    pos_b = rng.randrange(len(slots[slot_b].team_ids))
    return slot_a, pos_a, slot_b, pos_b


def _swap_is_valid(slots: List[_Slot], slot_a: int, pos_a: int, slot_b: int, pos_b: int) -> bool:
    a, b = slots[slot_a], slots[slot_b]
    team_a = a.team_ids[pos_a]
    team_b = b.team_ids[pos_b]
    if team_a == team_b:
        return False
    # Neither team may end up appearing twice in its new tournament.
    if team_a in b.team_ids or team_b in a.team_ids:
        return False
    # Neither team may end up double-booked on the other tournament's date.
    if a.date != b.date:
        for slot in slots:
            if slot is a or slot is b:
                continue
            if slot.date == b.date and team_a in slot.team_ids:
                return False
            if slot.date == a.date and team_b in slot.team_ids:
                return False
    return True


def _apply_swap(slots: List[_Slot], slot_a: int, pos_a: int, slot_b: int, pos_b: int) -> None:
    a, b = slots[slot_a], slots[slot_b]
    a.team_ids[pos_a], b.team_ids[pos_b] = b.team_ids[pos_b], a.team_ids[pos_a]
    a.changed = True
    b.changed = True


def _rebuild_tournament(slot: _Slot) -> Dict[str, Any]:
    """Regenerate a slot's ``teams``/``games`` from its (possibly swapped) team_ids."""
    teams = [Team(club=club, label=label, age_group=age_group) for club, label, age_group in slot.team_ids]
    if slot.host_club:
        host_teams = [t for t in teams if t.club == slot.host_club]
        other_teams = [t for t in teams if t.club != slot.host_club]
        if host_teams:
            teams = host_teams + other_teams

    games = generate_round_robin_games(teams, slot.parallel_games)

    tournament = dict(slot.tournament)
    tournament["teams"] = [
        {"club": t.club, "label": t.label, "age_group": t.age_group} for t in teams
    ]
    tournament["games"] = [
        {
            "home": g.home.label,
            "away": g.away.label,
            "parallel_slot": g.parallel_slot,
            "round_number": g.round_number,
        }
        for g in games
    ]
    return tournament


def optimize_candidate(
    candidate: Dict[str, Any],
    problem: Optional[Dict[str, Any]] = None,
    *,
    iterations: int = 4000,
    seed: int = 0,
    weights: Optional[Dict[str, float]] = None,
    per_age_group_weights: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """Locally optimize *candidate* by reassigning teams to its existing tournament slots.

    Simulated annealing over one-for-one team swaps between same-age-group
    tournaments, minimizing a weighted combination of repeated opponent
    pairings, same-club pairings/clustering, and short turnarounds (mirrors
    the metrics in :func:`tournament_scheduler.planning_contract.score_candidate`).
    Every team's total participation count and every tournament's roster
    size are preserved exactly, by construction, since the search only ever
    swaps one team for another.

    *weights* overrides :data:`DEFAULT_WEIGHTS` globally; *per_age_group_weights*
    (``{age_group: {name: value}}``) additionally overrides specific weights
    for a single age group on top of that, since different age groups can
    need different tradeoffs between opponent diversity and turnaround
    spacing (issue #257 follow-up).

    Deterministic for a given *seed*. Returns a new candidate dict; does not
    mutate *candidate*.
    """
    resolved_weights = dict(DEFAULT_WEIGHTS)
    if weights:
        resolved_weights.update(weights)

    slots, untouched = _build_slots(candidate, problem)
    rng = random.Random(seed)

    if not slots or iterations <= 0:
        return dict(candidate)

    current_score = _objective(slots, resolved_weights, per_age_group_weights)
    best_score = current_score

    for step in range(iterations):
        move = _candidate_swaps(slots, rng)
        if move is None:
            break
        slot_a, pos_a, slot_b, pos_b = move
        if not _swap_is_valid(slots, slot_a, pos_a, slot_b, pos_b):
            continue

        _apply_swap(slots, slot_a, pos_a, slot_b, pos_b)
        new_score = _objective(slots, resolved_weights, per_age_group_weights)
        delta = new_score - current_score

        temperature = max(1e-6, 1.0 - step / iterations)
        accept = delta <= 0 or rng.random() < math.exp(-delta / (temperature * 5))

        if accept:
            current_score = new_score
            best_score = min(best_score, new_score)
        else:
            # Revert: swapping the same pair back is its own inverse.
            _apply_swap(slots, slot_a, pos_a, slot_b, pos_b)

    initial_score = _objective(_build_slots(candidate, problem)[0], resolved_weights, per_age_group_weights)
    rebuilt_tournaments = [
        _rebuild_tournament(slot) if slot.changed else slot.tournament for slot in slots
    ]

    result = dict(candidate)
    result["schema_version"] = candidate.get("schema_version", CANDIDATE_SCHEMA_VERSION)
    result["tournaments"] = rebuilt_tournaments + untouched
    result["source"] = {
        "planner": "stage3_optimizer",
        "version": None,
        "base_source": candidate.get("source"),
        "iterations": iterations,
        "seed": seed,
        "objective_before": initial_score,
        "objective_after": best_score,
        "per_age_group_weights": per_age_group_weights or None,
    }
    return result
