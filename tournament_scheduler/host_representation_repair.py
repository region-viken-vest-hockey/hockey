"""Deterministic host-representation repair for `SeasonPlanner` (issue #322).

Split out of `season_planner.py` to keep that file within the repository's
file-length guideline; this module still operates directly on planner
internals (participation/game-count counters, roster, fairness deficit
scoring), so -- unlike `hosting_coverage.py`/`host_representation.py` -- it
is not a pure, planner-independent module.

issue #323 P0: as of the participant-derived host/arena search in
`season_planner.py`, this module is no longer called from the baseline
build loop -- a tournament's host is now always chosen from clubs already
represented by its selected participants, so there is nothing left to
repair in the normal path. It is retained as a tested, available
primitive (e.g. for future manual-resolution tooling), not as part of the
automatic placement flow.
"""

from __future__ import annotations

from typing import List, Optional, Set

from tournament_scheduler.host_representation import (
    clubs_represent_same_club,
    host_eligible_teams,
    host_represented_in,
)
from tournament_scheduler.models import Team
from tournament_scheduler.participant_selection import deficit_score


def repair_host_representation(
    planner,
    participants: List[Team],
    *,
    age_group: str,
    period: Optional[str],
    home_club: str,
    already_used_today: Optional[Set[str]],
) -> List[Team]:
    """Ensure *home_club* is represented in *participants* when it has an
    eligible registered team in *age_group* (issue #322's hard
    host-representation invariant).

    issue #323 P0: no longer called from `season_planner.py`'s baseline
    build loop -- retained as a tested, available primitive only (see
    module docstring). Repairs the participant set deterministically
    without changing its size -- replacing the current participant least in
    need of another tournament (lowest fairness deficit) with the eligible
    home team most in need of one -- and keeps the participation/game-count
    counters `SeasonPlanner._record_grouping` already recorded for this slot
    in sync with the swap.

    Returns *participants* unchanged when home_club is already represented,
    when home_club has no eligible registered team in this age group at all
    (the invariant does not apply), when every eligible home team is
    already committed to another same-date tournament of this age group
    (repair would violate the hard same-date-uniqueness rule), or when
    every eligible home team is already at its participation target
    (issue #323: swapping one in would over-shoot the team's strong
    participation goal, which this deterministic repair primitive
    deliberately avoids; the target is a strong goal, not a hard rule, so a
    controller may still choose a bounded over-target repair elsewhere). The
    independent verifier's `host_team_missing` check is the safety net for
    any case left unrepaired.
    """
    if host_represented_in(participants, home_club):
        return participants

    eligible_home_teams = host_eligible_teams(planner.roster.by_age_group(age_group), home_club, age_group)
    if not eligible_home_teams:
        return participants

    already_used = already_used_today or set()
    current_keys = {planner._team_key(t) for t in participants}
    available_home_teams = [
        t
        for t in eligible_home_teams
        if planner._team_key(t) not in already_used
        and planner._team_key(t) not in current_keys
        and not planner._team_at_target(t, period)
    ]
    if not available_home_teams:
        return participants

    home_team = min(
        available_home_teams,
        key=lambda t: (-deficit_score(planner, t, age_group, period), planner.roster.teams.index(t)),
    )

    if not participants:
        return [home_team]

    team_to_remove = min(
        participants,
        key=lambda t: (deficit_score(planner, t, age_group, period), participants.index(t)),
    )
    repaired = [t for t in participants if t is not team_to_remove]
    repaired.append(home_team)
    _resync_counters(planner, participants, team_to_remove, home_team, period)
    _resync_grouping(planner, repaired, team_to_remove, home_team)
    return repaired


def repair_and_finalize(
    planner,
    participants: List[Team],
    *,
    age_group: str,
    period: Optional[str],
    home_club: str,
    tournament_date,
    already_used_today: Optional[Set[str]],
    teams_used_today_by_age_group: dict,
) -> List[Team]:
    """`repair_host_representation`, keeping the caller's per-(date,
    age_group) "already used today" tracking set in sync with any swap, and
    placing any home-club participant(s) first (host-plays-at-home
    convention)."""
    pre_repair_keys = {planner._team_key(t) for t in participants}
    repaired = repair_host_representation(
        planner,
        participants,
        age_group=age_group,
        period=period,
        home_club=home_club,
        already_used_today=already_used_today,
    )
    changed_keys = pre_repair_keys ^ {planner._team_key(t) for t in repaired}
    if changed_keys:
        key = (tournament_date, age_group)
        teams_used_today_by_age_group.setdefault(key, set()).symmetric_difference_update(changed_keys)
    host_teams = [t for t in repaired if clubs_represent_same_club(t.club, home_club)]
    if not host_teams:
        return repaired
    return host_teams + [t for t in repaired if t not in host_teams]


def _resync_counters(planner, participants, team_to_remove, home_team, period) -> None:
    games_added = max(0, len(participants) - 1)
    key_removed = planner._team_key(team_to_remove)
    key_added = planner._team_key(home_team)
    half_participations = (
        planner._tournament_participations_by_half.get(period)
        if period in ("before_christmas", "after_christmas")
        else None
    )
    planner._invite_counts[key_removed] = max(0, planner._invite_counts.get(key_removed, 0) - 1)
    planner._invite_counts[key_added] = planner._invite_counts.get(key_added, 0) + 1
    planner._tournament_participations[key_removed] = max(
        0, planner._tournament_participations.get(key_removed, 0) - 1
    )
    planner._tournament_participations[key_added] = planner._tournament_participations.get(key_added, 0) + 1
    if half_participations is not None:
        half_participations[key_removed] = max(0, half_participations.get(key_removed, 0) - 1)
        half_participations[key_added] = half_participations.get(key_added, 0) + 1
    planner._running_game_counts[key_removed] = max(
        0, planner._running_game_counts.get(key_removed, 0) - games_added
    )
    planner._running_game_counts[key_added] = planner._running_game_counts.get(key_added, 0) + games_added


def _resync_grouping(planner, repaired, team_to_remove, home_team) -> None:
    key_removed = planner._team_key(team_to_remove)
    key_added = planner._team_key(home_team)
    remaining_keys = [planner._team_key(t) for t in repaired]
    removed_grouped = planner._grouped_with.get(key_removed)
    if removed_grouped is not None:
        removed_grouped.discard(key_added)
    for other_key in remaining_keys:
        if other_key == key_removed:
            continue
        other_grouped = planner._grouped_with.get(other_key)
        if other_grouped is not None:
            other_grouped.discard(key_removed)
    added_grouped = planner._grouped_with.setdefault(key_added, set())
    added_grouped.update(k for k in remaining_keys if k != key_added)
    for other_key in remaining_keys:
        if other_key == key_added:
            continue
        planner._grouped_with.setdefault(other_key, set()).add(key_added)
