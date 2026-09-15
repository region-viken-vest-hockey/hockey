"""Tournament roster-size planning helpers for `SeasonPlanner`.

Split out of `participant_selection.py` (which re-exports all of these names
for backward compatibility) to keep that module under the file-length
guideline -- these functions answer "how many slots/how big" a given age
group's participation demand needs, a distinct concern from *which* teams
actually get picked for a given slot.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from tournament_scheduler.effective_tournament_shape import compute_effective_tournament_shape
from tournament_scheduler.participant_relocation import MIN_TEAMS_PER_TOURNAMENT


def club_demand_shares(planner, age_group: str, period: Optional[str] = None) -> Dict[str, float]:
    """Return `{club: club_target_sum / age_group_target_sum}` for `age_group`.

    issue #327: a club's fair proportional entitlement to participation slots
    is its share of total registered *demand* (per-team targets, not raw team
    counts) in the age group/half -- for equal per-team targets this reduces
    to `club_team_count / total_team_count`, matching the issue's worked
    example, but stays correct when explicit per-team targets differ.
    """
    teams = planner.roster.by_age_group(age_group)
    totals: Dict[str, int] = {}
    grand_total = 0
    for team in teams:
        target = planner._team_target_tournament_count(team, period)
        totals[team.club] = totals.get(team.club, 0) + target
        grand_total += target
    if grand_total <= 0:
        return {}
    return {club: total / grand_total for club, total in totals.items()}


def club_share_deficit(planner, age_group: str, period: Optional[str], club: str) -> float:
    """Return how many participation slots `club` is behind its fair share.

    issue #327: compares `club`'s actual running participation count in
    `age_group`/`period` against `club_demand_shares(...)`'s proportional
    entitlement out of the total participations already invited in this
    age group/period so far -- a positive value means the club is
    materially behind where its registered demand share says it should be.

    Returns ``0.0`` (no deficit-driven relaxation) for any planner double
    that doesn't expose the full-roster/running-count attributes this needs
    (`_team_target_tournament_count`, `_tournament_participations`) -- those
    are real `SeasonPlanner` machinery, not part of the minimal interface
    `pick_scored_participants` otherwise requires, so a lightweight test/
    caller-supplied planner stand-in simply gets the pre-#327 behavior.
    """
    teams = planner.roster.by_age_group(age_group)
    if not teams:
        return 0.0
    try:
        share = club_demand_shares(planner, age_group, period).get(club)
        if not share:
            return 0.0
        if period in ("before_christmas", "after_christmas"):
            counts = planner._tournament_participations_by_half.get(period, {})
        else:
            counts = planner._tournament_participations
    except AttributeError:
        return 0.0
    total_invites = 0
    club_invites = 0
    for team in teams:
        count = counts.get(planner._team_key(team), 0)
        total_invites += count
        if team.club == club:
            club_invites += count
    return share * total_invites - club_invites


def _participation_demand(planner, age_group: str, period: Optional[str] = None) -> Tuple[int, int]:
    """Return `(total_target_participations, tournament_capacity)` for `age_group`.

    Shared by `target_tournaments_for_age_group` (how many tournament slots
    that demand needs) and `plan_roster_sizes_for_age_group` (how the demand
    should be packed into those slots) so the two never derive the demand
    differently.
    """
    from tournament_scheduler.participant_selection import max_teams_for

    teams = planner.roster.by_age_group(age_group)
    if len(teams) < MIN_TEAMS_PER_TOURNAMENT:
        return 0, 0

    age_group_targets = getattr(planner, "participation_targets_by_age_group", {}) or {}
    age_group_target = age_group_targets.get(age_group, {}) if isinstance(age_group_targets, dict) else {}
    before_target = age_group_target.get("before_christmas")
    after_target = age_group_target.get("after_christmas")
    if period == "before_christmas" and before_target is not None:
        default_target = before_target
    elif period == "after_christmas" and after_target is not None:
        default_target = after_target
    elif before_target is not None and after_target is not None:
        default_target = before_target + after_target
    else:
        capacity = min(len(teams), max_teams_for(planner, age_group)) or 1
        inferred = max(1, math.ceil(len(teams) / capacity))
        default_target = planner.target_tournament_count or inferred

    total_target = sum((t.target_tournament_count or default_target) for t in teams)
    capacity = min(len(teams), max_teams_for(planner, age_group)) or 1
    return total_target, capacity


def _registered_team_count(planner, age_group: str) -> int:
    return len(planner.roster.by_age_group(age_group))


def _effective_shape_for(planner, age_group: str):
    """Effective-shape rule: the shape the complete registered pool actually supports.

    Falls back to ``registered_team_count=0`` for a lightweight test/caller
    planner stand-in that doesn't expose the rounds/parallel-games
    attributes -- matching the rest of this module's tolerance for a
    minimal planner interface (see `club_share_deficit`).
    """
    registered_team_count = _registered_team_count(planner, age_group)
    rounds = getattr(planner, "rounds_per_tournament_for_age_group", {}) or {}
    parallel = getattr(planner, "parallel_games_for_age_group", {}) or {}
    return compute_effective_tournament_shape(
        age_group,
        registered_team_count,
        configured_rounds=rounds.get(age_group) if isinstance(rounds, dict) else None,
        parallel_game_capacity=parallel.get(age_group) if isinstance(parallel, dict) else None,
    )


def fixed_cohort_shape_for(planner, age_group: str):
    """Return the effective shape when the whole registered pool is fixed.

    When the complete Stage-1 registered pool is exactly the independently
    derived effective tournament team count, there is no legal participant
    subset to optimize: every materialized unpinned tournament for this age
    group must use the full pool together.
    """
    shape = _effective_shape_for(planner, age_group)
    if (
        shape.registered_team_count == shape.effective_team_count
        and shape.effective_team_count >= MIN_TEAMS_PER_TOURNAMENT
        and (shape.has_explicit_target or shape.parallel_game_capacity is not None)
    ):
        return shape
    return None


def fixed_cohort_participants(
    planner,
    age_group: str,
    period: Optional[str] = None,
    *,
    exclude_team_keys: Optional[set] = None,
    planned_roster_size: Optional[int] = None,
):
    """Return the full registered cohort when membership is mathematically fixed.

    ``None`` means the age group is not a fixed-cohort shape and generic
    participant selection should run. A list (possibly empty) is authoritative:
    the full cohort is selected unless it cannot legally be used for this slot
    because the cohort has already reached its target or was already used on
    this date. ``planned_roster_size`` is intentionally not allowed to shrink
    the returned cohort below the effective team count.
    """
    shape = fixed_cohort_shape_for(planner, age_group)
    if shape is None:
        return None

    registered = list(planner.roster.by_age_group(age_group))
    if exclude_team_keys and any(planner._team_key(team) in exclude_team_keys for team in registered):
        return []
    if any(planner._team_at_target(team, period) for team in registered):
        return []
    return registered


def target_tournaments_for_age_group(planner, age_group: str, period: Optional[str] = None) -> int:
    """Return the number of tournaments to aim for in `age_group`.

    When *period* is ``"before_christmas"`` or ``"after_christmas"`` and the
    planner has an explicit per-age-group split target, the returned value uses
    that half-season participation target as the default for teams in the age
    group.
    """
    total_target, capacity = _participation_demand(planner, age_group, period)
    if capacity == 0:
        return 0
    shape = _effective_shape_for(planner, age_group)
    if fixed_cohort_shape_for(planner, age_group) is not None or shape.has_explicit_target:
        if shape.effective_team_count < 2:
            return 0
        return total_target // shape.effective_team_count
    # No-bye invariant: do not create a leftover odd-sized tournament slot.
    return len(plan_roster_sizes(total_target, capacity))


def plan_roster_sizes(demand: int, capacity: int) -> List[int]:
    """Split participation demand into legal no-bye roster sizes.

    Every materialized tournament must have an even roster and at least four
    teams. If demand cannot be represented exactly with even sizes within the
    configured capacity, the remainder is left as an explicit participation
    shortfall/manual evidence by later verification instead of creating a
    pause/bye tournament.
    """
    if demand <= 0 or capacity < 4:
        return []
    max_even_capacity = capacity if capacity % 2 == 0 else capacity - 1
    if max_even_capacity < 4:
        return []

    best: List[int] = []
    best_total = 0
    max_slots = demand // 4
    for slot_count in range(1, max_slots + 1):
        max_total = min(demand, slot_count * max_even_capacity)
        # Largest even total this slot count can realize.
        total = max_total if max_total % 2 == 0 else max_total - 1
        min_total = slot_count * 4
        if total < min_total:
            continue
        base_even = (total // slot_count) // 2 * 2
        sizes = [base_even] * slot_count
        remainder = total - sum(sizes)
        idx = 0
        while remainder > 0 and idx < slot_count:
            add = min(remainder, max_even_capacity - sizes[idx])
            add -= add % 2
            if add > 0:
                sizes[idx] += add
                remainder -= add
            idx += 1
        if remainder == 0 and sum(sizes) > best_total:
            best = sizes
            best_total = sum(sizes)
    return sorted(best, reverse=True)


def plan_roster_sizes_for_age_group(planner, age_group: str, period: Optional[str] = None) -> List[int]:
    """Return the planned roster size for each tournament slot in `age_group`.

    The list has exactly `target_tournaments_for_age_group(planner, age_group,
    period)` entries, in the order those slots should be filled.
    """
    total_target, capacity = _participation_demand(planner, age_group, period)
    if capacity == 0:
        return []
    shape = _effective_shape_for(planner, age_group)
    if fixed_cohort_shape_for(planner, age_group) is not None or shape.has_explicit_target:
        if shape.effective_team_count < 2:
            return []
        return [shape.effective_team_count] * (total_target // shape.effective_team_count)
    return plan_roster_sizes(total_target, capacity)


def rebalance_roster_sizes_across_dates(
    date_groups: Sequence[Tuple[date, int]],
    flat_sizes: Sequence[int],
    capacity: int,
    distinct_team_count: int,
    min_teams: int = MIN_TEAMS_PER_TOURNAMENT,
) -> Tuple[Dict[date, List[int]], List[Dict[str, object]]]:
    """Resize `flat_sizes` so no date's parallel-slot demand exceeds the
    age group's distinct team pool.

    issue #318: `flat_sizes` (from `plan_roster_sizes_for_age_group`) is
    balanced for the *half* as a whole, but is consumed chronologically with
    no regard for how many parallel same-age-group slots land on a single
    date. When several slots share a date, the date's own eligible-team pool
    (`distinct_team_count`, e.g. 17 for a 17-team age group -- a team can't
    play twice on the same date) can be a tighter ceiling than either the
    per-slot `capacity` or the half-wide balance.

    `date_groups` is `(date, slot_count)` in chronological order, matching
    the order `flat_sizes` was assigned in. Demand that a date can't absorb
    is carried forward to the next date(s) with slack rather than dropped,
    so the common case (roster-size sum on one date > distinct team count,
    but the age group has enough *other* dates) is resolved without any lost
    tournaments. Returns `(sizes_by_date, evidence)`:

    - `sizes_by_date[date]` is the rebalanced list of roster sizes for that
      date's slots (each within `[min_teams, capacity]`, possibly fewer
      entries than the date's requested slot count when demand genuinely
      runs out).
    - `evidence` distinguishes two genuine-infeasibility causes so callers
      never have to guess why a slot went unfilled:
      - `"same_date_uniqueness_limit"`: this date structurally cannot host
        its requested slot count for this age group at all (`slot_count *
        min_teams > distinct_team_count`), independent of demand.
      - `"same_date_participant_pool_capacity"`: after using every date's
        full capacity, some participations still couldn't be placed before
        the group ran out of dates; `limiting_dates` lists which dates were
        at their team-pool ceiling.
    """
    sizes_by_date: Dict[date, List[int]] = {}
    evidence: List[Dict[str, object]] = []
    limiting_dates: List[date] = []
    carry_sizes: List[int] = []
    index = 0
    for slot_date, slot_count in date_groups:
        group_sizes = list(flat_sizes[index : index + slot_count])
        index += slot_count
        requested_sizes = carry_sizes + group_sizes

        if slot_count * min_teams > distinct_team_count:
            evidence.append(
                {
                    "category": "same_date_uniqueness_limit",
                    "date": slot_date.isoformat(),
                    "requested_slots": slot_count,
                    "feasible_slots": distinct_team_count // min_teams if min_teams else slot_count,
                    "distinct_team_count": distinct_team_count,
                }
            )

        total_requested = sum(requested_sizes)
        max_total = min(slot_count * capacity, distinct_team_count)
        if total_requested > max_total:
            limiting_dates.append(slot_date.isoformat())

        # The incoming flat plan is already authoritative for per-slot roster
        # shape. Preserve those whole slot sizes (including legal odd
        # input-constrained effective shapes) rather than re-packing the date
        # through the legacy even-only helper. Overflow is carried as whole
        # future slots, so an infeasible same-date grouping is reported as a
        # pool/date capacity problem instead of silently shrinking a roster.
        planned_sizes: List[int] = []
        overflow_sizes: List[int] = []
        used_total = 0
        for size in requested_sizes:
            if (
                len(planned_sizes) < slot_count
                and min_teams <= size <= capacity
                and used_total + size <= max_total
            ):
                planned_sizes.append(size)
                used_total += size
            else:
                overflow_sizes.append(size)

        sizes_by_date[slot_date] = planned_sizes
        carry_sizes = overflow_sizes

    carry = sum(carry_sizes)
    if carry > 0:
        evidence.append(
            {
                "category": "same_date_participant_pool_capacity",
                "unplaced_participations": carry,
                "limiting_dates": limiting_dates,
            }
        )
    return sizes_by_date, evidence
