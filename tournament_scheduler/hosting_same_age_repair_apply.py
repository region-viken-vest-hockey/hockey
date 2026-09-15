"""Same-age hosting-coverage repair execution for `SeasonPlanner` (issue #329).

`hosting_same_age_repair.py` computes deterministic candidate donor
tournaments as a pure function. This module actually *applies* a repair: it
needs `SeasonPlanner`'s live tournament-build pipeline (arena/slot search,
hosting-day bookkeeping), so it operates directly on planner internals and
is kept separate from the pure evidence module -- same split as
`hosting_cross_age_repair.py` / `hosting_cross_age_repair_apply.py`.

Unlike a cross-age repair, no participant ever changes here: the deficit
club is already represented in the donor tournament's roster, so only the
host/arena/start-time change. That means no participation-target
re-verification is needed -- a rejected candidate leaves no trace, so
trying several candidates for one deficit row in sequence is always safe.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from tournament_scheduler.hosting_coverage import hosting_coverage_matrix, unresolved_from_matrix
from tournament_scheduler.hosting_cross_age_repair_ops import build_tournament, move_hosting_day
from tournament_scheduler.hosting_same_age_repair import same_age_reallocation_candidates
from tournament_scheduler.models import Tournament


def attempt_same_age_repairs(planner, plan) -> List[Dict[str, Any]]:
    """Try to resolve each unresolved (club, age_group) hosting obligation by
    reassigning host to *club* on a tournament in that same age group where
    *club* already has a participating team.

    Mutates `plan.tournaments` and the planner's hosting-day bookkeeping in
    place for every repair actually applied. Returns one log entry per
    attempted deficit row: `{"club", "age_group", "status": "repaired",
    "donor_tournament_id", "tournament_id", "reason"}` when resolved, or
    `{"club", "age_group", "status": "unresolved", "rejected_candidates":
    [{"donor_tournament_id", "reason"}, ...]}` when not.

    Call once, after `plan.tournaments` is fully built (baseline + host
    substitution) and before `hosting_cross_age_repair_apply.attempt_cross_age_repairs`
    -- this repair is strictly cheaper (no participant/roster change, no
    other tournament touched), so it should get first chance at resolving
    each deficit row before the more disruptive cross-age reallocation is
    attempted.
    """
    skipped_age_groups = {entry["age_group"] for entry in plan.skipped_age_groups}
    coverage_teams = [
        {"club": t.club, "age_group": t.age_group}
        for t in planner.roster.teams
        if t.age_group not in skipped_age_groups
    ]
    coverage_rows = hosting_coverage_matrix(coverage_teams, _coverage_tournament_dicts(plan))
    unresolved_rows = unresolved_from_matrix(coverage_rows)
    if not unresolved_rows:
        return []

    log: List[Dict[str, Any]] = []

    for row in unresolved_rows:
        club, age_group = row["club"], row["age_group"]
        candidates = same_age_reallocation_candidates(club, age_group, _coverage_tournament_dicts(plan))

        rejected: List[Dict[str, str]] = []
        repaired_entry: Optional[Dict[str, Any]] = None
        for candidate in candidates:
            donor = next((t for t in plan.tournaments if t.id == candidate["tournament_id"]), None)
            if donor is None or donor.cancelled:
                continue
            outcome = _try_repair(planner, plan, donor, club, age_group)
            if outcome.get("tournament") is not None:
                repaired_entry = {
                    "club": club,
                    "age_group": age_group,
                    "status": "repaired",
                    "donor_tournament_id": donor.id,
                    "tournament_id": outcome["tournament"].id,
                    "reason": outcome["reason"],
                }
                break
            rejected.append({"donor_tournament_id": donor.id, "reason": outcome["reason"]})

        log.append(
            repaired_entry
            or {
                "club": club,
                "age_group": age_group,
                "status": "unresolved",
                "rejected_candidates": rejected,
            }
        )
    return log


def _coverage_tournament_dicts(plan) -> List[Dict[str, Any]]:
    return [
        {
            "id": t.id,
            "date": t.date.isoformat(),
            "arena": t.arena,
            "age_group": t.age_group,
            "host_club": t.host_club,
            "cancelled": t.cancelled,
            "teams": [{"club": team.club} for team in t.teams],
        }
        for t in plan.tournaments
    ]


def _try_repair(planner, plan, donor: Tournament, club: str, age_group: str) -> Dict[str, Any]:
    """Attempt one donor candidate. Returns `{"tournament": Tournament|None,
    "reason": str}` -- mutates planner/plan only when `"tournament"` is set.
    """
    reserved_events_by_club: Dict[str, List] = {}
    for t in plan.tournaments:
        if t.id == donor.id or t.cancelled:
            continue
        reservation = planner._reservation_event_for_tournament(t)
        if reservation is not None:
            reserved_events_by_club.setdefault(t.host_club, []).append(reservation)

    slot = planner._find_slot_for_tournament(
        donor.date,
        club,
        age_group,
        donor.games,
        reserved_events_by_club=reserved_events_by_club,
    )
    if slot is None:
        return {
            "tournament": None,
            "reason": f"no free arena/time slot for {club} to host {age_group} on {donor.date.isoformat()}",
        }
    final_host_club, start_time, _end = slot

    new_tournament = build_tournament(
        planner, donor.date, final_host_club, age_group, donor.teams, donor.games, start_time
    )

    move_hosting_day(planner, plan, donor, new_tournament)

    plan.tournaments.remove(donor)
    plan.tournaments.append(new_tournament)

    return {
        "tournament": new_tournament,
        "reason": (
            f"{club} had an unresolved {age_group} hosting obligation and already participates in the "
            f"{donor.date.isoformat()} {age_group} tournament at {donor.arena}; reassigned host to {club}."
        ),
    }
