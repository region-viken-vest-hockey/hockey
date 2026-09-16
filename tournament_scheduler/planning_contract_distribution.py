"""Hosting/date-distribution scoring blocks for `planning_contract.score_candidate`.

Split out of `planning_contract.py` (which calls these) to keep that module
under the file-length guideline -- these two blocks share no state with the
rest of `score_candidate` beyond the already-normalized `tournaments` list.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

from tournament_scheduler import planning_half


def hosting_fairness(tournaments: List[Dict[str, Any]], problem: Optional[Dict[str, Any]]) -> Tuple[Dict[str, int], int, Dict[str, Any]]:
    """Return `(counts_by_host, spread, hosting_coverage)`.

    issue #266 P0: a single global spread scalar hides a club that hosts
    nothing in one age group while over-hosting in another. When *problem*
    carries the registered roster, `hosting_coverage` also reports the full
    club x age-group coverage matrix (and its per-club/per-age-group
    rollups) alongside the spread.
    """
    host_counts: Dict[str, int] = {}
    for t in tournaments:
        host = t.get("host_club") or t.get("arena")
        if host:
            host_counts[host] = host_counts.get(host, 0) + 1
    spread = (max(host_counts.values()) - min(host_counts.values())) if host_counts else 0

    hosting_coverage: Dict[str, Any] = {}
    if problem is not None:
        from tournament_scheduler.hosting_coverage import (
            hosting_balance_matrix,
            hosting_breakdown_by_club_and_age_group,
            hosting_coverage_matrix,
            material_hosting_balance_imbalances,
            unresolved_from_matrix,
        )

        coverage_rows = hosting_coverage_matrix(problem.get("teams", []), tournaments)
        balance_rows = hosting_balance_matrix(problem.get("teams", []), tournaments)
        hosting_coverage = {
            "club_age_group_matrix": coverage_rows,
            "club_age_group_balance": balance_rows,
            "balance_imbalances": material_hosting_balance_imbalances(balance_rows),
            "unresolved_obligations": unresolved_from_matrix(coverage_rows),
            **hosting_breakdown_by_club_and_age_group(coverage_rows),
        }

    return host_counts, spread, hosting_coverage


def month_and_half_distribution(
    tournaments: List[Dict[str, Any]],
    split_date: Optional[date],
    parse_date: Callable[[Any], Optional[date]],
) -> Tuple[Dict[str, int], Dict[str, int], float]:
    """Return `(month_counts, half_counts, half_deviation_pct)`."""
    month_counts: Dict[str, int] = {}
    for t in tournaments:
        t_date = parse_date(t.get("date"))
        if t_date:
            key = f"{t_date.year:04d}-{t_date.month:02d}"
            month_counts[key] = month_counts.get(key, 0) + 1

    half_counts: Dict[str, int] = {"before_christmas": 0, "after_christmas": 0, "unsplit": 0}
    for t in tournaments:
        t_date = parse_date(t.get("date"))
        if t_date is None:
            continue
        half_counts[planning_half.tournament_half(t_date, split_date)] += 1

    split_total = half_counts["before_christmas"] + half_counts["after_christmas"]
    half_deviation_pct = (
        abs(half_counts["before_christmas"] - half_counts["after_christmas"]) / split_total * 100.0
        if split_total
        else 0.0
    )
    return month_counts, half_counts, half_deviation_pct
