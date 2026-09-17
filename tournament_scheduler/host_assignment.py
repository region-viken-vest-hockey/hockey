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

from tournament_scheduler.calendar_availability import (
    CalendarAvailability,
    classify_club_event,
)
from tournament_scheduler.club_distances import furthest_traveling_team
from tournament_scheduler.hosting_coverage import (
    hosting_targets_with_coverage_floor as _hosting_targets_with_coverage_floor,
    proportional_integer_targets as _shared_proportional_integer_targets,
)
from tournament_scheduler.models import Game, Team, Tournament
from tournament_scheduler.occupancy import required_ice_minutes
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


def slot_search_host_order(
    planner,
    host_club: str,
    age_group: str,
    candidate_hosts: Optional[Sequence[str]] = None,
) -> List[str]:
    """Return the ordered hosts a slot search would actually try.

    This is the single authoritative host-search order used by both the
    slot search itself and by placement evidence, so a report can state
    exactly which hosts were searched rather than listing candidates that
    were merely known to exist.

    A joint-club team (e.g. "Jar/Jutul") has no single physical arena of
    its own — each constituent club's arena is tried instead. issue #274:
    if an explicit shared-host decision (LLM/controller judgement, never
    calendar convenience) has already chosen a constituent for this
    (registration, age_group), search only that constituent -- automatic
    placement failure must fall through to manual placement for the chosen
    club, not silently try the other constituent. Otherwise, deterministic
    order across all constituents, before falling through to the general
    candidate list. The per-candidate slot search below already picks the
    first one with a free slot, so this reuses that mechanism rather than
    adding a separate "most available" comparison.
    """
    if "/" in host_club:
        decisions = getattr(planner, "shared_host_decisions", None) or {}
        chosen = decisions.get((host_club, age_group))
        if chosen:
            search_hosts = [chosen]
        else:
            search_hosts = sorted(part.strip() for part in host_club.split("/") if part.strip())
    else:
        search_hosts = [host_club]
    if candidate_hosts:
        for candidate in candidate_hosts:
            if candidate not in search_hosts:
                search_hosts.append(candidate)
    return search_hosts


def _is_movable_event(club: str, event: Any) -> bool:
    """True when *event* is a host-controlled ``movable_busy`` interval."""
    availability, _ = classify_club_event(club, getattr(event, "name", ""))
    return availability == CalendarAvailability.MOVABLE_BUSY


def _movable_slot_evidence(
    club: str,
    check_date: date,
    start_time: str,
    required_minutes: int,
    events: List[Any],
) -> Dict[str, Any]:
    """Describe the movable event a candidate slot would displace.

    Returns an empty dict when no movable event overlaps the slot -- callers
    must not mark a placement as requiring confirmation without concrete
    evidence of which host-controlled interval it uses.
    """
    from tournament_scheduler.utils.slot_finder import _event_busy_range_on_date

    try:
        start_hour, start_minute = (int(part) for part in str(start_time).split(":", 1))
    except (AttributeError, ValueError):
        return {}
    start_minutes = start_hour * 60 + start_minute
    end_minutes = start_minutes + required_minutes
    for event in events:
        if not _is_movable_event(club, event):
            continue
        busy_range = _event_busy_range_on_date(event, check_date)
        if busy_range is None:
            continue
        if start_minutes < busy_range[1] and busy_range[0] < end_minutes:
            availability, reason = classify_club_event(club, getattr(event, "name", ""))
            return {
                "availability": availability.value,
                "calendar_event": getattr(event, "name", ""),
                "reason": reason
                or "host-controlled interval may be moved or replaced for an RVV tournament",
                "requires_host_confirmation": True,
            }
    return {}


def find_slot_for_tournament(
    planner,
    tournament_date: date,
    host_club: str,
    age_group: str,
    games: List[Game],
    preferred_start: Optional[str] = None,
    candidate_hosts: Optional[Sequence[str]] = None,
    reserved_events_by_club: Optional[Dict[str, List]] = None,
    placement_evidence: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[str, str, str]]:
    """Find a time-of-day slot for the tournament, preferring the assigned host.

    The *games* list may be generated from any participant order; this helper
    only uses it to infer the hall occupancy duration and the participant set
    for travel-aware preferred-start heuristics.

    When no unconditionally free slot exists for a searched host,
    this retries once with that host's host-controlled ``movable_busy`` events
    (e.g. Kongsberg open ice) removed from the busy set -- moving/replacing
    such an event is the host's decision and is a legitimate placement
    candidate for its own tournament. A slot found that way is recorded in
    *placement_evidence* with ``requires_host_confirmation: True`` so the
    caller/audit can tell it apart from verified free ice; it is never
    silently treated as unconditionally free.
    """
    if not planner.events_by_club and not reserved_events_by_club:
        return None

    ice_time = getattr(planner, "ice_time_for_age_group", {}).get(age_group) or getattr(planner, "round_length_for_age_group", {}).get(age_group)
    if not ice_time or not games:
        return None

    max_round = max(g.round_number for g in games)
    required_minutes = required_ice_minutes(ice_time, max_round)
    if required_minutes <= 0:
        return None

    search_hosts = slot_search_host_order(planner, host_club, age_group, candidate_hosts)

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

        # No unconditionally free slot for this host. If the host
        # controls a movable interval it may displace (open ice), retry with
        # those events removed. Only the scraped movable events are dropped --
        # in-plan reservations for already-placed tournaments stay busy.
        scraped_events = list(planner.events_by_club.get(candidate_host, []))
        movable_scraped = [event for event in scraped_events if _is_movable_event(candidate_host, event)]
        if movable_scraped:
            movable_ids = {id(event) for event in movable_scraped}
            movable_events_by_club = {
                club: list(events) for club, events in events_by_club.items()
            }
            movable_events_by_club[candidate_host] = [
                event
                for event in movable_events_by_club.get(candidate_host, [])
                if id(event) not in movable_ids
            ]
            movable_slot = planner.scheduler.find_arena_slot_for_date(
                tournament_date,
                candidate_host,
                required_minutes,
                movable_events_by_club,
                **slot_kwargs,
            )
            if movable_slot is not None:
                if placement_evidence is not None:
                    placement_evidence.update(
                        _movable_slot_evidence(
                            candidate_host,
                            tournament_date,
                            movable_slot[1],
                            required_minutes,
                            scraped_events,
                        )
                    )
                return movable_slot

    return None
