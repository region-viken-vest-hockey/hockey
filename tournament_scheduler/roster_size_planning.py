"""Per-date roster-size rebalancing across an entire `scheduled` list.

issue #318: split out of `SeasonPlanner` -- this fans
`participant_selection.rebalance_roster_sizes_across_dates` out across every
(age_group, period) group in a full season's `scheduled` list and folds the
per-group evidence into one list, deduplicating against relocation evidence
already produced for the same date.
"""

from __future__ import annotations

from datetime import date
from itertools import groupby
from typing import Callable, Dict, List, Optional, Tuple

from tournament_scheduler.participant_selection import (
    max_teams_for,
    plan_roster_sizes_for_age_group,
    rebalance_roster_sizes_across_dates,
)


def compute_rebalanced_roster_sizes(
    planner,
    scheduled: List[Tuple[date, str]],
    period_for_date: Callable[[date], Optional[str]],
    same_date_relocation_evidence: List[Dict[str, object]],
) -> Tuple[Dict[Tuple[str, Optional[str]], Dict[date, List[int]]], List[Dict[str, object]]]:
    """Rebalance planned roster sizes per (age_group, period, date).

    issue #316: carries unplaceable demand forward to a later date with
    slack instead of consuming the half-wide balanced list blindly in
    chronological order, so a same-date squeeze doesn't silently cost a
    materializable tournament.

    issue #318: `scheduled` here already reflects any relocation performed
    upstream (`participant_relocation.relocate_structurally_impossible_scheduled_slots`),
    so `same_date_uniqueness_limit` entries this produces can only recur for
    slots relocation genuinely could not place anywhere else -- those are
    superseded by the richer `same_date_relocation_evidence` entries (which
    include every alternative date considered and why), so the bare
    duplicate is dropped in favor of that richer one.
    """
    relocated_evidence_keys = {
        (entry["age_group"], entry["period"], entry["date"]) for entry in same_date_relocation_evidence
    }

    rebalanced_sizes_by_key: Dict[Tuple[str, Optional[str]], Dict[date, List[int]]] = {}
    same_date_capacity_evidence: List[Dict[str, object]] = list(same_date_relocation_evidence)
    for age_group in sorted({ag for _, ag in scheduled}):
        ag_dates_with_period = [(d, period_for_date(d)) for d, ag in scheduled if ag == age_group]
        for period in (None, "before_christmas", "after_christmas"):
            period_dates = [d for d, p in ag_dates_with_period if p == period]
            if not period_dates:
                continue
            date_groups = [(d, len(list(group))) for d, group in groupby(period_dates)]
            flat_sizes = plan_roster_sizes_for_age_group(planner, age_group, period)
            distinct_team_count = len(planner.roster.by_age_group(age_group))
            capacity = min(distinct_team_count, max_teams_for(planner, age_group)) or 1
            sizes_by_date, evidence = rebalance_roster_sizes_across_dates(
                date_groups, flat_sizes, capacity, distinct_team_count
            )
            rebalanced_sizes_by_key[(age_group, period)] = sizes_by_date
            for entry in evidence:
                if (
                    entry.get("category") == "same_date_uniqueness_limit"
                    and (age_group, period, entry.get("date")) in relocated_evidence_keys
                ):
                    continue
                same_date_capacity_evidence.append({**entry, "age_group": age_group, "period": period})

    return rebalanced_sizes_by_key, same_date_capacity_evidence
