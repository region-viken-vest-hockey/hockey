"""Deterministic cross-age hosting-coverage repair facts (issue #328).

`hosting_coverage.py` reasons about club x age-group hosting coverage strictly
*within* one age group: a club's tournaments in a different age group never
count toward its own coverage row there. That is correct for *coverage*
itself, but it means nothing ever asks whether a physical club's surplus
(duplicate) hosting in one age group could legally satisfy a missing
obligation in a *different* age group before that obligation is emitted as
`unresolved_hosting_obligation`.

Pure functions over the shared `planning_problem`/`candidate` contracts, like
`hosting_coverage.py` -- no `SeasonPlanner` dependency, so this module is safe
for the canonical LLM-directed decision path (see
`tests/test_architecture_boundaries.py`). Actually *applying* a repair needs a
live tournament-build pipeline and lives in `hosting_cross_age_repair_apply.py`
instead; this module only computes evidence and candidate donor slots -- it
never decides which repair (if any) to apply.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from tournament_scheduler.host_representation import constituent_clubs as _constituent_clubs
from tournament_scheduler.hosting_coverage import (
    hosting_targets_with_coverage_floor as _hosting_targets_with_coverage_floor,
    required_club_age_group_pairs as _required_club_age_group_pairs,
)


def club_hosting_evidence(
    teams: Iterable[Dict[str, Any]],
    tournaments: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """One row per (physical club, age_group) with a registered team.

    Each row: ``{"club", "age_group", "registered_team_count",
    "hosting_target", "actual_hosting_count", "coverage_satisfied",
    "surplus_hosting_count"}``. ``hosting_target`` is computed the same way
    `host_assignment.hosting_targets_for_age_group` computes it during
    baseline building -- `hosting_coverage.hosting_targets_with_coverage_floor`
    over this age group's own club-weight map and this age group's own total
    hosted-tournament count -- but here it is measured against the *final*
    tournament list, after any host substitution, not against in-progress
    planner state. ``surplus_hosting_count`` is how many *more* tournaments
    than its target this club hosted in this age group -- exactly the
    duplicate/extra hosting a cross-age repair could try to reuse.

    A joint registration's own row (e.g. ``"Kongsberg/Tønsberg"``) is resolved
    through its physical constituents the same way `hosting_coverage_matrix`
    already does, so a shared registration's obligation/surplus is always
    attributed to a real, physically hostable club.
    """
    teams = list(teams)
    tournaments = [t for t in tournaments if not t.get("cancelled")]

    team_counts: Dict[Tuple[str, str], int] = {}
    for team in teams:
        club = team.get("club")
        age_group = team.get("age_group")
        if not club or not age_group:
            continue
        team_counts[(club, age_group)] = team_counts.get((club, age_group), 0) + 1

    hosted_counts: Dict[Tuple[str, str], int] = {}
    tournament_counts_by_age_group: Dict[str, int] = {}
    for tournament in tournaments:
        host = tournament.get("host_club")
        age_group = tournament.get("age_group")
        if not age_group:
            continue
        tournament_counts_by_age_group[age_group] = tournament_counts_by_age_group.get(age_group, 0) + 1
        if not host:
            continue
        hosted_counts[(host, age_group)] = hosted_counts.get((host, age_group), 0) + 1

    weights_by_age_group: Dict[str, Dict[str, int]] = {}
    for club, age_group in _required_club_age_group_pairs(teams):
        weights_by_age_group.setdefault(age_group, {})[club] = team_counts.get((club, age_group), 0)

    targets_by_age_group: Dict[str, Dict[str, int]] = {}
    for age_group, weights in weights_by_age_group.items():
        targets, _unmet = _hosting_targets_with_coverage_floor(
            weights, tournament_counts_by_age_group.get(age_group, 0)
        )
        targets_by_age_group[age_group] = targets

    rows: List[Dict[str, Any]] = []
    for club, age_group in _required_club_age_group_pairs(teams):
        constituents = _constituent_clubs(club)
        actual = sum(hosted_counts.get((constituent, age_group), 0) for constituent in constituents)
        target = targets_by_age_group.get(age_group, {}).get(club, 0)
        rows.append(
            {
                "club": club,
                "age_group": age_group,
                "registered_team_count": team_counts.get((club, age_group), 0),
                "hosting_target": target,
                "actual_hosting_count": actual,
                "coverage_satisfied": actual > 0,
                "surplus_hosting_count": max(0, actual - target),
            }
        )
    return rows


def candidate_reallocation_slots(
    club: str,
    deficit_age_group: str,
    tournaments: Iterable[Dict[str, Any]],
    evidence: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Donor tournaments *club* could try to repurpose for *deficit_age_group*.

    A donor is a tournament this exact physical club currently hosts in a
    *different* age group where this club's own evidence row shows a
    ``surplus_hosting_count > 0`` -- i.e. hosting more than its own target
    there, never a tournament that is itself needed to meet another age
    group's own coverage/participation. Ordered deterministically: the
    surplus age group with the largest surplus first, then latest date first
    within an age group (repurposing the most recently duplicated slot
    disturbs the fewest already-communicated dates); ties broken by
    tournament id for full determinism.

    Does not itself check arena/duration/calendar/participant feasibility --
    that requires a live planner and is `hosting_cross_age_repair_apply.py`'s
    job. This only narrows the search to tournaments that are structurally
    plausible donors at all.
    """
    surplus_by_age_group = {
        row["age_group"]: row["surplus_hosting_count"]
        for row in evidence
        if row["club"] == club and row["age_group"] != deficit_age_group and row["surplus_hosting_count"] > 0
    }
    if not surplus_by_age_group:
        return []

    candidates = [
        {
            "tournament_id": t.get("id"),
            "date": t.get("date"),
            "arena": t.get("arena"),
            "age_group": t.get("age_group"),
            "source_surplus_hosting_count": surplus_by_age_group[t.get("age_group")],
        }
        for t in tournaments
        if not t.get("cancelled")
        and t.get("host_club") == club
        and t.get("age_group") in surplus_by_age_group
    ]
    # Largest surplus first, then latest date first, then tournament id --
    # all three descending, so one `reverse=True` sort suffices.
    candidates.sort(
        key=lambda c: (
            c["source_surplus_hosting_count"],
            str(c["date"] or ""),
            str(c["tournament_id"] or ""),
        ),
        reverse=True,
    )
    return candidates


def unresolved_with_evidence(
    coverage_rows: Iterable[Dict[str, Any]],
    evidence: Iterable[Dict[str, Any]],
    tournaments: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """`hosting_coverage.unresolved_from_matrix`, with `candidate_reallocation_slots`
    attached to each unresolved row.

    Consumed by `planning_contract.verify_candidate` (canonical candidate
    path) and `pipeline/audit_context.py` -- both expose this evidence
    without applying a repair themselves, per issue #328's "expose legal
    repair candidates and verify them deterministically; the LLM/audit can
    judge whether a trade-off is desirable" boundary.
    """
    evidence = list(evidence)
    tournaments = list(tournaments)
    unresolved: List[Dict[str, Any]] = []
    for row in coverage_rows:
        if not row.get("unresolved"):
            continue
        club, age_group = row["club"], row["age_group"]
        unresolved.append(
            {
                "club": club,
                "age_group": age_group,
                "candidate_reallocation_slots": candidate_reallocation_slots(
                    club, age_group, tournaments, evidence
                ),
            }
        )
    return unresolved
