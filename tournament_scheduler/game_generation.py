"""Game-generation helpers for `SeasonPlanner`."""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

from tournament_scheduler.game_metrics import (
    arena_counts,
    diversity_score,
    month_balance_score,
    pairwise_matchup_score,
)
from tournament_scheduler.limited_rounds import (
    count_same_club_games,
    generate_limited_round_games,
    minimum_same_club_games_for_limited_rounds,
)
from tournament_scheduler.models import Game, Team

__all__ = [
    "arena_counts",
    "best_round_subset",
    "count_same_club_games",
    "diversity_score",
    "generate_limited_round_games",
    "generate_round_robin_games",
    "generate_tournament_games",
    "minimum_same_club_games_for_limited_rounds",
    "month_balance_score",
    "pairwise_matchup_score",
    "rebalance_rounds",
]


def generate_tournament_games(
    teams: Sequence[Team],
    parallel_games: int,
    rounds_per_tournament: int | None = None,
) -> List[Game]:
    """Generate canonical games for a tournament.

    With no explicit round count this is the historical complete round-robin.
    With a configured round count, build that many rounds directly and minimize
    same-physical-club pairings before deterministic ordering preferences.
    """
    if rounds_per_tournament is None:
        return generate_round_robin_games(teams, parallel_games)
    return generate_limited_round_games(teams, parallel_games, rounds_per_tournament)


def generate_round_robin_games(teams: Sequence[Team], parallel_games: int) -> List[Game]:
    """Generate a complete round-robin schedule for `teams` using the circle method."""
    n = len(teams)
    if n < 2:
        return []

    parallel_games = max(1, parallel_games)

    roster = list(teams)
    has_bye = n % 2 == 1
    if has_bye:
        roster = roster + [None]  # type: ignore[list-item]

    slot_count = len(roster)
    num_rounds = slot_count - 1
    half = slot_count // 2

    games: List[Game] = []
    rotation = roster[:]

    for round_index in range(num_rounds):
        round_pairs: List[Tuple[Team, Team]] = []
        for i in range(half):
            home = rotation[i]
            away = rotation[slot_count - 1 - i]
            if home is None or away is None:
                continue
            round_pairs.append((home, away))

        if round_index % 2 == 1:
            # Swap home/away for balanced hosting — but preserve the pinned team
            # (roster[0], i.e. the host club when the caller places the host at
            # index 0) as home so that the arena-owning club always appears as
            # the home team in their own games.
            pinned = roster[0]
            round_pairs = [
                (home, away) if (home is pinned or away is None)
                else (away, home)
                for home, away in round_pairs
            ]

        for slot_index, (home, away) in enumerate(round_pairs):
            games.append(
                Game(
                    home=home,
                    away=away,
                    parallel_slot=slot_index % parallel_games,
                    round_number=round_index + 1,
                )
            )

        rotation = [rotation[0]] + [rotation[-1]] + rotation[1:-1]

    round_sizes: dict[int, int] = {}
    for game in games:
        round_sizes[game.round_number] = round_sizes.get(game.round_number, 0) + 1
    expected_round_size = n // 2
    if round_sizes and all(count == expected_round_size for count in round_sizes.values()):
        return games
    return rebalance_rounds(games, parallel_games)


def rebalance_rounds(games: Sequence[Game], parallel_games: int) -> List[Game]:
    """Pack games into the smallest balanced set of rounds possible."""
    if not games:
        return []

    parallel_games = max(1, parallel_games)

    team_degree: dict[str, int] = {}
    for game in games:
        team_degree[game.home.label] = team_degree.get(game.home.label, 0) + 1
        team_degree[game.away.label] = team_degree.get(game.away.label, 0) + 1

    max_team_games = max(team_degree.values(), default=0)
    required_rounds = max(max_team_games, math.ceil(len(games) / parallel_games))
    required_rounds = max(1, required_rounds)

    base_size, remainder = divmod(len(games), required_rounds)
    ideal_sizes = [base_size + (1 if i < remainder else 0) for i in range(required_rounds)]

    def _target_candidates() -> list[list[int]]:
        candidates: list[list[int]] = []

        def build(prefix: list[int], remaining: int, max_next: int, rounds_left: int) -> None:
            if rounds_left == 0:
                if remaining == 0:
                    candidates.append(prefix[:])
                return

            if remaining < rounds_left or remaining > rounds_left * parallel_games:
                return

            upper = min(max_next, remaining - (rounds_left - 1))
            lower = max(1, math.ceil(remaining / rounds_left))
            for size in range(upper, lower - 1, -1):
                prefix.append(size)
                build(prefix, remaining - size, size, rounds_left - 1)
                prefix.pop()

        build([], len(games), parallel_games, required_rounds)
        if not candidates:
            candidates.append(ideal_sizes)

        average = len(games) / required_rounds
        candidates.sort(
            key=lambda sizes: (
                max(sizes) - min(sizes),
                sum((size - average) ** 2 for size in sizes),
                tuple(-size for size in sizes),
            )
        )
        return candidates

    ordered = list(enumerate(games))
    ordered.sort(
        key=lambda item: (
            -(team_degree[item[1].home.label] + team_degree[item[1].away.label]),
            item[1].round_number or 0,
            item[1].home.label,
            item[1].away.label,
            item[0],
        )
    )

    round_games: list[list[tuple[int, Game]]] = []
    round_teams: list[set[str]] = []
    round_counts: list[int] = []

    def try_targets(target_sizes: list[int]) -> bool:
        nonlocal round_games, round_teams, round_counts
        round_games = [[] for _ in range(required_rounds)]
        round_teams = [set() for _ in range(required_rounds)]
        round_counts = [0 for _ in range(required_rounds)]

        def backtrack(index: int) -> bool:
            if index >= len(ordered):
                return True

            remaining_slots = sum(target_sizes[r] - round_counts[r] for r in range(required_rounds))
            if remaining_slots < len(ordered) - index:
                return False

            original_index, game = ordered[index]
            candidate_rounds = [
                r for r in range(required_rounds)
                if round_counts[r] < target_sizes[r]
                and game.home.label not in round_teams[r]
                and game.away.label not in round_teams[r]
            ]
            candidate_rounds.sort(key=lambda r: (round_counts[r], r))

            for round_index in candidate_rounds:
                round_games[round_index].append((original_index, game))
                round_counts[round_index] += 1
                round_teams[round_index].update({game.home.label, game.away.label})

                if backtrack(index + 1):
                    return True

                round_games[round_index].pop()
                round_counts[round_index] -= 1
                round_teams[round_index].discard(game.home.label)
                round_teams[round_index].discard(game.away.label)

            return False

        return backtrack(0)

    solved = False
    for target_sizes in _target_candidates():
        if try_targets(target_sizes):
            solved = True
            break

    if not solved:
        return list(games)

    rebased: List[Game] = []
    for round_index, games_in_round in enumerate(round_games, start=1):
        games_in_round.sort(
            key=lambda item: (
                item[1].round_number or 0,
                item[0],
                item[1].home.label,
                item[1].away.label,
            )
        )
        for slot_index, (_, game) in enumerate(games_in_round):
            game.round_number = round_index
            game.parallel_slot = slot_index % parallel_games
            rebased.append(game)
    return rebased


def best_round_subset(candidates: Sequence[tuple[int, Game]], parallel_games: int) -> list[tuple[int, Game]]:
    """Return the largest compatible subset of games for one round."""
    limit = max(1, parallel_games)
    ordered = list(candidates)
    best: list[tuple[int, Game]] = []
    best_signature: tuple[int, ...] | None = None

    def signature(selection: list[tuple[int, Game]]) -> tuple[int, ...]:
        return tuple(index for index, _ in selection)

    def consider(selection: list[tuple[int, Game]]) -> None:
        nonlocal best, best_signature
        current_signature = signature(selection)
        if len(selection) > len(best):
            best = selection[:]
            best_signature = current_signature
        elif len(selection) == len(best):
            if best_signature is None or current_signature < best_signature:
                best = selection[:]
                best_signature = current_signature

    def backtrack(index: int, chosen: list[tuple[int, Game]], used_teams: set[str]) -> None:
        consider(chosen)

        if index >= len(ordered) or len(chosen) >= limit:
            return
        if len(chosen) + (len(ordered) - index) <= len(best):
            return

        backtrack(index + 1, chosen, used_teams)

        original_index, game = ordered[index]
        if game.home.label in used_teams or game.away.label in used_teams:
            return
        chosen.append((original_index, game))
        backtrack(index + 1, chosen, used_teams | {game.home.label, game.away.label})
        chosen.pop()

    backtrack(0, [], set())
    return best


