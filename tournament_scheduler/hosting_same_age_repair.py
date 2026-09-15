"""Deterministic same-age hosting-coverage repair facts (issue #329).

`hosting_cross_age_repair.py` can only resolve a club x age-group hosting
obligation by repurposing that physical club's own *surplus* hosting in a
*different* age group. A club that hosts nothing anywhere (e.g. a
newly-active club with a registered, eligible team that simply never wins
the fairness/diversity scoring competition against established clubs) has
no surplus to reallocate, so that repair has nothing to work with -- even
though the club may already be participating in several tournaments of the
very age group it is missing hosting coverage for.

This module covers that gap: it looks for a tournament in the *same*
deficit age group where the physical club already has a participating team
but is not yet the host. Repurposing such a tournament needs no participant
swap at all -- the club is already represented, so the #322/#323
host-representation invariant is trivially satisfied; only the host/arena/
start-time need to change. That makes it strictly cheaper and lower-risk
than a cross-age repair (which removes a whole donor tournament and must
re-verify participation targets), so it should be tried first.

Pure functions over the shared ``planning_problem``/``candidate`` contracts,
like ``hosting_cross_age_repair.py`` -- no ``SeasonPlanner`` dependency, so
this module is safe for the canonical LLM-directed decision path (see
``tests/test_architecture_boundaries.py``). Actually *applying* a repair
needs a live tournament-build pipeline and lives in
``hosting_same_age_repair_apply.py`` instead; this module only computes
candidate donor tournaments -- it never decides which repair (if any) to
apply.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from tournament_scheduler.host_representation import constituent_clubs as _constituent_clubs


def same_age_reallocation_candidates(
    club: str,
    age_group: str,
    tournaments: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Tournaments in *age_group* where physical *club* already participates
    but does not yet host.

    A candidate is a non-cancelled tournament in exactly this age group
    whose host is some other club, but at least one of its teams belongs to
    *club* or a joint registration sharing a constituent with *club* (the
    same shared-registration matching ``hosting_cross_age_repair.py`` uses).
    Ordered deterministically: latest date first (repurposing the most
    recently-committed slot disturbs the fewest already-communicated dates),
    tie-broken by tournament id.

    Does not itself check arena/duration/calendar feasibility -- that
    requires a live planner and is ``hosting_same_age_repair_apply.py``'s
    job. This only narrows the search to tournaments that are structurally
    plausible donors at all.
    """
    club_constituents = set(_constituent_clubs(club))

    def _participates(tournament: Dict[str, Any]) -> bool:
        for team in tournament.get("teams", []) or []:
            team_club = team.get("club")
            if not team_club:
                continue
            if club_constituents & set(_constituent_clubs(team_club)):
                return True
        return False

    candidates = [
        {
            "tournament_id": t.get("id"),
            "date": t.get("date"),
            "arena": t.get("arena"),
            "host_club": t.get("host_club"),
        }
        for t in tournaments
        if not t.get("cancelled")
        and t.get("age_group") == age_group
        and t.get("host_club") != club
        and _participates(t)
    ]
    candidates.sort(
        key=lambda c: (str(c["date"] or ""), str(c["tournament_id"] or "")),
        reverse=True,
    )
    return candidates
