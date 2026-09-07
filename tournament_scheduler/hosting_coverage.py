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


def _constituent_clubs(club: str) -> List[str]:
    """Split a joint-registration club label (``"Kongsberg/Tønsberg"``) into
    its constituent clubs. A plain single-club label returns itself.

    Generic on purpose -- issue #274 explicitly forbids hardcoding any
    specific pair of clubs. Mirrors the ``"/"``-splitting convention already
    used by ``host_assignment.py``/``season_planner.py`` for joint hosts.
    """
    if "/" not in club:
        return [club]
    parts = [part.strip() for part in club.split("/") if part.strip()]
    return parts or [club]


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
        # A joint registration (e.g. "Kongsberg/Tønsberg") is always
        # resolved to a single physical host club by the time a real
        # tournament exists (season_planner.py never leaves "A/B" as a
        # literal host_club) -- so a joint row's obligation is satisfied by
        # *any* of its constituents hosting that age group, not by an exact
        # string match on the joint label itself (issue #274).
        constituents = _constituent_clubs(club)
        hosted = sum(hosted_counts.get((constituent, age_group), 0) for constituent in constituents)
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


def shared_registration_facts(
    teams: Iterable[Dict[str, Any]],
    tournaments: Iterable[Dict[str, Any]],
    club_calendar_status: Dict[str, str] | None = None,
) -> List[Dict[str, Any]]:
    """Deterministic facts for every joint-club registration (issue #274).

    A joint registration such as ``"Kongsberg/Tønsberg"`` has more than one
    legitimate physical host; *which* constituent should carry a given
    ``(registration, age_group)`` obligation is a contextual fairness
    judgement this module deliberately does not make (see
    ``shared_host_decision.py`` -- Python's job here stops at exposing the
    facts an LLM/controller needs to make that call).

    Returns one entry per joint-club ``(club, age_group)`` row with a
    registered team: ``{"registration", "age_group", "constituents": [...],
    "hosted_by_constituent": {club: count in this age group},
    "hosted_by_constituent_total": {club: count across all age groups},
    "calendar_trust": {club: status}, "automatic_placement_possible":
    {club: bool}}``. Rows whose club label has no ``"/"`` are omitted --
    those are ordinary single-club obligations with nothing to decide.
    """
    teams = list(teams)
    tournaments = list(tournaments)
    club_calendar_status = club_calendar_status or {}

    total_hosted_by_club: Dict[str, int] = {}
    hosted_by_club_and_age: Dict[Tuple[str, str], int] = {}
    for tournament in tournaments:
        if tournament.get("cancelled"):
            continue
        host = tournament.get("host_club")
        age_group = tournament.get("age_group")
        if not host or not age_group:
            continue
        total_hosted_by_club[host] = total_hosted_by_club.get(host, 0) + 1
        hosted_by_club_and_age[(host, age_group)] = hosted_by_club_and_age.get((host, age_group), 0) + 1

    facts: List[Dict[str, Any]] = []
    for club, age_group in required_club_age_group_pairs(teams):
        if "/" not in club:
            continue
        constituents = _constituent_clubs(club)
        facts.append(
            {
                "registration": club,
                "age_group": age_group,
                "constituents": constituents,
                "hosted_by_constituent": {
                    part: hosted_by_club_and_age.get((part, age_group), 0) for part in constituents
                },
                "hosted_by_constituent_total": {
                    part: total_hosted_by_club.get(part, 0) for part in constituents
                },
                "calendar_trust": {
                    part: club_calendar_status.get(part, "unknown") for part in constituents
                },
                "automatic_placement_possible": {
                    part: club_calendar_status.get(part, "unknown") == "known" for part in constituents
                },
            }
        )
    return facts


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
