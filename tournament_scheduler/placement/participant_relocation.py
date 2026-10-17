"""Relocation of same-date parallel slots that a date cannot legally host.

issue #318: split out of `planning.participant_selection.py` (which keeps the
legacy baseline/fallback selection heuristics) since this is a distinct,
self-contained structural-feasibility repair step.
"""

from __future__ import annotations

from datetime import date
from itertools import groupby
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from tournament_scheduler.models import overlapping_age_groups
from tournament_scheduler.shape.effective_shape import compute_effective_tournament_shape

MIN_TEAMS_PER_TOURNAMENT = 3


def minimum_materializable_team_count(
    age_group: str,
    registered_team_count: int,
    configured_rounds: Optional[int] = None,
    parallel_game_capacity: Optional[int] = None,
) -> int:
    """Smallest roster a single `age_group` slot can legally be materialized at.

    issue #449: age groups with an explicit tournament target (for example
    U12/JU12, which require four teams) cannot legally run a slot smaller than
    their effective shape, so the relocation predicate must not use the generic
    `MIN_TEAMS_PER_TOURNAMENT`. Input-constrained pools keep their smaller
    effective count, and generic age groups keep the generic minimum.
    """
    shape = compute_effective_tournament_shape(
        age_group,
        registered_team_count,
        configured_rounds=configured_rounds,
        parallel_game_capacity=parallel_game_capacity,
    )
    if not shape.has_explicit_target:
        return MIN_TEAMS_PER_TOURNAMENT
    return max(MIN_TEAMS_PER_TOURNAMENT, shape.effective_team_count)


def relocate_structurally_impossible_slots(
    date_groups: Sequence[Tuple[date, int]],
    distinct_team_count: int,
    age_group: str,
    age_groups_by_date: Dict[date, List[str]],
    alternative_dates: Sequence[date],
    min_teams: int = MIN_TEAMS_PER_TOURNAMENT,
) -> Tuple[List[Tuple[date, int]], List[Dict[str, object]]]:
    """Relocate excess same-date parallel slots to another legal date.

    issue #318: `rebalance_roster_sizes_across_dates` proves a date cannot
    host all of its requested parallel slots for `age_group` (`slot_count *
    min_teams > distinct_team_count`) but only redistributes *participant
    demand* across the dates already selected for this (age_group, period)
    -- it never tries moving the excess *slot* itself to a different date.
    This relocates each excess slot to the first alternative date (within
    the same planning half, via `alternative_dates`) that can legally host
    one more `age_group` slot:

    - adding it must not itself push that date over its own team-
      uniqueness ceiling (`(existing_count + 1) * min_teams <=
      distinct_team_count`);
    - the date must not already host an age group that overlaps `age_group`
      (parallel same-`age_group` pools on one date are fine -- that's the
      whole point of this relocation -- but overlapping *different* age
      groups on one date is the same hard conflict `_check_overlap_collision`
      guards against later).

    `age_groups_by_date` is mutated in place to record every relocation, so
    later relocation attempts (other age groups sharing this function's
    caller loop) see an up-to-date picture of what already landed on each
    date.

    Returns the adjusted `(date, slot_count)` groups (a previously-unused
    date may gain a slot; the original date's count only drops for slots
    that were actually relocated) and structured evidence for every date
    that still could not host its full requested count after relocation was
    attempted -- each entry lists every alternative date considered and why
    it was rejected, so "the original date was full" is never the whole
    proof of infeasibility.
    """
    counts: Dict[date, int] = dict(date_groups)
    order: List[date] = [d for d, _ in date_groups]
    evidence: List[Dict[str, object]] = []

    for slot_date in list(order):
        slot_count = counts[slot_date]
        if min_teams <= 0 or slot_count * min_teams <= distinct_team_count:
            continue
        feasible_slots = distinct_team_count // min_teams
        excess = slot_count - feasible_slots
        alternatives_considered: List[Dict[str, object]] = []
        relocated = 0

        for _ in range(excess):
            placed = False
            for candidate in alternative_dates:
                if candidate == slot_date:
                    continue
                candidate_count = counts.get(candidate, 0)
                existing_ags = age_groups_by_date.get(candidate, [])
                overlaps = any(
                    existing != age_group
                    and (
                        age_group in overlapping_age_groups(existing)
                        or existing in overlapping_age_groups(age_group)
                    )
                    for existing in existing_ags
                )
                if overlaps:
                    alternatives_considered.append(
                        {"date": candidate.isoformat(), "rejected_reason": "age_group_overlap"}
                    )
                    continue
                if (candidate_count + 1) * min_teams > distinct_team_count:
                    alternatives_considered.append(
                        {"date": candidate.isoformat(), "rejected_reason": "same_date_uniqueness_limit"}
                    )
                    continue

                counts[slot_date] -= 1
                counts[candidate] = candidate_count + 1
                if candidate not in order:
                    order.append(candidate)
                age_groups_by_date.setdefault(candidate, []).append(age_group)
                relocated += 1
                placed = True
                break
            if not placed:
                break

        remaining_excess = excess - relocated
        if remaining_excess > 0:
            evidence.append(
                {
                    "category": "same_date_uniqueness_limit",
                    "date": slot_date.isoformat(),
                    "requested_slots": slot_count,
                    "feasible_slots": feasible_slots,
                    "distinct_team_count": distinct_team_count,
                    "relocated_slots": relocated,
                    "unrelocated_slots": remaining_excess,
                    "alternatives_considered": alternatives_considered,
                }
            )

    new_date_groups = [(d, counts[d]) for d in sorted(order) if counts[d] > 0]
    return new_date_groups, evidence


def relocate_structurally_impossible_scheduled_slots(
    roster,
    scheduled: List[Tuple[date, str]],
    free_dates: Sequence[date],
    period_for_date: Callable[[date], Optional[str]],
    min_teams_for: Optional[Callable[[str], int]] = None,
) -> Tuple[List[Tuple[date, str]], List[Dict[str, object]]]:
    """Apply `relocate_structurally_impossible_slots` across every
    (age_group, period) group in a full `scheduled` list.

    issue #318: `SeasonPlanner` builds `scheduled` as a flat list of
    (date, age_group) pairs spanning every age group and planning half; this
    fans the per-group relocation out across all of them and folds the
    per-group evidence back into a single list, tagging each entry with the
    age_group/period it came from.

    issue #449: `min_teams_for` supplies the per-age-group minimum legal
    roster size (see `minimum_materializable_team_count`). Without it the
    generic `MIN_TEAMS_PER_TOURNAMENT` applies to every age group, which
    under-counts shapes such as U12/JU12 that need four teams per slot.
    """
    same_date_relocation_evidence: List[Dict[str, object]] = []
    age_groups_by_date: Dict[date, List[str]] = {}
    for d, ag in scheduled:
        age_groups_by_date.setdefault(d, []).append(ag)

    relocation_happened = False
    for age_group in sorted({ag for _, ag in scheduled}):
        distinct_team_count = len(roster.by_age_group(age_group))
        ag_dates_with_period = [(d, period_for_date(d)) for d, ag in scheduled if ag == age_group]
        for period in (None, "before_christmas", "after_christmas"):
            period_dates = [d for d, p in ag_dates_with_period if p == period]
            if not period_dates:
                continue
            date_groups = [(d, len(list(group))) for d, group in groupby(period_dates)]
            alternative_dates = [d for d in free_dates if period_for_date(d) == period]
            min_teams = min_teams_for(age_group) if min_teams_for else MIN_TEAMS_PER_TOURNAMENT
            new_date_groups, relocation_evidence = relocate_structurally_impossible_slots(
                date_groups,
                distinct_team_count,
                age_group,
                age_groups_by_date,
                alternative_dates,
                min_teams=min_teams,
            )
            if new_date_groups != date_groups:
                relocation_happened = True
                scheduled = [
                    (d, ag) for d, ag in scheduled if not (ag == age_group and period_for_date(d) == period)
                ]
                for d, count in new_date_groups:
                    scheduled.extend([(d, age_group)] * count)
            for entry in relocation_evidence:
                same_date_relocation_evidence.append({**entry, "age_group": age_group, "period": period})

    if relocation_happened:
        scheduled.sort(key=lambda item: (item[0], item[1]))

    return scheduled, same_date_relocation_evidence
