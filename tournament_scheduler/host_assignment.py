"""Host-assignment helpers for `SeasonPlanner`.

issue #262 P1: this module is the *legacy* `SeasonPlanner` baseline/fallback
generator's heuristic policy (completion-ratio/recency/holiday/streak host
ranking in `assign_hosts`'s `_score`, and the travel-to-preferred-start-time
bucketing in `find_slot_for_tournament`). It must not be imported by
`stage3_optimizer.py` or any other canonical LLM-directed decision path —
those paths use deterministic facts only (e.g. `problem["clubs"]`,
`problem["club_calendar_status"]`, arena/date/time interval checks), never
this module's heuristic ranking. `tests/test_architecture_boundaries.py`
enforces this.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tournament_scheduler.club_distances import furthest_traveling_team
from tournament_scheduler.hosting_coverage import (
    hosting_targets_with_coverage_floor as _hosting_targets_with_coverage_floor,
    proportional_integer_targets as _shared_proportional_integer_targets,
)
from tournament_scheduler.models import Game, Team, Tournament
from tournament_scheduler.utils.slot_finder import matchday_duration_minutes
from tournament_scheduler.warnings import holiday_heavy_weekend_dates


def pick_spread_dates(
    planner,
    free_dates: Sequence[date],
    window_start: date,
    window_end: date,
    age_groups: Sequence[str] = (),
    scheduled_age_groups_by_date: Optional[Dict[date, List[str]]] = None,
    target_count: Optional[int] = None,
) -> List[date]:
    """Compatibility wrapper that delegates to participant selection."""
    from tournament_scheduler.participant_selection import pick_spread_dates as _pick_spread_dates

    return _pick_spread_dates(
        planner,
        free_dates,
        window_start,
        window_end,
        age_groups=age_groups,
        scheduled_age_groups_by_date=scheduled_age_groups_by_date,
        target_count=target_count,
    )


def default_target_count(num_free_dates: int) -> int:
    """Heuristic when no age-group-specific target is available."""
    return max(1, num_free_dates)


def assign_hosts(planner, scheduled: Sequence[Tuple[date, str]]) -> List[str]:
    """Assign a host club to each scheduled `(date, age_group)`.

    The assignment follows the integer per-age-group hosting targets first,
    then uses weekend/holiday load and recency to spread those assignments in
    time. A club whose rounded target is zero must not take a tournament while
    another club is still below its proportional target. Same arena on the
    same day is allowed as long as the planner can sequence the tournaments
    without an actual overlap.
    """
    if not scheduled:
        planner._arena_day_collisions = []
        return []

    age_totals: Dict[str, int] = {}
    for _, age_group in scheduled:
        age_totals[age_group] = age_totals.get(age_group, 0) + 1

    targets_by_age = {
        age_group: hosting_targets_for_age_group(planner, age_group, count)
        for age_group, count in age_totals.items()
    }
    actual_by_age: Dict[str, Dict[str, int]] = {
        age_group: {club: 0 for club in targets}
        for age_group, targets in targets_by_age.items()
    }

    clubs_by_age: Dict[str, List[str]] = {}
    for team in planner.roster.teams:
        clubs = clubs_by_age.setdefault(team.age_group, [])
        if team.club not in clubs:
            clubs.append(team.club)

    assignments: List[str] = []
    all_clubs = planner.roster.clubs()
    last_hosted_date_by_club: Dict[str, date] = {}
    last_hosted_date_by_club_age: Dict[Tuple[str, str], date] = {}
    consecutive_streak_by_club_age: Dict[Tuple[str, str], int] = {}
    holiday_heavy_host_count_by_club: Dict[str, int] = {}
    first_date = min(tournament_date for tournament_date, _ in scheduled)
    last_date = max(tournament_date for tournament_date, _ in scheduled)
    holiday_heavy_dates = holiday_heavy_weekend_dates(first_date, last_date)

    for tournament_date, age_group in scheduled:
        targets = targets_by_age.get(age_group, {})
        base_candidate_pool = (
            list(targets)
            if targets
            else list(clubs_by_age.get(age_group, [])) or list(all_clubs)
        )
        if not base_candidate_pool:
            assignments.append("")
            continue

        # A club with no trustworthy calendar evidence this run still
        # competes normally for hosting duty here -- excluding it would
        # just shift its share onto other clubs (see scheduler.py's
        # find_arena_slot_for_date, which returns a provisional slot for
        # such a club instead of a real one, and season_planner.py's
        # manual_booking_reason flagging for the resulting tournament).

        actual_counts = actual_by_age.get(age_group, {})
        if targets:
            under_target = [
                club
                for club in base_candidate_pool
                if actual_counts.get(club, 0) < targets.get(club, 0)
            ]
            # Integer targets sum to the number of scheduled tournaments, so
            # this should normally be non-empty until the final assignment.
            # The fallback keeps the helper robust for synthetic/external use.
            candidate_pool = under_target or base_candidate_pool
        else:
            candidate_pool = base_candidate_pool

        candidate_order = {club: idx for idx, club in enumerate(base_candidate_pool)}
        is_holiday_heavy = tournament_date in holiday_heavy_dates

        def _projected_streak(club: str) -> int:
            key = (club, age_group)
            previous_date = last_hosted_date_by_club_age.get(key)
            if previous_date is not None and (tournament_date - previous_date).days == 7:
                return consecutive_streak_by_club_age.get(key, 1) + 1
            return 1

        def _score(club: str) -> Tuple[float, int, int, int, int, int, int]:
            previous_date = last_hosted_date_by_club.get(club)
            gap = (tournament_date - previous_date).days if previous_date is not None else 10_000
            target = max(0, targets.get(club, 0))
            actual = actual_counts.get(club, 0)
            completion_ratio = actual / target if target > 0 else float("inf")
            remaining = max(0, target - actual)
            return (
                completion_ratio,
                _projected_streak(club),
                holiday_heavy_host_count_by_club.get(club, 0) + (1 if is_holiday_heavy else 0),
                -gap,
                -remaining,
                actual,
                candidate_order.get(club, 0),
            )

        host = min(candidate_pool, key=_score)
        assignments.append(host)
        actual_counts[host] = actual_counts.get(host, 0) + 1
        age_key = (host, age_group)
        previous_age_date = last_hosted_date_by_club_age.get(age_key)
        if previous_age_date is not None and (tournament_date - previous_age_date).days == 7:
            consecutive_streak_by_club_age[age_key] = consecutive_streak_by_club_age.get(age_key, 1) + 1
        else:
            consecutive_streak_by_club_age[age_key] = 1
        last_hosted_date_by_club_age[age_key] = tournament_date
        last_hosted_date_by_club[host] = tournament_date
        if is_holiday_heavy:
            holiday_heavy_host_count_by_club[host] = holiday_heavy_host_count_by_club.get(host, 0) + 1

    planner._arena_day_collisions = []
    return assignments


def hosting_targets_for_age_group(planner, age_group: str, tournament_count: int) -> Dict[str, int]:
    """Return integer host targets for one age group.

    issue #266: guarantees every club with a team in this age group a
    coverage-floor target of at least 1 whenever the age group has at least
    as many scheduled tournaments as clubs needing coverage (see
    `hosting_coverage.hosting_targets_with_coverage_floor`). Callers that
    also need the structural-shortfall list (fewer tournaments than clubs)
    should call `hosting_coverage_unmet_for_age_group` instead of/alongside
    this.
    """
    club_team_counts = _club_team_counts_for_age_group(planner, age_group)
    targets, _unmet = _hosting_targets_with_coverage_floor(club_team_counts, tournament_count)
    return targets


def hosting_coverage_unmet_for_age_group(planner, age_group: str, tournament_count: int) -> List[str]:
    """Clubs with a team in *age_group* that cannot get a coverage-floor
    target of 1 because there are fewer scheduled tournaments than clubs
    needing coverage this age group this season (issue #266) -- a
    structural shortfall, not something a slot search could fix.
    """
    club_team_counts = _club_team_counts_for_age_group(planner, age_group)
    _targets, unmet = _hosting_targets_with_coverage_floor(club_team_counts, tournament_count)
    return unmet


def _club_team_counts_for_age_group(planner, age_group: str) -> Dict[str, int]:
    teams = planner.roster.by_age_group(age_group)
    club_team_counts: Dict[str, int] = {}
    for team in teams:
        club_team_counts[team.club] = club_team_counts.get(team.club, 0) + 1
    return club_team_counts


def proportional_integer_targets(weights: Dict[str, int], total: int) -> Dict[str, int]:
    """Round weighted quotas to integers that sum to `total`.

    Delegates to `hosting_coverage.proportional_integer_targets` -- the
    canonical (non-legacy) path needs the same largest-remainder rounding
    for its own proportional-burden metrics (issue #266) but cannot import
    this module (`tests/test_architecture_boundaries.py`), so the math
    lives there and this is a thin compatibility wrapper.
    """
    return _shared_proportional_integer_targets(weights, total)


def find_slot_for_tournament(
    planner,
    tournament_date: date,
    host_club: str,
    age_group: str,
    games: List[Game],
    preferred_start: Optional[str] = None,
    candidate_hosts: Optional[Sequence[str]] = None,
    reserved_events_by_club: Optional[Dict[str, List]] = None,
) -> Optional[Tuple[str, str, str]]:
    """Find a time-of-day slot for the tournament, preferring the assigned host.

    The *games* list may be generated from any participant order; this helper
    only uses it to infer the hall occupancy duration and the participant set
    for travel-aware preferred-start heuristics.
    """
    if not planner.events_by_club and not reserved_events_by_club:
        return None

    round_length = planner.round_length_for_age_group.get(age_group)
    if not round_length:
        return None

    if not games:
        return None

    max_round = max(g.round_number for g in games)
    required_minutes = matchday_duration_minutes(round_length, max_round)
    if required_minutes <= 0:
        return None

    # A joint-club team (e.g. "Jar/Jutul") has no single physical arena of
    # its own — try each constituent club's arena instead, in a
    # deterministic order, before falling through to the general candidate
    # list. The per-candidate slot search below already picks the first one
    # with a free slot, so this reuses that mechanism rather than adding a
    # separate "most available" comparison.
    if "/" in host_club:
        search_hosts = sorted(part.strip() for part in host_club.split("/") if part.strip())
    else:
        search_hosts = [host_club]
    if candidate_hosts:
        for candidate in candidate_hosts:
            if candidate not in search_hosts:
                search_hosts.append(candidate)

    for candidate_host in search_hosts:
        candidate_preferred_start = preferred_start
        if candidate_preferred_start is None:
            unique_teams: list[Team] = []
            seen_labels: set[str] = set()
            for game in games:
                for team in (game.home, game.away):
                    if team.label in seen_labels:
                        continue
                    seen_labels.add(team.label)
                    unique_teams.append(team)

            tournament = Tournament(
                date=tournament_date,
                arena=planner.club_arenas.get(candidate_host, candidate_host),
                age_group=age_group,
                teams=unique_teams,
                host_club=candidate_host,
            )
            travel = furthest_traveling_team(tournament)
            if travel is None:
                candidate_preferred_start = "11:00"
            else:
                _, km = travel
                if km >= 120:
                    candidate_preferred_start = "12:00"
                elif km >= 60:
                    candidate_preferred_start = "11:30"
                else:
                    candidate_preferred_start = "11:00"

        events_by_club = {club: list(events) for club, events in planner.events_by_club.items()}
        if reserved_events_by_club:
            for club, events in reserved_events_by_club.items():
                events_by_club.setdefault(club, []).extend(events)

        slot_kwargs: Dict[str, Any] = {"preferred_start": candidate_preferred_start}
        planner_club_calendar_status = getattr(planner, "club_calendar_status", None)
        if planner_club_calendar_status:
            # Only pass this through when the planner actually carries
            # calendar-status data (issue #262 P0) -- keeps test doubles and
            # other callers of `find_arena_slot_for_date` that predate this
            # parameter working unchanged.
            slot_kwargs["club_calendar_status"] = planner_club_calendar_status
        slot = planner.scheduler.find_arena_slot_for_date(
            tournament_date,
            candidate_host,
            required_minutes,
            events_by_club,
            **slot_kwargs,
        )
        if slot is not None:
            return slot

    return None
