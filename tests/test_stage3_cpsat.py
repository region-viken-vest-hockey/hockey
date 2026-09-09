"""Unit tests for tournament_scheduler.stage3_cpsat (issue #276 Phase 2/3)."""

from __future__ import annotations

import pytest

ortools = pytest.importorskip(
    "ortools", reason="OR-Tools is the optional 'cpsat' extra; skip when not installed"
)
from ortools.sat.python import cp_model

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


def _half_split_candidate() -> dict:
    """Tournaments spanning both sides of the Christmas boundary (issue #298)."""
    teams = {f"T{i}": _team(f"Club{i}", f"T{i}", "U10") for i in range(1, 9)}
    group_a = [teams["T1"], teams["T2"], teams["T3"], teams["T4"]]
    group_b = [teams["T5"], teams["T6"], teams["T7"], teams["T8"]]
    return {
        "schema_version": 1,
        "tournaments": [
            _tournament("t1", "2026-11-01", "Arena1", "U10", group_a),
            _tournament("t2", "2026-12-06", "Arena1", "U10", group_a),
            _tournament("t3", "2027-01-10", "Arena5", "U10", group_b),
            _tournament("t4", "2027-02-14", "Arena5", "U10", group_b),
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

    def test_baseline_duplicate_date_conflict_is_reported_before_solving(self):
        """issue #298 Phase 1: "If the baseline itself violates an encoded
        CP-SAT constraint, report exactly which invariant conflicts with the
        known baseline rather than timing out opaquely." A team already
        appearing twice on the same date for the same age group (e.g. two
        parallel pools scheduled the same day) can never satisfy the
        no-duplicate-participation-on-one-date constraint regardless of how
        the rest of the model is assigned -- this must be caught and named
        before the solver ever runs, not surfaced as a bare INFEASIBLE."""
        only_team = _team("Club1", "T1", "U10")
        candidate = {
            "schema_version": 1,
            "tournaments": [
                _tournament("t1", "2026-01-05", "Arena1", "U10", [only_team]),
                _tournament("t2", "2026-01-05", "Arena2", "U10", [only_team]),
            ],
        }

        with pytest.raises(CpSatNoCandidate) as excinfo:
            optimize_candidate_cp_sat(candidate, None, solve_budget_seconds=5.0, seed=1)

        assert excinfo.value.status == "BASELINE_CONSTRAINT_CONFLICT"
        diagnostics = excinfo.value.diagnostics
        assert diagnostics["violated_constraint"] == "no_duplicate_participation_on_one_date"
        conflicts = diagnostics["baseline_conflicts"]
        assert len(conflicts) == 1
        assert conflicts[0]["team"] == "Club1:T1:U10"
        assert conflicts[0]["date"] == "2026-01-05"
        assert sorted(conflicts[0]["tournament_ids"]) == ["t1", "t2"]

    def test_empty_candidate_returns_empty_status_without_solving(self):
        candidate = {"schema_version": 1, "tournaments": []}

        optimized = optimize_candidate_cp_sat(candidate, None, solve_budget_seconds=5.0, seed=1)

        assert optimized["tournaments"] == []
        assert optimized["source"]["status"] == "EMPTY"

    def test_raises_cp_sat_no_candidate_on_solver_timeout(self, monkeypatch):
        """Simulate the solver exhausting its time budget without reaching
        OPTIMAL/FEASIBLE. A real wall-clock timeout on a small enough model to
        run in CI would be flaky (the solver may finish before the budget
        expires); forcing ``Solve`` to return UNKNOWN exercises the exact
        status-handling branch a genuine timeout takes, deterministically."""
        candidate = _clustered_candidate()

        def _fake_solve(self, model):
            return cp_model.UNKNOWN

        monkeypatch.setattr(cp_model.CpSolver, "Solve", _fake_solve)

        with pytest.raises(CpSatNoCandidate) as excinfo:
            optimize_candidate_cp_sat(candidate, None, solve_budget_seconds=0.1, seed=1)

        assert excinfo.value.status == "UNKNOWN"
        assert excinfo.value.runtime_seconds >= 0.0

    def test_no_candidate_error_carries_reproducible_fingerprints(self):
        only_team = _team("Club1", "T1", "U10")
        candidate = {
            "schema_version": 1,
            "tournaments": [
                _tournament("t1", "2026-01-05", "Arena1", "U10", [only_team]),
                _tournament("t2", "2026-01-05", "Arena2", "U10", [only_team]),
            ],
        }
        problem = {"teams": [only_team]}

        with pytest.raises(CpSatNoCandidate) as excinfo:
            optimize_candidate_cp_sat(candidate, problem, solve_budget_seconds=5.0, seed=1)

        from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

        assert excinfo.value.baseline_candidate_fingerprint == stable_payload_sha256(
            candidate["tournaments"]
        )
        assert excinfo.value.problem_fingerprint == stable_payload_sha256(problem)

    def test_source_metadata_includes_reproducible_fingerprints(self):
        candidate = _clustered_candidate()
        problem = {"teams": []}

        optimized = optimize_candidate_cp_sat(candidate, problem, solve_budget_seconds=5.0, seed=1)

        from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

        source = optimized["source"]
        assert source["baseline_candidate_fingerprint"] == stable_payload_sha256(
            candidate["tournaments"]
        )
        assert source["problem_fingerprint"] == stable_payload_sha256(problem)
        assert source["candidate_fingerprint"] == stable_payload_sha256(optimized["tournaments"])

    def test_records_baseline_hints_used(self):
        candidate = _clustered_candidate()

        optimized = optimize_candidate_cp_sat(candidate, None, solve_budget_seconds=5.0, seed=1)

        assert optimized["source"]["baseline_hints"] is True

    def test_hints_every_decision_variable_at_its_baseline_value(self, monkeypatch):
        """issue #298: the baseline is always a feasible starting point for
        this model -- every x[slot, team] decision variable must be hinted
        at 1 when that team is in the slot's baseline roster, 0 otherwise,
        so CP-SAT can validate/repair a known-feasible solution immediately
        instead of rediscovering it from scratch."""
        candidate = _clustered_candidate()
        captured_models: list = []
        original_solve = cp_model.CpSolver.Solve

        def _capturing_solve(self, model):
            captured_models.append(model)
            return original_solve(self, model)

        monkeypatch.setattr(cp_model.CpSolver, "Solve", _capturing_solve)

        optimize_candidate_cp_sat(candidate, None, solve_budget_seconds=5.0, seed=1)

        assert len(captured_models) == 1
        proto = captured_models[0].Proto()
        hinted_names = [proto.variables[idx].name for idx in proto.solution_hint.vars]
        hinted_values = list(proto.solution_hint.values)
        hints_by_name = dict(zip(hinted_names, hinted_values))

        x_hints = {name: value for name, value in hints_by_name.items() if name.startswith("x_t")}
        assert x_hints
        # Every tournament's baseline roster is 4 teams out of the full
        # 8-team U10 pool -- exactly 4 of that tournament's x-vars must be
        # hinted 1, the rest 0.
        for t_index in range(len(candidate["tournaments"])):
            prefix = f"x_t{t_index}_team"
            values = [value for name, value in x_hints.items() if name.startswith(prefix)]
            assert sum(values) == 4
            assert len(values) == 8

    def test_empty_candidate_source_includes_fingerprints(self):
        candidate = {"schema_version": 1, "tournaments": []}

        optimized = optimize_candidate_cp_sat(candidate, None, solve_budget_seconds=5.0, seed=1)

        source = optimized["source"]
        assert source["baseline_candidate_fingerprint"] is not None
        assert source["problem_fingerprint"] is None
        assert source["candidate_fingerprint"] is not None

    def test_source_metadata_includes_half_diagnostics_for_combined_mode(self):
        """issue #298: solver diagnostics (team/slot/variable/constraint/
        hint counts, half, budget, status, runtime) are always persisted,
        even when not decomposed by half."""
        candidate = _clustered_candidate()

        optimized = optimize_candidate_cp_sat(candidate, None, solve_budget_seconds=5.0, seed=1)

        source = optimized["source"]
        assert source["decompose_by_half"] is False
        assert len(source["half_diagnostics"]) == 1
        diag = source["half_diagnostics"][0]
        assert diag["half"] == "combined"
        assert diag["mode"] == "quality"
        assert diag["team_count"] == 8
        assert diag["slot_count"] == 4
        assert diag["assignment_var_count"] > 0
        assert diag["pair_var_count"] > 0
        assert diag["constraint_count"] > 0
        assert diag["hint_count"] == diag["assignment_var_count"]
        assert diag["status"] in ("OPTIMAL", "FEASIBLE")
        assert diag["runtime_seconds"] >= 0.0

    def test_feasibility_only_skips_objective_and_pair_variables(self):
        """issue #298 Phase 1: feasibility_only proves the baseline
        reproduces cleanly without the pair/co-occurrence/objective
        machinery that exists purely for quality optimization."""
        candidate = _clustered_candidate()

        optimized = optimize_candidate_cp_sat(
            candidate, None, solve_budget_seconds=5.0, seed=1, feasibility_only=True
        )

        source = optimized["source"]
        assert source["mode"] == "feasibility_only"
        diag = source["half_diagnostics"][0]
        assert diag["mode"] == "feasibility_only"
        assert diag["pair_var_count"] == 0
        assert source["objective_value"] == 0.0
        verification = verify_candidate(optimized, None)
        assert verification["ok"], verification

    def test_decompose_by_half_splits_into_independent_solver_groups(self):
        """issue #298 Phase 2: participant assignment is solved
        independently per planning-half rather than one monolithic model."""
        candidate = _half_split_candidate()
        problem = {"christmas_split_date": "2026-12-24"}

        optimized = optimize_candidate_cp_sat(
            candidate, problem, solve_budget_seconds=5.0, seed=1, decompose_by_half=True
        )

        source = optimized["source"]
        assert source["decompose_by_half"] is True
        halves = {d["half"] for d in source["half_diagnostics"]}
        assert halves == {"before_christmas", "after_christmas"}
        for diag in source["half_diagnostics"]:
            assert diag["slot_count"] == 2
            assert diag["status"] in ("OPTIMAL", "FEASIBLE")

        verification = verify_candidate(optimized, None)
        assert verification["ok"], verification
        before = score_candidate(
            {"tournaments": [t for t in optimized["tournaments"] if t["date"] < "2026-12-24"]}
        )
        after = score_candidate(
            {"tournaments": [t for t in optimized["tournaments"] if t["date"] >= "2026-12-24"]}
        )
        # Each half's own baseline participation (2 appearances per team,
        # one per half) must be preserved independently.
        assert set(before["participation"]["counts_by_team"].values()) == {2}
        assert set(after["participation"]["counts_by_team"].values()) == {2}

    def test_no_candidate_error_carries_diagnostics(self, monkeypatch):
        """issue #298: a no-candidate result carries explainable solver
        evidence rather than an opaque timeout."""
        candidate = _clustered_candidate()

        def _fake_solve(self, model):
            return cp_model.UNKNOWN

        monkeypatch.setattr(cp_model.CpSolver, "Solve", _fake_solve)

        with pytest.raises(CpSatNoCandidate) as excinfo:
            optimize_candidate_cp_sat(candidate, None, solve_budget_seconds=0.1, seed=1)

        diagnostics = excinfo.value.diagnostics
        assert diagnostics["half"] == "combined"
        assert diagnostics["status"] == "UNKNOWN"
        assert diagnostics["slot_count"] == 4
        assert diagnostics["team_count"] == 8
        assert diagnostics["objective_value"] == 0.0
