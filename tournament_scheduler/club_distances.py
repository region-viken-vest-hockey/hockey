"""Approximate driving distances between RVV club arenas (kilometres).

Provides distance lookups for the nine Region Viken Vest clubs:

    Kongsberg, Jar, Holmen, Ringerike, Skien, Jutul, Frisk Asker,
    Tønsberg, Sandefjord Penguins

Distances are derived from the great-circle ("as the crow flies") distance
between each club's home arena, computed via the haversine formula, and then
scaled up by a fixed road-correction factor to approximate real driving
distance (roads are rarely straight lines, especially in Norway's
fjord/valley terrain).

Usage::

    from tournament_scheduler.club_distances import distance, furthest_traveling_team
    from tournament_scheduler.models import Tournament

    km = distance("Kongsberg", "Jar")       # -> ~85
    result = furthest_traveling_team(tournament)  # -> (Team, km) or None
"""

import math
from typing import Dict, Optional, Tuple

from tournament_scheduler.club_registry import canonicalize_club_name
from tournament_scheduler.models import SeasonPlan, Tournament, Team, find_duplicate_labels, team_key

# ---------------------------------------------------------------------------
# Club arena coordinates (latitude, longitude in decimal degrees)
#
# Approximate real-world coordinates of each club's home ice hockey arena.
# Used to compute great-circle distances via the haversine formula.
# ---------------------------------------------------------------------------

_CLUB_COORDINATES: Dict[str, Tuple[float, float]] = {
    # Kongsberg - Kongsberghallen
    "Kongsberg": (59.6669, 9.6500),
    # Jar - Jarhallen (Bærum)
    "Jar": (59.8989, 10.5722),
    # Holmen - Holmen ishall (Asker)
    "Holmen": (59.8434, 10.4569),
    # Ringerike - Ringerikshallen (Hønefoss)
    "Ringerike": (60.1690, 10.2580),
    # Skien - Skien ishall
    "Skien": (59.2096, 9.6080),
    # Jutul - Bærum ishall (Bekkestua)
    "Jutul": (59.9000, 10.5333),
    # Frisk Asker - Varner Arena (Asker)
    "Frisk Asker": (59.8331, 10.4356),
    # Tønsberg - Tønsberghallen
    "Tønsberg": (59.2674, 10.4076),
    # Sandefjord Penguins - Bugården ishall
    "Sandefjord Penguins": (59.1313, 10.2167),
}

# ---------------------------------------------------------------------------
# Road-correction factor
#
# Great-circle ("as the crow flies") distances underestimate real driving
# distances, since roads follow terrain, fjords and valleys rather than
# straight lines. This factor scales the haversine distance up to better
# approximate actual road travel distance.
# ---------------------------------------------------------------------------

_ROAD_DISTANCE_FACTOR = 1.3

# ---------------------------------------------------------------------------
# Earth radius (km), used by the haversine formula
# ---------------------------------------------------------------------------

_EARTH_RADIUS_KM = 6371.0


def _haversine_km(coord_a: Tuple[float, float], coord_b: Tuple[float, float]) -> float:
    """Return the great-circle distance in km between two (lat, lon) points."""
    lat1, lon1 = coord_a
    lat2, lon2 = coord_b

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return _EARTH_RADIUS_KM * c

# ---------------------------------------------------------------------------
# Arena-to-club reverse mapping
#
# Maps arena names (e.g. "Kongsberghallen") back to the club name they
# belong to, so we can resolve which club a team's travel endpoint is.
# ---------------------------------------------------------------------------

_ARENA_TO_CLUB: Dict[str, str] = {
    "Kongsberghallen": "Kongsberg",
    "Jarhallen": "Jar",
    "Holmenkollen ishall": "Holmen",
    "Ringerikshallen": "Ringerike",
    "Skien ishall": "Skien",
    "Bærum ishall": "Jutul",
    "Varner Arena": "Frisk Asker",
    "Tønsberghallen": "Tønsberg",
    "Bugården ishall": "Sandefjord Penguins",
}

def _normalize_club_name(club: Optional[str]) -> Optional[str]:
    """Resolve shortened/legacy club names before distance/coordinate lookups.

    Delegates to `club_registry.canonicalize_club_name` (issue #272) so travel
    metrics share one canonicalization source of truth with the rest of the
    pipeline instead of maintaining a separate alias table.
    """
    if club is None:
        return None
    return canonicalize_club_name(club)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def distance(club_a: str, club_b: str) -> int:
    """Return the approximate driving distance in km between two clubs' arenas.

    The lookup is symmetric — the order of arguments does not matter.
    Returns 0 when the distance for a given pair is unknown (e.g. one of
    the clubs is not in the RVV region), or when both clubs are the same.

    The distance is computed from the great-circle distance between the
    clubs' arena coordinates (haversine formula), scaled by
    ``_ROAD_DISTANCE_FACTOR`` to approximate real driving distance.
    """
    club_a = _normalize_club_name(club_a) or club_a
    club_b = _normalize_club_name(club_b) or club_b

    if club_a == club_b:
        return 0

    coord_a = _CLUB_COORDINATES.get(club_a)
    coord_b = _CLUB_COORDINATES.get(club_b)
    if coord_a is None or coord_b is None:
        return 0

    great_circle_km = _haversine_km(coord_a, coord_b)
    return round(great_circle_km * _ROAD_DISTANCE_FACTOR)


def arena_to_club(arena: str) -> Optional[str]:
    """Return the club name that owns *arena*, or ``None`` if unknown."""
    return _normalize_club_name(_ARENA_TO_CLUB.get(arena))


def furthest_traveling_team(
    tournament: Tournament,
) -> Optional[Tuple[Team, int]]:
    """Return the team in *tournament* with the longest estimated travel to
    the host arena, along with the distance in km.

    Returns ``(team, distance_km)`` when there is at least one participating
    team from a different club than the host, and the host arena can be
    mapped back to a club. Returns ``None`` when the tournament has no
    participants, the host is unknown, or all participants are local
    (distance 0).
    """
    if not tournament.teams or not tournament.arena:
        return None

    host_club_name = arena_to_club(tournament.arena)
    if host_club_name is None:
        # If we can't map the arena to a known club, try using host_club
        # directly (some tournaments set host_club instead).
        host_club_name = tournament.host_club
    host_club_name = _normalize_club_name(host_club_name)
    if host_club_name is None:
        return None

    best: Optional[Tuple[Team, int]] = None

    for team in tournament.teams:
        team_club = _normalize_club_name(team.club)
        if team_club == host_club_name:
            # Local team — distance 0, skip unless all are local.
            continue
        km = distance(team_club or team.club, host_club_name)
        if km > 0 and (best is None or km > best[1]):
            best = (team, km)

    return best


def compute_team_travel_distances(plan: SeasonPlan) -> dict[str, int]:
    """Return a dict mapping each team's disambiguated key (see
    :func:`tournament_scheduler.models.team_key`) to its total travel
    distance (km) across all *away* tournaments in the season plan.

    An *away* tournament is one where the host club differs from the team's
    club.  Cancelled tournaments are skipped.  Teams that never travel to an
    away tournament still appear in the dict with a value of 0.

    Keys are disambiguated with club and age group whenever a label is
    shared by more than one distinct team (e.g. "Skien" fielded in both U8
    and U9), so travel belonging to unrelated teams is never summed
    together under one label.
    """
    duplicate_labels = find_duplicate_labels(
        team for tournament in plan.tournaments for team in tournament.teams
    )

    totals: dict[str, int] = {}

    for tournament in plan.tournaments:
        if tournament.cancelled:
            continue

        # Resolve host club — same pattern as furthest_traveling_team
        host_club = _normalize_club_name(arena_to_club(tournament.arena))
        if host_club is None:
            host_club = _normalize_club_name(tournament.host_club)

        for team in tournament.teams:
            key = team_key(team, duplicate_labels)

            # Ensure every team appears in the dict at least once
            if key not in totals:
                totals[key] = 0

            if host_club is None:
                # Unknown host — can't compute travel, but team is registered
                continue

            team_club = _normalize_club_name(team.club)
            if team_club == host_club:
                # Local tournament — no travel distance added
                continue

            km = distance(team_club or team.club, host_club)
            totals[key] += km

    return totals
