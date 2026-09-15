"""Shared deterministic feasibility predicate for tournament roster shape.

Distinguishes *avoidable* byes/underscheduling (the canonical registered
team pool for an age group could support a bigger no-bye shape, but a
planner picked a smaller subset) from *input-constrained* adaptation (the
complete registered pool is itself too small to satisfy the preferred
shape, so the planner must schedule the real teams with the best legal
shape they support). One implementation, reused by roster sizing, CP-SAT,
the planning-contract verifier and audit evidence, so none of them can
drift out of sync on where that line falls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# Pause/bye teams are not allowed in any materialized tournament: every
# scheduled team must play in every round. With the current U12/JU12 capacity
# of 4 teams this also means those tournaments must be exactly 4 teams / 6 games.
# Canonically defined here and re-exported from `planning_contract` (which
# owns the rest of the planning-contract verifier) to avoid a circular import
# between the two modules.
NO_BYE_MIN_TEAMS_PER_TOURNAMENT = 4
NO_BYE_EXACT_TEAM_COUNT_BY_AGE_GROUP = {"U12": 4, "JU12": 4}


@dataclass(frozen=True)
class EffectiveTournamentShape:
    age_group: str
    registered_team_count: int
    configured_rounds: Optional[int]
    parallel_game_capacity: Optional[int]
    normal_shape_feasible: bool
    preferred_no_bye_team_count: int
    input_constrained: bool
    effective_team_count: int
    effective_round_count: int
    unavoidable_bye_count: int
    reason: Optional[str]
    # True when `preferred_no_bye_team_count` is a real target (an
    # exact-size override or a rounds-derived minimum) that a single
    # tournament instance must reach. False for a generic age group with
    # neither: a season spreads its registered pool across many tournament
    # instances over time, so no single instance is required to draw the
    # whole pool at once, and any even subset stays fully legal -- only an
    # odd subset that could have been made even (or wasn't the whole
    # available/capacity ceiling) is an avoidable defect.
    has_explicit_target: bool

    @property
    def preferred_team_count(self) -> int:
        return self.preferred_no_bye_team_count

    @property
    def adaptation_reason(self) -> Optional[str]:
        return self.reason

    def as_dict(self) -> dict:
        return {
            "age_group": self.age_group,
            "registered_team_count": self.registered_team_count,
            "configured_rounds": self.configured_rounds,
            "parallel_game_capacity": self.parallel_game_capacity,
            "normal_shape_feasible": self.normal_shape_feasible,
            "preferred_team_count": self.preferred_no_bye_team_count,
            "preferred_no_bye_team_count": self.preferred_no_bye_team_count,
            "input_constrained": self.input_constrained,
            "effective_team_count": self.effective_team_count,
            "effective_round_count": self.effective_round_count,
            "unavoidable_bye_count": self.unavoidable_bye_count,
            "adaptation_reason": self.reason,
            "reason": self.reason,
        }


def _ceil_to_even(value: float) -> int:
    rounded = math.ceil(value)
    return rounded if rounded % 2 == 0 else rounded + 1


def _max_unique_opponent_rounds(team_count: int) -> int:
    """Most rounds a round-robin schedule can cover without repeating a pair.

    An even roster needs ``n - 1`` rounds to cover every pair once. An odd
    roster needs ``n`` rounds, with exactly one team resting each round.
    """
    if team_count < 2:
        return 0
    return team_count - 1 if team_count % 2 == 0 else team_count


def compute_effective_tournament_shape(
    age_group: str,
    registered_team_count: int,
    configured_rounds: Optional[int] = None,
    parallel_game_capacity: Optional[int] = None,
) -> EffectiveTournamentShape:
    """Derive the legal tournament shape *age_group* can actually support.

    ``registered_team_count`` must be the complete canonical Stage-1
    registered team population for the age group -- never just the
    participants a planner already selected for one tournament -- or this
    cannot distinguish avoidable from input-constrained scarcity.
    """
    exact_override = NO_BYE_EXACT_TEAM_COUNT_BY_AGE_GROUP.get(age_group)
    has_explicit_target = exact_override is not None or bool(configured_rounds)
    if exact_override is not None:
        preferred = exact_override
    elif configured_rounds:
        preferred = max(NO_BYE_MIN_TEAMS_PER_TOURNAMENT, _ceil_to_even(configured_rounds + 1))
    else:
        # No configured round count and no exact-size override: a season
        # spreads this age group's registered pool across many tournament
        # instances over time, so there is no single-instance target size to
        # enforce -- `preferred` here is only the informational ceiling
        # (see `has_explicit_target` above).
        preferred = registered_team_count

    capacity_cap = parallel_game_capacity * 2 if parallel_game_capacity and parallel_game_capacity > 0 else None
    if capacity_cap is not None:
        preferred = min(preferred, capacity_cap)

    if registered_team_count < 2:
        return EffectiveTournamentShape(
            age_group=age_group,
            registered_team_count=registered_team_count,
            configured_rounds=configured_rounds,
            parallel_game_capacity=parallel_game_capacity,
            normal_shape_feasible=False,
            preferred_no_bye_team_count=preferred,
            input_constrained=True,
            effective_team_count=registered_team_count,
            effective_round_count=0,
            unavoidable_bye_count=0,
            reason="registered_pool_too_small_for_tournament",
            has_explicit_target=has_explicit_target,
        )

    ceiling = capacity_cap if capacity_cap is not None else registered_team_count
    ceiling = min(ceiling, registered_team_count)
    if not has_explicit_target:
        # No specific target: the ceiling itself (the most this age group
        # could ever supply to one instance) is the only thing that can be
        # "input-constrained" -- it's constrained exactly when it's odd, since
        # even that maximum can't reach a bye-free shape.
        preferred = ceiling
        effective_team_count = ceiling
        input_constrained = ceiling % 2 == 1
        reason = "registered_pool_too_small" if input_constrained else None
    elif registered_team_count >= preferred and (capacity_cap is None or capacity_cap >= preferred):
        # Neither the registered pool nor capacity is the limiting factor:
        # the preferred shape is fully achievable.
        effective_team_count = preferred
        input_constrained = False
        reason = None
    else:
        effective_team_count = ceiling
        input_constrained = True
        reason = (
            "parallel_capacity_limited"
            if capacity_cap is not None and capacity_cap < registered_team_count and capacity_cap < preferred
            else "registered_pool_too_small"
        )

    effective_round_count = _max_unique_opponent_rounds(effective_team_count)
    if configured_rounds:
        effective_round_count = min(configured_rounds, effective_round_count)
    unavoidable_bye_count = effective_round_count if effective_team_count % 2 == 1 else 0

    return EffectiveTournamentShape(
        age_group=age_group,
        registered_team_count=registered_team_count,
        configured_rounds=configured_rounds,
        parallel_game_capacity=parallel_game_capacity,
        normal_shape_feasible=not input_constrained,
        preferred_no_bye_team_count=preferred,
        input_constrained=input_constrained,
        effective_team_count=effective_team_count,
        effective_round_count=effective_round_count,
        unavoidable_bye_count=unavoidable_bye_count,
        reason=reason,
        has_explicit_target=has_explicit_target,
    )


def shape_violation(
    shape: EffectiveTournamentShape,
    actual_team_count: int,
    actual_bye_round_count: int,
) -> bool:
    """True when *actual_team_count*/*actual_bye_round_count* is an avoidable
    defect against *shape*, rather than a legal (possibly input-constrained)
    realization of it.
    """
    if not shape.has_explicit_target:
        # No single-instance target: any even subset of the registered pool
        # is fully legal. An odd subset is only legal when it's the entire
        # available/capacity ceiling (`effective_team_count`) -- otherwise a
        # bigger, even alternative was available and this is avoidable.
        if actual_team_count % 2 == 0:
            return actual_bye_round_count > 0
        if actual_team_count == shape.effective_team_count:
            return actual_bye_round_count > shape.unavoidable_bye_count
        return True

    if actual_team_count < shape.effective_team_count:
        return True
    if actual_team_count == shape.effective_team_count:
        expected_byes = shape.unavoidable_bye_count if shape.input_constrained else 0
        return actual_bye_round_count > expected_byes or (
            actual_team_count % 2 == 1 and not shape.input_constrained
        )
    # actual_team_count > shape.effective_team_count: only a defect if it
    # still has an odd/bye shape of its own that the achievable shape
    # doesn't require.
    return actual_team_count % 2 == 1 or actual_bye_round_count > 0
