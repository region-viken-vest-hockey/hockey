"""Deterministic club x age-group hosting-coverage facts (issue #266).

Pure functions over the shared ``planning_problem``/``candidate`` contracts
(``planning_contract.py``) -- no ``SeasonPlanner`` dependency, so this module
is safe for the canonical LLM-directed decision path to import (see
``tests/test_architecture_boundaries.py``, which forbids that path from
depending on ``host_assignment.py``'s heuristic ranking).

Two distinct concerns, kept separate per issue #266:

- *coverage*: every (club, age_group) with a registered team must host at
  least one tournament in that age group during the season. Zero is an
  unresolved obligation -- it is never satisfied by extra tournaments for
  that club in a *different* age group.
- *proportional burden*: once coverage is satisfied (or surfaced as
  unresolved), remaining hosting load should roughly track each club's team
  count, not default to whichever club has the most convenient ice.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple


def required_club_age_group_pairs(teams: Iterable[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """Every distinct (club, age_group) with at least one registered team."""
    seen: set[Tuple[str, str]] = set()
    ordered: List[Tuple[str, str]] = []
    for team in teams:
        club = team.get("club")
        age_group = team.get("age_group")
        if not club or not age_group:
            continue
        key = (club, age_group)
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def hosting_coverage_matrix(
    teams: Iterable[Dict[str, Any]],
    tournaments: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return one row per (club, age_group) with a registered team.

    Each row: ``{"club", "age_group", "teams" (registered team count),
    "hosted" (tournaments actually hosted by that club in that age group),
    "unresolved" (True when hosted == 0)}``. A club's tournaments in a
    *different* age group never count toward its row here -- that is the
    whole point of the club x age-group breakdown issue #266 asks for
    instead of one aggregate hosting count per club.
    """
    team_counts: Dict[Tuple[str, str], int] = {}
    for team in teams:
        club = team.get("club")
        age_group = team.get("age_group")
        if not club or not age_group:
            continue
        team_counts[(club, age_group)] = team_counts.get((club, age_group), 0) + 1

    hosted_counts: Dict[Tuple[str, str], int] = {}
    for tournament in tournaments:
        if tournament.get("cancelled"):
            continue
        host = tournament.get("host_club")
        age_group = tournament.get("age_group")
        if not host or not age_group:
            continue
        hosted_counts[(host, age_group)] = hosted_counts.get((host, age_group), 0) + 1

    rows: List[Dict[str, Any]] = []
    for club, age_group in required_club_age_group_pairs(teams):
        hosted = hosted_counts.get((club, age_group), 0)
        rows.append(
            {
                "club": club,
                "age_group": age_group,
                "teams": team_counts.get((club, age_group), 0),
                "hosted": hosted,
                "unresolved": hosted == 0,
            }
        )
    return rows


def unresolved_from_matrix(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Return ``{club, age_group}`` for every unresolved coverage row."""
    return [
        {"club": row["club"], "age_group": row["age_group"]}
        for row in rows
        if row.get("unresolved")
    ]


def hosting_breakdown_by_club_and_age_group(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate a coverage matrix into per-club and per-age-group summaries."""
    by_club: Dict[str, Dict[str, int]] = {}
    by_age_group: Dict[str, Dict[str, int]] = {}
    for row in rows:
        club = row["club"]
        age_group = row["age_group"]
        hosted = int(row.get("hosted", 0))
        unresolved = 1 if row.get("unresolved") else 0
        club_entry = by_club.setdefault(club, {"hosted": 0, "unresolved": 0})
        club_entry["hosted"] += hosted
        club_entry["unresolved"] += unresolved
        age_entry = by_age_group.setdefault(age_group, {"hosted": 0, "unresolved": 0})
        age_entry["hosted"] += hosted
        age_entry["unresolved"] += unresolved
    return {"by_club": by_club, "by_age_group": by_age_group}


def hosting_targets_with_coverage_floor(
    weights: Dict[str, int], total: int
) -> Tuple[Dict[str, int], List[str]]:
    """Proportional integer hosting targets with a coverage floor of 1.

    Plain largest-remainder rounding (`proportional_integer_targets`) can
    round a small club's share down to 0 even though it fields a real team
    -- issue #266's policy is that every club with a registered team in an
    age group must be *targeted* to host at least once there; 0 is an
    unresolved obligation, never something another (larger) club's target
    should silently absorb.

    Returns ``(targets, unmet)``: *targets* sums to *total* and gives every
    club with a positive weight at least 1 whenever that is arithmetically
    possible (borrowing 1 from whichever remaining club currently holds the
    largest target). *unmet* lists clubs that cannot be given a floor of 1
    because there are literally fewer tournaments than clubs needing
    coverage this age group this season -- a structural shortfall to
    surface directly as an unresolved hosting obligation, not something a
    slot search could ever fix.
    """
    weighted_clubs = [club for club, weight in weights.items() if weight > 0]
    if total <= 0:
        return {club: 0 for club in weights}, list(weighted_clubs)

    targets = proportional_integer_targets(weights, total)

    if len(weighted_clubs) > total:
        # Not enough tournaments this age group for every club to host once
        # -- do not invent extra tournaments to force it. Whoever the
        # ordinary rounding already favors keeps their slot(s); the rest
        # are reported as unmet.
        unmet = [club for club in weighted_clubs if targets.get(club, 0) == 0]
        return targets, unmet

    zero_clubs = [club for club in weighted_clubs if targets.get(club, 0) == 0]
    if not zero_clubs:
        return targets, []

    targets = dict(targets)
    for club in zero_clubs:
        targets[club] = 1
        donor = max(
            (c for c in weighted_clubs if c != club and targets.get(c, 0) > 1),
            key=lambda c: (targets[c], c),
            default=None,
        )
        if donor is not None:
            targets[donor] -= 1
    return targets, []


def proportional_integer_targets(weights: Dict[str, int], total: int) -> Dict[str, int]:
    """Round weighted quotas to integers that sum to *total*.

    Largest-remainder rounding. Lives here (rather than the legacy
    ``host_assignment.py``) so the canonical path can reuse the same
    proportional-burden math issue #266 asks for without importing a module
    ``tests/test_architecture_boundaries.py`` forbids it from depending on.
    ``host_assignment.py`` delegates to this implementation.
    """
    if total <= 0 or not weights:
        return {club: 0 for club in weights}
    weight_sum = sum(max(0, weight) for weight in weights.values()) or 1
    raw_targets = {
        club: max(0, weight) / weight_sum * total
        for club, weight in weights.items()
    }
    targets: Dict[str, int] = {}
    remainders: List[Tuple[float, str]] = []
    assigned = 0
    for club, raw in raw_targets.items():
        rounded = int(raw)
        targets[club] = rounded
        assigned += rounded
        remainders.append((raw - rounded, club))
    remainders.sort(key=lambda item: (-item[0], item[1]))
    for _, club in remainders[: max(0, total - assigned)]:
        targets[club] += 1
    return targets
