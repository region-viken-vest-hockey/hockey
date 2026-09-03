"""Host-assignment helpers for `SeasonPlanner`."""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from tournament_scheduler.club_distances import furthest_traveling_team
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
    """Return integer host targets for one age group."""
    teams = planner.roster.by_age_group(age_group)
    club_team_counts: Dict[str, int] = {}
    for team in teams:
        club_team_counts[team.club] = club_team_counts.get(team.club, 0) + 1
    return proportional_integer_targets(club_team_counts, tournament_count)


def proportional_integer_targets(weights: Dict[str, int], total: int) -> Dict[str, int]:
    """Round weighted quotas to integers that sum to `total`."""
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

        slot = planner.scheduler.find_arena_slot_for_date(
            tournament_date,
            candidate_host,
            required_minutes,
            events_by_club,
            preferred_start=candidate_preferred_start,
        )
        if slot is not None:
            return slot

    return None
