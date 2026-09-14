"""Planner-mutating primitives for `hosting_cross_age_repair_apply.py` (issue #328).

Split out purely to keep `hosting_cross_age_repair_apply.py` within the
repository's file-length guideline; these are still an implementation detail
of that module's `_try_repair`, not a standalone public API.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from tournament_scheduler.host_representation import host_eligible_teams, host_represented_in
from tournament_scheduler.models import Tournament
from tournament_scheduler.participant_selection import deficit_score
from tournament_scheduler.warnings import _club_calendar_available


def participants_with_host_represented(planner, participants, age_group, period, home_club, already_used):
    """Read-only variant of `host_representation_repair.repair_host_representation`.

    Must not mutate planner counters -- unlike that function, a rejected
    repair candidate here must leave no trace, so the swap is only computed
    (not committed) until the whole candidate has passed every other check.
    Returns `None` when *home_club* cannot be represented at all (no
    eligible/available team), rather than silently returning *participants*
    unchanged -- callers need to distinguish "already represented"/"now
    represented" from "impossible" here.
    """
    if host_represented_in(participants, home_club):
        return participants
    eligible_home_teams = host_eligible_teams(planner.roster.by_age_group(age_group), home_club, age_group)
    if not eligible_home_teams:
        return None

    current_keys = {planner._team_key(t) for t in participants}
    available_home_teams = [
        t
        for t in eligible_home_teams
        if planner._team_key(t) not in already_used
        and planner._team_key(t) not in current_keys
        and not planner._team_at_target(t, period)
    ]
    if not available_home_teams:
        return None

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
    return [t for t in participants if t is not team_to_remove] + [home_team]


def build_tournament(planner, tournament_date, host_club, age_group, participants, games, start_time) -> Tournament:
    arena = planner.club_arenas.get(host_club, host_club)
    if planner.club_calendar_status:
        constituents = [part.strip() for part in host_club.split("/") if part.strip()] or [host_club]
        calendar_verified = any(planner.club_calendar_status.get(part) == "known" for part in constituents)
    else:
        calendar_verified = _club_calendar_available(host_club, planner.available_calendar_clubs)
    manual_booking_reason = (
        None
        if calendar_verified
        else f"Kalender utilgjengelig for {host_club} — istid må bookes/verifiseres manuelt."
    )
    ag_weight = planner.preferanse_vekt_by_age_group.get(age_group, 0.0)
    date_pref_total = sum(p.vekt for p in planner.date_preferences if p.fra <= tournament_date <= p.til)
    return Tournament(
        date=tournament_date,
        arena=arena,
        age_group=age_group,
        teams=participants,
        games=games,
        host_club=host_club,
        start_time=start_time,
        preferanse_vekt=ag_weight,
        scoring_weight_term=ag_weight + date_pref_total,
        manual_booking_reason=manual_booking_reason,
    )


def remove_tournament_bookkeeping(planner, tournament: Tournament, period: Optional[str]) -> None:
    """Inverse of `SeasonPlanner._record_grouping`/`_record_opponent_history`
    for a whole tournament being removed.

    Deliberately leaves `_grouped_with` untouched: it is a cumulative "have
    these two teams ever shared a tournament this season" signal used only to
    softly discourage repeated pairings in *later* selection, and two teams
    may still share another still-existing tournament this season -- erring
    towards over-caution there (a stale "grouped" flag) never violates a hard
    constraint, unlike incorrectly clearing a still-valid one would.
    """
    games_removed = max(0, len(tournament.teams) - 1)
    half_participations = (
        planner._tournament_participations_by_half.get(period)
        if period in ("before_christmas", "after_christmas")
        else None
    )
    for team in tournament.teams:
        key = planner._team_key(team)
        planner._invite_counts[key] = max(0, planner._invite_counts.get(key, 0) - 1)
        planner._tournament_participations[key] = max(0, planner._tournament_participations.get(key, 0) - 1)
        if half_participations is not None:
            half_participations[key] = max(0, half_participations.get(key, 0) - 1)
        planner._running_game_counts[key] = max(0, planner._running_game_counts.get(key, 0) - games_removed)
    for game in tournament.games:
        if game.home is None or game.away is None:
            continue
        pair = frozenset((planner._team_key(game.home), planner._team_key(game.away)))
        if pair in planner._opponent_history:
            planner._opponent_history[pair] = max(0, planner._opponent_history[pair] - 1)


def move_hosting_day(planner, plan, donor: Tournament, new_tournament: Tournament) -> None:
    donor_month_key = (donor.date.year, donor.date.month)
    still_hosts_that_day = any(
        t is not donor and not t.cancelled and t.host_club == donor.host_club and t.date == donor.date
        for t in plan.tournaments
    )
    if not still_hosts_that_day:
        days = planner._hosting_days_by_club_month.get((donor.host_club, donor_month_key))
        if days is not None:
            days.discard(donor.date)

    new_month_key = (new_tournament.date.year, new_tournament.date.month)
    planner._hosting_days_by_club_month.setdefault((new_tournament.host_club, new_month_key), set()).add(
        new_tournament.date
    )
