"""Unit tests for tournament_scheduler.stage3_engine (issue #276 Phase 1)."""

from __future__ import annotations

import pytest

from tournament_scheduler.stage3_engine import (
    DEFAULT_ENGINE,
    ENGINE_CHOICES,
    UnknownEngineError,
    run_planner,
)
from tournament_scheduler.stage3_optimizer import optimize_candidate


def _team(club: str, label: str, age_group: str) -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(t_id: str, date_str: str, arena: str, age_group: str, teams: list[dict]) -> dict:
    game_pairs = [(a["label"], b["label"]) for i, a in enumerate(teams) for b in teams[i + 1 :]]
    return {
        "id": t_id,
        "date": date_str,
        "arena": arena,
        "age_group": age_group,
        "host_club": teams[0]["club"] if teams else None,
        "teams": teams,
        "games": [
            {"home": home, "away": away, "parallel_slot": 0, "round_number": 1}
            for home, away in game_pairs
        ],
    }


def _clustered_candidate() -> dict:
    teams = {f"T{i}": _team(f"Club{i}", f"T{i}", "U10") for i in range(1, 9)}
    group_a = [teams["T1"], teams["T2"], teams["T3"], teams["T4"]]
    group_b = [teams["T5"], teams["T6"], teams["T7"], teams["T8"]]
    return {
        "schema_version": 1,
        "tournaments": [
            _tournament("t1", "2026-01-05", "Arena1", "U10", group_a),
            _tournament("t2", "2026-02-04", "Arena1", "U10", group_a),
            _tournament("t3", "2026-03-06", "Arena5", "U10", group_b),
            _tournament("t4", "2026-04-05", "Arena5", "U10", group_b),
        ],
    }


def _without_timings(candidate: dict) -> dict:
    """Drop `source.timings` (wall-clock, so never reproducible run-to-run)
    before comparing two candidates for equality."""
    result = dict(candidate)
    source = result.get("source")
    if isinstance(source, dict) and "timings" in source:
        source = dict(source)
        del source["timings"]
        result["source"] = source
    return result


class TestRunPlanner:
    def test_default_engine_is_local_search(self):
        assert DEFAULT_ENGINE == "local_search"
        assert set(ENGINE_CHOICES) == {"local_search", "cp_sat"}

    def test_local_search_is_the_default_and_matches_direct_call(self):
        candidate = _clustered_candidate()

        via_engine = run_planner(problem=None, baseline=candidate, request={"iterations": 500, "seed": 7})
        direct = optimize_candidate(candidate, None, iterations=500, seed=7)

        assert _without_timings(via_engine) == _without_timings(direct)

    def test_explicit_local_search_matches_default(self):
        candidate = _clustered_candidate()

        via_default = run_planner(problem=None, baseline=candidate, request={"iterations": 500, "seed": 3})
        via_explicit = run_planner(
            engine="local_search", problem=None, baseline=candidate, request={"iterations": 500, "seed": 3}
        )

        assert _without_timings(via_default) == _without_timings(via_explicit)

    def test_unknown_engine_raises(self):
        candidate = _clustered_candidate()

        with pytest.raises(UnknownEngineError):
            run_planner(engine="minizinc", problem=None, baseline=candidate)

    def test_cp_sat_dispatch_routes_to_cpsat_module(self):
        pytest.importorskip(
            "ortools", reason="OR-Tools is the optional 'cpsat' extra; skip when not installed"
        )
        candidate = _clustered_candidate()

        result = run_planner(
            engine="cp_sat",
            problem=None,
            baseline=candidate,
            request={"solve_budget_seconds": 5.0, "seed": 1},
        )

        assert result["source"]["planner"] == "cp_sat"

    def test_cp_sat_unavailable_propagates_when_ortools_missing(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def _blocked_import(name, *args, **kwargs):
            if name == "ortools.sat.python" or name.startswith("ortools"):
                raise ImportError("no ortools in this test")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _blocked_import)

        from tournament_scheduler.stage3_cpsat import CpSatUnavailable

        candidate = _clustered_candidate()
        with pytest.raises(CpSatUnavailable):
            run_planner(engine="cp_sat", problem=None, baseline=candidate)

    def test_request_ignores_keys_for_the_unselected_engine(self):
        """One request dict built for either engine should not raise merely
        because it carries the other engine's tuning knobs."""
        candidate = _clustered_candidate()

        result = run_planner(
            engine="local_search",
            problem=None,
            baseline=candidate,
            request={"iterations": 200, "seed": 1, "solve_budget_seconds": 30.0},
        )

        assert result["schema_version"] == candidate["schema_version"]
