"""#451: CP-SAT participation results are judged by the canonical evaluator.

The solver's own objective is never the evidence. Every solve records the
participation change that ``score_candidate`` / ``verify_candidate`` measure,
and the same candidates go through the A/B contract used for every other
Stage 3 engine. Fixtures are small and deterministic; these tests do not claim
the fixed-skeleton result is a global optimum.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ortools", reason="OR-Tools is the optional 'cpsat' extra; skip when not installed")

from tournament_scheduler.planning.contract import score_candidate, verify_candidate
from tournament_scheduler.stage3.ab import build_ab_report
from tournament_scheduler.stage3.cpsat import optimize_candidate_cp_sat
from tournament_scheduler.stage3.cpsat_parity import PARITY_METRICS, verified_participation_deltas

SPLIT = "2026-12-24"


def _team(label: str) -> dict:
    return {"club": f"Club{label}", "label": label, "age_group": "U10"}


def _tournament(t_id: str, date: str, teams: list[dict], host: str | None = None) -> dict:
    return {
        "id": t_id,
        "date": date,
        "arena": f"Arena{t_id}",
        "age_group": "U10",
        "host_club": host or teams[0]["club"],
        "teams": teams,
        "games": [
            {"home": a["label"], "away": b["label"], "parallel_slot": 0, "round_number": 1}
            for i, a in enumerate(teams)
            for b in teams[i + 1 :]
        ],
    }


def _under_target_baseline() -> dict:
    """Six registered teams, three 4-team tournaments: T5/T6 never play.

    Capacity is 12 slots for 6 teams, so an even 2 each exists. One tournament
    is before Christmas, two after, which gives the ordinary 1+1 half targets
    a season total of 2 with no season-wide override. Each tournament has a
    different host so the host-presence rule does not pin one team to all
    three.
    """
    teams = {f"T{i}": _team(f"T{i}") for i in range(1, 7)}
    core = [teams[f"T{i}"] for i in range(1, 5)]
    return {
        "schema_version": 1,
        "tournaments": [
            _tournament("t1", "2026-11-01", core, host="ClubT1"),
            _tournament("t2", "2027-01-10", core, host="ClubT2"),
            _tournament("t3", "2027-02-14", core, host="ClubT3"),
        ],
    }


def _under_target_problem() -> dict:
    return {
        "christmas_split_date": SPLIT,
        "teams": [_team(f"T{i}") for i in range(1, 7)],
        "participation_targets_by_age_group": {"U10": {"before_christmas": 1, "after_christmas": 1}},
    }


def _two_plus_four_candidate() -> tuple[dict, dict]:
    pool = [_team(f"T{i}") for i in range(1, 5)]
    dates = ["2026-11-01", "2026-12-06", "2027-01-10", "2027-02-14", "2027-03-07", "2027-04-11"]
    candidate = {
        "schema_version": 1,
        "tournaments": [_tournament(f"t{n}", d, pool) for n, d in enumerate(dates, start=1)],
    }
    problem = {
        "christmas_split_date": SPLIT,
        "participation_targets_by_age_group": {"U10": {"before_christmas": 3, "after_christmas": 3}},
    }
    return candidate, problem


class TestVerifiedParticipationDeltas:
    def test_combined_mode_repairs_an_under_target_baseline_without_a_season_override(self):
        baseline = _under_target_baseline()
        problem = _under_target_problem()

        optimized = optimize_candidate_cp_sat(baseline, problem, solve_budget_seconds=5.0, seed=1)

        counts = score_candidate(optimized, problem=problem)["participation"]["counts_by_team"]
        assert counts == {f"T{i}": 2 for i in range(1, 7)}
        report = optimized["source"]["participation_verification"]
        assert optimized["source"]["decompose_by_half"] is False
        assert report["verified"] is True
        assert report["baseline"]["season_total_absolute_deviation"] > 0
        assert report["optimized"]["season_total_absolute_deviation"] == 0
        assert report["delta"]["season_total_absolute_deviation"] == -report["baseline"][
            "season_total_absolute_deviation"
        ]

    def test_recorded_deltas_equal_an_independent_evaluation(self):
        baseline = _under_target_baseline()
        problem = _under_target_problem()

        optimized = optimize_candidate_cp_sat(baseline, problem, solve_budget_seconds=5.0, seed=1)

        recorded = optimized["source"]["participation_verification"]
        before = score_candidate(baseline, problem=problem)["participation"]
        after = score_candidate(optimized, problem=problem)["participation"]
        assert set(recorded["delta"]) == set(PARITY_METRICS)
        for metric in PARITY_METRICS:
            assert recorded["baseline"][metric] == before[metric]
            assert recorded["optimized"][metric] == after[metric]
            assert recorded["delta"][metric] == after[metric] - before[metric]
        assert recorded["verified"] is verify_candidate(optimized, problem)["ok"]

    def test_cross_half_two_plus_four_split_reports_no_worse_half_deviation(self):
        baseline, problem = _two_plus_four_candidate()

        optimized = optimize_candidate_cp_sat(
            baseline, problem, solve_budget_seconds=5.0, seed=1, decompose_by_half=True
        )

        report = optimized["source"]["participation_verification"]
        assert report["verified"] is True
        assert report["delta"]["half_total_absolute_deviation"] <= 0
        # The fixed skeleton forces 2+4 whatever the solver does; the report
        # says what the evaluator measured instead of claiming a 3+3 optimum.
        assert report["optimized"]["half_total_absolute_deviation"] == (
            report["baseline"]["half_total_absolute_deviation"]
        )

    def test_helper_reports_a_hard_invalid_candidate_as_unverified(self):
        baseline = _under_target_baseline()
        problem = _under_target_problem()
        broken = {**baseline, "tournaments": [dict(t) for t in baseline["tournaments"]]}
        # Same teams on the same date: a hard duplicate-participation violation.
        broken["tournaments"][1]["date"] = broken["tournaments"][0]["date"]

        report = verified_participation_deltas(baseline, broken, problem)

        assert report["verified"] is verify_candidate(broken, problem)["ok"]
        assert report["verified"] is False


class TestAbContractParity:
    def test_participation_improves_and_other_trade_offs_are_reported_not_hidden(self):
        """The A/B contract judges the whole quality vector, not just the goal
        the solver was asked to close. Here the repaired participation costs
        some opponent diversity, so the report must not call the result
        dominant; it must name the regression and keep participation clean."""
        baseline = _under_target_baseline()
        problem = _under_target_problem()

        optimized = optimize_candidate_cp_sat(baseline, problem, solve_budget_seconds=5.0, seed=1)
        report = build_ab_report(baseline, optimized, problem)

        assert report["hard_constraint_regressed"] is False
        assert report["new"]["verification"]["ok"] is True
        old_participation = report["old"]["score"]["participation"]
        new_participation = report["new"]["score"]["participation"]
        assert new_participation["season_total_absolute_deviation"] < old_participation[
            "season_total_absolute_deviation"
        ]
        regressions = report["overall_comparison"]["regressions"]
        assert not [name for name in regressions if name.startswith("participation.")]
        assert report["dominates_baseline"] is (not regressions and not report["per_age_group_regressions"])

    def test_ab_participation_agrees_with_recorded_solver_diagnostics(self):
        baseline, problem = _two_plus_four_candidate()

        optimized = optimize_candidate_cp_sat(
            baseline, problem, solve_budget_seconds=5.0, seed=1, decompose_by_half=True
        )
        report = build_ab_report(baseline, optimized, problem)
        recorded = optimized["source"]["participation_verification"]

        for metric in PARITY_METRICS:
            assert report["new"]["score"]["participation"][metric] == recorded["optimized"][metric]
            assert report["old"]["score"]["participation"][metric] == recorded["baseline"][metric]
        assert report["dominates_baseline"] is True

    def test_repeated_solves_record_identical_verified_deltas(self):
        baseline = _under_target_baseline()
        problem = _under_target_problem()

        first = optimize_candidate_cp_sat(baseline, problem, solve_budget_seconds=5.0, seed=1)
        second = optimize_candidate_cp_sat(baseline, problem, solve_budget_seconds=5.0, seed=1)

        assert first["source"]["participation_verification"] == second["source"]["participation_verification"]
