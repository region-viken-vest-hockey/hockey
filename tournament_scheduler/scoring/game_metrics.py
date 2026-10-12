"""Plan metric helpers used by SeasonPlanner."""

from __future__ import annotations

from typing import Dict, Sequence

from tournament_scheduler.models import Tournament


def arena_counts(tournaments: Sequence[Tournament]) -> Dict[str, int]:
    """Count tournaments per arena."""
    counts: Dict[str, int] = {}
    for tournament in tournaments:
        counts[tournament.arena] = counts.get(tournament.arena, 0) + 1
    return counts


def diversity_score(planner, tournaments: Sequence[Tournament]) -> float:
    """Opponent-variety diversity score grounded in `_opponent_history`."""
    opponents_faced: Dict[str, set] = {}
    for pair in planner._opponent_history:
        a, b = tuple(pair)
        opponents_faced.setdefault(a, set()).add(b)
        opponents_faced.setdefault(b, set()).add(a)

    if not opponents_faced:
        return 0.0

    teams_by_key = {planner._team_key(team): team for team in planner.roster.teams}

    ratios = []
    for key, faced in opponents_faced.items():
        team = teams_by_key.get(key)
        if team is None:
            continue
        available = [
            planner._team_key(other)
            for other in planner.roster.teams
            if planner._team_key(other) != key
            and other.age_group == team.age_group
            and other.club != team.club
        ]
        if not available:
            continue
        ratios.append(len(faced & set(available)) / len(available))

    if not ratios:
        return 0.0
    return round(sum(ratios) / len(ratios), 3)


def pairwise_matchup_score(planner, tournaments: Sequence[Tournament]) -> float:
    """Fraction of scheduled matchups that are first-time pairings."""
    seen_pairs: Dict[frozenset, int] = {}
    novel_total = 0
    game_total = 0

    for tournament in tournaments:
        for game in tournament.games:
            if game.home is None or game.away is None:
                continue
            pair = frozenset((planner._team_key(game.home), planner._team_key(game.away)))
            game_total += 1
            if pair not in seen_pairs:
                novel_total += 1
            seen_pairs[pair] = seen_pairs.get(pair, 0) + 1

    if game_total == 0:
        return 0.0
    return round(novel_total / game_total, 3)


def month_balance_score(planner, expected_per_month: float) -> float:
    """Score how evenly tournaments are spread across the season's months."""
    if expected_per_month <= 0 or not planner._month_counts:
        return 0.0

    deviation_total = 0.0
    for count in planner._month_counts.values():
        deviation_total += abs(count - expected_per_month) / expected_per_month

    avg_deviation = deviation_total / len(planner._month_counts)
    return round(max(0.0, 1.0 - avg_deviation), 3)
