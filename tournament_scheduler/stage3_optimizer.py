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

- The tournament *skeleton* — arenas, hosts, and how many teams each
  tournament holds — is taken as given from the input candidate. This
  optimizer does not invent tournament slots; it only decides which teams
  fill them and, optionally, when they happen.
- Within each age group, teams are swapped one-for-one between tournaments
  via simulated annealing, so every team's total participation count and
  every tournament's roster size stay exactly as in the input (the hard
  requirements :func:`verify_candidate` checks for these are preserved by
  construction, not re-derived here).
- Optionally (``move_dates=True``, ``rvv-miniputt plan optimize --move-dates``),
  the search can also swap two same-age-group tournaments' *dates* — the
  turnaround/gap regressions found in issue #257's A/B benchmarks are
  fundamentally date-driven and a team-swap-only search cannot fix them,
  since which teams meet is orthogonal to when a tournament happens. Off by
  default to keep the "skeleton taken as given" behavior of the first
  optimizer version unchanged when not explicitly requested.
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
from dataclasses import asdict, dataclass, field
from datetime import date
from itertools import combinations
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
    arena: Optional[str] = None
    team_ids: List[TeamIdentity] = field(default_factory=list)
    changed: bool = False
    date_changed: bool = False


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
                arena=tournament.get("arena"),
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


def _date_swap_candidates(slots: List[_Slot], rng: random.Random) -> Optional[Tuple[int, int]]:
    """Pick two same-age-group slots with different dates to swap dates between.

    Only the ``date`` moves; each slot keeps its own arena, host and teams,
    so this move never changes opponent pairings or same-club clustering —
    it only reshuffles *when* a tournament happens, which is what turnaround
    spacing depends on.
    """
    by_age_group: Dict[str, List[int]] = {}
    for index, slot in enumerate(slots):
        by_age_group.setdefault(slot.age_group, []).append(index)

    candidates = [indices for indices in by_age_group.values() if len(indices) >= 2]
    if not candidates:
        return None
    indices = rng.choice(candidates)
    slot_a, slot_b = rng.sample(indices, 2)
    if slots[slot_a].date == slots[slot_b].date:
        return None
    return slot_a, slot_b


def _date_swap_is_valid(slots: List[_Slot], slot_a: int, slot_b: int) -> bool:
    a, b = slots[slot_a], slots[slot_b]
    new_a_date, new_b_date = b.date, a.date

    for slot in slots:
        if slot is a or slot is b:
            continue
        # No other tournament may already occupy the same arena on the date
        # a slot is moving to.
        if a.arena and slot.arena == a.arena and slot.date == new_a_date:
            return False
        if b.arena and slot.arena == b.arena and slot.date == new_b_date:
            return False
        # No team from the moving slot may already be scheduled elsewhere
        # (same age group) on the date it's moving to.
        if slot.age_group == a.age_group and slot.date == new_a_date:
            if any(team in slot.team_ids for team in a.team_ids):
                return False
        if slot.age_group == b.age_group and slot.date == new_b_date:
            if any(team in slot.team_ids for team in b.team_ids):
                return False
    return True


def _apply_date_swap(slots: List[_Slot], slot_a: int, slot_b: int) -> None:
    a, b = slots[slot_a], slots[slot_b]
    a.date, b.date = b.date, a.date
    a.date_changed = True
    b.date_changed = True


def _rebuild_tournament(slot: _Slot) -> Dict[str, Any]:
    """Regenerate a slot's ``teams``/``games``/``date`` from search state."""
    tournament = dict(slot.tournament)
    if slot.date_changed:
        tournament["date"] = slot.date.isoformat()

    if not slot.changed:
        return tournament

    teams = [Team(club=club, label=label, age_group=age_group) for club, label, age_group in slot.team_ids]
    if slot.host_club:
        host_teams = [t for t in teams if t.club == slot.host_club]
        other_teams = [t for t in teams if t.club != slot.host_club]
        if host_teams:
            teams = host_teams + other_teams

    games = generate_round_robin_games(teams, slot.parallel_games)

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
    move_dates: bool = False,
    date_swap_probability: float = 0.3,
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

    When *move_dates* is true, the search also considers swapping two
    same-age-group tournaments' dates (each tournament keeps its own arena,
    host and teams — see :func:`_date_swap_candidates`), attempted with
    probability *date_swap_probability* each step and a team swap otherwise.
    Off by default, matching the first optimizer version's "skeleton taken
    as given" behavior.

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
        try_date_swap = move_dates and rng.random() < date_swap_probability
        if try_date_swap:
            date_move = _date_swap_candidates(slots, rng)
            if date_move is None or not _date_swap_is_valid(slots, *date_move):
                continue
            slot_a, slot_b = date_move
            _apply_date_swap(slots, slot_a, slot_b)
            new_score = _objective(slots, resolved_weights, per_age_group_weights)
            delta = new_score - current_score
            temperature = max(1e-6, 1.0 - step / iterations)
            accept = delta <= 0 or rng.random() < math.exp(-delta / (temperature * 5))
            if accept:
                current_score = new_score
                best_score = min(best_score, new_score)
            else:
                # Revert: swapping the same pair of dates back is its own inverse.
                _apply_date_swap(slots, slot_a, slot_b)
            continue

        move = _candidate_swaps(slots, rng)
        if move is None:
            # With move_dates on, a date swap may still be possible even
            # when no team swap is (e.g. single-team-per-tournament age
            # groups), so don't give up on the whole search — just skip
            # this step's team-swap attempt.
            if move_dates:
                continue
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
        _rebuild_tournament(slot) if (slot.changed or slot.date_changed) else slot.tournament
        for slot in slots
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
        "move_dates": move_dates,
    }
    return result


# ---------------------------------------------------------------------------
# Baseline-bounded / lexicographic participant optimization (issue #257 Task 2)
# ---------------------------------------------------------------------------
#
# The generic weighted-sum optimizer above trades every metric against every
# other, so it can (and does) buy opponent-diversity gains by spending down
# turnaround spacing or same-club clustering the caller never asked to give
# up. This section instead treats *each age group's current assignment* as a
# hard baseline: a candidate move is only ever considered if it does not push
# any metric tracked by stage3_ab's A/B promotion comparison worse than that
# baseline — same-club pairings/clustering, turnaround gaps, hosting spread,
# pair-repeat counts, and unique-pairs/novelty/inter-club diversity alike
# (participation counts and per-tournament roster/host are already invariant
# under team-swap-only moves, by construction). Inside that feasible region,
# search minimizes repeated inter-club opponent pairs lexicographically
# ahead of maximizing unique pairings/novelty as a tie-breaker. If no move
# improves an age group without leaving its feasible region, that age
# group's assignment is returned unchanged from the input rather than
# forcing a worse result.


@dataclass(frozen=True)
class _GroupMetrics:
    """The subset of :func:`tournament_scheduler.planning_contract.score_candidate`
    metrics that are meaningful *within one age group* and relevant to
    participant (team-swap-only) optimization."""

    pairs_meeting_3_plus: int
    max_pair_repeat: int
    same_club_pairing_count: int
    max_same_club_teams_per_tournament: int
    gaps_under_7: int
    gaps_under_14: int
    hosting_spread: int
    unique_pairs: int
    pairwise_novelty: float
    inter_club_diversity: float
    min_turnaround_days: Optional[int]


def _group_metrics(slots: List[_Slot]) -> _GroupMetrics:
    """Compute :class:`_GroupMetrics` for one age group's slots.

    Mirrors ``score_candidate``'s definitions exactly (see
    ``planning_contract.score_candidate``), computed directly from slot
    state rather than regenerated games — round-robin generation makes each
    team pair within a tournament play exactly one game, so pair
    co-occurrence counts here are equivalent to that function's per-game
    counts without needing to materialize games on every search step.
    """
    pair_counts = _pair_counts(slots)
    total_pairings = sum(pair_counts.values())
    unique_pairs = len(pair_counts)
    pairwise_novelty = (unique_pairs / total_pairings) if total_pairings else 0.0
    pairs_meeting_3_plus = sum(1 for count in pair_counts.values() if count >= 3)
    max_pair_repeat = max(pair_counts.values()) if pair_counts else 0
    same_club_pairing_count = sum(1 for (a, b) in pair_counts if a[0] == b[0])
    inter_club_pairs = sum(1 for (a, b) in pair_counts if a[0] != b[0])

    # The universe of *possible* inter-club opponents is every cross-club
    # pair among all teams that appear anywhere in this age group's slots —
    # not just pairs that happened to share a tournament — matching
    # `score_candidate`'s `inter_club_diversity` denominator exactly.
    all_team_ids = sorted({identity for slot in slots for identity in slot.team_ids})
    inter_club_universe = sum(1 for a, b in combinations(all_team_ids, 2) if a[0] != b[0])
    inter_club_diversity = (inter_club_pairs / inter_club_universe) if inter_club_universe else 0.0

    max_same_club_teams_per_tournament = 0
    for slot in slots:
        club_counts: Dict[str, int] = {}
        for identity in slot.team_ids:
            club_counts[identity[0]] = club_counts.get(identity[0], 0) + 1
        if club_counts:
            max_same_club_teams_per_tournament = max(
                max_same_club_teams_per_tournament, max(club_counts.values())
            )

    dates_by_team: Dict[TeamIdentity, List[date]] = {}
    for slot in slots:
        for identity in slot.team_ids:
            dates_by_team.setdefault(identity, []).append(slot.date)
    gaps_under_7 = 0
    gaps_under_14 = 0
    min_turnaround_days: Optional[int] = None
    for dates in dates_by_team.values():
        ordered = sorted(dates)
        for prev, nxt in zip(ordered, ordered[1:]):
            gap = (nxt - prev).days
            if min_turnaround_days is None or gap < min_turnaround_days:
                min_turnaround_days = gap
            if gap < 7:
                gaps_under_7 += 1
            if gap < 14:
                gaps_under_14 += 1

    host_counts: Dict[str, int] = {}
    for slot in slots:
        host = slot.host_club or slot.arena
        if host:
            host_counts[host] = host_counts.get(host, 0) + 1
    hosting_spread = (max(host_counts.values()) - min(host_counts.values())) if host_counts else 0

    return _GroupMetrics(
        pairs_meeting_3_plus=pairs_meeting_3_plus,
        max_pair_repeat=max_pair_repeat,
        same_club_pairing_count=same_club_pairing_count,
        max_same_club_teams_per_tournament=max_same_club_teams_per_tournament,
        gaps_under_7=gaps_under_7,
        gaps_under_14=gaps_under_14,
        hosting_spread=hosting_spread,
        unique_pairs=unique_pairs,
        pairwise_novelty=pairwise_novelty,
        inter_club_diversity=inter_club_diversity,
        min_turnaround_days=min_turnaround_days,
    )


def _within_bounds(current: _GroupMetrics, baseline: _GroupMetrics) -> bool:
    """True if *current* weakly Pareto-dominates *baseline* on every metric
    :mod:`tournament_scheduler.stage3_ab` treats as protected for A/B
    promotion (issue #257 Task 2's "other existing protected A/B metrics
    must not regress") — not just the explicitly-named subset. A move that
    trades e.g. unique-pair diversity for fewer 3+ repeats is a real
    lexicographic tradeoff the *scorer* would flag as `regressed`, so it is
    rejected here too, not merely penalized.
    """
    if baseline.min_turnaround_days is None:
        turnaround_ok = True
    elif current.min_turnaround_days is None:
        turnaround_ok = False
    else:
        turnaround_ok = current.min_turnaround_days >= baseline.min_turnaround_days
    return (
        current.same_club_pairing_count <= baseline.same_club_pairing_count
        and current.max_same_club_teams_per_tournament <= baseline.max_same_club_teams_per_tournament
        and current.gaps_under_7 <= baseline.gaps_under_7
        and current.gaps_under_14 <= baseline.gaps_under_14
        and current.hosting_spread <= baseline.hosting_spread
        and current.pairs_meeting_3_plus <= baseline.pairs_meeting_3_plus
        and current.max_pair_repeat <= baseline.max_pair_repeat
        and current.unique_pairs >= baseline.unique_pairs
        and current.pairwise_novelty >= baseline.pairwise_novelty - 1e-9
        and current.inter_club_diversity >= baseline.inter_club_diversity - 1e-9
        and turnaround_ok
    )


def _lexicographic_score(m: _GroupMetrics) -> float:
    """A single float approximating the lexicographic order for annealing.

    Primary: fewer pairs meeting 3+ times, then a lower max repeat. Secondary
    tie-breaker: more unique pairs / higher novelty. The gaps between
    successive constant magnitudes assume group sizes small enough (a season
    age group, not the whole league) that a secondary-metric delta can never
    outweigh a one-unit primary-metric step; :func:`_strictly_better` (exact
    lexicographic comparison, no constants) is the actual promotion gate —
    this score only steers the search.
    """
    return (
        m.pairs_meeting_3_plus * 1_000_000.0
        + m.max_pair_repeat * 1_000.0
        - m.unique_pairs * 1.0
        - m.pairwise_novelty * 0.5
    )


def _strictly_better(new: _GroupMetrics, baseline: _GroupMetrics) -> bool:
    """True if *new* weakly dominates *baseline* (see :func:`_within_bounds`)
    AND is a genuine improvement on at least the lexicographic primary
    metrics (pairs meeting 3+ times, then max pair repeat) — or, failing
    that, on the unique-pairs/novelty tie-breaker. A candidate that only
    matches the baseline everywhere is not "improved"; callers should retain
    the baseline in that case.
    """
    if not _within_bounds(new, baseline):
        return False
    if new.pairs_meeting_3_plus != baseline.pairs_meeting_3_plus:
        return new.pairs_meeting_3_plus < baseline.pairs_meeting_3_plus
    if new.max_pair_repeat != baseline.max_pair_repeat:
        return new.max_pair_repeat < baseline.max_pair_repeat
    if new.unique_pairs != baseline.unique_pairs:
        return new.unique_pairs > baseline.unique_pairs
    return new.pairwise_novelty > baseline.pairwise_novelty


def _search_group_bounded(
    group_slots: List["_Slot"],
    baseline_metrics: _GroupMetrics,
    baseline_team_ids: List[List[TeamIdentity]],
    iterations: int,
    seed: int,
) -> Tuple[_GroupMetrics, List[List[TeamIdentity]]]:
    """One seeded bounded local-search restart over *group_slots*.

    Mutates *group_slots* during the search but always restores it to
    *baseline_team_ids* before returning — callers own applying whichever
    result (this seed's, another seed's, or the baseline) wins. Never
    returns a result outside the feasible region: infeasible moves are
    rejected on the spot, not merely penalized.
    """
    rng = random.Random(seed)
    best_metrics = baseline_metrics
    best_team_ids = [list(ids) for ids in baseline_team_ids]
    current_score = _lexicographic_score(baseline_metrics)
    best_score = current_score

    for step in range(iterations):
        move = _candidate_swaps(group_slots, rng)
        if move is None:
            break
        slot_a, pos_a, slot_b, pos_b = move
        if not _swap_is_valid(group_slots, slot_a, pos_a, slot_b, pos_b):
            continue

        _apply_swap(group_slots, slot_a, pos_a, slot_b, pos_b)
        new_metrics = _group_metrics(group_slots)
        if not _within_bounds(new_metrics, baseline_metrics):
            _apply_swap(group_slots, slot_a, pos_a, slot_b, pos_b)  # revert: self-inverse
            continue

        new_score = _lexicographic_score(new_metrics)
        delta = new_score - current_score
        temperature = max(1e-6, 1.0 - step / iterations)
        accept = delta <= 0 or rng.random() < math.exp(-delta / (temperature * 1000.0))
        if accept:
            current_score = new_score
            if new_score < best_score:
                best_score = new_score
                best_metrics = new_metrics
                best_team_ids = [list(slot.team_ids) for slot in group_slots]
        else:
            _apply_swap(group_slots, slot_a, pos_a, slot_b, pos_b)

    for slot, ids in zip(group_slots, baseline_team_ids):
        slot.team_ids = list(ids)
    return best_metrics, best_team_ids


def optimize_candidate_participants_bounded(
    candidate: Dict[str, Any],
    problem: Optional[Dict[str, Any]] = None,
    *,
    iterations: int = 4000,
    seed: int = 0,
) -> Dict[str, Any]:
    """Single-seed baseline-bounded participant optimization (issue #257 Task 2).

    Convenience wrapper around :func:`optimize_candidate_participants_bounded_multi_seed`
    with one seed/restart.
    """
    return optimize_candidate_participants_bounded_multi_seed(
        candidate, problem, seeds=(seed,), iterations=iterations
    )


def optimize_candidate_participants_bounded_multi_seed(
    candidate: Dict[str, Any],
    problem: Optional[Dict[str, Any]] = None,
    *,
    seeds: Tuple[int, ...] = (0,),
    iterations: int = 4000,
) -> Dict[str, Any]:
    """Baseline-bounded participant optimization with multiple seeds/restarts
    (issue #257 Tasks 2-3).

    Runs :func:`_search_group_bounded` for every age group independently and
    for every seed in *seeds*, then keeps whichever seed's result is the
    best *lexicographic* improvement over that age group's own baseline
    (never a candidate that merely satisfies the bounds without improving —
    :func:`_strictly_better` is the promotion gate). An age group with no
    improving seed is returned byte-for-byte identical to the input.

    ``result["source"]["per_age_group_status"]`` reports, per age group,
    ``"improved"`` or ``"unchanged"``, the baseline/optimized metrics, the
    winning seed (if any), and a reason for an ``"unchanged"`` fallback —
    the evaluation shape issue #257 Task 3 asks for.
    """
    slots, untouched = _build_slots(candidate, problem)
    if not slots or iterations <= 0 or not seeds:
        return dict(candidate)

    by_age_group: Dict[str, List[int]] = {}
    for index, slot in enumerate(slots):
        by_age_group.setdefault(slot.age_group, []).append(index)

    per_group_status: Dict[str, Dict[str, Any]] = {}

    for age_group, indices in sorted(by_age_group.items()):
        group_slots = [slots[i] for i in indices]
        baseline_team_ids = [list(slot.team_ids) for slot in group_slots]
        baseline_metrics = _group_metrics(group_slots)

        if len(indices) < 2:
            per_group_status[age_group] = {
                "status": "unchanged",
                "reason": "fewer than two tournaments in age group; nothing to swap",
                "baseline_metrics": asdict(baseline_metrics),
                "optimized_metrics": asdict(baseline_metrics),
                "seed_used": None,
            }
            continue

        overall_best_metrics = baseline_metrics
        overall_best_team_ids = baseline_team_ids
        overall_best_seed: Optional[int] = None

        for run_seed in seeds:
            candidate_metrics, candidate_team_ids = _search_group_bounded(
                group_slots, baseline_metrics, baseline_team_ids, iterations, run_seed
            )
            if _strictly_better(candidate_metrics, overall_best_metrics):
                overall_best_metrics = candidate_metrics
                overall_best_team_ids = candidate_team_ids
                overall_best_seed = run_seed

        if overall_best_seed is not None:
            for slot, ids in zip(group_slots, overall_best_team_ids):
                if ids != slot.team_ids:
                    slot.team_ids = list(ids)
                    slot.changed = True
            per_group_status[age_group] = {
                "status": "improved",
                "reason": None,
                "baseline_metrics": asdict(baseline_metrics),
                "optimized_metrics": asdict(overall_best_metrics),
                "seed_used": overall_best_seed,
            }
        else:
            for slot, ids in zip(group_slots, baseline_team_ids):
                slot.team_ids = list(ids)
                slot.changed = False
            per_group_status[age_group] = {
                "status": "unchanged",
                "reason": "no seed found a lexicographic improvement within the baseline non-regression bounds",
                "baseline_metrics": asdict(baseline_metrics),
                "optimized_metrics": asdict(baseline_metrics),
                "seed_used": None,
            }

    rebuilt_tournaments = [
        _rebuild_tournament(slot) if slot.changed else slot.tournament for slot in slots
    ]

    result = dict(candidate)
    result["schema_version"] = candidate.get("schema_version", CANDIDATE_SCHEMA_VERSION)
    result["tournaments"] = rebuilt_tournaments + untouched
    result["source"] = {
        "planner": "stage3_optimizer_participants_bounded",
        "base_source": candidate.get("source"),
        "iterations": iterations,
        "seeds": list(seeds),
        "per_age_group_status": per_group_status,
    }
    return result
