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
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from itertools import combinations
from typing import Any, Dict, List, Optional, Set, Tuple

from .game_generation import generate_round_robin_games
from .models import Team
from . import planning_half
from .planning_contract import (
    CANDIDATE_SCHEMA_VERSION,
    _parse_date,
    _team_identity,
    external_calendar_conflict,
)
from .utils.slot_finder import matchday_duration_minutes, parse_time

# Fixed candidate start times for the move_slots search (issue #262 P1).
# The planning_problem contract does not carry per-time-of-day calendar
# evidence (only per-club/per-date club_busy_dates), so this move can only
# search a bounded set of plausible windows and rely on interval-overlap
# checks against sibling tournaments -- it cannot verify a candidate time
# against a host's real external bookings the way scheduler.find_arena_slot_for_date
# does. Mirrors the slot_finder default search window (10:00-15:30).
_SLOT_TIME_CANDIDATES: Tuple[str, ...] = (
    "10:00", "10:30", "11:00", "11:30", "12:00",
    "12:30", "13:00", "13:30", "14:00", "14:30", "15:00",
)

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
    start_time: Optional[str] = None
    duration_minutes: int = 0
    changed: bool = False
    date_changed: bool = False
    host_changed: bool = False
    start_time_changed: bool = False


def _infer_parallel_games(tournament: Dict[str, Any], problem: Optional[Dict[str, Any]]) -> int:
    if problem:
        pg = (problem.get("parallel_games") or {}).get(tournament.get("age_group"))
        if isinstance(pg, int) and pg > 0:
            return pg
    games = tournament.get("games") or []
    if games:
        return max(g.get("parallel_slot", 0) for g in games) + 1
    return 1


def _infer_duration_minutes(tournament: Dict[str, Any], problem: Optional[Dict[str, Any]]) -> int:
    if not problem:
        return 0
    round_length = (problem.get("round_length_minutes") or {}).get(tournament.get("age_group"))
    if not isinstance(round_length, int) or round_length <= 0:
        return 0
    games = tournament.get("games") or []
    max_round = max((g.get("round_number", 0) for g in games), default=0)
    if max_round <= 0:
        return 0
    return matchday_duration_minutes(round_length, max_round)


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
                start_time=tournament.get("start_time"),
                duration_minutes=_infer_duration_minutes(tournament, problem),
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


class _SearchState:
    """Incrementally-maintained objective total + feasibility indexes for the
    weighted-sum simulated-annealing search in :func:`optimize_candidate`
    (issue #265 P0).

    Rebuilding :func:`_objective` from scratch and rescanning every slot for
    a feasibility check on every proposed move dominates Stage 3 v2
    optimizer runtime on a production-sized season (152 tournaments): most
    proposed moves touch only two tournaments and two teams. This class
    tracks the handful of counters/indexes each metric and feasibility check
    actually depends on (pair counts, per-slot club counts, per-team date
    multisets, an arena/date occupancy index) and updates only the entries a
    move touches, so per-step cost scales with the size of the *change*, not
    with the season size.

    Each ``apply_*`` method mutates the underlying slot(s) exactly like the
    (now-retired) standalone ``_apply_*`` helpers did, and returns the
    resulting change in the objective total (already added to
    :attr:`total`); calling the same method again with the same arguments is
    still its own inverse, so callers revert a rejected move exactly as
    before. Host and start-time moves never change :attr:`total` -- neither
    ``host_club``/``arena``/``start_time`` is an input to :func:`_objective`
    -- so :meth:`move_host`/:meth:`move_slot_time` only maintain the arena
    occupancy index, with no objective delta to compute.

    :meth:`full_objective` recomputes :func:`_objective` from the live slot
    state and is used only to cross-check correctness (see
    ``tests/test_stage3_optimizer.py``'s incremental-vs-full property tests
    and the ``debug_reference`` escape hatch below) -- never on the hot path.
    """

    def __init__(
        self,
        slots: List[_Slot],
        weights_by_age_group: Dict[str, Dict[str, float]],
    ) -> None:
        self.slots = slots
        self.weights_by_age_group = weights_by_age_group
        self.age_group_by_team: Dict[TeamIdentity, str] = {
            identity: slot.age_group for slot in slots for identity in slot.team_ids
        }
        self.pair_counts: Dict[Tuple[TeamIdentity, TeamIdentity], int] = _pair_counts(slots)
        self.club_counts_by_slot: List[Dict[str, int]] = []
        for slot in slots:
            counts: Dict[str, int] = {}
            for identity in slot.team_ids:
                counts[identity[0]] = counts.get(identity[0], 0) + 1
            self.club_counts_by_slot.append(counts)
        self.dates_by_team: Dict[TeamIdentity, Counter] = {}
        for slot in slots:
            for identity in slot.team_ids:
                self.dates_by_team.setdefault(identity, Counter())[slot.date] += 1
        self.arena_date_index: Dict[Tuple[str, date], Set[int]] = {}
        for index, slot in enumerate(slots):
            if slot.arena:
                self.arena_date_index.setdefault((slot.arena, slot.date), set()).add(index)

        total = 0.0
        for pair, count in self.pair_counts.items():
            total += self._pair_contribution(pair, count)
        for slot_index, counts in enumerate(self.club_counts_by_slot):
            age_group = slots[slot_index].age_group
            for count in counts.values():
                total += self._club_contribution(age_group, count)
        for team in self.dates_by_team:
            total += self._team_gap_contribution(team)
        self.total = total

    # -- metric-contribution helpers (pure functions of counts) ------------

    def _weights(self, age_group: str) -> Dict[str, float]:
        return self.weights_by_age_group.get(age_group, DEFAULT_WEIGHTS)

    def _pair_contribution(self, pair: Tuple[TeamIdentity, TeamIdentity], count: int) -> float:
        if count <= 0:
            return 0.0
        a, b = pair
        weights = self._weights(self.age_group_by_team.get(a, ""))
        total = 0.0
        if count > 1:
            total += weights["pair_repeat"] * (count - 1) ** 2
        if a[0] == b[0]:
            total += weights["same_club_pairing"] * count
        return total

    def _club_contribution(self, age_group: str, count: int) -> float:
        if count <= 1:
            return 0.0
        return self._weights(age_group)["same_club_cluster"] * (count - 1) ** 2

    def _team_gap_contribution(self, team: TeamIdentity) -> float:
        counter = self.dates_by_team.get(team)
        if not counter:
            return 0.0
        ordered = sorted(counter.keys())
        if len(ordered) < 2:
            return 0.0
        weights = self._weights(self.age_group_by_team.get(team, ""))
        total = 0.0
        for prev, nxt in zip(ordered, ordered[1:]):
            gap = (nxt - prev).days
            if gap < 7:
                total += weights["gap_under_7"]
            elif gap < 14:
                total += weights["gap_under_14"]
        return total

    # -- atomic incremental updates -----------------------------------------
    #
    # Each of these mutates exactly one counter/index entry and returns the
    # resulting change in the objective total. Applying a sequence of these
    # for one proposed move and summing the returned deltas always equals
    # (full objective after) - (full objective before), by telescoping --
    # true regardless of move ordering or shared entities between two slots
    # (e.g. a team common to both tournaments in a date swap), since each
    # step's delta is computed from the *current* live state, not a cached
    # snapshot.

    def _adjust_pair(self, pair: Tuple[TeamIdentity, TeamIdentity], delta_count: int) -> float:
        old_count = self.pair_counts.get(pair, 0)
        new_count = old_count + delta_count
        change = self._pair_contribution(pair, new_count) - self._pair_contribution(pair, old_count)
        if new_count <= 0:
            self.pair_counts.pop(pair, None)
        else:
            self.pair_counts[pair] = new_count
        return change

    def _adjust_club(self, slot_index: int, club: str, delta_count: int, age_group: str) -> float:
        counts = self.club_counts_by_slot[slot_index]
        old_count = counts.get(club, 0)
        new_count = old_count + delta_count
        change = self._club_contribution(age_group, new_count) - self._club_contribution(age_group, old_count)
        if new_count <= 0:
            counts.pop(club, None)
        else:
            counts[club] = new_count
        return change

    def _move_team_date(self, team: TeamIdentity, old_date: date, new_date: date) -> float:
        before = self._team_gap_contribution(team)
        counter = self.dates_by_team.setdefault(team, Counter())
        counter[old_date] -= 1
        if counter[old_date] <= 0:
            del counter[old_date]
        counter[new_date] += 1
        after = self._team_gap_contribution(team)
        return after - before

    def _reindex_arena(self, index: int, old_arena: Optional[str], old_date: date) -> None:
        if old_arena:
            bucket = self.arena_date_index.get((old_arena, old_date))
            if bucket is not None:
                bucket.discard(index)
                if not bucket:
                    del self.arena_date_index[(old_arena, old_date)]
        slot = self.slots[index]
        if slot.arena:
            self.arena_date_index.setdefault((slot.arena, slot.date), set()).add(index)

    # -- feasibility index reads ---------------------------------------------

    def has_team_on_date(self, team: TeamIdentity, when: date) -> bool:
        return self.dates_by_team.get(team, {}).get(when, 0) > 0

    def arena_bucket(self, arena: Optional[str], when: date) -> Set[int]:
        if not arena:
            return set()
        return self.arena_date_index.get((arena, when), set())

    # -- move application (mirrors the old standalone _apply_* helpers) ------

    def apply_team_swap(self, slot_a: int, pos_a: int, slot_b: int, pos_b: int) -> float:
        a, b = self.slots[slot_a], self.slots[slot_b]
        team_a = a.team_ids[pos_a]
        team_b = b.team_ids[pos_b]
        delta = 0.0

        for i, other in enumerate(a.team_ids):
            if i == pos_a:
                continue
            delta += self._adjust_pair(tuple(sorted((team_a, other))), -1)
            delta += self._adjust_pair(tuple(sorted((team_b, other))), 1)
        for i, other in enumerate(b.team_ids):
            if i == pos_b:
                continue
            delta += self._adjust_pair(tuple(sorted((team_b, other))), -1)
            delta += self._adjust_pair(tuple(sorted((team_a, other))), 1)

        delta += self._adjust_club(slot_a, team_a[0], -1, a.age_group)
        delta += self._adjust_club(slot_a, team_b[0], 1, a.age_group)
        delta += self._adjust_club(slot_b, team_b[0], -1, b.age_group)
        delta += self._adjust_club(slot_b, team_a[0], 1, b.age_group)

        if a.date != b.date:
            delta += self._move_team_date(team_a, a.date, b.date)
            delta += self._move_team_date(team_b, b.date, a.date)

        a.team_ids[pos_a], b.team_ids[pos_b] = team_b, team_a
        a.changed = True
        b.changed = True
        self.total += delta
        return delta

    def apply_date_swap(self, slot_a: int, slot_b: int) -> float:
        a, b = self.slots[slot_a], self.slots[slot_b]
        old_a_date, old_b_date = a.date, b.date
        delta = 0.0
        for team in a.team_ids:
            delta += self._move_team_date(team, old_a_date, old_b_date)
        for team in b.team_ids:
            delta += self._move_team_date(team, old_b_date, old_a_date)

        a.date, b.date = old_b_date, old_a_date
        self._reindex_arena(slot_a, a.arena, old_a_date)
        self._reindex_arena(slot_b, b.arena, old_b_date)
        a.date_changed = True
        b.date_changed = True
        self.total += delta
        return delta

    def move_date(self, index: int, new_date: date) -> float:
        """Move a single slot to *new_date* (issue #293's within-half move).

        Unlike :meth:`apply_date_swap`, this doesn't pair with another slot
        -- the caller draws *new_date* from the free calendar, not from
        another tournament's date. Calling this again with the slot's prior
        date is its own inverse, exactly like the other ``apply_*``/``move_*``
        methods, so a rejected proposal can be reverted the same way.
        """
        slot = self.slots[index]
        old_date = slot.date
        delta = 0.0
        for team in slot.team_ids:
            delta += self._move_team_date(team, old_date, new_date)
        slot.date = new_date
        self._reindex_arena(index, slot.arena, old_date)
        slot.date_changed = True
        self.total += delta
        return delta

    def move_host(self, index: int, new_host: str, new_arena: Optional[str]) -> None:
        # Host/arena are not inputs to _objective (only team_ids/dates are),
        # so a host move never changes the objective total -- only the arena
        # occupancy index needs to move with it.
        slot = self.slots[index]
        old_arena, old_date = slot.arena, slot.date
        slot.host_club = new_host
        slot.arena = new_arena
        slot.host_changed = True
        self._reindex_arena(index, old_arena, old_date)

    def move_slot_time(self, index: int, new_time: str) -> None:
        # start_time is likewise not an _objective input; no arena/date
        # index change either, since arena and date are unchanged.
        slot = self.slots[index]
        slot.start_time = new_time
        slot.start_time_changed = True

    def full_objective(
        self,
        base_weights: Dict[str, float],
        per_age_group: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> float:
        """Reference recomputation from live slot state (debug/test only)."""
        return _objective(self.slots, base_weights, per_age_group)


def _candidate_swaps(
    slots: List[_Slot], rng: random.Random, by_age_group: Optional[Dict[str, List[int]]] = None
) -> Optional[Tuple[int, int, int, int]]:
    """Pick a random pair of (slot_index, team_position) to swap.

    Returns ``None`` when no swap is possible (e.g. a single-tournament age
    group). Only considers swaps between two *different* tournaments in the
    same age group, since participation targets and roster sizes are only
    meaningful to preserve within an age group.

    *by_age_group* (issue #265 P0: "candidate generation does not rebuild
    age-group slot lists on every move") lets a caller doing many proposals
    in a row (:func:`optimize_candidate`'s search loop) pass in the
    slot-index-by-age-group grouping once -- which slot belongs to which age
    group, and whether it has any teams, never changes over the course of a
    team-swap/date-swap search, only *which* teams occupy a slot does -- so
    rebuilding it from a full scan of *slots* on every step is pure waste.
    Rebuilt from *slots* when omitted, preserving the original standalone
    behavior for other callers (e.g. :func:`_search_group_bounded`).
    """
    if by_age_group is None:
        by_age_group = {}
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


def _swap_is_valid(
    slots: List[_Slot],
    slot_a: int,
    pos_a: int,
    slot_b: int,
    pos_b: int,
    state: Optional["_SearchState"] = None,
) -> bool:
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
        if state is not None:
            # issue #265 P0: O(1) index lookups instead of scanning every
            # other slot for a date collision -- team_a/team_b's own current
            # participation dates already include any other tournament they
            # play on the target date, if one exists.
            if state.has_team_on_date(team_a, b.date):
                return False
            if state.has_team_on_date(team_b, a.date):
                return False
        else:
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


def _date_swap_candidates(
    slots: List[_Slot], rng: random.Random, by_age_group: Optional[Dict[str, List[int]]] = None
) -> Optional[Tuple[int, int]]:
    """Pick two same-age-group slots with different dates to swap dates between.

    Only the ``date`` moves; each slot keeps its own arena, host and teams,
    so this move never changes opponent pairings or same-club clustering —
    it only reshuffles *when* a tournament happens, which is what turnaround
    spacing depends on.

    *by_age_group* (issue #265 P0): same precomputed-once grouping as
    :func:`_candidate_swaps` -- slot-to-age-group membership is static
    across a search, so a caller doing repeated proposals should pass it in
    rather than rebuilding it from a full scan of *slots* every step.
    """
    if by_age_group is None:
        by_age_group = {}
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


def _date_swap_is_valid(
    slots: List[_Slot],
    slot_a: int,
    slot_b: int,
    state: Optional["_SearchState"] = None,
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]] = None,
    split_date: Optional[date] = None,
    allow_cross_half_moves: bool = False,
) -> bool:
    a, b = slots[slot_a], slots[slot_b]
    new_a_date, new_b_date = b.date, a.date

    # issue #293: a date swap is a date *move* for both slots (each ends up
    # on the other's date) -- reject it if that would carry either
    # tournament across the Christmas boundary. Cross-half movement must be
    # an explicit policy override (`problem["allow_cross_half_moves"]`), not
    # a side effect of the optimizer chasing a better score.
    if split_date is not None and not allow_cross_half_moves:
        if planning_half.tournament_half(a.date, split_date) != planning_half.tournament_half(b.date, split_date):
            return False

    # issue #264 P0: a date swap keeps each tournament's own host/arena, but
    # moving it to the other tournament's date can still walk it into a real
    # external booking that the sibling-slot-only checks below can't see.
    if external_calendar_conflict(club_busy_intervals, a.host_club, new_a_date, a.start_time, a.duration_minutes):
        return False
    if external_calendar_conflict(club_busy_intervals, b.host_club, new_b_date, b.start_time, b.duration_minutes):
        return False

    if state is not None:
        # issue #265 P0: arena-bucket + per-team-date-index lookups instead
        # of scanning every other slot in the season.
        occupants_a = state.arena_bucket(a.arena, new_a_date) - {slot_a, slot_b}
        if a.arena and occupants_a:
            return False
        occupants_b = state.arena_bucket(b.arena, new_b_date) - {slot_a, slot_b}
        if b.arena and occupants_b:
            return False
        # A team that happens to play in *both* a and b (roster membership
        # is unchanged by a date swap) still ends up on the same two dates
        # either way -- exclude b's/a's own membership from the collision
        # check, matching the original "skip slot a/b themselves" scan.
        if any(
            state.has_team_on_date(team, new_a_date) and team not in b.team_ids
            for team in a.team_ids
        ):
            return False
        if any(
            state.has_team_on_date(team, new_b_date) and team not in a.team_ids
            for team in b.team_ids
        ):
            return False
        return True

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


def _within_half_date_move_candidates(
    slots: List[_Slot],
    rng: random.Random,
    problem: Optional[Dict[str, Any]],
    split_date: Optional[date],
) -> Optional[Tuple[int, date]]:
    """Propose moving one slot to a genuinely new date within its own half.

    Unlike :func:`_date_swap_candidates` (which only reshuffles dates
    already in use by two existing slots), this draws a candidate date from
    the full planning window (``problem["start_date"]..["end_date"]``),
    restricted to the same side of *split_date* as the slot's current date
    -- issue #293's "first safe expansion": consider verified alternative
    dates for the same host/arena inside the same half, rather than moving a
    tournament across the Christmas boundary.
    """
    if not slots or not problem:
        return None
    window_start = _parse_date(problem.get("start_date"))
    window_end = _parse_date(problem.get("end_date"))
    if window_start is None or window_end is None:
        return None

    index = rng.randrange(len(slots))
    slot = slots[index]
    current_half = planning_half.tournament_half(slot.date, split_date)
    if split_date is None:
        half_start, half_end = window_start, window_end
    elif current_half == "before_christmas":
        half_start, half_end = window_start, split_date - timedelta(days=1)
    else:
        half_start, half_end = split_date, window_end

    span = (half_end - half_start).days
    if span <= 0:
        return None
    new_date = half_start + timedelta(days=rng.randrange(span + 1))
    if new_date == slot.date:
        return None
    return index, new_date


def _within_half_date_move_is_valid(
    slots: List[_Slot],
    index: int,
    new_date: date,
    state: Optional["_SearchState"] = None,
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]] = None,
) -> bool:
    slot = slots[index]

    if external_calendar_conflict(
        club_busy_intervals, slot.host_club, new_date, slot.start_time, slot.duration_minutes
    ):
        return False

    if state is not None:
        occupants = state.arena_bucket(slot.arena, new_date) - {index}
        if slot.arena and occupants:
            return False
        if any(state.has_team_on_date(team, new_date) for team in slot.team_ids):
            return False
        return True

    for other_index, other in enumerate(slots):
        if other_index == index:
            continue
        if slot.arena and other.arena == slot.arena and other.date == new_date:
            return False
        if other.age_group == slot.age_group and other.date == new_date:
            if any(team in other.team_ids for team in slot.team_ids):
                return False
    return True


def _time_to_minutes(time_str: str) -> int:
    t = parse_time(time_str)
    return t.hour * 60 + t.minute


def _interval_overlaps(
    slots: List[_Slot],
    index: int,
    arena: Optional[str],
    check_date: date,
    start_time: Optional[str],
    duration_minutes: int,
    state: Optional["_SearchState"] = None,
) -> bool:
    """True if (arena, check_date, [start_time, start_time+duration)) overlaps
    any other slot's own arena/date/time interval.

    Shared by :func:`_host_move_is_valid` and :func:`_slot_time_move_is_valid`
    (issue #262 P1) -- both moves change one of arena/host/start_time for a
    single slot and need the same hard "no two tournaments occupy the same
    hall at overlapping times" check the deterministic verifier
    (``planning_contract.verify_candidate`` via ``arena_conflicts``) already
    enforces at the end of a search.

    When *state* is given (issue #265 P0), only the small bucket of slots
    already sharing this exact (arena, date) is scanned -- typically one or
    two tournaments -- instead of every slot in the season.
    """
    if not arena or not start_time or duration_minutes <= 0:
        return False
    new_start = _time_to_minutes(start_time)
    new_end = new_start + duration_minutes
    candidates = state.arena_bucket(arena, check_date) if state is not None else range(len(slots))
    for i in candidates:
        if i == index:
            continue
        other = slots[i]
        if state is None and (other.arena != arena or other.date != check_date):
            continue
        if not other.start_time or other.duration_minutes <= 0:
            continue
        other_start = _time_to_minutes(other.start_time)
        other_end = other_start + other.duration_minutes
        if new_start < other_end and other_start < new_end:
            return True
    return False


def _host_move_candidates(slots: List[_Slot], rng: random.Random) -> Optional[Tuple[int, str]]:
    """Pick a slot and an alternative host among its own participating clubs.

    Restricted to clubs already fielding a team in *this* tournament (a
    deterministic fact from the candidate itself, mirroring the existing
    "a host must field a team in the age group it hosts" hard invariant) --
    this is a hosting-fairness/travel move ("prefer a different host this
    weekend"), not a search over every club in the roster.
    """
    eligible: List[int] = []
    for index, slot in enumerate(slots):
        clubs = {identity[0] for identity in slot.team_ids}
        if len(clubs - {slot.host_club}) > 0:
            eligible.append(index)
    if not eligible:
        return None
    index = rng.choice(eligible)
    slot = slots[index]
    candidate_hosts = sorted({identity[0] for identity in slot.team_ids} - {slot.host_club})
    return index, rng.choice(candidate_hosts)


def _host_move_is_valid(
    slots: List[_Slot],
    index: int,
    new_host: str,
    club_arenas: Dict[str, str],
    club_calendar_status: Dict[str, str],
    state: Optional["_SearchState"] = None,
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]] = None,
) -> bool:
    # issue #262 P0: a club with no trustworthy calendar evidence this run
    # must never be handed hosting duty by the search either.
    if club_calendar_status and club_calendar_status.get(new_host, "unknown") != "known":
        return False
    new_arena = club_arenas.get(new_host)
    if not new_arena:
        return False
    slot = slots[index]
    if _interval_overlaps(slots, index, new_arena, slot.date, slot.start_time, slot.duration_minutes, state):
        return False
    # issue #264 P0: "known" status only proves the club was scraped this
    # run, not that this exact date/time is free -- check the new host's
    # real busy intervals too, not just sibling-candidate collisions.
    return not external_calendar_conflict(
        club_busy_intervals, new_host, slot.date, slot.start_time, slot.duration_minutes
    )


def _slot_time_move_candidates(slots: List[_Slot], rng: random.Random) -> Optional[Tuple[int, str]]:
    """Pick a slot and an alternative start time from the fixed candidate window."""
    eligible = [i for i, slot in enumerate(slots) if slot.duration_minutes > 0]
    if not eligible:
        return None
    index = rng.choice(eligible)
    current = slots[index].start_time
    candidates = [t for t in _SLOT_TIME_CANDIDATES if t != current]
    if not candidates:
        return None
    return index, rng.choice(candidates)


def _slot_time_move_is_valid(
    slots: List[_Slot],
    index: int,
    new_time: str,
    state: Optional["_SearchState"] = None,
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]] = None,
) -> bool:
    slot = slots[index]
    if _interval_overlaps(slots, index, slot.arena, slot.date, new_time, slot.duration_minutes, state):
        return False
    # issue #264 P0: also reject a new time that collides with the host's
    # own real external booking on this date, not just sibling candidates.
    return not external_calendar_conflict(
        club_busy_intervals, slot.host_club, slot.date, new_time, slot.duration_minutes
    )


def _rebuild_tournament(slot: _Slot) -> Dict[str, Any]:
    """Regenerate a slot's ``teams``/``games``/``date``/``host_club``/
    ``arena``/``start_time`` from search state."""
    tournament = dict(slot.tournament)
    if slot.date_changed:
        tournament["date"] = slot.date.isoformat()
    if slot.start_time_changed:
        tournament["start_time"] = slot.start_time
    if slot.host_changed:
        tournament["host_club"] = slot.host_club
        if slot.arena:
            tournament["arena"] = slot.arena

    if not (slot.changed or slot.host_changed):
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
    move_dates_within_half: bool = False,
    move_hosts: bool = False,
    move_slots: bool = False,
    plateau_iterations: Optional[int] = None,
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

    When *move_dates_within_half* is true (issue #293), the search also
    considers moving a single tournament to a genuinely new date drawn from
    the planning window (not just swapping between two already-scheduled
    dates), restricted to the same side of ``problem["christmas_split_date"]``
    as its current date -- see :func:`_within_half_date_move_candidates`.
    Like *move_dates*, moving a tournament across the Christmas boundary is
    rejected unless ``problem["allow_cross_half_moves"]`` is set.

    When *move_hosts* is true (issue #262 P1), the search also considers
    reassigning a tournament's host club to another club already fielding a
    team in that same tournament (a deterministic fact, not a heuristic
    ranking — see :func:`_host_move_candidates`), rejecting any club whose
    calendar status (``problem["club_calendar_status"]``) is not
    ``"known"`` and any move that would double-book the new host's arena.

    When *move_slots* is true (issue #262 P1), the search also considers
    reassigning a tournament's start time among a fixed set of candidate
    windows (see :data:`_SLOT_TIME_CANDIDATES`), rejecting any move that
    would double-book its arena. The planning_problem contract does not
    carry per-time external calendar evidence, so this move only checks
    for conflicts against sibling tournaments in the same candidate, not a
    host's real external bookings.

    *move_dates*/*move_hosts*/*move_slots* each add to the same per-step
    "try a special move instead of a team swap" probability
    (*date_swap_probability*); when more than one is enabled, the move kind
    attempted each such step is chosen uniformly at random among them.

    The search stops early once it plateaus -- no improvement to the
    best-ever score for *plateau_iterations* consecutive proposals (default
    ``max(500, 50 * number of slots)``) -- rather than always consuming the
    full *iterations* budget on an already-converged season (issue #265 P0:
    "make search budget adaptive instead of blindly consuming iterations").
    ``result["source"]["search_summary"]`` records why the search stopped
    and how many moves were attempted/valid/accepted/improving;
    ``result["source"]["timings"]`` records wall-clock phase durations
    (issue #265 P0 instrumentation).

    Per-step objective/feasibility evaluation is incremental (see
    :class:`_SearchState`): a proposed move's score delta and validity are
    computed from indexes covering only the entities that move touches, not
    by rescanning/rebuilding the whole season, so cost scales with the
    season size only once (index construction), not on every step.

    Deterministic for a given *seed*. Returns a new candidate dict; does not
    mutate *candidate*.
    """
    timings: Dict[str, float] = {}
    t_start = time.perf_counter()

    resolved_weights = dict(DEFAULT_WEIGHTS)
    if weights:
        resolved_weights.update(weights)

    t0 = time.perf_counter()
    slots, untouched = _build_slots(candidate, problem)
    timings["baseline_construction"] = time.perf_counter() - t0

    rng = random.Random(seed)

    if not slots or iterations <= 0:
        return dict(candidate)

    club_arenas: Dict[str, str] = dict((problem or {}).get("clubs") or {})
    club_calendar_status: Dict[str, str] = dict((problem or {}).get("club_calendar_status") or {})
    club_busy_intervals: Dict[str, List[Dict[str, str]]] = dict((problem or {}).get("club_busy_intervals") or {})
    split_date = _parse_date((problem or {}).get("christmas_split_date"))
    allow_cross_half_moves = bool((problem or {}).get("allow_cross_half_moves"))

    special_moves: List[str] = []
    if move_dates:
        special_moves.append("date")
    if move_dates_within_half:
        special_moves.append("within_half_date")
    if move_hosts:
        special_moves.append("host")
    if move_slots:
        special_moves.append("slot_time")

    t0 = time.perf_counter()
    weights_by_age_group: Dict[str, Dict[str, float]] = {
        age_group: _resolve_weights(resolved_weights, per_age_group_weights, age_group)
        for age_group in {slot.age_group for slot in slots}
    }
    state = _SearchState(slots, weights_by_age_group)
    # issue #265 P0: slot-to-age-group membership is static across the
    # search (only *which teams* occupy a slot changes), so build it once
    # instead of rescanning every slot on every proposal.
    by_age_group: Dict[str, List[int]] = {}
    for index, slot in enumerate(slots):
        if slot.team_ids:
            by_age_group.setdefault(slot.age_group, []).append(index)
    date_swap_by_age_group: Dict[str, List[int]] = {}
    for index, slot in enumerate(slots):
        date_swap_by_age_group.setdefault(slot.age_group, []).append(index)
    timings["index_construction"] = time.perf_counter() - t0

    initial_score = state.total
    current_score = state.total
    best_score = current_score

    plateau_limit = plateau_iterations if plateau_iterations is not None else max(500, 50 * len(slots))
    since_improvement = 0
    stop_reason = "max_iterations"
    attempted = 0
    valid_moves = 0
    accepted_moves = 0
    improved_moves = 0

    def _consider(delta: float, step: int) -> bool:
        nonlocal current_score, best_score, since_improvement
        temperature = max(1e-6, 1.0 - step / iterations)
        accept = delta <= 0 or rng.random() < math.exp(-delta / (temperature * 5))
        if accept:
            current_score += delta
            if current_score < best_score - 1e-9:
                best_score = current_score
                since_improvement = 0
        return accept

    t0 = time.perf_counter()
    step = 0
    while step < iterations:
        if since_improvement >= plateau_limit:
            stop_reason = "plateau"
            break

        # issue #265 P0: counts every proposal attempt, valid or not, toward
        # the plateau window -- "no accepted material improvement for N
        # proposals" (not just N *valid* proposals), so a run stuck
        # generating mostly-infeasible moves still stops promptly rather
        # than silently burning the rest of the iteration budget.
        since_improvement += 1
        attempted += 1
        try_special = bool(special_moves) and rng.random() < date_swap_probability
        if try_special:
            kind = rng.choice(special_moves)

            if kind == "date":
                date_move = _date_swap_candidates(slots, rng, date_swap_by_age_group)
                if date_move is None or not _date_swap_is_valid(
                    slots,
                    *date_move,
                    state=state,
                    club_busy_intervals=club_busy_intervals,
                    split_date=split_date,
                    allow_cross_half_moves=allow_cross_half_moves,
                ):
                    step += 1
                    continue
                valid_moves += 1
                slot_a, slot_b = date_move
                delta = state.apply_date_swap(slot_a, slot_b)
                if _consider(delta, step):
                    accepted_moves += 1
                    if delta < -1e-9:
                        improved_moves += 1
                else:
                    # Revert: swapping the same pair of dates back is its own inverse.
                    state.apply_date_swap(slot_a, slot_b)
                step += 1
                continue

            if kind == "within_half_date":
                within_half_move = _within_half_date_move_candidates(slots, rng, problem, split_date)
                if within_half_move is None or not _within_half_date_move_is_valid(
                    slots,
                    *within_half_move,
                    state=state,
                    club_busy_intervals=club_busy_intervals,
                ):
                    step += 1
                    continue
                valid_moves += 1
                index, new_date = within_half_move
                old_date = slots[index].date
                delta = state.move_date(index, new_date)
                if _consider(delta, step):
                    accepted_moves += 1
                    if delta < -1e-9:
                        improved_moves += 1
                else:
                    # Revert: moving the slot back to its prior date is its own inverse.
                    state.move_date(index, old_date)
                step += 1
                continue

            if kind == "host":
                host_move = _host_move_candidates(slots, rng)
                if host_move is None:
                    step += 1
                    continue
                index, new_host = host_move
                if not _host_move_is_valid(
                    slots, index, new_host, club_arenas, club_calendar_status, state, club_busy_intervals
                ):
                    step += 1
                    continue
                valid_moves += 1
                old_host, old_arena = slots[index].host_club, slots[index].arena
                new_arena = club_arenas.get(new_host, old_arena)
                state.move_host(index, new_host, new_arena)
                # Host/arena are not _objective inputs, so this move never
                # changes the score -- always "accept" (matches the prior
                # delta<=0 SA rule, which always accepted a zero delta) and
                # skip the temperature/acceptance machinery entirely.
                accepted_moves += 1
                step += 1
                continue

            if kind == "slot_time":
                slot_move = _slot_time_move_candidates(slots, rng)
                if slot_move is None:
                    step += 1
                    continue
                index, new_time = slot_move
                if not _slot_time_move_is_valid(slots, index, new_time, state, club_busy_intervals):
                    step += 1
                    continue
                valid_moves += 1
                state.move_slot_time(index, new_time)
                # start_time is likewise not an _objective input.
                accepted_moves += 1
                step += 1
                continue

        move = _candidate_swaps(slots, rng, by_age_group)
        if move is None:
            # With a special move enabled, one may still be possible even
            # when no team swap is (e.g. single-team-per-tournament age
            # groups), so don't give up on the whole search — just skip
            # this step's team-swap attempt.
            if special_moves:
                step += 1
                continue
            stop_reason = "no_moves_possible"
            break
        slot_a, pos_a, slot_b, pos_b = move
        if not _swap_is_valid(slots, slot_a, pos_a, slot_b, pos_b, state):
            step += 1
            continue
        valid_moves += 1

        delta = state.apply_team_swap(slot_a, pos_a, slot_b, pos_b)
        if _consider(delta, step):
            accepted_moves += 1
            if delta < -1e-9:
                improved_moves += 1
        else:
            # Revert: swapping the same pair back is its own inverse.
            state.apply_team_swap(slot_a, pos_a, slot_b, pos_b)
        step += 1

    timings["search"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    rebuilt_tournaments = [
        _rebuild_tournament(slot)
        if (slot.changed or slot.date_changed or slot.host_changed or slot.start_time_changed)
        else slot.tournament
        for slot in slots
    ]
    timings["candidate_rebuild"] = time.perf_counter() - t0
    timings["total"] = time.perf_counter() - t_start

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
        "move_hosts": move_hosts,
        "move_slots": move_slots,
        "timings": timings,
        "search_summary": {
            "iterations_budget": iterations,
            "iterations_consumed": step,
            "stop_reason": stop_reason,
            "attempted_moves": attempted,
            "valid_moves": valid_moves,
            "accepted_moves": accepted_moves,
            "improved_moves": improved_moves,
            "plateau_iterations": plateau_limit,
        },
    }
    return result


# ---------------------------------------------------------------------------
# Multi-objective (Pareto) search (issue #264 P1 / issue #265 P1)
# ---------------------------------------------------------------------------
#
# optimize_candidate() above reduces every soft dimension to one scalar via
# a caller-chosen weighted sum, so the caller has to already know the right
# tradeoff before searching. Producing several genuinely different tradeoff
# candidates by calling optimize_candidate() N independent times and
# manually diffing/filtering the results is exactly the "N reruns for N
# candidates" issue #265 P1 flags. This section instead drives ONE shared,
# deliberate sequence of search epochs -- each epoch reuses the existing
# fast, incrementally-scored single-objective annealer with a different
# weight-vector "corner" -- and maintains a bounded non-dominated archive
# across all of them, so a caller gets a small representative Pareto
# portfolio from one call instead of assembling one by hand.
#
# Dominance is checked on each epoch's *once-per-epoch* full
# planning_contract.score_candidate() result, never on the per-step hot
# path inside optimize_candidate() (which remains untouched and just as
# fast as before) -- so adding multi-objective awareness does not multiply
# the cost of the inner search loop itself.

# Objective vector dimensions, all oriented "lower is better" so a single
# uniform dominance check works across every dimension (score_candidate's
# inter_club_diversity is a "higher is better" fraction, so it is stored
# inverted -- see _objective_vector).
_PARETO_DIMENSIONS: Tuple[str, ...] = (
    "max_pair_repeat",
    "same_club_pairing_count",
    "gaps_under_7",
    "gaps_under_14",
    "hosting_spread",
    "inter_club_diversity_inverted",
    "temporal_max_gap_days",
)


def _objective_vector(score: Dict[str, Any]) -> Dict[str, float]:
    """Extract a uniformly "lower is better" objective vector from a
    :func:`tournament_scheduler.planning_contract.score_candidate` result."""
    gaps = (score.get("turnaround") or {}).get("gaps_under_days") or {}
    opponent = score.get("opponent_diversity") or {}
    hosting = score.get("hosting") or {}
    temporal = score.get("temporal") or {}
    return {
        "max_pair_repeat": float(opponent.get("max_pair_repeat", 0)),
        "same_club_pairing_count": float(opponent.get("same_club_pairing_count", 0)),
        "gaps_under_7": float(gaps.get(7, 0)),
        "gaps_under_14": float(gaps.get(14, 0)),
        "hosting_spread": float(hosting.get("spread", 0)),
        "inter_club_diversity_inverted": 1.0 - float(opponent.get("inter_club_diversity", 0.0)),
        "temporal_max_gap_days": float(temporal.get("max_gap_days", 0)),
    }


def _dominates(a: Dict[str, float], b: Dict[str, float], tol: float = 1e-9) -> bool:
    """True if objective vector *a* Pareto-dominates *b*: weakly better (or
    equal, within *tol*) in every dimension, and strictly better in at
    least one."""
    at_least_as_good = all(a[key] <= b[key] + tol for key in a)
    strictly_better = any(a[key] < b[key] - tol for key in a)
    return at_least_as_good and strictly_better


def _vectors_equal(a: Dict[str, float], b: Dict[str, float], tol: float = 1e-9) -> bool:
    return all(abs(a[key] - b[key]) <= tol for key in a)


def _default_pareto_weight_vectors(base_weights: Dict[str, float]) -> List[Dict[str, float]]:
    """Build the default set of search-epoch weight vectors: one "corner"
    per :data:`DEFAULT_WEIGHTS` key (that dimension emphasized, all others
    zeroed, so the epoch searches purely for that tradeoff extreme) plus one
    balanced epoch using *base_weights* unchanged."""
    vectors: List[Dict[str, float]] = []
    for key in base_weights:
        vector = {k: 0.0 for k in base_weights}
        vector[key] = max(base_weights[key], 1.0) * 5.0
        vectors.append(vector)
    vectors.append(dict(base_weights))
    return vectors


def _downselect_archive(archive: List[Dict[str, Any]], max_size: int) -> List[Dict[str, Any]]:
    """Reduce *archive* to at most *max_size* entries, preferring the
    per-dimension extremes (issue #264 P1: "return a small representative
    set — extremes plus useful knee/balanced points") over an arbitrary
    truncation. A no-op when already within budget."""
    if len(archive) <= max_size or not archive:
        return archive

    chosen: List[int] = []
    chosen_set: set = set()
    for dim in archive[0]["vector"]:
        best_index = min(range(len(archive)), key=lambda i: archive[i]["vector"][dim])
        if best_index not in chosen_set:
            chosen_set.add(best_index)
            chosen.append(best_index)
        if len(chosen) >= max_size:
            break

    if len(chosen) < max_size:
        remaining = [i for i in range(len(archive)) if i not in chosen_set]
        remaining.sort(key=lambda i: sum(archive[i]["vector"].values()))
        for index in remaining:
            if len(chosen) >= max_size:
                break
            chosen.append(index)
            chosen_set.add(index)

    return [archive[i] for i in sorted(chosen)]


def optimize_candidate_pareto(
    candidate: Dict[str, Any],
    problem: Optional[Dict[str, Any]] = None,
    *,
    iterations_per_epoch: int = 2000,
    seed: int = 0,
    weight_vectors: Optional[List[Dict[str, float]]] = None,
    per_age_group_weights: Optional[Dict[str, Dict[str, float]]] = None,
    move_dates: bool = False,
    date_swap_probability: float = 0.3,
    move_dates_within_half: bool = False,
    move_hosts: bool = False,
    move_slots: bool = False,
    max_archive_size: int = 5,
) -> Dict[str, Any]:
    """Multi-objective Stage 3 search over a small, deliberate sequence of
    weight-vector epochs, maintaining one bounded non-dominated archive
    across all of them (issue #264 P1, issue #265 P1).

    Each epoch calls :func:`optimize_candidate` unchanged (same fast,
    incrementally-scored inner loop) with *weights* set to that epoch's
    entry from *weight_vectors* (default: :func:`_default_pareto_weight_vectors`,
    one corner per :data:`DEFAULT_WEIGHTS` key plus a balanced epoch).
    After each epoch, the resulting candidate is scored once with
    :func:`tournament_scheduler.planning_contract.score_candidate` and
    checked against the running archive: dominated/duplicate results are
    dropped, and any archive entries the new result dominates are removed.

    Returns a dict with:

    - ``baseline_objective_vector`` / ``baseline_score``: the input
      candidate's own objective vector, for comparison.
    - ``candidates``: up to *max_archive_size* non-dominated entries, each
      with ``candidate``, ``objective_vector``, ``score`` (the full
      :func:`score_candidate` breakdown), ``weights_used``, ``epoch``,
      ``dominates_baseline``, and an independent ``verify_result`` --
      :func:`~tournament_scheduler.planning_contract.verify_candidate` is
      only ever run on archive candidates that survive dominance
      filtering, never on every internal per-epoch proposal.
    - ``search_summary``: epoch count and, per epoch, the weights used and
      the underlying :func:`optimize_candidate` call's own search summary.

    Deterministic for a given *seed* (each epoch uses ``seed + epoch_index``).
    Does not mutate *candidate*.
    """
    from .planning_contract import score_candidate, verify_candidate

    resolved_weights = dict(DEFAULT_WEIGHTS)
    resolved_vectors = weight_vectors or _default_pareto_weight_vectors(resolved_weights)

    baseline_score = score_candidate(candidate, problem=problem)
    baseline_vector = _objective_vector(baseline_score)

    archive: List[Dict[str, Any]] = []

    def _try_add(entry: Dict[str, Any]) -> None:
        nonlocal archive
        for existing in archive:
            if _vectors_equal(existing["vector"], entry["vector"]) or _dominates(existing["vector"], entry["vector"]):
                return
        archive = [existing for existing in archive if not _dominates(entry["vector"], existing["vector"])]
        archive.append(entry)

    epoch_summaries: List[Dict[str, Any]] = []
    for epoch, weights in enumerate(resolved_vectors):
        epoch_seed = seed + epoch
        epoch_result = optimize_candidate(
            candidate,
            problem,
            iterations=iterations_per_epoch,
            seed=epoch_seed,
            weights=weights,
            per_age_group_weights=per_age_group_weights,
            move_dates=move_dates,
            date_swap_probability=date_swap_probability,
            move_dates_within_half=move_dates_within_half,
            move_hosts=move_hosts,
            move_slots=move_slots,
        )
        epoch_score = score_candidate(epoch_result, problem=problem)
        epoch_vector = _objective_vector(epoch_score)
        epoch_summaries.append(
            {
                "epoch": epoch,
                "weights": weights,
                "seed": epoch_seed,
                "objective_vector": epoch_vector,
                "search_summary": (epoch_result.get("source") or {}).get("search_summary"),
            }
        )
        _try_add(
            {
                "candidate": epoch_result,
                "vector": epoch_vector,
                "score": epoch_score,
                "weights": weights,
                "epoch": epoch,
            }
        )

    archive = _downselect_archive(archive, max_archive_size)

    candidates_out: List[Dict[str, Any]] = []
    for entry in archive:
        candidates_out.append(
            {
                "candidate": entry["candidate"],
                "objective_vector": entry["vector"],
                "score": entry["score"],
                "weights_used": entry["weights"],
                "epoch": entry["epoch"],
                "verify_result": verify_candidate(entry["candidate"], problem),
                "dominates_baseline": (
                    _dominates(entry["vector"], baseline_vector) or _vectors_equal(entry["vector"], baseline_vector)
                ),
            }
        )

    return {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "baseline_objective_vector": baseline_vector,
        "baseline_score": baseline_score,
        "candidates": candidates_out,
        "search_summary": {
            "epochs": len(resolved_vectors),
            "epoch_summaries": epoch_summaries,
            "archive_size": len(candidates_out),
        },
    }


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


# ---------------------------------------------------------------------------
# Baseline-bounded schedule (date-only) conflict repair (issue #257 skeleton
# follow-up)
# ---------------------------------------------------------------------------
#
# optimize_candidate_participants_bounded[_multi_seed] only reassigns *which*
# teams fill an existing tournament slot, so it cannot fix a violation like
# `arena_interval_conflict` that comes from *when*/*where* two tournaments
# overlap — that's structural to the skeleton (dates), not the participant
# assignment. Those conflicts are also often cross-age-group (two different
# age groups' tournaments sharing an arena at an overlapping time), so unlike
# the participant optimizer this pass searches across the whole season at
# once rather than one age group at a time.
#
# Moves are date swaps only (see `_apply_date_swap`): each tournament keeps
# its own arena, host and roster, so opponent diversity, same-club
# clustering and participation counts are invariant by construction — only
# hard-violation count and turnaround spacing can change, so those are what
# get measured and bounded.


def _per_age_group_gaps(
    slots: List["_Slot"],
) -> Dict[str, Tuple[int, int, Optional[int]]]:
    """Cheap, slot-state-only per-age-group turnaround metrics: (gaps under
    7 days, gaps under 14 days, min turnaround days).

    A season-wide total can hide one age group regressing while others
    improve — the same masking issue Task 1 fixed for the A/B report — so
    the repair search bounds these per age group, not just in aggregate.
    Computed directly from slot dates (no candidate materialization/
    ``score_candidate`` call needed), so it's cheap enough to call on every
    search step.
    """
    dates_by_team: Dict[TeamIdentity, List[date]] = {}
    for slot in slots:
        for identity in slot.team_ids:
            dates_by_team.setdefault(identity, []).append(slot.date)
    gaps_7_by_group: Dict[str, int] = {}
    gaps_14_by_group: Dict[str, int] = {}
    min_turnaround_by_group: Dict[str, int] = {}
    for identity, dates in dates_by_team.items():
        age_group = identity[2]
        ordered = sorted(dates)
        for prev, nxt in zip(ordered, ordered[1:]):
            gap = (nxt - prev).days
            if gap < 7:
                gaps_7_by_group[age_group] = gaps_7_by_group.get(age_group, 0) + 1
            if gap < 14:
                gaps_14_by_group[age_group] = gaps_14_by_group.get(age_group, 0) + 1
            if age_group not in min_turnaround_by_group or gap < min_turnaround_by_group[age_group]:
                min_turnaround_by_group[age_group] = gap
    age_groups = {identity[2] for identity in dates_by_team}
    return {
        age_group: (
            gaps_7_by_group.get(age_group, 0),
            gaps_14_by_group.get(age_group, 0),
            min_turnaround_by_group.get(age_group),
        )
        for age_group in age_groups
    }


def _gaps_within_bounds(
    trial: Dict[str, Tuple[int, int, Optional[int]]],
    baseline: Dict[str, Tuple[int, int, Optional[int]]],
) -> bool:
    """True if *trial* is no worse than *baseline* in every age group."""
    for age_group, (baseline_g7, baseline_g14, baseline_min) in baseline.items():
        trial_g7, trial_g14, trial_min = trial.get(age_group, (0, 0, None))
        if trial_g7 > baseline_g7 or trial_g14 > baseline_g14:
            return False
        if baseline_min is not None:
            if trial_min is None or trial_min < baseline_min:
                return False
    return True


def _repair_search(
    slots: List["_Slot"],
    untouched: List[Dict[str, Any]],
    base_candidate_template: Dict[str, Any],
    problem: Optional[Dict[str, Any]],
    baseline_violations: int,
    baseline_gaps_7: int,
    baseline_gaps_14: int,
    baseline_per_age_group_gaps: Dict[str, Tuple[int, int, Optional[int]]],
    baseline_dates: List[date],
    iterations: int,
    seed: int,
) -> Tuple[int, int, int, List[date]]:
    """One seeded bounded local-search restart over *slots*' dates.

    Mutates *slots* during the search but always restores it to
    *baseline_dates* before returning — callers own applying whichever
    result (this seed's, another seed's, or the baseline) wins. Never
    accepts a swap that would push the season's hard-violation count, the
    season-wide turnaround gap totals, or *any single age group's*
    turnaround gaps worse than the input baseline. The cheap per-age-group
    gap check runs before the expensive full-candidate verifier call, so an
    infeasible move is rejected without materializing/verifying at all.
    """
    from .planning_contract import verify_candidate

    split_date = _parse_date((problem or {}).get("christmas_split_date"))
    allow_cross_half_moves = bool((problem or {}).get("allow_cross_half_moves"))

    def _materialize() -> Dict[str, Any]:
        result = dict(base_candidate_template)
        result["tournaments"] = [
            _rebuild_tournament(slot) if (slot.changed or slot.date_changed) else slot.tournament
            for slot in slots
        ] + untouched
        return result

    def _violation_count() -> int:
        return len(verify_candidate(_materialize(), problem)["violations"])

    def _score(violations: int, gaps_7: int, gaps_14: int) -> float:
        return violations * 1_000_000.0 + gaps_7 * 1_000.0 + gaps_14

    rng = random.Random(seed)
    current_v, current_g7, current_g14 = baseline_violations, baseline_gaps_7, baseline_gaps_14
    current_score = _score(current_v, current_g7, current_g14)
    best_v, best_g7, best_g14 = current_v, current_g7, current_g14
    best_score = current_score
    best_dates = list(baseline_dates)

    for step in range(iterations):
        if best_v == 0:
            break
        i, j = rng.sample(range(len(slots)), 2)
        if slots[i].date == slots[j].date:
            continue
        if not _date_swap_is_valid(
            slots, i, j, split_date=split_date, allow_cross_half_moves=allow_cross_half_moves
        ):
            continue

        _apply_date_swap(slots, i, j)

        trial_per_age_group_gaps = _per_age_group_gaps(slots)
        if not _gaps_within_bounds(trial_per_age_group_gaps, baseline_per_age_group_gaps):
            _apply_date_swap(slots, i, j)  # revert: self-inverse
            continue

        trial_g7 = sum(g7 for g7, _, _ in trial_per_age_group_gaps.values())
        trial_g14 = sum(g14 for _, g14, _ in trial_per_age_group_gaps.values())
        if trial_g7 > baseline_gaps_7 or trial_g14 > baseline_gaps_14:
            _apply_date_swap(slots, i, j)
            continue

        trial_v = _violation_count()
        if trial_v > baseline_violations:
            _apply_date_swap(slots, i, j)
            continue

        trial_score = _score(trial_v, trial_g7, trial_g14)
        delta = trial_score - current_score
        temperature = max(1e-6, 1.0 - step / iterations)
        accept = delta <= 0 or rng.random() < math.exp(-delta / (temperature * 1000.0))
        if accept:
            current_v, current_g7, current_g14 = trial_v, trial_g7, trial_g14
            current_score = trial_score
            if trial_score < best_score:
                best_score = trial_score
                best_v, best_g7, best_g14 = trial_v, trial_g7, trial_g14
                best_dates = [slot.date for slot in slots]
        else:
            _apply_date_swap(slots, i, j)

    for slot, d in zip(slots, baseline_dates):
        slot.date = d
        slot.date_changed = False

    return best_v, best_g7, best_g14, best_dates


def repair_schedule_conflicts_bounded(
    candidate: Dict[str, Any],
    problem: Optional[Dict[str, Any]] = None,
    *,
    iterations: int = 8000,
    seed: int = 0,
) -> Dict[str, Any]:
    """Single-seed baseline-bounded schedule-conflict repair (issue #257).

    Convenience wrapper around :func:`repair_schedule_conflicts_bounded_multi_seed`
    with one seed/restart.
    """
    return repair_schedule_conflicts_bounded_multi_seed(
        candidate, problem, seeds=(seed,), iterations=iterations
    )


def repair_schedule_conflicts_bounded_multi_seed(
    candidate: Dict[str, Any],
    problem: Optional[Dict[str, Any]] = None,
    *,
    seeds: Tuple[int, ...] = (0,),
    iterations: int = 8000,
) -> Dict[str, Any]:
    """Baseline-bounded date-swap-only repair for hard schedule conflicts,
    with multiple seeds/restarts (issue #257 skeleton follow-up).

    Searches for a sequence of tournament-date swaps (arena/host/roster
    unchanged) that reduces the season's hard-violation count — e.g.
    `arena_interval_conflict` — without pushing turnaround gaps worse than
    the input. Runs across the whole season at once (not per age group),
    since a skeleton conflict like two different age groups sharing an
    arena at an overlapping time can only be fixed by a swap that spans
    them. Keeps whichever seed's result is the best (fewest violations,
    then fewest turnaround-gap regressions); the season is returned
    byte-for-byte unchanged if no seed improves on the baseline, including
    when the baseline already has zero hard violations (nothing to repair).
    """
    from .planning_contract import verify_candidate

    slots, untouched = _build_slots(candidate, problem)
    if len(slots) < 2 or iterations <= 0 or not seeds:
        return dict(candidate)

    base_candidate_template = dict(candidate)
    base_candidate_template["schema_version"] = candidate.get("schema_version", CANDIDATE_SCHEMA_VERSION)

    def _materialize_initial() -> Dict[str, Any]:
        result = dict(base_candidate_template)
        result["tournaments"] = [slot.tournament for slot in slots] + untouched
        return result

    baseline_candidate = _materialize_initial()
    baseline_violations = len(verify_candidate(baseline_candidate, problem)["violations"])

    if baseline_violations == 0:
        result = dict(candidate)
        result["source"] = {
            "planner": "stage3_optimizer_schedule_repair",
            "base_source": candidate.get("source"),
            "status": "unchanged",
            "reason": "baseline already has zero hard violations; nothing to repair",
            "seed_used": None,
            "baseline_violations": 0,
            "best_violations": 0,
        }
        return result

    baseline_per_age_group_gaps = _per_age_group_gaps(slots)
    baseline_gaps_7 = sum(g7 for g7, _, _ in baseline_per_age_group_gaps.values())
    baseline_gaps_14 = sum(g14 for _, g14, _ in baseline_per_age_group_gaps.values())
    baseline_dates = [slot.date for slot in slots]

    overall_best_violations = baseline_violations
    overall_best_gaps_7 = baseline_gaps_7
    overall_best_gaps_14 = baseline_gaps_14
    overall_best_dates = baseline_dates
    overall_best_seed: Optional[int] = None

    for run_seed in seeds:
        trial_violations, trial_gaps_7, trial_gaps_14, trial_dates = _repair_search(
            slots,
            untouched,
            base_candidate_template,
            problem,
            baseline_violations,
            baseline_gaps_7,
            baseline_gaps_14,
            baseline_per_age_group_gaps,
            baseline_dates,
            iterations,
            run_seed,
        )
        improved = trial_violations < overall_best_violations or (
            trial_violations == overall_best_violations
            and (trial_gaps_7 < overall_best_gaps_7 or trial_gaps_14 < overall_best_gaps_14)
        )
        if improved:
            overall_best_violations = trial_violations
            overall_best_gaps_7 = trial_gaps_7
            overall_best_gaps_14 = trial_gaps_14
            overall_best_dates = trial_dates
            overall_best_seed = run_seed

    if overall_best_seed is not None:
        for slot, d in zip(slots, overall_best_dates):
            if d != slot.date:
                slot.date = d
                slot.date_changed = True
        status = "improved"
        reason = None
    else:
        for slot, d in zip(slots, baseline_dates):
            slot.date = d
            slot.date_changed = False
        status = "unchanged"
        reason = "no seed found a date-swap sequence that reduces hard violations without regressing turnaround gaps"

    result = dict(base_candidate_template)
    result["tournaments"] = [
        _rebuild_tournament(slot) if (slot.changed or slot.date_changed) else slot.tournament
        for slot in slots
    ] + untouched
    result["source"] = {
        "planner": "stage3_optimizer_schedule_repair",
        "base_source": candidate.get("source"),
        "iterations": iterations,
        "seeds": list(seeds),
        "status": status,
        "reason": reason,
        "seed_used": overall_best_seed,
        "baseline_violations": baseline_violations,
        "best_violations": overall_best_violations,
        "baseline_gaps_under_7": baseline_gaps_7,
        "best_gaps_under_7": overall_best_gaps_7,
        "baseline_gaps_under_14": baseline_gaps_14,
        "best_gaps_under_14": overall_best_gaps_14,
    }
    return result
