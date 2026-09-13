"""Shared/joint-registration club-matching semantics (issue #322).

Pure functions with no ``SeasonPlanner`` dependency, reused by every module
that needs to know whether a team's club label "represents" a given
physical host club: the baseline planner (``season_planner.py``), the
independent verifier (``planning_contract.py``), the Stage 3 local
optimizer (``stage3_optimizer.py``), and CP-SAT (``stage3_cpsat.py``). A
single implementation here keeps the "shared/joint registration counts for
either physical constituent" rule consistent everywhere it is enforced.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple


def constituent_clubs(club: str) -> List[str]:
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


def clubs_represent_same_club(club_a: str, club_b: str) -> bool:
    """True when *club_a* and *club_b* share at least one constituent club.

    Covers the ordinary case (equal single-club labels) and every
    shared/joint-registration case: physical host ``"Jutul"`` and
    participant club ``"Jutul/Jar"`` share the constituent ``"Jutul"``, so
    this returns ``True`` for either order of the arguments.
    """
    if not club_a or not club_b:
        return False
    return bool(set(constituent_clubs(club_a)) & set(constituent_clubs(club_b)))


def _team_club(team: Any) -> str:
    if isinstance(team, dict):
        return team.get("club") or ""
    return getattr(team, "club", "") or ""


def _team_age_group(team: Any) -> str:
    if isinstance(team, dict):
        return team.get("age_group") or ""
    return getattr(team, "age_group", "") or ""


def host_eligible_teams(teams: Iterable[Any], host_club: str, age_group: str) -> List[Any]:
    """Registered *teams* (dicts or :class:`~tournament_scheduler.models.Team`
    instances) representing *host_club* in *age_group*.

    A team "represents" a host when its club label's constituents overlap
    the host's constituents (see :func:`clubs_represent_same_club`), so a
    shared/joint registration such as ``"Jutul/Jar"`` counts as
    representation for either physical constituent host.
    """
    if not host_club or not age_group:
        return []
    return [
        team
        for team in teams
        if _team_age_group(team) == age_group and clubs_represent_same_club(_team_club(team), host_club)
    ]


def host_has_eligible_team(teams: Iterable[Any], host_club: str, age_group: str) -> bool:
    """True when *host_club* has at least one eligible registered team in
    *age_group* -- i.e. the hard host-representation invariant actually
    applies to this (host, age_group) combination."""
    return any(True for _ in host_eligible_teams(teams, host_club, age_group))


def host_represented_in(participants: Iterable[Any], host_club: str) -> bool:
    """True when at least one of *participants* represents *host_club*,
    regardless of age group (callers already filter *participants* to the
    tournament's own age group)."""
    if not host_club:
        return True
    return any(clubs_represent_same_club(_team_club(team), host_club) for team in participants)


def swap_breaks_host_representation(
    host_club: Optional[str],
    age_group: str,
    team_ids: Iterable[Tuple[str, str, str]],
    outgoing: Tuple[str, str, str],
    incoming: Tuple[str, str, str],
    problem: Optional[Dict[str, Any]],
    cache: Optional[Dict[Tuple[str, str], bool]] = None,
) -> bool:
    """True when replacing *outgoing* with *incoming* among *team_ids* would
    leave *host_club* unrepresented, and *host_club* has an eligible
    registered team in *age_group* so the invariant actually applies (the
    Stage 3 local-search team-swap guard)."""
    if not host_club or not clubs_represent_same_club(outgoing[0], host_club):
        return False
    if clubs_represent_same_club(incoming[0], host_club):
        return False
    if any(tid != outgoing and clubs_represent_same_club(tid[0], host_club) for tid in team_ids):
        return False
    return cached_host_has_eligible_team(problem, host_club, age_group, cache)


def cached_host_has_eligible_team(
    problem: Optional[Dict[str, Any]],
    host_club: Optional[str],
    age_group: str,
    cache: Optional[Dict[Tuple[str, str], bool]] = None,
) -> bool:
    """Memoized :func:`host_has_eligible_team` over ``problem["teams"]``.

    Returns ``False`` (no invariant to protect) when *problem* is not
    supplied, so callers without a problem contract (e.g. isolated search
    unit tests) keep pre-#322 behavior unchanged.
    """
    if problem is None or not host_club:
        return False
    key = (host_club, age_group)
    if cache is not None and key in cache:
        return cache[key]
    eligible = host_has_eligible_team(problem.get("teams", []), host_club, age_group)
    if cache is not None:
        cache[key] = eligible
    return eligible
