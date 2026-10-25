"""Downstream planning reads only the canonical snake_case config keys (issue #337)."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict

from tournament_scheduler.planning.contract import build_planning_problem


def _config(**extra: Any) -> Dict[str, Any]:
    config: Dict[str, Any] = {
        "teams": [],
        "age_groups": ["U10"],
        "parallel_games": {"U10": 2},
        "round_length_minutes": {"U10": 30},
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
    }
    config.update(extra)
    return config


def _problem(config: Dict[str, Any]) -> Dict[str, Any]:
    return build_planning_problem(config, None, date(2026, 9, 1), date(2027, 4, 30))


def test_canonical_max_hosting_deviation_is_honoured():
    problem = _problem(_config(max_hosting_deviation=3))

    assert problem["max_hosting_deviation"] == 3


def test_legacy_camel_case_alias_is_not_read_downstream():
    """Aliases are converted at the input boundary, so downstream never sees the camelCase key."""

    problem = _problem(_config(maxHostingDeviation=5))

    assert problem["max_hosting_deviation"] == 1
