"""Consolidated season temporal-coverage model.

Earlier warnings treated "finishes too early relative to the planning
window" (``early_finish``) and "gaps between existing tournaments" as
separate, narrower checks. Both are really the same underlying question: is
a team's participation reasonably spread across the *whole* usable season,
``season_start -> first tournament -> ... -> last tournament -> season_end``?

A team whose tournaments are tightly packed together early in the season
(e.g. six tournaments finishing by early February in a season that runs
into spring) shows up here via a large ``finish_gap_days`` even though every
*existing* tournament might be well spaced from its neighbours — the old
intra-season-only gap check could miss that entirely.

This module is intentionally planner/candidate-agnostic: it takes a season
window and a team's tournament dates and returns the gap breakdown. Both
``tournament_scheduler.warnings`` (SeasonPlanner-side reporting) and
``tournament_scheduler.planning_contract.score_candidate`` (the generic
candidate scorer used for plan selection/optimization) build on it, so the
two callers can't drift into inconsistent definitions of "temporal
coverage" again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, Hashable, List, Sequence, Tuple

# Default max-gap threshold (days) above which a team's season coverage is
# reported as an offender. Mirrors SeasonPlanner's historical
# ``max_early_finish_gap_days`` default so the generic candidate scorer and
# the SeasonPlanner-side warnings agree on what counts as "too sparse"
# unless a caller overrides it.
DEFAULT_TEMPORAL_COVERAGE_THRESHOLD_DAYS = 60


@dataclass(frozen=True)
class TeamTemporalCoverage:
    """Gap breakdown for one team's participation across a season window."""

    team_key: Hashable
    club: str
    age_group: str
    lead_gap_days: int
    finish_gap_days: int
    max_intra_gap_days: int
    max_gap_days: int
    tournament_dates: Tuple[date, ...]


def team_temporal_coverage(
    team_key: Hashable,
    club: str,
    age_group: str,
    season_start: date,
    season_end: date,
    tournament_dates: Sequence[date],
) -> TeamTemporalCoverage:
    """Compute one team's gap breakdown over ``season_start..season_end``.

    ``max_gap_days`` is the largest single gap anywhere along the chain
    season_start -> dates... -> season_end, so a team that plays everything
    in a short early window is flagged the same way a team with genuinely
    sparse intra-season spacing is, without needing two separate checks.

    *season_start*/*season_end* are normalized to plain ``date`` (callers
    sometimes carry a ``datetime`` for the season window, e.g.
    ``SeasonPlan.start_date``/``end_date``) -- ``date - datetime`` raises
    ``TypeError`` even though ``datetime`` subclasses ``date``.
    """
    if isinstance(season_start, datetime):
        season_start = season_start.date()
    if isinstance(season_end, datetime):
        season_end = season_end.date()
    dates = sorted(set(tournament_dates))
    if not dates:
        span = max(0, (season_end - season_start).days)
        return TeamTemporalCoverage(team_key, club, age_group, span, span, 0, span, ())

    lead_gap = max(0, (dates[0] - season_start).days)
    finish_gap = max(0, (season_end - dates[-1]).days)
    intra_gaps = [max(0, (b - a).days) for a, b in zip(dates, dates[1:])]
    max_intra_gap = max(intra_gaps) if intra_gaps else 0
    max_gap = max(lead_gap, finish_gap, max_intra_gap)
    return TeamTemporalCoverage(
        team_key, club, age_group, lead_gap, finish_gap, max_intra_gap, max_gap, tuple(dates)
    )


def season_temporal_coverage(
    season_start: date,
    season_end: date,
    dates_by_team: Dict[Hashable, Sequence[date]],
    team_meta: Dict[Hashable, Tuple[str, str]],
) -> List[TeamTemporalCoverage]:
    """Compute gap breakdowns for every team with recorded tournament dates."""
    results = []
    for team_key, dates in dates_by_team.items():
        club, age_group = team_meta.get(team_key, ("", ""))
        results.append(
            team_temporal_coverage(team_key, club, age_group, season_start, season_end, dates)
        )
    return results


def temporal_offenders(
    coverages: Sequence[TeamTemporalCoverage], threshold_days: int
) -> List[TeamTemporalCoverage]:
    """Return every team whose max gap exceeds *threshold_days*, worst first.

    Deliberately returns all offenders, not just the single worst team, so
    one extreme case doesn't hide other poorly distributed teams.
    """
    return sorted(
        (c for c in coverages if c.max_gap_days > threshold_days),
        key=lambda c: c.max_gap_days,
        reverse=True,
    )
