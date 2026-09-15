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
from datetime import date
from itertools import combinations
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Dict, Optional

from .host_representation import clubs_represent_same_club as _clubs_represent_same_club
from .models import Team
from .effective_tournament_shape import compute_effective_tournament_shape, shape_violation
from .participant_roster_sizing import fixed_cohort_shape_for
from .planning_contract import CANDIDATE_SCHEMA_VERSION
from .stage3_cpsat_club_cap import build_club_excess_terms
from .stage3_cpsat_diagnostics import raise_host_not_represented
from .stage3_cpsat_slots import (
    TeamIdentity as TeamIdentity,
    _TournamentSlot as _TournamentSlot,
    _active_slots as _active_slots,
    _baseline_same_club_pairings as _baseline_same_club_pairings,
    _games_to_dicts as _games_to_dicts,
    _group_slots_by_half as _group_slots_by_half,
    _parallel_games as _parallel_games,
    _registered_team_map as _registered_team_map,
    _resolve_participation_target as _resolve_participation_target,
    _resolve_split_date as _resolve_split_date,
    _team_dict as _team_dict,
)

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
        # Effective-shape rule: judge the fixed skeleton's roster size against the
        # shape the *complete registered pool* for this age group actually
        # supports, not an unconditional even/exact-4 rule -- an
        # input-constrained shape (the whole pool is too small) is legal,
        # only an avoidable one (the pool could support a bigger no-bye
        # shape) is infeasible.
        registered_team_count = len(teams_by_age_group.get(slot.age_group, []))
        rounds_per_tournament = (problem or {}).get("rounds_per_tournament") or {}
        parallel_games_capacity = (problem or {}).get("parallel_games") or {}
        shape = compute_effective_tournament_shape(
            slot.age_group,
            registered_team_count,
            configured_rounds=rounds_per_tournament.get(slot.age_group),
            parallel_game_capacity=parallel_games_capacity.get(slot.age_group),
        )
        if shape_violation(shape, slot.roster_size, 0):
            raise CpSatNoCandidate(
                "INFEASIBLE_NO_BYE_ROSTER_SIZE",
                perf_counter() - started,
                baseline_candidate_fingerprint=baseline_fingerprint,
                problem_fingerprint=problem_fingerprint,
                diagnostics={
                    "reason": "bye_team_not_allowed",
                    "tournament_id": slot.tournament_id,
                    "age_group": slot.age_group,
                    "roster_size": slot.roster_size,
                    "required_team_count": shape.effective_team_count,
                },
            )
        eligible = teams_by_age_group.get(slot.age_group, [])
        for identity in eligible:
            x[(slot.index, identity)] = model.NewBoolVar(
                f"x_t{slot.index}_team{team_index[identity]}"
            )
        model.Add(sum(x[(slot.index, identity)] for identity in eligible) == slot.roster_size)

        fixed_cohort_shape = None
        if problem is not None:
            roster_view = SimpleNamespace(by_age_group=lambda group: teams_by_age_group.get(group, []))
            planner_view = SimpleNamespace(
                roster=roster_view,
                rounds_per_tournament_for_age_group=rounds_per_tournament,
                parallel_games_for_age_group=parallel_games_capacity,
            )
            fixed_cohort_shape = fixed_cohort_shape_for(planner_view, slot.age_group)

        if fixed_cohort_shape is not None:
            # Membership is not an optimization dimension for full-pool
            # shapes: every registered identity must appear in every slot.
            for identity in eligible:
                model.Add(x[(slot.index, identity)] == 1)
        elif slot.pinned:
            baseline_set = set(slot.baseline_team_ids)
            for identity in eligible:
                model.Add(x[(slot.index, identity)] == int(identity in baseline_set))

        if slot.host_club:
            host_vars = [x[(slot.index, i)] for i in eligible if _clubs_represent_same_club(i[0], slot.host_club)]
            if host_vars:
                model.Add(sum(host_vars) >= 1)
            else:
                # issue #323 P0: an empty host_vars here means the upstream
                # host-representation invariant was violated -- see
                # stage3_cpsat_diagnostics.raise_host_not_represented.
                raise_host_not_represented(
                    CpSatNoCandidate,
                    half_label=half_label,
                    feasibility_only=feasibility_only,
                    team_count=len(team_map),
                    slot_count=len(slots),
                    solve_budget_seconds=solve_budget_seconds,
                    seed=seed,
                    started=started,
                    tournament_id=slot.tournament_id,
                    host_club=slot.host_club,
                    age_group=slot.age_group,
                    baseline_fingerprint=baseline_fingerprint,
                    problem_fingerprint=problem_fingerprint,
                )

    baseline_participations: Counter[TeamIdentity] = Counter()
    for slot in slots:
        baseline_participations.update(slot.baseline_team_ids)
    slot_indexes_by_age_group: "dict[str, list[int]]" = defaultdict(list)
    for slot in slots:
        slot_indexes_by_age_group[slot.age_group].append(slot.index)

    # The participation/no-duplicate-date constraints below must cover every
    # registered/eligible team for the age groups actually represented in
    # *slots* -- not just identities already present in `baseline_participations`.
    # A registered team with zero baseline appearances in this half would
    # otherwise get assignment variables (from the eligibility loop above)
    # but no participation cap/target/deficit term and no same-date
    # exclusivity constraint, making it invisible to the target-authoritative
    # model precisely in the case (an under-target team the baseline
    # dropped entirely) that matters most.
    eligible_identities: "set[TeamIdentity]" = set()
    for age_group in {slot.age_group for slot in slots}:
        eligible_identities.update(teams_by_age_group.get(age_group, []))

    # The configured participation target (per-team override, or
    # the age group's authoritative before/after-Christmas value for this
    # half) is now what CP-SAT optimizes against -- never exceeded (hard
    # cap below), preferably met (deficit objective term further down) --
    # instead of preserving the baseline's own count as authoritative. The
    # baseline remains only a search hint (`AddHint` below). Identities with
    # no configured target (non-canonical age groups) keep the original
    # exact baseline-lock behavior as a defensive fallback.
    participation_deficit_terms: "list[Any]" = []
    for identity in eligible_identities:
        baseline_count = baseline_participations.get(identity, 0)
        vars_for_identity = [
            x[(slot_index, identity)]
            for slot_index in slot_indexes_by_age_group.get(identity[2], [])
            if (slot_index, identity) in x
        ]
        if not vars_for_identity:
            continue
        count_expr = sum(vars_for_identity)
        target = _resolve_participation_target(identity, team_map, problem, half_label)
        if target is None:
            model.Add(count_expr == baseline_count)
            continue
        model.Add(count_expr <= target)
        if not feasibility_only:
            deficit = model.NewIntVar(0, target, f"participation_deficit_team{team_index[identity]}")
            model.Add(deficit >= target - count_expr)
            participation_deficit_terms.append(deficit)

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

    # Uses `eligible_identities`, not just `baseline_participations`, so a
    # registered team CP-SAT newly assigns into a half (zero baseline
    # appearances) is still bound by this hard rule instead of relying on
    # the independent verifier to catch it after the fact.
    for identity in eligible_identities:
        for (age_group, _on_date), slot_indexes in slots_by_age_date.items():
            if age_group != identity[2] or len(slot_indexes) < 2:
                continue
            vars_on_date = [x[(slot_index, identity)] for slot_index in slot_indexes if (slot_index, identity) in x]
            if len(vars_on_date) > 1:
                model.Add(sum(vars_on_date) <= 1)

    pair_serial = 0
    objective_terms: "list[Any]" = []
    # Closing a configured participation gap outranks pairing
    # quality (repeat-opponent penalties below top out at 2000) so the
    # solver prioritizes meeting the target over tie-breaking on variety.
    objective_terms.extend(5000 * term for term in participation_deficit_terms)
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

        # issue #324: explicit per-(slot, club) 3rd-or-later concentration
        # term -- see stage3_cpsat_club_cap.build_club_excess_terms.
        club_excess_terms = build_club_excess_terms(model, x, slots, teams_by_age_group)

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
        for identity in eligible_identities:
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
        # turnaround are secondary tie-breakers. issue #324: a 3rd-or-later
        # team from one club outweighs every other quality term below (and
        # every repeat-opponent term) so it stays legal only when forced by
        # the hard constraints above -- only the participation-deficit terms
        # (weight 5000, closing a configured participation gap) outrank it.
        objective_terms.extend(1000 * term for term in repeat_excess_terms)
        objective_terms.extend(2000 * term for term in third_plus_excess_terms)
        objective_terms.extend(3000 * term for term in club_excess_terms)
        objective_terms.extend(25 * term for term in same_club_terms)
        objective_terms.extend(10 * term for term in gap_under_7_terms)
        objective_terms.extend(2 * term for term in gap_under_14_terms)
        if objective_terms:
            model.Minimize(sum(objective_terms))

    # issue #298: the baseline candidate is feasible for every structural
    # constraint above (same roster sizes, same-or-fewer same-club pairings,
    # pinned/host membership) but unhinted CP-SAT still has to *rediscover*
    # that from scratch, and can burn its entire time budget searching
    # without ever reporting FEASIBLE/OPTIMAL on a large enough model.
    # Hinting every decision variable at its baseline value gives the solver
    # a known-mostly-feasible starting point to validate/repair immediately,
    # so a solve that would otherwise time out at UNKNOWN can still return
    # the baseline (or better) within budget. The participation cap above
    # means a baseline that *exceeds* its configured target is no longer
    # itself feasible against this hint -- CP-SAT is expected to repair that
    # case by searching away from the hint, not reproduce it.
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
                not _clubs_represent_same_club(identity[0], slot.host_club),
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
        age_group = tournaments_by_index[slot.index].get("age_group")
        rounds = ((problem or {}).get("rounds_per_tournament") or {}).get(age_group)
        games = _games_to_dicts(
            model_teams,
            _parallel_games(tournaments_by_index[slot.index], problem),
            rounds if isinstance(rounds, int) else None,
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
    * every registered/eligible team's participation is capped at its
      configured target (per-team override, or the age group's authoritative
      before/after-Christmas value for the half) and optimized toward it via
      a deficit objective term -- an identity with no configured target
      (non-canonical age groups only) falls back to its exact baseline
      count instead;
    * a team cannot appear in two tournaments on the same date -- enforced
      for every registered/eligible team, not just ones already present in
      the baseline, so CP-SAT cannot introduce a duplicate-date violation of
      its own accord;
    * pinned tournaments keep their exact participant set;
    * when the baseline tournament contains a host-club team, the optimized
      tournament still contains at least one host-club team.

    Baseline participation counts are used only as a search hint
    (``AddHint``), never as the constraint authority, so CP-SAT can repair a
    baseline that under- or over-shoots its configured target -- including a
    registered team with zero baseline appearances in a half. The
    independent verifier/A/B report remain responsible for deciding whether
    the resulting candidate is useful.

    *feasibility_only* (issue #298 Phase 1) builds only the hard constraints
    and stops at the first solution -- proving the model can reach a
    feasible assignment, isolated from quality search.

    *decompose_by_half* (issue #298 Phase 2) solves participant assignment
    independently for each :mod:`planning_half` (before/after Christmas)
    instead of one monolithic model, using ``problem["christmas_split_date"]``
    as the shared boundary. Each half is capped against its own configured
    per-half target (see above), so a half with no slots is simply skipped.

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
                "capped_at_configured_target_per_half_with_baseline_hint"
                if decompose_by_half
                else "capped_at_configured_target_with_baseline_hint"
            ),
            "same_club_pairings": "no_worse_than_baseline",
            "turnaround": "soft_pairwise_hint_only" if not feasibility_only else "not_modeled",
        },
    }
    return result
