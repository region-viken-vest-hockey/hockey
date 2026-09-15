"""Limited-round tournament game generation."""

from __future__ import annotations

from itertools import combinations
from typing import Sequence

from ortools.sat.python import cp_model

from tournament_scheduler.host_representation import constituent_clubs
from tournament_scheduler.models import Game, Team


def same_physical_club(a: Team, b: Team) -> bool:
    return bool(set(constituent_clubs(a.club)) & set(constituent_clubs(b.club)))


def count_same_club_games(games: Sequence[Game]) -> int:
    """Count games between teams from the same physical club."""
    return sum(1 for game in games if same_physical_club(game.home, game.away))


def generate_limited_round_games(teams: Sequence[Team], parallel_games: int, rounds: int) -> list[Game]:
    """Generate the best deterministic limited-round schedule."""
    roster = list(teams)
    n = len(roster)
    if n < 2 or rounds <= 0:
        return []
    parallel_games = max(1, parallel_games)
    games_per_round = min(parallel_games, n // 2)
    if games_per_round <= 0:
        return []

    pairs = list(combinations(range(n), 2))
    target_games = min(rounds * games_per_round, len(pairs))
    model = cp_model.CpModel()
    x: dict[tuple[int, int], cp_model.IntVar] = {}
    for r in range(rounds):
        for p_idx, _pair in enumerate(pairs):
            x[(r, p_idx)] = model.NewBoolVar(f"x_{r}_{p_idx}")

    for r in range(rounds):
        round_vars = [x[(r, p_idx)] for p_idx in range(len(pairs))]
        if target_games == rounds * games_per_round:
            model.Add(sum(round_vars) == games_per_round)
        else:
            model.Add(sum(round_vars) <= games_per_round)
        for team_idx in range(n):
            model.Add(sum(x[(r, p_idx)] for p_idx, pair in enumerate(pairs) if team_idx in pair) <= 1)

    for p_idx in range(len(pairs)):
        model.Add(sum(x[(r, p_idx)] for r in range(rounds)) <= 1)

    total_games = model.NewIntVar(0, target_games, "total_games")
    model.Add(total_games == sum(x.values()))
    counts = []
    for team_idx in range(n):
        count = model.NewIntVar(0, rounds, f"count_{team_idx}")
        model.Add(
            count
            == sum(
                x[(r, p_idx)]
                for r in range(rounds)
                for p_idx, pair in enumerate(pairs)
                if team_idx in pair
            )
        )
        counts.append(count)
    min_count = model.NewIntVar(0, rounds, "min_count")
    max_count = model.NewIntVar(0, rounds, "max_count")
    model.AddMinEquality(min_count, counts)
    model.AddMaxEquality(max_count, counts)
    spread = model.NewIntVar(0, rounds, "spread")
    model.Add(spread == max_count - min_count)
    same = sum(
        x[(r, p_idx)]
        for r in range(rounds)
        for p_idx, (a, b) in enumerate(pairs)
        if same_physical_club(roster[a], roster[b])
    )
    signature_penalty = sum(
        (r + 1) * (p_idx + 1) * x[(r, p_idx)]
        for r in range(rounds)
        for p_idx in range(len(pairs))
    )
    model.Minimize(
        (target_games - total_games) * 1_000_000
        + same * 10_000
        + spread * 1_000
        + signature_penalty
    )

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    solver.parameters.max_time_in_seconds = 10.0
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return []

    result: list[Game] = []
    for r in range(rounds):
        selected = [pairs[p_idx] for p_idx in range(len(pairs)) if solver.Value(x[(r, p_idx)])]
        selected.sort(key=lambda p: (roster[p[0]].label, roster[p[1]].label))
        for slot, (a, b) in enumerate(selected):
            result.append(Game(home=roster[a], away=roster[b], parallel_slot=slot, round_number=r + 1))
    return result


def minimum_same_club_games_for_limited_rounds(teams: Sequence[Team], parallel_games: int, rounds: int) -> int:
    """Return the minimum same-club games achievable for the tournament shape."""
    return count_same_club_games(generate_limited_round_games(teams, parallel_games, rounds))
