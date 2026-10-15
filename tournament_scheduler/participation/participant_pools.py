"""Canonical rotatable sibling-pool resolution and schedule consequences.

A multi-team registration is not, by itself, evidence that squad labels are
interchangeable.  This module therefore recognizes only explicit entries in
``planning_problem["rotatable_participant_pools"]``.  The promoted planning
problem is revision-bound canonical verification context, so maintenance,
repair, audit, and replacement consumers all resolve the same declaration.

Each entry has ``club`` and ``age_group`` and may name ``team_labels``.  When
labels are omitted, all registered teams for that club and age group form the
pool.  At least two registered labels are required.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from itertools import combinations
from typing import Any, Mapping

from tournament_scheduler.participation.temporal_coverage import team_temporal_coverage
from tournament_scheduler.planning import half as planning_half

TeamIdentity = tuple[str, str, str]

ROTATABLE_POOLS_KEY = "rotatable_participant_pools"
CLOSE_GAP_DAYS = 7
PREFERRED_GAP_DAYS = 14


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def resolve_rotatable_participant_pool(
    problem: Mapping[str, Any] | None,
    identity: TeamIdentity,
) -> dict[str, Any] | None:
    """Resolve the explicit rotatable pool containing ``identity``.

    Invalid, ambiguous, singleton, or registration-mismatched declarations do
    not silently weaken exact-team policy; they resolve to no pool.
    """

    if not problem:
        return None
    club, label, age_group = identity
    registered = sorted(
        {
            str(team.get("label") or "")
            for team in problem.get("teams", []) or []
            if str(team.get("club") or "") == club
            and str(team.get("age_group") or "") == age_group
            and str(team.get("label") or "")
            and not bool(team.get("guest", False))
        }
    )
    matches: list[dict[str, Any]] = []
    for raw in problem.get(ROTATABLE_POOLS_KEY, []) or []:
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("club") or "") != club or str(raw.get("age_group") or "") != age_group:
            continue
        declared = raw.get("team_labels")
        labels = (
            sorted({str(item).strip() for item in declared if str(item).strip()})
            if isinstance(declared, list)
            else registered
        )
        if len(labels) < 2 or label not in labels or any(item not in registered for item in labels):
            continue
        matches.append(
            {
                "club": club,
                "age_group": age_group,
                "team_labels": labels,
                "pool_id": str(raw.get("pool_id") or f"{club}|{age_group}"),
                "rotatable": True,
            }
        )
    if len(matches) != 1:
        return None
    return matches[0]


def _minimum_unavoidable_spacing(
    date_loads: list[tuple[date, int]], capacity: int
) -> dict[str, int | None]:
    """Assign opportunities to interchangeable lanes with minimum congestion.

    The state stores each anonymous participant lane's latest appearance.
    Enumerating which lanes receive each date's opportunities gives exact
    minima for the small sibling pools and season horizons in this domain.
    """

    # cost = same-day overflow, back-to-back days, gaps <7,
    # consecutive-weekend gaps, gaps <14
    states: dict[tuple[int | None, ...], tuple[int, int, int, int, int]] = {
        (None,) * capacity: (0, 0, 0, 0, 0)
    }
    for on_date, load in date_loads:
        ordinal = on_date.toordinal()
        assigned_load = min(load, capacity)
        overflow = max(0, load - capacity)
        next_states: dict[tuple[int | None, ...], tuple[int, int, int, int, int]] = {}
        for state, cost in states.items():
            for selected in combinations(range(capacity), assigned_load):
                updated = list(state)
                delta = [overflow, 0, 0, 0, 0]
                for index in selected:
                    previous = state[index]
                    if previous is not None:
                        gap = ordinal - previous
                        delta[1] += int(gap == 1)
                        delta[2] += int(gap < CLOSE_GAP_DAYS)
                        delta[3] += int(6 <= gap <= 8)
                        delta[4] += int(gap < PREFERRED_GAP_DAYS)
                    updated[index] = ordinal
                # Lanes are interchangeable. Sorting collapses symmetric states.
                key = tuple(sorted(updated, key=lambda value: -1 if value is None else value))
                candidate = tuple(cost[i] + delta[i] for i in range(5))
                incumbent = next_states.get(key)
                if incumbent is None or candidate < incumbent:
                    next_states[key] = candidate
        states = next_states
    best = min(states.values(), default=(0, 0, 0, 0, 0))
    return {
        "unavoidable_same_day_overflow": best[0],
        "unavoidable_back_to_back_days": best[1],
        "unavoidable_gaps_under_7": best[2],
        "unavoidable_consecutive_weekends": best[3],
        "unavoidable_gaps_under_14": best[4],
    }


def participant_pool_schedule_profile(
    plan: Mapping[str, Any],
    pool: Mapping[str, Any],
    *,
    problem: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return distributable opportunity/load and label diagnostics for a pool."""

    club = str(pool.get("club") or "")
    age_group = str(pool.get("age_group") or "")
    labels = [str(item) for item in pool.get("team_labels", []) or []]
    label_counts = Counter({label: 0 for label in labels})
    loads: Counter[date] = Counter()

    for tournament in plan.get("tournaments", []) or []:
        if tournament.get("cancelled") or str(tournament.get("age_group") or "") != age_group:
            continue
        present = {
            str(team.get("label") or "")
            for team in tournament.get("teams", []) or []
            if str(team.get("club") or "") == club
            and str(team.get("label") or "") in label_counts
            and not bool(team.get("guest", False))
        }
        if not present:
            continue
        parsed = _parse_date(tournament.get("date"))
        for label in present:
            label_counts[label] += 1
            if parsed is not None:
                loads[parsed] += 1

    date_loads = sorted(loads.items())
    spacing = _minimum_unavoidable_spacing(date_loads, len(labels))
    unique_dates = [item[0] for item in date_loads]
    union_gaps = [(nxt - previous).days for previous, nxt in zip(unique_dates, unique_dates[1:])]
    spacing["pool_opportunity_min_gap_days"] = min(union_gaps) if union_gaps else None
    spacing["pool_opportunity_gaps_under_14"] = sum(
        1 for gap in union_gaps if gap < PREFERRED_GAP_DAYS
    )

    temporal = {
        "lead_gap_days": None,
        "finish_gap_days": None,
        "max_intra_gap_days": max(union_gaps, default=0),
        "max_gap_days": max(union_gaps, default=0),
    }
    if problem:
        season_start = _parse_date(problem.get("start_date"))
        season_end = _parse_date(problem.get("end_date"))
        if season_start is not None and season_end is not None:
            split = _parse_date(problem.get("christmas_split_date"))
            active_start, active_end = planning_half.active_window_for_age_group(
                age_group,
                season_start,
                season_end,
                split,
                problem.get("participation_targets_by_age_group"),
            )
            coverage = team_temporal_coverage(
                (club, str(pool.get("pool_id") or "rotatable-pool"), age_group),
                club,
                age_group,
                active_start,
                active_end,
                unique_dates,
            )
            temporal = {
                "lead_gap_days": coverage.lead_gap_days,
                "finish_gap_days": coverage.finish_gap_days,
                "max_intra_gap_days": coverage.max_intra_gap_days,
                "max_gap_days": coverage.max_gap_days,
            }

    counts = [label_counts[label] for label in labels]
    return {
        "pool": {
            "pool_id": str(pool.get("pool_id") or ""),
            "club": club,
            "age_group": age_group,
            "team_labels": labels,
            "rotatable": True,
        },
        "opportunity_count": sum(counts),
        "active_date_count": len(unique_dates),
        "tournament_dates": [on_date.isoformat() for on_date in unique_dates],
        "date_loads": [
            {"date": on_date.isoformat(), "opportunities": load}
            for on_date, load in date_loads
        ],
        "spacing": spacing,
        "temporal": temporal,
        "label_distribution": {
            "counts": {label: label_counts[label] for label in labels},
            "spread": max(counts, default=0) - min(counts, default=0),
            "deprived_labels": [label for label in labels if label_counts[label] == 0],
        },
    }


__all__ = [
    "ROTATABLE_POOLS_KEY",
    "participant_pool_schedule_profile",
    "resolve_rotatable_participant_pool",
]
