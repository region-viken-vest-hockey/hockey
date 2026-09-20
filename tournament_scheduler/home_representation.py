"""Deterministic intra-club home-tournament representation accounting.

Hosting *coverage* and *balance* (``hosting_coverage.py``) answer whether a
club hosts its share of a club x age-group's tournaments. They say nothing
about *which* of a multi-team club's sibling teams actually shows up when the
club hosts: a club with two registered U12 teams can satisfy its hosting
obligation while the same sibling team represents it at nearly every home
tournament, leaving the other sibling almost absent from home ice.

This module owns that one fact family. It is a pure function library over the
shared ``teams``/``tournaments`` contracts -- no planner internals, no search
and no mutation -- so the canonical scorer (``planning_contract``), the
audit evidence layer and the repair provider all read the *same* accounting
instead of each re-deriving "home appearances" with subtly different rules.

For every ``club x age_group`` with at least two registered teams it counts how
many of that club's home tournaments in the age group each sibling team
participated in. The pool spread is ``max - min``; a spread of 0 or 1 is
considered balanced (the issue's "0-1 is normally balanced when mathematically
possible"), so the *material* skew used by the objective model is
``max(0, spread - 1)`` summed/maxed over pools.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from tournament_scheduler.host_representation import clubs_represent_same_club

#: A spread of 0 or 1 across sibling home appearances is balanced.
BALANCED_SPREAD_DAYS = 1


def _team_club(team: Mapping[str, Any]) -> str:
    return str(team.get("club") or "")


def _team_label(team: Mapping[str, Any]) -> str:
    return str(team.get("label") or "")


def _team_age_group(team: Mapping[str, Any]) -> str:
    return str(team.get("age_group") or "")


def multi_team_pools(teams: Iterable[Mapping[str, Any]]) -> List[Tuple[str, str]]:
    """Every ``(club, age_group)`` that has at least two registered teams.

    Order is deterministic (first-seen order in *teams*, which the canonical
    roster already fixes), so callers get a stable list to iterate.
    """
    counts: Dict[Tuple[str, str], int] = defaultdict(int)
    order: List[Tuple[str, str]] = []
    for team in teams:
        club = _team_club(team)
        age_group = _team_age_group(team)
        if not club or not age_group:
            continue
        key = (club, age_group)
        if key not in counts:
            order.append(key)
        counts[key] += 1
    return [key for key in order if counts[key] >= 2]


def _pool_teams(
    teams: Iterable[Mapping[str, Any]], club: str, age_group: str
) -> List[Mapping[str, Any]]:
    seen: set[str] = set()
    out: List[Mapping[str, Any]] = []
    for team in teams:
        if _team_age_group(team) != age_group:
            continue
        if not clubs_represent_same_club(_team_club(team), club):
            continue
        label = _team_label(team)
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(team)
    out.sort(key=lambda team: _team_label(team))
    return out


def _home_tournaments(
    tournaments: Iterable[Mapping[str, Any]], club: str, age_group: str
) -> List[Mapping[str, Any]]:
    out = []
    for tournament in tournaments:
        if tournament.get("cancelled"):
            continue
        if str(tournament.get("age_group") or "") != age_group:
            continue
        host = str(tournament.get("host_club") or "")
        if not host or not clubs_represent_same_club(host, club):
            continue
        out.append(tournament)
    return out


def home_representation_rows(
    teams: Iterable[Mapping[str, Any]],
    tournaments: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """One row per multi-team ``club x age_group`` pool.

    ``home_appearances`` maps each *registered* sibling label to the number of
    the club's home tournaments in that age group it participated in. A
    tournament is counted at most once per team even if the roster lists it
    twice, and only same-age participants of a club that represents the host
    count. ``spread`` is ``max - min``; ``material_spread`` is
    ``max(0, spread - 1)`` so the canonical "0-1 is balanced" policy is encoded
    once, here, instead of in every consumer.
    """
    teams = list(teams)
    tournaments = list(tournaments)
    rows: List[Dict[str, Any]] = []
    for club, age_group in multi_team_pools(teams):
        pool = _pool_teams(teams, club, age_group)
        if len(pool) < 2:
            continue
        appearances: Dict[str, int] = {_team_label(team): 0 for team in pool}
        home = _home_tournaments(tournaments, club, age_group)
        for tournament in home:
            participants = {
                _team_label(team)
                for team in tournament.get("teams", []) or []
                if _team_age_group(team) == age_group
                and clubs_represent_same_club(_team_club(team), club)
            }
            for label in participants:
                if label in appearances:
                    appearances[label] += 1
        values = list(appearances.values())
        max_appearances = max(values) if values else 0
        min_appearances = min(values) if values else 0
        spread = max_appearances - min_appearances
        rows.append(
            {
                "club": club,
                "age_group": age_group,
                "team_count": len(pool),
                "teams": [
                    {"club": _team_club(team), "label": _team_label(team)}
                    for team in pool
                ],
                "home_tournament_count": len(home),
                "home_appearances": dict(appearances),
                "max_appearances": max_appearances,
                "min_appearances": min_appearances,
                "spread": spread,
                "material_spread": max(0, spread - BALANCED_SPREAD_DAYS),
                "balanced": spread <= BALANCED_SPREAD_DAYS,
                "evidence": _evidence(club, age_group, appearances),
            }
        )
    return rows


def home_representation_summary(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Bounded summary of the pool rows for the shared quality objective.

    ``max_spread``/``skewed_pool_count`` describe the raw skew; the objective
    model consumes ``max_material_spread``/``material_skew_pool_count`` so a
    spread of 1 is not treated as a defect.
    """
    rows = list(rows)
    material = [int(row.get("material_spread") or 0) for row in rows]
    return {
        "pool_count": len(rows),
        "balanced_pool_count": sum(1 for row in rows if row.get("balanced")),
        "skewed_pool_count": sum(1 for row in rows if not row.get("balanced")),
        "material_skew_pool_count": sum(1 for value in material if value > 0),
        "max_spread": max((int(row.get("spread") or 0) for row in rows), default=0),
        "max_material_spread": max(material, default=0),
        "total_material_spread": sum(material),
        "pools": rows,
    }


def home_representation_facts(
    teams: Iterable[Mapping[str, Any]],
    tournaments: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Canonical accounting entry point: rows plus their bounded summary."""
    return home_representation_summary(home_representation_rows(teams, tournaments))


def _evidence(club: str, age_group: str, appearances: Mapping[str, int]) -> str:
    parts = ", ".join(
        f"{label}={appearances[label]}"
        for label in sorted(appearances, key=lambda name: (-appearances[name], name))
    )
    return f"{club} {age_group}: {parts} home appearance(s)"


__all__ = [
    "BALANCED_SPREAD_DAYS",
    "home_representation_facts",
    "home_representation_rows",
    "home_representation_summary",
    "multi_team_pools",
]
