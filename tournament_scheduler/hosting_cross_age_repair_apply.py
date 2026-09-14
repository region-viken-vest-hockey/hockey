"""Cross-age hosting-coverage repair execution for `SeasonPlanner` (issue #328).

`hosting_cross_age_repair.py` computes deterministic evidence (coverage
deficits, surplus hosting, candidate donor tournaments) as pure functions.
This module actually *applies* a repair: it needs `SeasonPlanner`'s live
tournament-build pipeline (participant selection, host/slot search, game
generation, participation counters), so -- like `host_representation_repair.py`
-- it operates directly on planner internals and is kept separate from the
pure evidence module.

A candidate repair is only ever committed to `plan.tournaments`/the planner's
counters after every feasibility check has passed (participation-target
safety, host representation, arena/slot availability); a rejected candidate
leaves no trace, so trying several candidates for one deficit row in sequence
is always safe.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Set

from tournament_scheduler import planning_half
from tournament_scheduler.hosting_coverage import hosting_coverage_matrix, unresolved_from_matrix
from tournament_scheduler.hosting_cross_age_repair import candidate_reallocation_slots, club_hosting_evidence
from tournament_scheduler.hosting_cross_age_repair_ops import (
    build_tournament,
    move_hosting_day,
    participants_with_host_represented,
    remove_tournament_bookkeeping,
)
from tournament_scheduler.models import Tournament

# Mirrors `season_planner.MIN_TEAMS_PER_TOURNAMENT` -- not imported directly
# to avoid a circular import (season_planner.py calls into this module).
_MIN_TEAMS_PER_TOURNAMENT = 3


def attempt_cross_age_repairs(planner, plan) -> List[Dict[str, Any]]:
    """Try to resolve each unresolved (club, age_group) hosting obligation by
    repurposing one of that physical club's own surplus/duplicate hosting
    assignments in a different age group.

    Mutates `plan.tournaments` and the planner's participation/game-count
    counters in place for every repair actually applied. Returns one log
    entry per attempted deficit row: `{"club", "age_group", "status":
    "repaired", "donor_tournament_id", "tournament_id", "reason"}` when
    resolved, or `{"club", "age_group", "status": "unresolved",
    "rejected_candidates": [{"donor_tournament_id", "reason"}, ...]}` when not.

    Call once, after `plan.tournaments` is fully built (baseline + host
    substitution) and before any metric/coverage computation that reads
    `plan.tournaments` -- everything downstream (warnings, hosting coverage,
    fairness gate) then naturally reflects the repaired plan.
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

    split_date = planner._christmas_split_date(plan.start_date, plan.end_date)
    log: List[Dict[str, Any]] = []

    for row in unresolved_rows:
        club, age_group = row["club"], row["age_group"]
        evidence = club_hosting_evidence(coverage_teams, _coverage_tournament_dicts(plan))
        candidates = candidate_reallocation_slots(club, age_group, _coverage_tournament_dicts(plan), evidence)

        rejected: List[Dict[str, str]] = []
        repaired_entry: Optional[Dict[str, Any]] = None
        for candidate in candidates:
            donor = next((t for t in plan.tournaments if t.id == candidate["tournament_id"]), None)
            if donor is None or donor.cancelled:
                continue
            period = _period_for_date(planner, donor.date, split_date)
            outcome = _try_repair(planner, plan, donor, club, age_group, period)
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
        }
        for t in plan.tournaments
    ]


def _period_for_date(planner, on_date: date, split_date: Optional[date]) -> Optional[str]:
    half = planning_half.tournament_half(on_date, split_date)
    if planner._has_split_tournament_targets() and half in ("before_christmas", "after_christmas"):
        return half
    return None


def _try_repair(
    planner,
    plan,
    donor: Tournament,
    club: str,
    deficit_age_group: str,
    period: Optional[str],
) -> Dict[str, Any]:
    """Attempt one donor candidate. Returns `{"tournament": Tournament|None,
    "reason": str}` -- mutates planner/plan only when `"tournament"` is set.
    """
    if _removal_breaks_participation_targets(planner, donor, period):
        return {
            "tournament": None,
            "reason": "removing the donor tournament would drop another team below its participation target",
        }

    already_used = _already_used_team_keys(planner, donor.date, deficit_age_group, plan)
    participants = planner._select_participants(
        deficit_age_group,
        period,
        exclude_team_keys=already_used,
        planned_roster_size=None,
        hosting_priority_clubs={club},
    )
    participants = participants_with_host_represented(
        planner, participants, deficit_age_group, period, club, already_used
    )
    if participants is None:
        return {"tournament": None, "reason": f"{club} has no available/eligible team for {deficit_age_group}"}
    if len(participants) < _MIN_TEAMS_PER_TOURNAMENT:
        return {"tournament": None, "reason": f"only {len(participants)} eligible teams for {deficit_age_group}"}

    games = planner.generate_round_robin_games(participants, planner._parallel_games_for(deficit_age_group))
    if not games:
        return {"tournament": None, "reason": "no round-robin games could be generated"}

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
        deficit_age_group,
        games,
        reserved_events_by_club=reserved_events_by_club,
    )
    if slot is None:
        return {
            "tournament": None,
            "reason": f"no free arena/time slot for {club} to host {deficit_age_group} on {donor.date.isoformat()}",
        }
    final_host_club, start_time, _end = slot

    new_tournament = build_tournament(planner, donor.date, final_host_club, deficit_age_group, participants, games, start_time)

    remove_tournament_bookkeeping(planner, donor, period)
    planner._record_grouping(participants, period)
    planner._record_opponent_history(games)
    move_hosting_day(planner, plan, donor, new_tournament)

    plan.tournaments.remove(donor)
    plan.tournaments.append(new_tournament)

    return {
        "tournament": new_tournament,
        "reason": (
            f"{club} had an unresolved {deficit_age_group} hosting obligation; repurposed its surplus "
            f"{donor.age_group} hosting on {donor.date.isoformat()} at {donor.arena}"
        ),
    }


def _removal_breaks_participation_targets(planner, donor: Tournament, period: Optional[str]) -> bool:
    """True when removing *donor* would drop any of its own participants
    below their own participation target -- i.e. this donor tournament was
    not really surplus for at least one of its teams, even though the host
    club itself had surplus *hosting* coverage in this age group."""
    for team in donor.teams:
        key = planner._team_key(team)
        current = planner._tournament_participations.get(key, 0)
        target = planner._team_target_tournament_count(team, period)
        if current - 1 < target:
            return True
    return False


def _already_used_team_keys(planner, on_date: date, age_group: str, plan) -> Set[str]:
    used: Set[str] = set()
    for t in plan.tournaments:
        if t.cancelled or t.date != on_date or t.age_group != age_group:
            continue
        used.update(planner._team_key(team) for team in t.teams)
    return used
