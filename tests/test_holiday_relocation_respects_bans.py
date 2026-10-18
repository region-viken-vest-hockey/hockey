"""Relocation must not move a tournament onto an operator-banned date (issue #423).

The relocation path is a maintenance search: it may only land on dates the
independent verifier and the planner would accept. An operator ban inside the
window is forbidden even when it is an ordinary, non-holiday weekend.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, List

from tournament_scheduler.planning.contract import build_planning_problem
from tournament_scheduler.planning.date_policy import problem_forbidden_dates
from tournament_scheduler.repair.date_policy_relocation import (
    relocate_excluded_date_tournaments,
)

WINDOW_START = date(2026, 9, 1)
WINDOW_END = date(2027, 4, 30)
HOLIDAY = date(2026, 12, 26)
#: Nearest admissible weekend to the holiday tournament; banning it must steer
#: relocation to another admissible date.
BANNED = date(2026, 12, 13)


def _teams(clubs: Iterable[str]) -> List[Dict[str, str]]:
    return [
        {"club": club, "label": f"{club} {index}", "age_group": "U10"}
        for club in clubs
        for index in (1, 2)
    ]


def _problem(teams: List[Dict[str, str]], banned: List[date]) -> Dict[str, Any]:
    config: Dict[str, Any] = {
        "teams": teams,
        "age_groups": ["U10"],
        "parallel_games": {"U10": 2},
        "round_length_minutes": {"U10": 30},
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
    }
    problem = build_planning_problem(config, None, WINDOW_START, WINDOW_END)
    problem["clubs"] = {club: f"{club} Arena" for club in {team["club"] for team in teams}}
    problem.setdefault("manual_adjustments", {})["banned_dates"] = [d.isoformat() for d in banned]
    return problem


def _tournament(tournament_id: str, day: str, host: str, teams: List[Dict[str, str]]) -> Dict[str, Any]:
    labels = [team["label"] for team in teams]
    return {
        "id": tournament_id,
        "date": day,
        "arena": f"{host} Arena",
        "age_group": "U10",
        "host_club": host,
        "teams": teams,
        "games": [{"home": labels[0], "away": labels[1], "parallel_slot": 0, "round_number": 1}],
        "start_time": "10:00",
    }


def test_relocation_never_lands_on_operator_banned_date() -> None:
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams, banned=[BANNED])
    assert BANNED.isoformat() in {k.isoformat() for k in problem_forbidden_dates(problem)}
    plan = {
        "schema_version": 1,
        "start_date": WINDOW_START.isoformat(),
        "end_date": WINDOW_END.isoformat(),
        "tournaments": [_tournament("T1", HOLIDAY.isoformat(), "Nordby", teams)],
    }

    result = relocate_excluded_date_tournaments(plan, problem)

    assert result["moves"], result["unresolved"]
    for move in result["moves"]:
        assert move["to_date"] != BANNED.isoformat()
        assert date.fromisoformat(move["to_date"]) not in problem_forbidden_dates(problem)
