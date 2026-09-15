"""Participant-selection helpers for `SeasonPlanner`.

issue #262 P1: this module is the *legacy* `SeasonPlanner` baseline/fallback
generator's heuristic policy (fixed weight coefficients for deficit, repeat
matchups, previous grouping, invite counts, club diversity — see
`participant_selection_score`). It must not be imported by
`stage3_optimizer.py` or any other canonical LLM-directed decision path;
those paths get their own explicit, overridable weights (see
`stage3_optimizer.DEFAULT_WEIGHTS`) rather than depending on this module's
baked-in constants. `tests/test_architecture_boundaries.py` enforces this.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Set

from tournament_scheduler.host_representation import clubs_represent_same_club
from tournament_scheduler.models import Team, overlapping_age_groups
from tournament_scheduler.planning_contract import HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT
from tournament_scheduler.participant_relocation import MIN_TEAMS_PER_TOURNAMENT as MIN_TEAMS_PER_TOURNAMENT, relocate_structurally_impossible_slots as relocate_structurally_impossible_slots
from tournament_scheduler.participant_roster_sizing import (
    _participation_demand as _participation_demand,
    club_demand_shares as club_demand_shares,
    club_share_deficit as club_share_deficit,
    fixed_cohort_participants as fixed_cohort_participants,
    fixed_cohort_shape_for as fixed_cohort_shape_for,
    plan_roster_sizes as plan_roster_sizes,
    plan_roster_sizes_for_age_group as plan_roster_sizes_for_age_group,
    rebalance_roster_sizes_across_dates as rebalance_roster_sizes_across_dates,
    target_tournaments_for_age_group as target_tournaments_for_age_group,
)

# issue #327: a club materially behind its proportional demand share (see
# `club_share_deficit`) needs at least this many slots of headroom before a
# 3rd-or-later same-club candidate is allowed to compete ahead of the
# hard-cap-only fallback tier -- a fractional/noise-level deficit must not
# relax the #324 <=2 preference.
CLUB_SHARE_DEFICIT_THRESHOLD = 1.0


def default_target_count(num_free_dates: int) -> int:
    """Heuristic when no explicit target count is available."""
    return max(1, num_free_dates)


def pick_spread_dates(
    planner,
    free_dates: Sequence[date],
    window_start: date,
    window_end: date,
    age_groups: Sequence[str] = (),
    scheduled_age_groups_by_date: Optional[Dict[date, List[str]]] = None,
    target_count: Optional[int] = None,
) -> List[date]:
    """Pick free dates for one age group, spread across the season window."""
    if not free_dates:
        return []

    scheduled_age_groups_by_date = scheduled_age_groups_by_date or {}

    # issue #316: the requested volume is authoritative (derived upstream from
    # team_count * participation_target / tournament_capacity) and must not be
    # capped to the number of distinct free dates -- when it exceeds that, the
    # date skeleton needs repeated (date, age_group) entries (parallel pools),
    # not a silently shrunk target.
    target_count = max(1, target_count or default_target_count(len(free_dates)))

    if target_count == 1:
        return list(free_dates[:target_count])

    total_days = (window_end - window_start).days

    expected_per_month = planner._expected_monthly_load(window_start, window_end, target_count)

    bucket_span = total_days / target_count
    chosen: List[date] = []
    used: Set[date] = set()

    ag_index = 0
    scheduled_by_date: Dict[date, List[str]] = {}

    for i in range(target_count):
        bucket_start = window_start + timedelta(days=int(i * bucket_span))
        bucket_end = window_start + timedelta(days=int((i + 1) * bucket_span))
        bucket_center = bucket_start + (bucket_end - bucket_start) / 2
        half_span_days = max(1.0, (bucket_end - bucket_start).days / 2)

        candidates = [d for d in free_dates if bucket_start <= d <= bucket_end and d not in used]
        if not candidates:
            candidates = [d for d in free_dates if d not in used]
        if not candidates:
            # Every free date has already been used at least once for this
            # age group: the requested volume needs parallel pools on a date
            # that's already scheduled (issue #316), not a skipped slot.
            candidates = [d for d in free_dates if bucket_start <= d <= bucket_end]
            if not candidates:
                candidates = list(free_dates)
        if not candidates:
            continue

        if age_groups:
            predicted_age_group = planner._next_age_group(
                age_groups, ag_index, bucket_center, scheduled_by_date
            )
            predicted_participants = planner._select_participants(predicted_age_group)

            def combined_score(d: date) -> float:
                spread_penalty = abs((d - bucket_center).days) / half_span_days
                diversity_penalty = planner._score_candidate_date(
                    d, predicted_age_group, predicted_participants, expected_per_month,
                    tournament_weight=planner.preferanse_vekt_by_age_group.get(predicted_age_group, 0.0),
                )
                same_day_penalty = len(scheduled_age_groups_by_date.get(d, [])) * 50.0
                overlap_penalty = 0.0
                for existing in scheduled_age_groups_by_date.get(d, []):
                    if (
                        predicted_age_group in overlapping_age_groups(existing)
                        or existing in overlapping_age_groups(predicted_age_group)
                    ):
                        overlap_penalty += 100.0
                return spread_penalty + diversity_penalty + same_day_penalty + overlap_penalty

            best = min(candidates, key=combined_score)

            ag_index = (age_groups.index(predicted_age_group) + 1) % len(age_groups)
            scheduled_by_date.setdefault(best, []).append(predicted_age_group)
        else:
            best = min(candidates, key=lambda d: abs((d - bucket_center).days))

        chosen.append(best)
        used.add(best)

    return sorted(chosen)


def next_age_group(
    planner,
    age_groups: Sequence[str],
    start_index: int,
    tournament_date: date,
    scheduled_by_date: Dict[date, List[str]],
) -> str:
    """Pick the next age group to schedule, round-robin from `start_index`."""
    already_on_date = scheduled_by_date.get(tournament_date, [])

    for offset in range(len(age_groups)):
        candidate = age_groups[(start_index + offset) % len(age_groups)]
        overlaps_existing = any(
            candidate in overlapping_age_groups(existing) or existing in overlapping_age_groups(candidate)
            for existing in already_on_date
        )
        if not overlaps_existing:
            return candidate

    return age_groups[start_index % len(age_groups)]


def select_participants(
    planner,
    age_group: str,
    period: Optional[str] = None,
    *,
    exclude_team_keys: Optional[set] = None,
    planned_roster_size: Optional[int] = None,
    hosting_priority_clubs: Optional[Set[str]] = None,
) -> List[Team]:
    """Select the teams to invite to a tournament for the given age group.

    issue #297: `period` (``"before_christmas"``/``"after_christmas"``) makes
    eligibility and deficit ranking half-aware. Without it, a team that used
    up its season-wide target during the front-loaded before-Christmas half
    gets excluded from every after-Christmas candidate pool too, starving
    the second half of eligible participants even when the date skeleton
    itself was built with a balanced half split.

    `exclude_team_keys` removes teams already invited to another tournament
    of this same age group on this same calendar date -- eligibility here is
    otherwise governed only by season/half participation targets, which does
    not prevent the same under-target team being picked twice when the date
    skeleton schedules two same-age-group tournaments (parallel pools/venues)
    on one date. Without this, `verify_candidate`'s hard
    `duplicate_participation_same_date` check can fail on the planner's own
    baseline output.

    issue #316: `planned_roster_size`, when given, caps this slot to the
    balanced size `plan_roster_sizes_for_age_group` assigned it instead of
    always filling up to tournament capacity -- greedily filling every slot
    to capacity can strand too few teams for a later, still-required slot
    even though the total demand was perfectly packable.

    issue #323: `hosting_priority_clubs`, when given, only re-sorts among
    candidates that are already legal (past the `_team_at_target`/
    `exclude_team_keys` hard filters above) -- a candidate whose club
    represents one of these clubs (via `clubs_represent_same_club`) is
    preferred when otherwise competitive, so a legal team from a
    tournament's original host club is more likely to naturally end up
    among the selected participants without ever bypassing a hard
    eligibility check.
    """
    fixed_cohort = fixed_cohort_participants(
        planner,
        age_group,
        period,
        exclude_team_keys=exclude_team_keys,
        planned_roster_size=planned_roster_size,
    )
    if fixed_cohort is not None:
        return fixed_cohort

    candidates = planner.roster.by_age_group(age_group)
    if not candidates:
        return []

    candidates = [t for t in candidates if not planner._team_at_target(t, period)]
    if not candidates:
        return []

    if exclude_team_keys:
        candidates = [t for t in candidates if planner._team_key(t) not in exclude_team_keys]
        if not candidates:
            return []

    max_teams = participant_limit_for(planner, age_group, len(candidates))
    if planned_roster_size is not None:
        max_teams = min(max_teams, planned_roster_size)
    return pick_scored_participants(
        planner, candidates, max_teams, age_group, period, hosting_priority_clubs=hosting_priority_clubs
    )


def cap_per_club_deficit_aware(planner, teams: Sequence[Team], age_group: str) -> List[Team]:
    """Compatibility wrapper for the scored participant selector."""
    return pick_scored_participants(planner, teams, len(teams), age_group)


def participant_limit_for(planner, age_group: str, team_count: int) -> int:
    """Return the max teams that fit a tournament for `team_count` rosters."""
    base_capacity = max_teams_for(planner, age_group)
    return min(base_capacity, team_count)


def max_teams_for(planner, age_group: str) -> int:
    """Return the largest tournament size for `age_group`."""
    return base_team_capacity(planner, age_group)


def max_club_teams_for(planner, age_group: str, club: str) -> int:
    """Return the preferred ceiling on teams from `club` in one `age_group` tournament.

    issue #324: this is a flat preference (`planner.max_club_teams_per_tournament`,
    normally 2) regardless of how many teams `club` has in the age group or how
    skewed the age group's fairness deficits are. A club having more teams
    should change how often those teams participate across the season (the
    deficit-aware selection score below already does that), not how many of
    them get clustered into one tournament.

    The cap is enforced as a strong scoring penalty in
    `participant_selection_score`, not a hard filter -- `pick_scored_participants`
    may still exceed it when no other legal candidate remains to complete the
    roster, and that fallback is counted via `planner._club_cap_overrides`.
    """
    return planner.max_club_teams_per_tournament


def club_count_excess_over_2(teams: Sequence[Team]) -> int:
    """Return `sum(max(0, count(club) - 2))` for `teams` (issue #324 metric)."""
    club_counts: Dict[str, int] = {}
    for team in teams:
        club_counts[team.club] = club_counts.get(team.club, 0) + 1
    return sum(max(0, count - 2) for count in club_counts.values())


def expected_average_for(planner, age_group: str) -> float:
    """Return the current running average game count for `age_group`."""
    teams = planner.roster.by_age_group(age_group)
    if not teams:
        return 0.0
    counts = [planner._running_game_counts.get(planner._team_key(team), 0) for team in teams]
    return sum(counts) / len(counts)


def deficit_score(planner, team: Team, age_group: str, period: Optional[str] = None) -> float:
    """Return how far below the fairness target `team` is."""
    if planner._team_at_target(team, period):
        return -1.0
    age_group_teams = planner.roster.by_age_group(age_group)
    if not age_group_teams:
        return 0.0
    key = planner._team_key(team)
    target = planner.fairness_model.planning_target_games_for_team(
        team,
        age_group_teams,
        planner._running_game_counts,
    )
    return target - planner._running_game_counts.get(key, 0)


def normalized_invite_count(planner, team: Team) -> float:
    """Return `team`'s invite count normalized by club-size-in-age-group."""
    key = planner._team_key(team)
    sibling_count = planner._club_age_group_team_counts.get(key, 1)
    return planner._invite_counts.get(key, 0) / max(1, sibling_count)


def club_diversity_penalty(
    planner,
    selected: Sequence[Team],
    remaining: Sequence[Team],
    team: Team,
) -> int:
    """Return a strong penalty for repeating a club before others are used.

    Clubs that are not yet represented in the current tournament get no
    penalty. Once every remaining club has been represented at least once,
    repeated clubs become feasible again and the penalty drops to a soft
    tie-breaker.
    """
    selected_clubs = {s.club for s in selected}
    if team.club not in selected_clubs:
        return 0

    remaining_new_clubs = {t.club for t in remaining if t.club not in selected_clubs}
    if not remaining_new_clubs:
        return 0

    repeated_count = sum(1 for s in selected if s.club == team.club)
    return 1000 + repeated_count * 250 + len(remaining_new_clubs) * 50


def pick_least_recently_grouped(
    planner,
    candidates: Sequence[Team],
    count: int,
    age_group: str,
) -> List[Team]:
    """Greedily build a subset using the shared participant-selection score."""
    return pick_scored_participants(planner, candidates, count, age_group)


def _within_club_cap(planner, selected: Sequence[Team], age_group: str, team: Team) -> bool:
    """Return whether adding `team` keeps its club at/under its preferred cap."""
    max_club = max_club_teams_for(planner, age_group, team.club)
    if max_club <= 0:
        return True
    club_count = sum(1 for s in selected if s.club == team.club)
    return club_count < max_club


def _within_hard_club_cap(selected: Sequence[Team], team: Team) -> bool:
    """Return whether adding `team` keeps its club at/under the hard maximum.

    issue #326: unlike `_within_club_cap`'s preferred cap, this is never
    relaxed -- `pick_scored_participants` must not select a team that would
    push its club's count in this tournament past
    `HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT`, even as a last-resort fallback.
    """
    club_count = sum(1 for s in selected if s.club == team.club)
    return club_count < HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT


def pick_scored_participants(
    planner,
    candidates: Sequence[Team],
    count: int,
    age_group: str,
    period: Optional[str] = None,
    *,
    hosting_priority_clubs: Optional[Set[str]] = None,
) -> List[Team]:
    """Greedily build a subset by minimizing a single balance score.

    issue #324 (reopened): a 3rd-or-later same-club candidate must not
    compete in the same score pool as candidates that stay within the
    preferred per-club cap -- a large deficit alone must never let it
    outscore an available different/under-cap-club candidate. Each pick is
    therefore restricted to the tier of candidates that stay within
    `max_club_teams_for` whenever that tier is non-empty; the full
    (cap-exceeding) pool is only considered once no legal candidate remains,
    which is exactly the "no other legal candidate can complete the roster"
    fallback `_club_cap_overrides` is meant to track.

    issue #326: that cap-exceeding fallback pool is itself bounded by
    `HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT` -- a 2 -> 3 relaxation is still
    allowed when no under-cap candidate remains, but a team that would push
    its club to a 4th (or later) selection in this tournament is never
    legal, at any tier. If every remaining candidate would breach that hard
    maximum, selection stops early and the roster is left short rather than
    silently exceeding it; callers (roster sizing / relocation / manual
    placement) surface that shortfall the same way they surface any other
    under-filled slot.

    issue #327: a 3rd-or-later same-club candidate is also promoted into
    this same legal tier -- ahead of the "no other legal candidate remains"
    fallback -- when that club is materially behind its proportional demand
    share (`club_share_deficit(...) >= CLUB_SHARE_DEFICIT_THRESHOLD`). A
    large club can otherwise be systematically underrepresented across the
    season even though its registration share says it should receive more
    slots, purely because some different/under-cap-club candidate is always
    technically available. The hard cap and `_club_cap_overrides` bookkeeping
    are unaffected.
    """
    remaining = list(candidates)
    if not remaining or count <= 0:
        return []

    candidate_order = {planner._team_key(team): index for index, team in enumerate(candidates)}
    selected: List[Team] = []

    while remaining and len(selected) < count:
        hard_legal_pool = [team for team in remaining if _within_hard_club_cap(selected, team)]
        if not hard_legal_pool:
            break
        legal_pool = [
            team
            for team in hard_legal_pool
            if _within_club_cap(planner, selected, age_group, team)
            or club_share_deficit(planner, age_group, period, team.club) >= CLUB_SHARE_DEFICIT_THRESHOLD
        ]
        pool = legal_pool or hard_legal_pool

        chosen = min(
            pool,
            key=lambda team: (
                participant_selection_score(
                    planner, selected, remaining, team, age_group, period,
                    hosting_priority_clubs=hosting_priority_clubs,
                ),
                candidate_order[planner._team_key(team)],
            ),
        )
        remaining.remove(chosen)
        selected.append(chosen)

        chosen_club_count = sum(1 for s in selected if s.club == chosen.club)
        if chosen_club_count > max_club_teams_for(planner, age_group, chosen.club):
            planner._club_cap_overrides += 1

    return selected


def participant_selection_score(
    planner,
    selected: Sequence[Team],
    remaining: Sequence[Team],
    team: Team,
    age_group: str,
    period: Optional[str] = None,
    *,
    hosting_priority_clubs: Optional[Set[str]] = None,
) -> float:
    """Return a single score for a candidate team (lower is better)."""
    team_key = planner._team_key(team)
    score = float(club_diversity_penalty(planner, selected, remaining, team))

    # issue #323: a soft tie-break nudge only -- applied on top of every
    # other (hard-filtered-first) term below, never able to override the
    # club-cap/deficit/repeat-matchup weighting on its own.
    if hosting_priority_clubs and any(
        clubs_represent_same_club(team.club, club) for club in hosting_priority_clubs
    ):
        score -= 300.0

    club_count = sum(1 for s in selected if s.club == team.club)
    max_club = max_club_teams_for(planner, age_group, team.club)
    club_deficit = club_share_deficit(planner, age_group, period, team.club)
    if max_club > 0:
        if club_count >= max_club:
            # issue #324: a 3rd-or-later team from one club must outrank
            # opponent/club diversity and fairness-deficit tie-breaks (the
            # terms below), so this stays legal only when it is the least
            # bad remaining candidate, not merely a competitive one.
            #
            # issue #327: unless the club is materially behind its
            # proportional demand share, in which case the cap penalty is
            # cut down so the continuous deficit pull below can outrank a
            # no-deficit different/under-cap-club candidate -- proportional
            # club-share fairness is a legitimate higher-priority reason to
            # use a 3rd (or, within the hard cap, 4th) team, not merely a
            # last-resort filler.
            if club_deficit >= CLUB_SHARE_DEFICIT_THRESHOLD:
                score += (club_count - max_club + 1) * 150.0
            else:
                score += (club_count - max_club + 1) * 1500.0
        else:
            score += club_count * 20.0

    # issue #327: a continuous pull toward a club's proportional demand
    # share, on top of the cap-penalty relief above -- reducing the fixed
    # cap-penalty step alone still leaves a within-cap different-club
    # candidate favored by a fixed margin regardless of how large the
    # deficit is; this term lets a materially larger deficit actually
    # outrank that candidate rather than merely narrowing the gap to it.
    # Never rewards a club that is already at/above its fair share.
    score -= max(0.0, club_deficit) * 100.0

    deficit = deficit_score(planner, team, age_group, period)
    score -= deficit * 350.0

    score += normalized_invite_count(planner, team) * 8.0

    repeat_matchup_total = 0.0
    for existing in selected:
        pair = frozenset((team_key, planner._team_key(existing)))
        repeat_matchup_total += planner._opponent_history.get(pair, 0)
    if selected:
        score += (repeat_matchup_total / len(selected)) * 180.0

    grouped_with = planner._grouped_with.get(team_key, set())
    if selected:
        score += sum(1 for s in selected if planner._team_key(s) in grouped_with) * 120.0

    return score


def base_team_capacity(planner, age_group: str) -> int:
    """Return the even team-count capacity implied by parallel games."""
    parallel_games = planner.parallel_games_for_age_group.get(age_group, 1)
    return max(1, parallel_games) * 2
