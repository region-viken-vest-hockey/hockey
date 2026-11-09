"""Bounded CP-SAT maintenance path: problem projection, solver contract, repair family.

Covers the ownership split in ADR 0008: the domain projects locks/pins/
cancellations into a solver-neutral ``MaintenanceProblem``; CP-SAT searches
inside it and returns revision-bound patches; the independent verifier stays
authoritative; and the participation-deviation family is served end to end by
that path with no second bespoke search.
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("ortools", reason="OR-Tools is the optional 'cpsat' extra")

from tournament_scheduler.participation.deviation_repair import (
    apply_participation_deviation_repair_option,
    enumerate_participation_deviation_repairs,
)
from tournament_scheduler.planning.contract import candidate_content_fingerprint, verify_candidate
from tournament_scheduler.stage3 import cpsat_maintenance
from tournament_scheduler.stage3.cpsat import CpSatNoCandidate, CpSatUnavailable
from tournament_scheduler.stage3.cpsat_maintenance import solve_maintenance_problem
from tournament_scheduler.stage3.maintenance_problem import (
    MaintenanceScopeError,
    StaleMaintenanceBaseline,
    apply_patches,
    build_participant_maintenance_problem,
)
from tournament_scheduler.stage3.optimizer import optimize_candidate

U11 = "U11"
CLUBS = ["A", "B", "C", "D", "E"]
DATES = ["2025-09-06", "2025-10-04", "2025-11-01", "2025-11-29", "2025-12-06"]
OMITTED = ["E", "E", "E", "A", "B"]


def _team(club: str, age_group: str = U11) -> dict[str, Any]:
    return {"club": club, "label": f"{club} 1", "age_group": age_group}


def _tournament(t_id: str, date: str, teams: list[dict[str, Any]], age_group: str = U11) -> dict[str, Any]:
    pairs = [(a["label"], b["label"]) for i, a in enumerate(teams) for b in teams[i + 1 :]]
    return {
        "id": t_id,
        "date": date,
        "arena": "Arena",
        "age_group": age_group,
        "host_club": teams[0]["club"],
        "start_time": "10:00",
        "teams": teams,
        "games": [{"home": h, "away": a, "parallel_slot": 0, "round_number": 1} for h, a in pairs],
    }


def _problem(teams: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    problem: dict[str, Any] = {
        "start_date": "2025-09-01",
        "end_date": "2026-06-30",
        "teams": [dict(t, target_tournament_count=None) for t in teams],
        "parallel_games": {},
        "participation_targets_by_age_group": {U11: {"before_christmas": 4, "after_christmas": 0}},
    }
    problem.update(extra)
    return problem


def _scenario(**problem_extra: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Five U11 teams, 4 per tournament; E is starved (2/4) while C and D are over (5/4)."""
    teams = [_team(club) for club in CLUBS]
    tournaments = [
        _tournament(f"t{i}", date, [t for t in teams if t["club"] != omitted])
        for i, (date, omitted) in enumerate(zip(DATES, OMITTED))
    ]
    return {"tournaments": tournaments}, _problem(teams, **problem_extra)


def _cand(result: Any) -> dict[str, Any]:
    assert result.candidate is not None
    return result.candidate


def _roster(candidate: dict[str, Any], tournament_id: str) -> list[str]:
    tournament = next(t for t in candidate["tournaments"] if t["id"] == tournament_id)
    return sorted(team["club"] for team in tournament["teams"])


def _deviation_total(candidate: dict[str, Any], problem: dict[str, Any]) -> int:
    return sum(
        abs(int(d.get("deviation", 0)))
        for d in verify_candidate(candidate, problem).get("participation_deviations", [])
    )


# ---------------------------------------------------------------------------
# MaintenanceProblem projection (#716)
# ---------------------------------------------------------------------------


def test_problem_is_deterministic_and_bound_to_the_baseline():
    candidate, problem = _scenario()
    first = build_participant_maintenance_problem(candidate, problem, age_groups=[U11])
    second = build_participant_maintenance_problem(copy.deepcopy(candidate), copy.deepcopy(problem), age_groups=[U11])
    assert first == second
    assert first.fingerprint() == second.fingerprint()
    assert first.baseline_fingerprint == candidate_content_fingerprint(candidate)
    assert first.mutable_tournament_ids == ("t0", "t1", "t2", "t3", "t4")
    assert first.mutable_dimensions == ("participants",)
    # A different budget or seed is a different question.
    assert (
        first.fingerprint()
        != build_participant_maintenance_problem(candidate, problem, age_groups=[U11], seed=1).fingerprint()
    )


def test_problem_requires_an_age_group():
    candidate, problem = _scenario()
    with pytest.raises(MaintenanceScopeError):
        build_participant_maintenance_problem(candidate, problem, age_groups=[])


def test_participant_lock_fixes_roster_but_placement_only_lock_does_not():
    candidate, problem = _scenario(
        canonical_baseline={
            "locks": {
                "t0": {"placement": True, "participants": False},
                "t1": {"placement": True, "participants": True},
            }
        },
        # The planning-problem contract folds every canonical lock into pins.
        manual_adjustments={"pinned_tournament_ids": ["t0", "t1"]},
    )
    built = build_participant_maintenance_problem(candidate, problem, age_groups=[U11])
    assert "t0" in built.mutable_tournament_ids, "placement-only approval must not freeze participants"
    assert "t1" not in built.mutable_tournament_ids
    assert built.fixed_reasons == {"t1": "participants_locked"}
    assert built.fixed_participants["t1"] == tuple(f"{c}:{c} 1:{U11}" for c in CLUBS if c != "E")


def test_operator_pin_and_requested_scope_fix_rosters():
    candidate, problem = _scenario(manual_adjustments={"pinned_tournament_ids": ["t2"]})
    built = build_participant_maintenance_problem(
        candidate, problem, age_groups=[U11], tournament_ids=["t0", "t2", "t4"]
    )
    assert built.mutable_tournament_ids == ("t0", "t4")
    assert built.fixed_reasons == {
        "t1": "outside_requested_scope",
        "t2": "operator_pinned",
        "t3": "outside_requested_scope",
    }


def test_cancelled_tournaments_are_excluded_not_searched():
    candidate, problem = _scenario()
    candidate["tournaments"][3]["cancelled"] = True
    built = build_participant_maintenance_problem(candidate, problem, age_groups=[U11])
    assert "t3" not in built.mutable_tournament_ids
    assert built.excluded_tournament_ids == {"t3": "cancelled"}


def test_other_age_groups_are_not_part_of_the_problem():
    candidate, problem = _scenario()
    candidate["tournaments"].append(_tournament("u9", "2025-10-11", [_team(c, "U9") for c in "ABCD"], "U9"))
    built = build_participant_maintenance_problem(candidate, problem, age_groups=[U11])
    assert "u9" not in built.mutable_tournament_ids
    assert set(built.eligible_values) == {U11}


# ---------------------------------------------------------------------------
# Solver contract (#716)
# ---------------------------------------------------------------------------


def test_solve_returns_a_verified_revision_bound_patch_that_resolves_the_deviation():
    candidate, problem = _scenario()
    before = _deviation_total(candidate, problem)
    assert before > 0
    built = build_participant_maintenance_problem(candidate, problem, age_groups=[U11])
    solved = solve_maintenance_problem(built, candidate, problem)

    assert solved.status == "solved" and solved.has_candidate
    assert solved.baseline_fingerprint == candidate_content_fingerprint(candidate)
    assert solved.candidate_fingerprint == candidate_content_fingerprint(_cand(solved))
    assert solved.problem_fingerprint == built.fingerprint()
    assert verify_candidate(_cand(solved), problem)["ok"] is True
    assert _deviation_total(_cand(solved), problem) < before
    # Lexicographic: the participation tier was solved first and reached zero.
    tiers = solved.diagnostics["half_diagnostics"][0]["tier_values"]
    assert tiers["participation"] == 0.0
    assert list(tiers) == ["participation", "opponent_variety", "sibling_and_spacing", "minimal_change"]


def test_solve_does_not_mutate_inputs_and_patches_only_changed_mutable_rosters():
    candidate, problem = _scenario()
    snapshot = copy.deepcopy(candidate)
    built = build_participant_maintenance_problem(candidate, problem, age_groups=[U11])
    solved = solve_maintenance_problem(built, candidate, problem)
    assert candidate == snapshot
    assert {p["tournament_id"] for p in solved.patches} <= set(built.mutable_tournament_ids)
    for tournament in candidate["tournaments"]:
        if tournament["id"] not in {p["tournament_id"] for p in solved.patches}:
            unchanged = next(t for t in _cand(solved)["tournaments"] if t["id"] == tournament["id"])
            assert unchanged == tournament, "untouched tournaments must be byte-equal to baseline"
    assert apply_patches(candidate, solved.patches) == _cand(solved)


def test_out_of_scope_state_is_frozen_exactly():
    candidate, problem = _scenario(
        canonical_baseline={"locks": {"t0": {"placement": False, "participants": True}}},
        manual_adjustments={"pinned_tournament_ids": ["t0"]},
    )
    built = build_participant_maintenance_problem(candidate, problem, age_groups=[U11])
    solved = solve_maintenance_problem(built, candidate, problem)
    assert solved.status == "solved"
    locked_before = next(t for t in candidate["tournaments"] if t["id"] == "t0")
    locked_after = next(t for t in _cand(solved)["tournaments"] if t["id"] == "t0")
    assert locked_after == locked_before
    for after, before in zip(_cand(solved)["tournaments"], candidate["tournaments"]):
        for key in ("date", "arena", "host_club", "start_time", "age_group"):
            assert after[key] == before[key], "participant maintenance must never move placement"


def test_placement_only_approval_still_lets_participants_change():
    candidate, problem = _scenario(
        canonical_baseline={"locks": {tid: {"placement": True, "participants": False} for tid in ("t0", "t1", "t2")}},
        manual_adjustments={"pinned_tournament_ids": ["t0", "t1", "t2"]},
    )
    built = build_participant_maintenance_problem(candidate, problem, age_groups=[U11])
    solved = solve_maintenance_problem(built, candidate, problem)
    assert solved.status == "solved"
    assert verify_candidate(_cand(solved), problem)["ok"] is True


def test_stale_baseline_is_rejected_before_any_solve():
    candidate, problem = _scenario()
    built = build_participant_maintenance_problem(candidate, problem, age_groups=[U11])
    moved = copy.deepcopy(candidate)
    moved["tournaments"][0]["date"] = "2025-09-13"
    with pytest.raises(StaleMaintenanceBaseline):
        solve_maintenance_problem(built, moved, problem)
    changed_problem = copy.deepcopy(problem)
    changed_problem["end_date"] = "2026-05-31"
    with pytest.raises(StaleMaintenanceBaseline):
        solve_maintenance_problem(built, candidate, changed_problem)


def test_everything_locked_reports_no_mutable_scope():
    candidate, problem = _scenario(manual_adjustments={"pinned_tournament_ids": [f"t{i}" for i in range(5)]})
    built = build_participant_maintenance_problem(candidate, problem, age_groups=[U11])
    solved = solve_maintenance_problem(built, candidate, problem)
    assert solved.status == "no_mutable_scope" and not solved.has_candidate
    assert solved.diagnostics["fixed_tournament_count"] == 5


def test_already_optimal_baseline_is_unchanged():
    candidate, problem = _scenario()
    built = build_participant_maintenance_problem(candidate, problem, age_groups=[U11])
    optimal = _cand(solve_maintenance_problem(built, candidate, problem))
    again = build_participant_maintenance_problem(optimal, problem, age_groups=[U11])
    result = solve_maintenance_problem(again, optimal, problem)
    assert result.status == "unchanged" and result.candidate is None and result.patches == ()


def test_unproven_unchanged_incumbent_is_budget_exhausted(monkeypatch):
    candidate, problem = _scenario()
    built = build_participant_maintenance_problem(candidate, problem, age_groups=[U11])

    def feasible_incumbent(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        incumbent = copy.deepcopy(candidate)
        incumbent["source"] = {
            "status": "FEASIBLE",
            "half_diagnostics": [{"status": "FEASIBLE", "lexicographic": True}],
            "lexicographic": True,
        }
        return incumbent

    monkeypatch.setattr(cpsat_maintenance, "optimize_candidate_cp_sat", feasible_incumbent)
    result = solve_maintenance_problem(built, candidate, problem)

    assert result.status == "budget_exhausted"
    assert result.candidate is None and result.patches == ()
    assert result.diagnostics["solver_status"] == "FEASIBLE"


@pytest.mark.parametrize(
    ("solver_status", "expected"),
    [
        ("UNKNOWN", "budget_exhausted"),
        ("INFEASIBLE", "infeasible"),
        ("INFEASIBLE_NO_BYE_ROSTER_SIZE", "infeasible"),
        ("BASELINE_CONSTRAINT_CONFLICT", "infeasible"),
        ("MODEL_INVALID", "solver_error"),
    ],
)
def test_budget_exhaustion_is_distinct_from_proven_infeasibility(monkeypatch, solver_status, expected):
    candidate, problem = _scenario()
    built = build_participant_maintenance_problem(candidate, problem, age_groups=[U11])

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise CpSatNoCandidate(solver_status, 0.1, diagnostics={"half": "unsplit"})

    monkeypatch.setattr(cpsat_maintenance, "optimize_candidate_cp_sat", boom)
    result = solve_maintenance_problem(built, candidate, problem)
    assert result.status == expected
    assert result.diagnostics["solver_status"] == solver_status
    assert result.candidate is None


def test_missing_backend_is_reported_not_raised(monkeypatch):
    candidate, problem = _scenario()
    built = build_participant_maintenance_problem(candidate, problem, age_groups=[U11])

    def missing(*_args: Any, **_kwargs: Any) -> Any:
        raise CpSatUnavailable("OR-Tools is required")

    monkeypatch.setattr(cpsat_maintenance, "optimize_candidate_cp_sat", missing)
    assert solve_maintenance_problem(built, candidate, problem).status == "unavailable"


def test_lexicographic_never_trades_a_higher_tier_for_a_lower_one():
    """With the default weighted sum a lower tier may in principle outbid a
    higher one; the staged solve fixes each tier's optimum before the next."""
    from tournament_scheduler.stage3.cpsat import optimize_candidate_cp_sat

    candidate, problem = _scenario()
    staged = optimize_candidate_cp_sat(candidate, problem, lexicographic=True, decompose_by_half=True)
    weighted = optimize_candidate_cp_sat(candidate, problem, decompose_by_half=True)
    assert staged["source"]["lexicographic"] is True
    assert weighted["source"]["lexicographic"] is False
    assert _deviation_total(staged, problem) <= _deviation_total(weighted, problem)


def test_season_hard_max_uses_one_model_without_losing_half_targets():
    clubs = [*CLUBS, "F"]
    teams = [_team(club) for club in clubs]
    by_club = {team["club"]: team for team in teams}
    dates = ["2025-09-06", "2025-11-01", "2026-01-10", "2026-03-07"]
    baseline_rosters = [
        ("A", "B", "E", "F"),
        ("B", "A", "E", "F"),
        ("C", "A", "B", "D"),
        ("D", "A", "B", "C"),
    ]
    tournaments = [
        _tournament(f"t{i}", on_date, [by_club[club] for club in roster])
        for i, (on_date, roster) in enumerate(zip(dates, baseline_rosters))
    ]
    candidate = {"tournaments": tournaments}
    problem = _problem(
        teams,
        christmas_split_date="2025-12-24",
        participation_hard_max=4,
        participation_targets_by_age_group={U11: {"before_christmas": 1, "after_christmas": 1}},
    )
    problem["teams"] = [dict(team, target_tournament_count=4 if team["club"] in {"A", "B"} else 2) for team in teams]
    built = build_participant_maintenance_problem(candidate, problem, age_groups=[U11])

    solved = solve_maintenance_problem(built, candidate, problem)
    result = _cand(solved)
    before = [tournament["id"] for tournament in tournaments[:2]]
    after = [tournament["id"] for tournament in tournaments[2:]]

    assert solved.status == "solved"
    assert solved.diagnostics["half_diagnostics"][0]["half"] == "combined"
    for club in clubs:
        half_target = 2 if club in {"A", "B"} else 1
        assert sum(club in _roster(result, tournament_id) for tournament_id in before) == half_target
        assert sum(club in _roster(result, tournament_id) for tournament_id in after) == half_target
    assert verify_candidate(result, problem)["ok"] is True


# ---------------------------------------------------------------------------
# Participation-deviation family served by the generic path (#717)
# ---------------------------------------------------------------------------


def _starved_scope() -> dict[str, str]:
    return {"team": "E 1", "club": "E", "age_group": U11, "scope": "before_christmas"}


def test_family_offers_a_cpsat_option_for_a_starved_team_and_applies_it():
    candidate, problem = _scenario()
    fingerprint = candidate_content_fingerprint(candidate)
    repair = enumerate_participation_deviation_repairs(candidate, problem, allow_search=True, scope=_starved_scope())
    assert repair["options"], repair["rejected_candidates"]
    option = repair["options"][0]
    assert option["arguments"]["engine"] == "cp_sat"
    assert option["arguments"]["dimensions"] == ["participants"]
    assert option["hard_feasible"] is True
    assert option["effects"]["deviation_after"] == 0
    assert option["effects"]["deviation_before"] == -2

    applied = apply_participation_deviation_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=fingerprint,
        scope=_starved_scope(),
    )
    assert applied["ok"] is True
    assert applied["after_fingerprint"] == option["evidence"]["result_fingerprint"]
    assert verify_candidate(applied["candidate"], problem)["ok"] is True


def test_apply_replays_the_previewed_custom_search_budget():
    candidate, problem = _scenario()
    repair = enumerate_participation_deviation_repairs(
        candidate,
        problem,
        allow_search=True,
        scope=_starved_scope(),
        search_budget_seconds=2.5,
    )
    option = repair["options"][0]

    assert option["arguments"]["search_budget_seconds"] == 2.5
    applied = apply_participation_deviation_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=repair["candidate_fingerprint"],
        scope=_starved_scope(),
        search_budget_seconds=option["arguments"]["search_budget_seconds"],
    )

    assert applied["ok"] is True
    assert applied["after_fingerprint"] == option["evidence"]["result_fingerprint"]


def test_apply_replays_the_previewed_custom_seed():
    candidate, problem = _scenario()
    repair = enumerate_participation_deviation_repairs(
        candidate,
        problem,
        allow_search=True,
        scope=_starved_scope(),
        seed=17,
    )
    option = repair["options"][0]

    assert option["arguments"]["seed"] == 17
    applied = apply_participation_deviation_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=repair["candidate_fingerprint"],
        scope=_starved_scope(),
        seed=option["arguments"]["seed"],
    )

    assert applied["ok"] is True
    assert applied["after_fingerprint"] == option["evidence"]["result_fingerprint"]


def test_apply_refuses_when_bounded_replay_returns_a_different_candidate(monkeypatch):
    """An apply-time incumbent may differ even with the same seed and model."""
    from dataclasses import replace

    import tournament_scheduler.participation.deviation_repair as family

    candidate, problem = _scenario()
    repair = enumerate_participation_deviation_repairs(candidate, problem, allow_search=True, scope=_starved_scope())
    option = repair["options"][0]
    real_solve = family.solve_maintenance_problem

    def different_incumbent(maintenance: Any, cand: Any, prob: Any) -> Any:
        solved = real_solve(maintenance, cand, prob)
        assert solved.candidate is not None
        changed = copy.deepcopy(solved.candidate)
        changed["tournaments"][0]["replay_marker"] = "different-incumbent"
        return replace(solved, candidate=changed, candidate_fingerprint=candidate_content_fingerprint(changed))

    monkeypatch.setattr(family, "solve_maintenance_problem", different_incumbent)
    applied = apply_participation_deviation_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=repair["candidate_fingerprint"],
        scope=_starved_scope(),
    )

    assert applied["ok"] is False
    assert applied["reason"] == "unknown_or_stale_option"


def test_apply_refuses_a_stale_fingerprint():
    candidate, problem = _scenario()
    repair = enumerate_participation_deviation_repairs(candidate, problem, allow_search=True, scope=_starved_scope())
    applied = apply_participation_deviation_repair_option(
        candidate,
        problem,
        option_id=repair["options"][0]["option_id"],
        expected_fingerprint="0" * 64,
        scope=_starved_scope(),
    )
    assert applied["ok"] is False and applied["reason"] == "stale_candidate_fingerprint"


def test_family_never_widens_into_placement_dimensions():
    candidate, problem = _scenario()
    repair = enumerate_participation_deviation_repairs(
        candidate,
        problem,
        allow_search=True,
        scope=_starved_scope(),
        dimensions=("participants", "host", "date", "slot"),
    )
    option = repair["options"][0]
    assert option["arguments"]["dimensions"] == ["participants"]
    assert option["arguments"]["unserved_dimensions"] == ["date", "host", "slot"]
    result = repair["result_candidates"][option["option_id"]]
    for after, before in zip(result["tournaments"], candidate["tournaments"]):
        for key in ("date", "arena", "host_club", "start_time"):
            assert after[key] == before[key]


def test_family_without_search_request_returns_no_options():
    candidate, problem = _scenario()
    repair = enumerate_participation_deviation_repairs(candidate, problem, scope=_starved_scope())
    assert repair["options"] == []
    assert repair["rejected_candidates"][0]["reason"] == "targeted_search_not_requested"


def test_family_reports_no_mutable_scope_with_the_classification():
    candidate, problem = _scenario(manual_adjustments={"pinned_tournament_ids": [f"t{i}" for i in range(5)]})
    repair = enumerate_participation_deviation_repairs(candidate, problem, allow_search=True, scope=_starved_scope())
    assert repair["options"] == []
    rejected = repair["rejected_candidates"][0]
    assert rejected["reason"] == "maintenance_search_no_mutable_scope"
    assert "proven_infeasible" in rejected and "avoidability" in rejected


def test_hard_invalid_solver_output_is_never_surfaced(monkeypatch):
    """The verifier stays authoritative: a patch that breaks a hard rule is rejected."""
    candidate, problem = _scenario()
    from tournament_scheduler.participation import deviation_repair

    real = deviation_repair.solve_maintenance_problem

    def corrupt(maintenance: Any, cand: Any, prob: Any) -> Any:
        result = real(maintenance, cand, prob)
        bad = copy.deepcopy(_cand(result))
        first = bad["tournaments"][0]
        first["teams"] = first["teams"] + [first["teams"][0]]  # duplicate team in one tournament
        from dataclasses import replace

        return replace(result, candidate=bad)

    monkeypatch.setattr(deviation_repair, "solve_maintenance_problem", corrupt)
    repair = enumerate_participation_deviation_repairs(candidate, problem, allow_search=True, scope=_starved_scope())
    assert repair["options"] == []
    assert repair["rejected_candidates"][0]["reason"] == "search_candidate_not_hard_valid"


def test_differential_cpsat_is_equal_or_better_than_the_local_search_optimizer():
    """The retired participant local-search loop was `optimize_candidate`
    restricted to participant moves. CP-SAT must find an equal-or-better
    verified outcome than every seed of it on the same fixture."""
    candidate, problem = _scenario()
    built = build_participant_maintenance_problem(candidate, problem, age_groups=[U11])
    cpsat_total = _deviation_total(_cand(solve_maintenance_problem(built, candidate, problem)), problem)
    verified = 0
    for seed in (0, 1, 2):
        legacy = optimize_candidate(
            copy.deepcopy(candidate),
            copy.deepcopy(problem),
            iterations=800,
            seed=seed,
            move_hosts=False,
            move_dates=False,
            move_slots=False,
        )
        if not verify_candidate(legacy, problem)["ok"]:
            continue
        verified += 1
        assert cpsat_total <= _deviation_total(legacy, problem)
    assert verified, "differential fixture must have at least one verified legacy outcome"


# ---------------------------------------------------------------------------
# One default path for combinatorial participant maintenance (#718)
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1] / "tournament_scheduler"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_participation_deviation_family_has_no_second_search_implementation():
    imports = _imports(_REPO / "participation" / "deviation_repair.py")
    assert "tournament_scheduler.stage3.optimizer" not in imports
    assert not any(name.endswith("optimize_candidate") for name in imports)
    assert "tournament_scheduler.stage3.cpsat_maintenance" in imports


def test_maintenance_modules_stay_solver_neutral_where_required():
    """Only the CP-SAT bridge and model may import OR-Tools or the CP-SAT model."""
    problem_imports = _imports(_REPO / "stage3" / "maintenance_problem.py")
    assert not any(name.startswith("ortools") for name in problem_imports)
    assert "tournament_scheduler.stage3.cpsat" not in problem_imports
    assert not any(name.endswith("verify_candidate") for name in _imports(_REPO / "stage3" / "cpsat_maintenance.py")), (
        "the solver bridge must not decide legality; verification stays with callers"
    )


# ---------------------------------------------------------------------------
# Reproducibility: a previewed repair must be the one that is applied
# ---------------------------------------------------------------------------


def test_solve_is_identical_across_processes_with_different_hash_seeds():
    """Equally optimal solutions must not depend on per-process string hashing:
    apply re-enumerates in a new process and has to reproduce the preview."""
    import json
    import os
    import subprocess
    import sys

    code = (
        "import json\n"
        "from tests.test_cpsat_maintenance import _scenario, U11\n"
        "from tournament_scheduler.stage3.maintenance_problem import build_participant_maintenance_problem\n"
        "from tournament_scheduler.stage3.cpsat_maintenance import solve_maintenance_problem\n"
        "c, p = _scenario()\n"
        "r = solve_maintenance_problem(build_participant_maintenance_problem(c, p, age_groups=[U11]), c, p)\n"
        "print(json.dumps([r.status, r.candidate_fingerprint]))\n"
    )
    root = Path(__file__).resolve().parents[1]
    outputs = set()
    for hash_seed in ("1", "2", "3", "4"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": str(root)}
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, cwd=root, env=env, check=True
        )
        outputs.add(json.dumps(json.loads(done.stdout.strip().splitlines()[-1])))
    assert len(outputs) == 1, outputs
