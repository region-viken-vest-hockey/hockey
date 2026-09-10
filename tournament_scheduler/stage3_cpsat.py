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

from . import planning_half
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
        diagnostics: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.status = status
        self.runtime_seconds = runtime_seconds
        self.baseline_candidate_fingerprint = baseline_candidate_fingerprint
        self.problem_fingerprint = problem_fingerprint
        # issue #298: solver-size/model diagnostics (team/slot/variable/
        # constraint counts, half, budget) so a no-candidate result is
        # explainable evidence rather than an opaque timeout -- the caller
        # can tell a genuinely oversized/complex model apart from a solver
        # fluke without re-instrumenting anything.
        self.diagnostics = dict(diagnostics or {})
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


def _resolve_split_date(
    problem: Optional[Dict[str, Any]], slots: "list[_TournamentSlot]"
) -> Optional[date]:
    """Resolve the shared Christmas-half boundary for *slots* (issue #298).

    Prefers ``problem["christmas_split_date"]`` -- the single boundary every
    engine already agrees on (:mod:`planning_half`) -- and only falls back to
    deriving it from the slot dates themselves when no problem contract is
    available (e.g. a bare candidate/tests).
    """
    raw = (problem or {}).get("christmas_split_date")
    if raw:
        parsed = _parse_date(raw)
        if parsed is not None:
            return parsed
    dates = [slot.on_date for slot in slots]
    if not dates:
        return None
    return planning_half.christmas_split_date(min(dates), max(dates))


def _group_slots_by_half(
    slots: "list[_TournamentSlot]", split_date: Optional[date]
) -> "list[tuple[str, list[_TournamentSlot]]]":
    """Partition *slots* into independent before/after-Christmas solver groups.

    Uses the shared :mod:`planning_half` contract from issue #293 as the
    solver boundary (issue #298 Phase 2): each half is solved as its own
    smaller participant-assignment model instead of one monolithic Oct-Apr
    model, which shrinks the assignment/pair-variable count and keeps a
    half-2 registration change from perturbing half-1's solve. Empty halves
    are dropped rather than solved as a no-op.
    """
    groups: "dict[str, list[_TournamentSlot]]" = defaultdict(list)
    for slot in slots:
        groups[planning_half.tournament_half(slot.on_date, split_date)].append(slot)
    ordered_labels = ["before_christmas", "after_christmas", "unsplit"]
    return [(label, groups[label]) for label in ordered_labels if groups.get(label)]


def _solve_slot_group(
    slots: "list[_TournamentSlot]",
    candidate: Dict[str, Any],
    problem: Optional[Dict[str, Any]],
    team_map: Dict[TeamIdentity, Dict[str, Any]],
    teams_by_age_group: "dict[str, list[TeamIdentity]]",
    team_index: "dict[TeamIdentity, int]",
    *,
    solve_budget_seconds: float,
    seed: int,
    feasibility_only: bool,
    half_label: str,
    baseline_fingerprint: Optional[str],
    problem_fingerprint: Optional[str],
) -> "tuple[dict[int, dict[str, Any]], dict[str, Any]]":
    """Build and solve one participant-assignment CP-SAT model over *slots*.

    Returns ``(patches, diagnostics)`` where *patches* maps each solved
    slot's ``tournament`` index to its new ``teams``/``games``, and
    *diagnostics* is the issue #298 solver-evidence record (team/slot/
    variable/constraint/hint counts, half, budget, status, runtime,
    objective) -- persisted regardless of outcome so a caller can tell a
    genuinely oversized/complex model apart from a solver fluke. Raises
    :class:`CpSatNoCandidate` (with *diagnostics* attached) when the solver
    does not reach ``OPTIMAL``/``FEASIBLE`` within budget.
    """
    from ortools.sat.python import cp_model

    started = perf_counter()
    model = cp_model.CpModel()
    x: "dict[tuple[int, TeamIdentity], Any]" = {}

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

    # Preserve each team's baseline participation exactly *within this
    # group*. When solving a single combined group this is the team's whole
    # season count; when decomposed by half (issue #298 Phase 2) this is the
    # team's baseline count for that half specifically, which is what makes
    # each half an independently valid participant-assignment problem.
    baseline_participations: Counter[TeamIdentity] = Counter()
    for slot in slots:
        baseline_participations.update(slot.baseline_team_ids)
    slot_indexes_by_age_group: "dict[str, list[int]]" = defaultdict(list)
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
    slots_by_age_date: "dict[tuple[str, date], list[int]]" = defaultdict(list)
    for slot in slots:
        slots_by_age_date[(slot.age_group, slot.on_date)].append(slot.index)

    # issue #298 Phase 1: unlike every other constraint above (roster size,
    # pinned membership, host presence, participation count), this one is
    # *not* automatically satisfied by a baseline built from its own slots --
    # it actively forbids something the baseline may already do (the same
    # team appearing in two same-age-group tournaments on one date, e.g. two
    # parallel pools scheduled the same day). Check the baseline against it
    # before adding it as a hard constraint, so a genuinely conflicting
    # baseline is reported as "here is exactly which invariant conflicts"
    # rather than handed to the solver to discover as an opaque INFEASIBLE.
    baseline_duplicate_date_conflicts: "list[Dict[str, Any]]" = []
    for slot in slots:
        for identity in slot.baseline_team_ids:
            slot_indexes = slots_by_age_date.get((slot.age_group, slot.on_date), [])
            same_date_slots = sorted(
                {
                    other.tournament_id
                    for other in slots
                    if other.index in slot_indexes and identity in other.baseline_team_ids
                }
            )
            if len(same_date_slots) > 1:
                baseline_duplicate_date_conflicts.append(
                    {
                        "team": ":".join(identity),
                        "age_group": slot.age_group,
                        "date": slot.on_date.isoformat(),
                        "tournament_ids": same_date_slots,
                    }
                )
    # Each conflicting (team, age_group, date) triple is discovered once per
    # slot it touches; de-duplicate before reporting.
    baseline_duplicate_date_conflicts = [
        dict(conflict)
        for conflict in {
            (conflict["team"], conflict["age_group"], conflict["date"]): conflict
            for conflict in baseline_duplicate_date_conflicts
        }.values()
    ]
    if baseline_duplicate_date_conflicts:
        diagnostics = {
            "half": half_label,
            "mode": "feasibility_only" if feasibility_only else "quality",
            "team_count": len(team_map),
            "slot_count": len(slots),
            "solve_budget_seconds": float(solve_budget_seconds),
            "seed": int(seed),
            "status": "BASELINE_CONSTRAINT_CONFLICT",
            "runtime_seconds": round(perf_counter() - started, 6),
            "violated_constraint": "no_duplicate_participation_on_one_date",
            "baseline_conflicts": baseline_duplicate_date_conflicts,
        }
        raise CpSatNoCandidate(
            "BASELINE_CONSTRAINT_CONFLICT",
            perf_counter() - started,
            baseline_candidate_fingerprint=baseline_fingerprint,
            problem_fingerprint=problem_fingerprint,
            diagnostics=diagnostics,
        )

    for identity in baseline_participations:
        for (age_group, _on_date), slot_indexes in slots_by_age_date.items():
            if age_group != identity[2] or len(slot_indexes) < 2:
                continue
            vars_on_date = [x[(slot_index, identity)] for slot_index in slot_indexes if (slot_index, identity) in x]
            if len(vars_on_date) > 1:
                model.Add(sum(vars_on_date) <= 1)

    pair_serial = 0
    objective_terms: "list[Any]" = []
    baseline_same_club = _baseline_same_club_pairings(slots)

    if feasibility_only:
        # issue #298 Phase 1: a dedicated feasibility-only mode that proves
        # the model can reproduce a valid assignment from the baseline
        # skeleton, without the pair/co-occurrence/objective machinery that
        # exists purely for quality optimization. Kept as a distinct code
        # path (rather than just an early Minimize skip) so the model size
        # itself is smaller and the solver stops at the first solution --
        # this isolates "can the hard constraints be satisfied at all" from
        # "how good is the optimized result".
        meet_by_pair: "dict[tuple[TeamIdentity, TeamIdentity], list[Any]]" = {}
    else:
        # Pair co-occurrence variables. Since tournament roster sizes are
        # fixed, total pair exposure is fixed; minimizing repeat excess
        # therefore maximizes distinct opponent pairs without a second
        # definition of capacity/participation correctness.
        meet_by_pair = defaultdict(list)
        same_club_terms: "list[Any]" = []
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

        repeat_excess_terms: "list[Any]" = []
        third_plus_excess_terms: "list[Any]" = []
        for pair_idx, meet_vars in enumerate(meet_by_pair.values()):
            count = model.NewIntVar(0, len(meet_vars), f"pair_count_{pair_idx}")
            model.Add(count == sum(meet_vars))
            excess_one = model.NewIntVar(0, len(meet_vars), f"repeat_excess_{pair_idx}")
            model.Add(excess_one >= count - 1)
            repeat_excess_terms.append(excess_one)
            excess_two = model.NewIntVar(0, len(meet_vars), f"third_plus_{pair_idx}")
            model.Add(excess_two >= count - 2)
            third_plus_excess_terms.append(excess_two)

        # Same-club pairings are a protected dimension in the first
        # experiment: CP-SAT may improve them but may not return more than
        # the baseline.
        if same_club_terms:
            model.Add(sum(same_club_terms) <= baseline_same_club)

        # Secondary turnaround signal. This counts close assignment pairs,
        # not score_candidate's exact consecutive-gap metric, so it is
        # deliberately an objective hint rather than a hard correctness
        # definition. The A/B report still rejects/flags any actual
        # protected-metric regression.
        gap_under_7_terms: "list[Any]" = []
        gap_under_14_terms: "list[Any]" = []
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

        # Repeated opponents dominate the first model. Third-and-later
        # repeats get an additional penalty; same-club pairings and short
        # turnaround are secondary tie-breakers.
        objective_terms.extend(1000 * term for term in repeat_excess_terms)
        objective_terms.extend(2000 * term for term in third_plus_excess_terms)
        objective_terms.extend(25 * term for term in same_club_terms)
        objective_terms.extend(10 * term for term in gap_under_7_terms)
        objective_terms.extend(2 * term for term in gap_under_14_terms)
        if objective_terms:
            model.Minimize(sum(objective_terms))

    # issue #298: the baseline candidate is always itself a feasible
    # assignment for this model (every constraint above was derived from it
    # -- same roster sizes, same participation counts, same-or-fewer
    # same-club pairings, pinned/host membership already satisfied) but
    # unhinted CP-SAT still has to *rediscover* that from scratch, and can
    # burn its entire time budget searching without ever reporting
    # FEASIBLE/OPTIMAL on a large enough model. Hinting every decision
    # variable at its baseline value gives the solver a known-feasible
    # starting point to validate/repair immediately, so a solve that would
    # otherwise time out at UNKNOWN can still return the baseline (or better)
    # within budget.
    for slot in slots:
        eligible = teams_by_age_group.get(slot.age_group, [])
        baseline_set = set(slot.baseline_team_ids)
        for identity in eligible:
            model.AddHint(x[(slot.index, identity)], 1 if identity in baseline_set else 0)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.1, float(solve_budget_seconds))
    solver.parameters.random_seed = int(seed)
    # One worker favors reproducibility for a fixed model/seed/OR-Tools
    # version. The experiment is bounded by wall-clock budget, not optimality.
    solver.parameters.num_search_workers = 1
    if feasibility_only:
        # Stop the instant any feasible solution (the hinted baseline, in
        # practice) is found -- feasibility proof, not quality search.
        solver.parameters.stop_after_first_solution = True
    status_code = solver.Solve(model)
    runtime_seconds = perf_counter() - started
    status_name = solver.StatusName(status_code)

    has_solution = status_code in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    team_count = len({identity for slot in slots for identity in teams_by_age_group.get(slot.age_group, [])})
    diagnostics: Dict[str, Any] = {
        "half": half_label,
        "mode": "feasibility_only" if feasibility_only else "quality",
        "team_count": team_count,
        "slot_count": len(slots),
        "assignment_var_count": len(x),
        "pair_var_count": pair_serial,
        "constraint_count": len(model.Proto().constraints),
        "hint_count": len(x),
        "solve_budget_seconds": float(solve_budget_seconds),
        "seed": int(seed),
        "status": status_name,
        "runtime_seconds": round(runtime_seconds, 6),
        "objective_value": float(solver.ObjectiveValue()) if has_solution and objective_terms else 0.0,
        "best_objective_bound": float(solver.BestObjectiveBound()) if has_solution and objective_terms else 0.0,
        "protected_same_club_pairings_baseline": baseline_same_club,
    }

    if status_code not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise CpSatNoCandidate(
            status_name,
            runtime_seconds,
            baseline_candidate_fingerprint=baseline_fingerprint,
            problem_fingerprint=problem_fingerprint,
            diagnostics=diagnostics,
        )

    tournaments_by_index = {slot.index: candidate["tournaments"][slot.index] for slot in slots}
    patches: "dict[int, dict[str, Any]]" = {}
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
        teams = [_team_dict(identity, team_map) for identity in selected]
        model_teams = [
            Team(
                club=team["club"],
                label=team["label"],
                age_group=team["age_group"],
                target_tournament_count=team.get("target_tournament_count"),
            )
            for team in teams
        ]
        games = _games_to_dicts(
            model_teams,
            _parallel_games(tournaments_by_index[slot.index], problem),
        )
        patches[slot.index] = {"teams": teams, "games": games}

    return patches, diagnostics


def optimize_candidate_cp_sat(
    candidate: Dict[str, Any],
    problem: Optional[Dict[str, Any]],
    *,
    solve_budget_seconds: float = 30.0,
    seed: int = 0,
    feasibility_only: bool = False,
    decompose_by_half: bool = False,
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

    *feasibility_only* (issue #298 Phase 1) builds only the hard constraints
    and stops at the first solution -- proving the model can reproduce a
    valid assignment from the baseline, isolated from quality search.

    *decompose_by_half* (issue #298 Phase 2) solves participant assignment
    independently for each :mod:`planning_half` (before/after Christmas)
    instead of one monolithic model, using ``problem["christmas_split_date"]``
    as the shared boundary. Each half preserves its own baseline
    participation counts exactly, so a half with no slots is simply skipped.

    *solve_budget_seconds* is a **total** wall-clock budget across every
    group actually solved (issue #310), not a per-group budget: it is
    divided evenly by the number of groups (1 when not decomposed, or the
    number of non-empty halves when decomposed) before being handed to each
    group's solver. Previously the same value was passed to every group
    unchanged, so a configured "15s" budget with ``decompose_by_half=True``
    could cost ~30s wall-clock (two halves each solving for the full 15s) --
    callers configuring a budget now get the total they asked for regardless
    of how many groups end up being solved.
    """
    try:
        from ortools.sat.python import cp_model  # noqa: F401 -- availability probe
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
            "mode": "feasibility_only" if feasibility_only else "quality",
            "decompose_by_half": bool(decompose_by_half),
            "half_diagnostics": [],
            "base_source": candidate.get("source"),
            "baseline_candidate_fingerprint": baseline_fingerprint,
            "problem_fingerprint": problem_fingerprint,
            "candidate_fingerprint": stable_payload_sha256(result.get("tournaments", [])),
        }
        return result

    team_map = _registered_team_map(candidate, problem)
    teams_by_age_group: "dict[str, list[TeamIdentity]]" = defaultdict(list)
    for identity in sorted(team_map):
        teams_by_age_group[identity[2]].append(identity)
    for slot in slots:
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
    for group_identities in teams_by_age_group.values():
        for identity in group_identities:
            if identity not in team_index:
                team_index[identity] = next_team_index
                next_team_index += 1

    if decompose_by_half:
        split_date = _resolve_split_date(problem, slots)
        groups = _group_slots_by_half(slots, split_date)
    else:
        groups = [("combined", slots)]

    per_group_budget_seconds = float(solve_budget_seconds) / max(1, len(groups))

    all_patches: "dict[int, dict[str, Any]]" = {}
    half_diagnostics: "list[Dict[str, Any]]" = []
    for half_label, group_slots in groups:
        patches, diagnostics = _solve_slot_group(
            group_slots,
            candidate,
            problem,
            team_map,
            teams_by_age_group,
            team_index,
            solve_budget_seconds=per_group_budget_seconds,
            seed=seed,
            feasibility_only=feasibility_only,
            half_label=half_label,
            baseline_fingerprint=baseline_fingerprint,
            problem_fingerprint=problem_fingerprint,
        )
        all_patches.update(patches)
        half_diagnostics.append(diagnostics)

    runtime_seconds = perf_counter() - started

    result = deepcopy(candidate)
    result.setdefault("schema_version", CANDIDATE_SCHEMA_VERSION)
    tournaments = result.get("tournaments", []) or []
    for slot_index, patch in all_patches.items():
        tournaments[slot_index]["teams"] = patch["teams"]
        tournaments[slot_index]["games"] = patch["games"]

    statuses = {d["status"] for d in half_diagnostics}
    overall_status = "OPTIMAL" if statuses == {"OPTIMAL"} else "FEASIBLE"
    total_objective = sum(d["objective_value"] for d in half_diagnostics)
    total_bound = sum(d["best_objective_bound"] for d in half_diagnostics)
    baseline_same_club_total = sum(
        d.get("protected_same_club_pairings_baseline", 0) for d in half_diagnostics
    )

    result["source"] = {
        "planner": "cp_sat",
        "engine_version": ENGINE_VERSION,
        "status": overall_status,
        "runtime_seconds": round(runtime_seconds, 6),
        "solve_budget_seconds": float(solve_budget_seconds),
        "fixed_skeleton": True,
        "objective_value": total_objective,
        "best_objective_bound": total_bound,
        "seed": int(seed),
        "baseline_hints": True,
        "mode": "feasibility_only" if feasibility_only else "quality",
        "decompose_by_half": bool(decompose_by_half),
        "half_diagnostics": half_diagnostics,
        "protected_same_club_pairings_baseline": baseline_same_club_total,
        "base_source": candidate.get("source"),
        "baseline_candidate_fingerprint": baseline_fingerprint,
        "problem_fingerprint": problem_fingerprint,
        "candidate_fingerprint": stable_payload_sha256(result.get("tournaments", [])),
        "encoded_scope": {
            "moves": ["participant_assignment"],
            "fixed": ["dates", "hosts", "arenas", "start_times", "roster_sizes", "tournament_count"],
            "participation": (
                "preserve_baseline_exactly_per_half" if decompose_by_half else "preserve_baseline_exactly"
            ),
            "same_club_pairings": "no_worse_than_baseline",
            "turnaround": "soft_pairwise_hint_only" if not feasibility_only else "not_modeled",
        },
    }
    return result
