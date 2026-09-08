"""Fixed-skeleton CP-SAT participant optimizer for Stage 3 shadow runs.

Issue #276 deliberately starts with the smallest useful CP-SAT search surface:
tournament dates, hosts, arenas, start times, capacities and tournament count
are immutable. Only participant/team assignment is optimized. The independent
``verify_candidate`` contract remains authoritative; this module merely emits a
candidate using equivalent structural constraints where useful.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from itertools import combinations
from time import perf_counter
from typing import Any, Dict, Iterable, Optional, Tuple

from .game_generation import generate_round_robin_games
from .models import Team
from .planning_contract import CANDIDATE_SCHEMA_VERSION, _parse_date, _team_identity

TeamIdentity = Tuple[str, str, str]
ENGINE_VERSION = 1


class CpSatUnavailable(RuntimeError):
    """Raised when OR-Tools is not installed in the current environment."""


class CpSatNoCandidate(RuntimeError):
    """Raised when CP-SAT cannot return a feasible candidate within its budget."""

    def __init__(
        self,
        status: str,
        runtime_seconds: float,
        *,
        baseline_candidate_fingerprint: Optional[str] = None,
        problem_fingerprint: Optional[str] = None,
    ) -> None:
        self.status = status
        self.runtime_seconds = runtime_seconds
        self.baseline_candidate_fingerprint = baseline_candidate_fingerprint
        self.problem_fingerprint = problem_fingerprint
        super().__init__(f"CP-SAT returned no candidate ({status})")


@dataclass(frozen=True)
class _TournamentSlot:
    index: int
    tournament_id: str
    age_group: str
    on_date: date
    host_club: str
    roster_size: int
    baseline_team_ids: tuple[TeamIdentity, ...]
    pinned: bool


def _registered_team_map(
    candidate: Dict[str, Any], problem: Optional[Dict[str, Any]]
) -> Dict[TeamIdentity, Dict[str, Any]]:
    """Return the participant universe, preserving original team payloads."""
    result: Dict[TeamIdentity, Dict[str, Any]] = {}
    if problem:
        for raw in problem.get("teams", []) or []:
            if isinstance(raw, dict):
                result[_team_identity(raw)] = dict(raw)
    for tournament in candidate.get("tournaments", []) or []:
        for raw in tournament.get("teams", []) or []:
            if isinstance(raw, dict):
                result.setdefault(_team_identity(raw), dict(raw))
    return result


def _active_slots(candidate: Dict[str, Any], problem: Optional[Dict[str, Any]]) -> list[_TournamentSlot]:
    pinned_ids = {
        str(value)
        for value in (((problem or {}).get("manual_adjustments") or {}).get("pinned_tournament_ids", []) or [])
    }
    slots: list[_TournamentSlot] = []
    for index, tournament in enumerate(candidate.get("tournaments", []) or []):
        if tournament.get("cancelled"):
            continue
        on_date = _parse_date(tournament.get("date"))
        if on_date is None:
            continue
        baseline = tuple(_team_identity(team) for team in tournament.get("teams", []) or [])
        tournament_id = str(tournament.get("id", index))
        slots.append(
            _TournamentSlot(
                index=index,
                tournament_id=tournament_id,
                age_group=str(tournament.get("age_group", "")),
                on_date=on_date,
                host_club=str(tournament.get("host_club") or ""),
                roster_size=len(baseline),
                baseline_team_ids=baseline,
                pinned=tournament_id in pinned_ids,
            )
        )
    return slots


def _baseline_same_club_pairings(slots: Iterable[_TournamentSlot]) -> int:
    """Count same-club round-robin games implied by baseline rosters."""
    total = 0
    for slot in slots:
        for left, right in combinations(slot.baseline_team_ids, 2):
            if left[0] == right[0]:
                total += 1
    return total


def _parallel_games(tournament: Dict[str, Any], problem: Optional[Dict[str, Any]]) -> int:
    if problem:
        configured = (problem.get("parallel_games") or {}).get(tournament.get("age_group"))
        if isinstance(configured, int) and configured > 0:
            return configured
    games = tournament.get("games", []) or []
    if games:
        return max(int(game.get("parallel_slot", 0)) for game in games) + 1
    return 1


def _team_dict(identity: TeamIdentity, source: Dict[TeamIdentity, Dict[str, Any]]) -> Dict[str, Any]:
    raw = source.get(identity)
    if raw is not None:
        return dict(raw)
    club, label, age_group = identity
    return {"club": club, "label": label, "age_group": age_group}


def _games_to_dicts(teams: list[Team], parallel_games: int) -> list[Dict[str, Any]]:
    return [
        {
            "home": game.home.label,
            "away": game.away.label,
            "parallel_slot": game.parallel_slot,
            "round_number": game.round_number,
        }
        for game in generate_round_robin_games(teams, parallel_games)
    ]


def optimize_candidate_cp_sat(
    candidate: Dict[str, Any],
    problem: Optional[Dict[str, Any]],
    *,
    solve_budget_seconds: float = 30.0,
    seed: int = 0,
) -> Dict[str, Any]:
    """Optimize participant assignment while preserving the baseline skeleton.

    Structural guarantees in this first shadow model:

    * dates/hosts/arenas/start times and tournament count are copied verbatim;
    * every tournament keeps exactly its baseline roster size;
    * every team keeps exactly its baseline season participation count;
    * a team cannot appear in two tournaments on the same date;
    * pinned tournaments keep their exact participant set;
    * when the baseline tournament contains a host-club team, the optimized
      tournament still contains at least one host-club team.

    Participant counts intentionally stay equal to the baseline even when the
    baseline itself contains a known manual shortfall. That isolates opponent
    assignment quality from season-shape/capacity work for the first CP-SAT
    proof and guarantees participation cannot regress. The independent A/B
    report remains responsible for deciding whether the candidate is useful.
    """
    try:
        from ortools.sat.python import cp_model
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise CpSatUnavailable(
            "OR-Tools is required for engine='cp-sat' (install the cpsat extra)"
        ) from exc

    from .pipeline.fingerprints import stable_payload_sha256

    baseline_fingerprint = stable_payload_sha256(candidate.get("tournaments", []))
    problem_fingerprint = stable_payload_sha256(problem) if problem is not None else None

    started = perf_counter()
    slots = _active_slots(candidate, problem)
    if not slots:
        result = deepcopy(candidate)
        result.setdefault("schema_version", CANDIDATE_SCHEMA_VERSION)
        result["source"] = {
            "planner": "cp_sat",
            "engine_version": ENGINE_VERSION,
            "status": "EMPTY",
            "runtime_seconds": 0.0,
            "solve_budget_seconds": float(solve_budget_seconds),
            "fixed_skeleton": True,
            "base_source": candidate.get("source"),
            "baseline_candidate_fingerprint": baseline_fingerprint,
            "problem_fingerprint": problem_fingerprint,
            "candidate_fingerprint": stable_payload_sha256(result.get("tournaments", [])),
        }
        return result

    team_map = _registered_team_map(candidate, problem)
    teams_by_age_group: dict[str, list[TeamIdentity]] = defaultdict(list)
    for identity in sorted(team_map):
        teams_by_age_group[identity[2]].append(identity)

    baseline_participations: Counter[TeamIdentity] = Counter()
    for slot in slots:
        baseline_participations.update(slot.baseline_team_ids)
        # Older planning_problem snapshots can omit a team already present in
        # the candidate. Keep the candidate as a safe fallback universe.
        known = set(teams_by_age_group[slot.age_group])
        for identity in slot.baseline_team_ids:
            if identity not in known:
                teams_by_age_group[slot.age_group].append(identity)
                known.add(identity)

    # Stable numeric indexes keep OR-Tools variable names compact and avoid
    # relying on arbitrary team labels being safe solver identifiers.
    team_index = {identity: idx for idx, identity in enumerate(sorted(team_map))}
    next_team_index = len(team_index)
    for identity in baseline_participations:
        if identity not in team_index:
            team_index[identity] = next_team_index
            next_team_index += 1

    model = cp_model.CpModel()
    x: dict[tuple[int, TeamIdentity], Any] = {}

    for slot in slots:
        eligible = teams_by_age_group.get(slot.age_group, [])
        for identity in eligible:
            x[(slot.index, identity)] = model.NewBoolVar(
                f"x_t{slot.index}_team{team_index[identity]}"
            )
        model.Add(sum(x[(slot.index, identity)] for identity in eligible) == slot.roster_size)

        if slot.pinned:
            baseline_set = set(slot.baseline_team_ids)
            for identity in eligible:
                model.Add(x[(slot.index, identity)] == int(identity in baseline_set))

        baseline_host_count = sum(1 for identity in slot.baseline_team_ids if identity[0] == slot.host_club)
        if slot.host_club and baseline_host_count:
            host_vars = [x[(slot.index, identity)] for identity in eligible if identity[0] == slot.host_club]
            if host_vars:
                model.Add(sum(host_vars) >= 1)

    # Preserve each team's baseline participation exactly. Because every
    # tournament roster size is also fixed, this prevents newly registered
    # but baseline-unused teams from silently entering the shadow candidate.
    slot_indexes_by_age_group: dict[str, list[int]] = defaultdict(list)
    for slot in slots:
        slot_indexes_by_age_group[slot.age_group].append(slot.index)
    for identity, baseline_count in baseline_participations.items():
        model.Add(
            sum(
                x[(slot_index, identity)]
                for slot_index in slot_indexes_by_age_group.get(identity[2], [])
                if (slot_index, identity) in x
            )
            == baseline_count
        )

    # No duplicate participation on one date.
    slots_by_age_date: dict[tuple[str, date], list[int]] = defaultdict(list)
    for slot in slots:
        slots_by_age_date[(slot.age_group, slot.on_date)].append(slot.index)
    for identity in baseline_participations:
        for (age_group, _on_date), slot_indexes in slots_by_age_date.items():
            if age_group != identity[2] or len(slot_indexes) < 2:
                continue
            vars_on_date = [x[(slot_index, identity)] for slot_index in slot_indexes if (slot_index, identity) in x]
            if len(vars_on_date) > 1:
                model.Add(sum(vars_on_date) <= 1)

    # Pair co-occurrence variables. Since tournament roster sizes are fixed,
    # total pair exposure is fixed; minimizing repeat excess therefore
    # maximizes distinct opponent pairs without a second definition of
    # capacity/participation correctness.
    meet_by_pair: dict[tuple[TeamIdentity, TeamIdentity], list[Any]] = defaultdict(list)
    same_club_terms: list[Any] = []
    pair_serial = 0
    for slot in slots:
        eligible = teams_by_age_group.get(slot.age_group, [])
        for left, right in combinations(eligible, 2):
            meet = model.NewBoolVar(f"meet_t{slot.index}_p{pair_serial}")
            pair_serial += 1
            model.Add(meet <= x[(slot.index, left)])
            model.Add(meet <= x[(slot.index, right)])
            model.Add(meet >= x[(slot.index, left)] + x[(slot.index, right)] - 1)
            pair = tuple(sorted((left, right)))
            meet_by_pair[pair].append(meet)
            if left[0] == right[0]:
                same_club_terms.append(meet)

    repeat_excess_terms: list[Any] = []
    third_plus_excess_terms: list[Any] = []
    for pair_idx, meet_vars in enumerate(meet_by_pair.values()):
        count = model.NewIntVar(0, len(meet_vars), f"pair_count_{pair_idx}")
        model.Add(count == sum(meet_vars))
        excess_one = model.NewIntVar(0, len(meet_vars), f"repeat_excess_{pair_idx}")
        model.Add(excess_one >= count - 1)
        repeat_excess_terms.append(excess_one)
        excess_two = model.NewIntVar(0, len(meet_vars), f"third_plus_{pair_idx}")
        model.Add(excess_two >= count - 2)
        third_plus_excess_terms.append(excess_two)

    # Same-club pairings are a protected dimension in the first experiment:
    # CP-SAT may improve them but may not return more than the baseline.
    baseline_same_club = _baseline_same_club_pairings(slots)
    if same_club_terms:
        model.Add(sum(same_club_terms) <= baseline_same_club)

    # Secondary turnaround signal. This counts close assignment pairs, not
    # score_candidate's exact consecutive-gap metric, so it is deliberately an
    # objective hint rather than a hard correctness definition. The A/B report
    # still rejects/flags any actual protected-metric regression.
    gap_under_7_terms: list[Any] = []
    gap_under_14_terms: list[Any] = []
    slot_by_index = {slot.index: slot for slot in slots}
    for identity in baseline_participations:
        indexes = sorted(
            slot_indexes_by_age_group.get(identity[2], []),
            key=lambda idx: (slot_by_index[idx].on_date, idx),
        )
        for left_index, right_index in combinations(indexes, 2):
            gap = abs((slot_by_index[right_index].on_date - slot_by_index[left_index].on_date).days)
            if gap >= 14:
                continue
            both = model.NewBoolVar(
                f"shortgap_team{team_index[identity]}_{left_index}_{right_index}"
            )
            model.Add(both <= x[(left_index, identity)])
            model.Add(both <= x[(right_index, identity)])
            model.Add(both >= x[(left_index, identity)] + x[(right_index, identity)] - 1)
            if gap < 7:
                gap_under_7_terms.append(both)
            else:
                gap_under_14_terms.append(both)

    objective_terms: list[Any] = []
    # Repeated opponents dominate the first model. Third-and-later repeats
    # get an additional penalty; same-club pairings and short turnaround are
    # secondary tie-breakers.
    objective_terms.extend(1000 * term for term in repeat_excess_terms)
    objective_terms.extend(2000 * term for term in third_plus_excess_terms)
    objective_terms.extend(25 * term for term in same_club_terms)
    objective_terms.extend(10 * term for term in gap_under_7_terms)
    objective_terms.extend(2 * term for term in gap_under_14_terms)
    if objective_terms:
        model.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.1, float(solve_budget_seconds))
    solver.parameters.random_seed = int(seed)
    # One worker favors reproducibility for a fixed model/seed/OR-Tools
    # version. The experiment is bounded by wall-clock budget, not optimality.
    solver.parameters.num_search_workers = 1
    status_code = solver.Solve(model)
    runtime_seconds = perf_counter() - started
    status_name = solver.StatusName(status_code)

    if status_code not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise CpSatNoCandidate(
            status_name,
            runtime_seconds,
            baseline_candidate_fingerprint=baseline_fingerprint,
            problem_fingerprint=problem_fingerprint,
        )

    result = deepcopy(candidate)
    result.setdefault("schema_version", CANDIDATE_SCHEMA_VERSION)
    tournaments = result.get("tournaments", []) or []

    for slot in slots:
        eligible = teams_by_age_group.get(slot.age_group, [])
        selected = [identity for identity in eligible if solver.Value(x[(slot.index, identity)])]
        selected.sort(
            key=lambda identity: (
                identity[0] != slot.host_club,
                identity[0],
                identity[1],
                identity[2],
            )
        )
        tournament = tournaments[slot.index]
        tournament["teams"] = [_team_dict(identity, team_map) for identity in selected]
        model_teams = [
            Team(
                club=team["club"],
                label=team["label"],
                age_group=team["age_group"],
                target_tournament_count=team.get("target_tournament_count"),
            )
            for team in tournament["teams"]
        ]
        tournament["games"] = _games_to_dicts(
            model_teams,
            _parallel_games(tournament, problem),
        )

    result["source"] = {
        "planner": "cp_sat",
        "engine_version": ENGINE_VERSION,
        "status": status_name,
        "runtime_seconds": round(runtime_seconds, 6),
        "solve_budget_seconds": float(solve_budget_seconds),
        "fixed_skeleton": True,
        "objective_value": float(solver.ObjectiveValue()) if objective_terms else 0.0,
        "best_objective_bound": float(solver.BestObjectiveBound()) if objective_terms else 0.0,
        "seed": int(seed),
        "protected_same_club_pairings_baseline": baseline_same_club,
        "base_source": candidate.get("source"),
        "baseline_candidate_fingerprint": baseline_fingerprint,
        "problem_fingerprint": problem_fingerprint,
        "candidate_fingerprint": stable_payload_sha256(result.get("tournaments", [])),
        "encoded_scope": {
            "moves": ["participant_assignment"],
            "fixed": ["dates", "hosts", "arenas", "start_times", "roster_sizes", "tournament_count"],
            "participation": "preserve_baseline_exactly",
            "same_club_pairings": "no_worse_than_baseline",
            "turnaround": "soft_pairwise_hint_only",
        },
    }
    return result
