"""Shared before/after-Christmas planning-half contract (issue #293).

The RVV Miniputt season is split into two independently plannable halves --
before Christmas and after Christmas -- because clubs can add or withdraw
teams over the Christmas break. This module is the single source of truth
for that boundary so `SeasonPlanner`, `planning_contract.verify_candidate`/
`score_candidate`, and Stage 3's local search all agree on the same split
date and the same half label for a given tournament date, instead of each
engine re-deriving or hardcoding its own Christmas check.
"""

from datetime import date
from typing import Literal, Optional, Tuple

Half = Literal["before_christmas", "after_christmas", "unsplit"]

_HALF_LABELS_NO: dict[Half, str] = {
    "before_christmas": "Før jul",
    "after_christmas": "Etter jul",
    "unsplit": "Udelt sesong",
}


def christmas_split_date(window_start: date, window_end: date) -> Optional[date]:
    """Return the before/after-New-Year cutoff date for a planning window, or ``None``.

    The cutoff is January 1 -- the first one on or after ``window_start``.
    Registration changes happen over the New Year break, not Christmas
    itself, so that is the boundary that separates the two planning halves.
    Returns ``None`` when the window doesn't span that date (e.g. a
    spring-only run), in which case the season has no meaningful
    before/after split.
    """
    split = date(window_start.year, 1, 1)
    if split < window_start:
        split = date(window_start.year + 1, 1, 1)
    if window_start <= split <= window_end:
        return split
    return None


def tournament_half(tournament_date: date, split_date: Optional[date]) -> Half:
    """Return which planning half ``tournament_date`` falls in.

    Returns ``"unsplit"`` when ``split_date`` is ``None`` (the planning
    window doesn't span Christmas), so callers never have to special-case a
    missing split separately from a genuine half.
    """
    if split_date is None:
        return "unsplit"
    return "before_christmas" if tournament_date < split_date else "after_christmas"


def half_label(half: Half) -> str:
    """Norwegian display label for a planning half, for reports/UI."""
    return _HALF_LABELS_NO.get(half, half)


def active_window_for_age_group(
    age_group: str,
    season_start: date,
    season_end: date,
    split_date: Optional[date],
    participation_targets_by_age_group: Optional[dict],
) -> Tuple[date, date]:
    """Return the (start, end) window `age_group` is actually expected to be active in.

    An age group with an authoritative ``before_christmas`` target of 0 is
    intentionally dormant until the New Year, so the window starts at
    ``split_date`` instead of ``season_start`` -- otherwise every team in
    that age group looks like it has a huge, meaningless lead gap.
    Symmetrically, an ``after_christmas`` target of 0 ends the window at
    ``split_date`` instead of ``season_end``. Falls back to
    ``(season_start, season_end)`` unchanged when there's no split date, no
    targets are configured for the age group, or both halves are active.
    """
    if split_date is None or not participation_targets_by_age_group:
        return season_start, season_end
    targets = participation_targets_by_age_group.get(age_group) or {}
    start = split_date if targets.get("before_christmas") == 0 else season_start
    end = split_date if targets.get("after_christmas") == 0 else season_end
    return start, end
