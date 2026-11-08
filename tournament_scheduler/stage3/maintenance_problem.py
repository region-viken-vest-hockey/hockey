"""Solver-neutral, domain-owned description of one bounded maintenance search.

A promoted season is maintained, not replanned. A maintenance search therefore
starts from an explicit *problem* that says exactly what may change, built
deterministically from canonical state, and ends in *candidate patches* that
are bound to the exact baseline they were solved against. Nothing here knows
about OR-Tools: the type carries plain JSON-safe values so any solver (or a
test double) can consume it, and the canonical lifecycle semantics (locks,
pins, cancellations, registered pool) are projected **once**, here, instead of
being re-derived inside a solver.

Ownership (see ADR 0008):

* this module projects canonical facts into mutable scope and fixed values;
* ``stage3/cpsat_maintenance.py`` searches inside that scope and returns
  patches; it never mutates canonical state;
* ``planning.contract.verify_candidate`` and the maintenance consequence gates
  remain the authority on whether a patch may be offered or applied.

Lock semantics are scope-aware: a participant lock (or an operator pin) fixes a
roster, a placement-only lock does **not**. Cancelled tournaments are not
searched and are never counted as participation opportunity.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from tournament_scheduler.planning.contract import _parse_date, _team_identity, candidate_content_fingerprint
from tournament_scheduler.stage3.maintenance_facts import participant_fixed_reasons, registered_pool

MAINTENANCE_PROBLEM_SCHEMA_VERSION = 1

DIMENSION_PARTICIPANTS = "participants"

# Hard rules the CP-SAT path encodes for pruning. Every one of them is still
# enforced independently by ``verify_candidate`` (ADR 0008, rule map).
PARTICIPANT_HARD_CONSTRAINTS: Tuple[str, ...] = (
    "roster_size_fixed_to_baseline",
    "full_pool_membership_for_fixed_cohort_shapes",
    "host_club_represented",
    "no_duplicate_participation_on_one_date",
    "participant_locked_and_pinned_rosters_fixed",
    "participation_hard_max",
    "club_hard_max_per_tournament",
    "same_club_pairings_no_worse_than_baseline",
)

# Priority tiers, highest first. Each tier is minimized before the next and its
# optimum is then fixed, so a lower tier cannot trade away a higher one.
OBJECTIVE_TIERS: Tuple[str, ...] = (
    "participation",
    "club_concentration",
    "opponent_variety",
    "sibling_and_spacing",
)

DEFAULT_SEARCH_BUDGET_SECONDS = 15.0

TeamId = Tuple[str, str, str]


class MaintenanceScopeError(ValueError):
    """The requested maintenance scope cannot be projected from canonical state."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def problem_content_fingerprint(problem: Optional[Mapping[str, Any]]) -> str:
    """Stable identity of the planning-problem facts a solve was based on."""
    return hashlib.sha256(_canonical_json(dict(problem or {})).encode("utf-8")).hexdigest()


def _team_key(identity: TeamId) -> str:
    return ":".join(identity)


@dataclass(frozen=True)
class MaintenanceProblem:
    """Bounded participant-maintenance scope, bound to one baseline.

    ``fixed_participants`` maps every tournament of an in-scope age group that
    may *not* change to its exact baseline roster; ``mutable_tournament_ids``
    are the only tournaments a solver may re-staff. Everything outside the
    scoped age groups is frozen implicitly (it is not part of the problem).
    """

    baseline_fingerprint: str
    problem_fingerprint: str
    scope_age_groups: Tuple[str, ...]
    mutable_tournament_ids: Tuple[str, ...]
    mutable_dimensions: Tuple[str, ...]
    fixed_participants: Mapping[str, Tuple[str, ...]]
    fixed_reasons: Mapping[str, str]
    eligible_values: Mapping[str, Tuple[str, ...]]
    excluded_tournament_ids: Mapping[str, str]
    hard_constraints: Tuple[str, ...] = PARTICIPANT_HARD_CONSTRAINTS
    objective_tiers: Tuple[str, ...] = OBJECTIVE_TIERS
    search_budget_seconds: float = DEFAULT_SEARCH_BUDGET_SECONDS
    seed: int = 0
    revision: Optional[str] = None
    schema_version: int = MAINTENANCE_PROBLEM_SCHEMA_VERSION
    # Optional narrowing: only these ``club:label:age_group`` teams may enter or
    # leave a roster (sibling-only substitution), and an optional home-balance
    # tier ``{"club": ..., "age_group": ...}``.
    movable_teams: Optional[Tuple[str, ...]] = None
    home_balance: Optional[Mapping[str, str]] = None
    repair_baseline_conflicts: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "baseline_fingerprint": self.baseline_fingerprint,
            "problem_fingerprint": self.problem_fingerprint,
            "scope_age_groups": list(self.scope_age_groups),
            "mutable_tournament_ids": list(self.mutable_tournament_ids),
            "mutable_dimensions": list(self.mutable_dimensions),
            "fixed_participants": {key: list(value) for key, value in sorted(self.fixed_participants.items())},
            "fixed_reasons": dict(sorted(self.fixed_reasons.items())),
            "eligible_values": {key: list(value) for key, value in sorted(self.eligible_values.items())},
            "excluded_tournament_ids": dict(sorted(self.excluded_tournament_ids.items())),
            "hard_constraints": list(self.hard_constraints),
            "objective_tiers": list(self.objective_tiers),
            "search_budget_seconds": float(self.search_budget_seconds),
            "seed": int(self.seed),
            "movable_teams": None if self.movable_teams is None else list(self.movable_teams),
            "home_balance": dict(self.home_balance) if self.home_balance else None,
            "repair_baseline_conflicts": bool(self.repair_baseline_conflicts),
        }

    def fingerprint(self) -> str:
        """Identity of the *question asked*: scope + baseline + budget + seed."""
        return hashlib.sha256(_canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MaintenanceSolveResult:
    """Outcome of one bounded maintenance solve. Never mutates canonical state.

    ``status`` is one of:

    * ``solved`` -- at least one mutable roster changed; ``patches`` and
      ``candidate`` are populated and bound to ``baseline_fingerprint``;
    * ``unchanged`` -- the baseline is already optimal inside the scope;
    * ``infeasible`` -- the solver proved no assignment satisfies the scope;
    * ``budget_exhausted`` -- the budget ran out before any answer: this is
      **not** proof of infeasibility;
    * ``solver_error`` -- the model or solver failed, also not proof of infeasibility;
    * ``no_mutable_scope`` -- locks/pins/cancellations leave nothing to change;
    * ``unavailable`` -- the solver backend is not installed.
    """

    status: str
    problem_fingerprint: str
    baseline_fingerprint: str
    candidate_fingerprint: Optional[str] = None
    patches: Tuple[Mapping[str, Any], ...] = ()
    candidate: Optional[Dict[str, Any]] = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def has_candidate(self) -> bool:
        return self.status == "solved" and self.candidate is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            **{
                k: getattr(self, k)
                for k in ("status", "problem_fingerprint", "baseline_fingerprint", "candidate_fingerprint")
            },
            "patches": [dict(patch) for patch in self.patches],
            "diagnostics": dict(self.diagnostics),
        }


class StaleMaintenanceBaseline(RuntimeError):
    """A solve or patch was attempted against a baseline other than the one the problem names."""

    def __init__(self, expected: str, actual: str, *, what: str = "candidate") -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"stale {what} fingerprint: problem is bound to {expected[:12]}, got {actual[:12]}")


def build_participant_maintenance_problem(
    candidate: Mapping[str, Any],
    problem: Optional[Mapping[str, Any]],
    *,
    age_groups: Iterable[str],
    tournament_ids: Optional[Iterable[str]] = None,
    revision: Optional[str] = None,
    search_budget_seconds: float = DEFAULT_SEARCH_BUDGET_SECONDS,
    seed: int = 0,
    movable_teams: Optional[Iterable[str]] = None,
    home_balance: Optional[Mapping[str, str]] = None,
    repair_baseline_conflicts: bool = False,
) -> MaintenanceProblem:
    """Project canonical state into a participants-only maintenance problem.

    *age_groups* bounds the neighbourhood (participation, duplicate-date and
    pairing rules are per age group, so a solve is only meaningful over whole
    age groups). *tournament_ids*, when given, further restricts which of
    those tournaments may be re-staffed; the rest stay in the problem as fixed
    context. Deterministic: the same canonical inputs always yield the same
    problem and fingerprint.
    """
    groups = tuple(sorted({str(group) for group in age_groups if str(group)}))
    if not groups:
        raise MaintenanceScopeError("a maintenance problem needs at least one age group")
    restricted = {str(item) for item in tournament_ids} if tournament_ids is not None else None
    roster_fixed = participant_fixed_reasons(problem)

    mutable: List[str] = []
    fixed: Dict[str, Tuple[str, ...]] = {}
    fixed_reasons: Dict[str, str] = {}
    excluded: Dict[str, str] = {}
    for index, tournament in enumerate(candidate.get("tournaments", []) or []):
        if str(tournament.get("age_group") or "") not in groups:
            continue
        tournament_id = str(tournament.get("id", index))
        if tournament.get("cancelled"):
            excluded[tournament_id] = "cancelled"
            continue
        if _parse_date(tournament.get("date")) is None:
            excluded[tournament_id] = "undated"
            continue
        baseline_roster = tuple(_team_key(_team_identity(dict(team))) for team in tournament.get("teams", []) or [])
        if tournament_id in roster_fixed:
            fixed[tournament_id] = baseline_roster
            fixed_reasons[tournament_id] = roster_fixed[tournament_id]
        elif restricted is not None and tournament_id not in restricted:
            fixed[tournament_id] = baseline_roster
            fixed_reasons[tournament_id] = "outside_requested_scope"
        else:
            mutable.append(tournament_id)

    pool = registered_pool(candidate, problem)
    return MaintenanceProblem(
        baseline_fingerprint=candidate_content_fingerprint(candidate),
        problem_fingerprint=problem_content_fingerprint(problem),
        scope_age_groups=groups,
        mutable_tournament_ids=tuple(mutable),
        mutable_dimensions=(DIMENSION_PARTICIPANTS,),
        fixed_participants=fixed,
        fixed_reasons=fixed_reasons,
        eligible_values={group: tuple(_team_key(i) for i in pool.get(group, [])) for group in groups},
        excluded_tournament_ids=excluded,
        search_budget_seconds=float(search_budget_seconds),
        seed=int(seed),
        revision=revision,
        movable_teams=None if movable_teams is None else tuple(sorted(str(t) for t in movable_teams)),
        home_balance=dict(home_balance) if home_balance else None,
        repair_baseline_conflicts=bool(repair_baseline_conflicts),
    )


def apply_patches(candidate: Mapping[str, Any], patches: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return a copy of *candidate* with *patches* applied. Pure; never mutates input."""
    result = deepcopy(dict(candidate))
    by_id = {str(patch["tournament_id"]): patch for patch in patches}
    for index, tournament in enumerate(result.get("tournaments", []) or []):
        patch = by_id.get(str(tournament.get("id", index)))
        if patch is not None:
            tournament["teams"] = deepcopy(patch["teams"])
            tournament["games"] = deepcopy(patch["games"])
    return result
