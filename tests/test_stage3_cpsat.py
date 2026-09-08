"""Unit tests for tournament_scheduler.stage3_cpsat (issue #276 Phase 2/3)."""

from __future__ import annotations

import pytest

ortools = pytest.importorskip(
    "ortools", reason="OR-Tools is the optional 'cpsat' extra; skip when not installed"
)

from tournament_scheduler.planning_contract import score_candidate, verify_candidate
from tournament_scheduler.stage3_cpsat import (
    CpSatNoCandidate,
    optimize_candidate_cp_sat,
)


def _team(club: str, label: str, age_group: str) -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(
    t_id: str,
    date_str: str,
    arena: str,
    age_group: str,
    teams: list[dict],
    host_club: str | None = None,
) -> dict:
    game_pairs = [(a["label"], b["label"]) for i, a in enumerate(teams) for b in teams[i + 1 :]]
    return {
        "id": t_id,
        "date": date_str,
        "arena": arena,
        "age_group": age_group,
        "host_club": host_club if host_club is not None else (teams[0]["club"] if teams else None),
        "teams": teams,
        "games": [
            {"home": home, "away": away, "parallel_slot": 0, "round_number": 1}
            for home, away in game_pairs
        ],
    }


def _clustered_candidate() -> dict:
    """A deliberately bad candidate: the same two quartets meet twice each."""
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


class TestOptimizeCandidateCpSat:
    def test_preserves_participation_counts(self):
        candidate = _clustered_candidate()
        before = score_candidate(candidate)["participation"]["counts_by_team"]

        optimized = optimize_candidate_cp_sat(candidate, None, solve_budget_seconds=5.0, seed=1)

        after = score_candidate(optimized)["participation"]["counts_by_team"]
        assert after == before

    def test_preserves_skeleton_dates_hosts_arenas_and_tournament_count(self):
        candidate = _clustered_candidate()

        optimized = optimize_candidate_cp_sat(candidate, None, solve_budget_seconds=5.0, seed=1)

        assert len(optimized["tournaments"]) == len(candidate["tournaments"])
        for before, after in zip(candidate["tournaments"], optimized["tournaments"]):
            assert after["id"] == before["id"]
            assert after["date"] == before["date"]
            assert after["arena"] == before["arena"]
            assert after["host_club"] == before["host_club"]
            assert len(after["teams"]) == len(before["teams"])

    def test_produces_a_valid_candidate(self):
        candidate = _clustered_candidate()

        optimized = optimize_candidate_cp_sat(candidate, None, solve_budget_seconds=5.0, seed=1)

        verification = verify_candidate(optimized, None)
        assert verification["ok"], verification

    def test_pinned_tournament_membership_is_preserved(self):
        candidate = _clustered_candidate()
        pinned_id = candidate["tournaments"][0]["id"]
        pinned_teams = [team["label"] for team in candidate["tournaments"][0]["teams"]]
        problem = {"manual_adjustments": {"pinned_tournament_ids": [pinned_id]}}

        optimized = optimize_candidate_cp_sat(
            candidate, problem, solve_budget_seconds=5.0, seed=1
        )

        pinned_tournament = next(t for t in optimized["tournaments"] if t["id"] == pinned_id)
        assert [team["label"] for team in pinned_tournament["teams"]] == pinned_teams

    def test_host_team_presence_is_preserved_where_baseline_had_it(self):
        candidate = _clustered_candidate()

        optimized = optimize_candidate_cp_sat(candidate, None, solve_budget_seconds=5.0, seed=1)

        for before, after in zip(candidate["tournaments"], optimized["tournaments"]):
            baseline_had_host = any(team["club"] == before["host_club"] for team in before["teams"])
            if baseline_had_host:
                assert any(team["club"] == after["host_club"] for team in after["teams"])

    def test_no_duplicate_participation_on_the_same_date(self):
        candidate = {
            "schema_version": 1,
            "tournaments": [
                _tournament(
                    "t1",
                    "2026-01-05",
                    "Arena1",
                    "U10",
                    [_team("Club1", "T1", "U10"), _team("Club2", "T2", "U10")],
                ),
                _tournament(
                    "t2",
                    "2026-01-05",
                    "Arena2",
                    "U10",
                    [_team("Club3", "T3", "U10"), _team("Club4", "T4", "U10")],
                ),
            ],
        }

        optimized = optimize_candidate_cp_sat(candidate, None, solve_budget_seconds=5.0, seed=1)

        labels_t1 = {team["label"] for team in optimized["tournaments"][0]["teams"]}
        labels_t2 = {team["label"] for team in optimized["tournaments"][1]["teams"]}
        assert not (labels_t1 & labels_t2)

    def test_records_solver_source_metadata(self):
        candidate = _clustered_candidate()

        optimized = optimize_candidate_cp_sat(candidate, None, solve_budget_seconds=5.0, seed=1)

        source = optimized["source"]
        assert source["planner"] == "cp_sat"
        assert source["status"] in ("OPTIMAL", "FEASIBLE")
        assert source["fixed_skeleton"] is True
        assert source["solve_budget_seconds"] == 5.0

    def test_raises_cp_sat_no_candidate_when_infeasible(self):
        """Two same-date, single-team-eligible tournaments both require the
        lone registered team to fill their roster, but the same-team/
        same-date constraint forbids it appearing in both -- an
        unsatisfiable model the solver must report as infeasible rather
        than silently returning a partial/invalid candidate."""
        only_team = _team("Club1", "T1", "U10")
        candidate = {
            "schema_version": 1,
            "tournaments": [
                _tournament("t1", "2026-01-05", "Arena1", "U10", [only_team]),
                _tournament("t2", "2026-01-05", "Arena2", "U10", [only_team]),
            ],
        }

        with pytest.raises(CpSatNoCandidate):
            optimize_candidate_cp_sat(candidate, None, solve_budget_seconds=5.0, seed=1)

    def test_empty_candidate_returns_empty_status_without_solving(self):
        candidate = {"schema_version": 1, "tournaments": []}

        optimized = optimize_candidate_cp_sat(candidate, None, solve_budget_seconds=5.0, seed=1)

        assert optimized["tournaments"] == []
        assert optimized["source"]["status"] == "EMPTY"
