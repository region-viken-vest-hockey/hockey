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
from typing import Literal, Optional

Half = Literal["before_christmas", "after_christmas", "unsplit"]

_HALF_LABELS_NO: dict[Half, str] = {
    "before_christmas": "Før jul",
    "after_christmas": "Etter jul",
    "unsplit": "Udelt sesong",
}


def christmas_split_date(window_start: date, window_end: date) -> Optional[date]:
    """Return the Christmas cutoff date for a planning window, or ``None``.

    The cutoff is December 24 of ``window_start``'s year. Returns ``None``
    when the window doesn't span that date (e.g. a spring-only run), in
    which case the season has no meaningful before/after split.
    """
    split = date(window_start.year, 12, 24)
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
