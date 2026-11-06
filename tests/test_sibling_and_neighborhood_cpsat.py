"""Sibling-only and neighborhood repair families served by the bounded CP-SAT path."""

from __future__ import annotations

import ast
import copy
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("ortools", reason="OR-Tools is the optional 'cpsat' extra")

from tests import test_intra_club_distribution as intra
from tournament_scheduler.planning.contract import verify_candidate
from tournament_scheduler.repair.intra_club_distribution import enumerate_intra_club_distribution_repairs
from tournament_scheduler.repair.search_neighborhood import (
    apply_search_neighborhood_repair_option,
    enumerate_search_neighborhood_repairs,
)
from tournament_scheduler.repair.sibling_search import solve_sibling_swaps
from tournament_scheduler.stage3.cpsat_maintenance import solve_maintenance_problem
from tournament_scheduler.stage3.maintenance_problem import build_participant_maintenance_problem

REPO = Path(__file__).resolve().parents[1] / "tournament_scheduler"
SCOPE = {"club": "Jar", "age_group": "U10"}
ALL_IDS = ["B1", "B2", "B3", "B4", "A1", "A2", "A3", "A4"]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


# ---------------------------------------------------------------------------
# Sibling-only scope
# ---------------------------------------------------------------------------


def test_sibling_plan_only_substitutes_siblings_inside_the_requested_tournaments():
    plan, problem = intra._plan(), intra._problem()
    found = solve_sibling_swaps(plan, problem, club="Jar", age_group="U10", tournament_ids=["B1", "B2", "B3", "B4"])
    assert found.status == "solved", found.diagnostics
    assert {s["tournament_id"] for s in found.swaps} <= {"B1", "B2", "B3", "B4"}
    for swap in found.swaps:
        assert swap["remove"]["club"] == swap["add"]["club"] == "Jar"


def test_sibling_plan_never_touches_non_siblings_or_placement():
    plan, problem = intra._plan(), intra._problem()
    maintenance = build_participant_maintenance_problem(
        plan,
        problem,
        age_groups=["U10"],
        tournament_ids=ALL_IDS,
        movable_teams=["Jar:Jar 1:U10", "Jar:Jar 2:U10"],
    )
    solved = solve_maintenance_problem(maintenance, plan, problem)
    assert solved.status == "solved"
    assert solved.candidate is not None
    for before, after in zip(plan["tournaments"], solved.candidate["tournaments"]):
        fillers = lambda t: sorted(team["label"] for team in t["teams"] if team["club"] != "Jar")  # noqa: E731
        assert fillers(before) == fillers(after), "non-sibling rosters must be frozen"
        for key in ("date", "arena", "host_club", "start_time"):
            assert before[key] == after[key]
        assert len([t for t in before["teams"] if t["club"] == "Jar"]) == len(
            [t for t in after["teams"] if t["club"] == "Jar"]
        )


def test_sibling_scope_is_part_of_the_problem_identity():
    plan, problem = intra._plan(), intra._problem()
    base = build_participant_maintenance_problem(plan, problem, age_groups=["U10"])
    scoped = build_participant_maintenance_problem(
        plan, problem, age_groups=["U10"], movable_teams=["Jar:Jar 1:U10", "Jar:Jar 2:U10"]
    )
    homed = build_participant_maintenance_problem(
        plan, problem, age_groups=["U10"], home_balance={"club": "Jar", "age_group": "U10"}
    )
    assert len({base.fingerprint(), scoped.fingerprint(), homed.fingerprint()}) == 3


def test_intra_club_widening_runs_through_the_maintenance_path_only_when_search_is_allowed(monkeypatch):
    import tournament_scheduler.repair.intra_club_distribution as module

    calls: list[dict[str, Any]] = []
    real = module.solve_sibling_swaps

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "solve_sibling_swaps", spy)
    plan, problem = intra._plan(), intra._problem()
    direct = enumerate_intra_club_distribution_repairs(plan, problem, allow_search=False, scope=SCOPE)
    assert calls == [], "the direct pass must not invoke a solver"
    searched = enumerate_intra_club_distribution_repairs(plan, problem, allow_search=True, scope=SCOPE)
    assert calls and all(c["club"] == "Jar" and c["age_group"] == "U10" for c in calls)
    assert {o["option_id"] for o in direct["options"]} <= {o["option_id"] for o in searched["options"]}
    for option in searched["options"]:
        assert option["hard_feasible"] is True
        assert option["effects"]["consequence_acceptable"] is True
        assert option["evidence"]["forbidden_dimension_moved"] is False


# ---------------------------------------------------------------------------
# search_neighborhood: participants first, local search only to widen into placement
# ---------------------------------------------------------------------------


def _host_missing_scenario() -> tuple[dict[str, Any], dict[str, Any]]:
    plan, problem = intra._plan(), intra._problem()
    broken = copy.deepcopy(plan)
    # B1 is hosted by F1 but F1 is not on the roster (F12 stands in): a
    # host_team_missing hard finding that only a roster change can repair.
    index = next(i for i, t in enumerate(broken["tournaments"]) if t["id"] == "B1")
    broken["tournaments"][index] = intra._tournament(
        "B1", "2026-09-12", "F1", ["Jar 1", "F12 1", "F2 1", "F3 1"], intra._teams()
    )
    return broken, problem


def test_neighborhood_participant_finding_is_served_by_cpsat_and_applies():
    broken, problem = _host_missing_scenario()
    codes = {v["code"] for v in verify_candidate(broken, problem)["violations"]}
    if "host_team_missing" not in codes:
        pytest.skip(f"fixture did not produce host_team_missing: {sorted(codes)}")
    repair = enumerate_search_neighborhood_repairs(broken, problem, dimensions=("participants",))
    assert repair["options"], repair["rejected_candidates"]
    option = repair["options"][0]
    assert option["arguments"]["engine"] == "cp_sat"
    assert option["arguments"]["dimensions"] == ["participants"]
    assert option["effects"]["hard_violations"] < 0
    result = repair["result_candidates"][option["option_id"]]
    assert verify_candidate(result, problem)["ok"] is True
    for before, after in zip(broken["tournaments"], result["tournaments"]):
        for key in ("date", "arena", "host_club", "start_time"):
            assert before[key] == after[key]
    applied = apply_search_neighborhood_repair_option(
        broken,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=repair["candidate_fingerprint"],
        dimensions=("participants",),
    )
    assert applied["ok"] is True


def test_participants_only_request_never_falls_back_to_local_search(monkeypatch):
    broken, problem = _host_missing_scenario()
    import tournament_scheduler.repair.search_neighborhood as module

    def forbidden(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("local search must not run for a participants-only request")

    monkeypatch.setattr(module, "optimize_candidate", forbidden)
    repair = enumerate_search_neighborhood_repairs(broken, problem, dimensions=("participants",))
    assert repair["options"] and repair["options"][0]["arguments"]["engine"] == "cp_sat"


def test_cpsat_result_that_transfers_hosting_responsibility_is_rejected(monkeypatch):
    """The responsibility guard applies to the CP-SAT path exactly as to local search."""
    from dataclasses import replace

    import tournament_scheduler.repair.search_neighborhood_cpsat as bridge

    broken, problem = _host_missing_scenario()
    real = bridge.solve_maintenance_problem

    def transferring(maintenance: Any, cand: Any, prob: Any) -> Any:
        solved = real(maintenance, cand, prob)
        assert solved.candidate is not None
        moved = copy.deepcopy(solved.candidate)
        for tournament in moved["tournaments"]:
            tournament["host_club"] = "F12"
        return replace(solved, candidate=moved)

    monkeypatch.setattr(bridge, "solve_maintenance_problem", transferring)
    monkeypatch.setattr(bridge, "verify_canonical_candidate", lambda *_a, **_k: {"ok": True, "violations": []})
    repair = enumerate_search_neighborhood_repairs(broken, problem, dimensions=("participants",))
    assert repair["options"] == []
    assert any("responsibility" in str(r.get("reason")) for r in repair["rejected_candidates"]), repair[
        "rejected_candidates"
    ]


def test_baseline_that_already_double_books_a_team_can_be_repaired():
    """duplicate_participation_same_date is the finding being repaired, so the
    solve must not refuse its own starting point."""
    plan, problem = intra._plan(), intra._problem()
    broken = copy.deepcopy(plan)
    index = next(i for i, t in enumerate(broken["tournaments"]) if t["id"] == "B4")
    # Jar 1 now plays B3 and B4 on the same day.
    broken["tournaments"][index] = intra._tournament(
        "B4", "2026-10-10", "F10", ["Jar 1", "F10 1", "F11 1", "F12 1"], intra._teams()
    )
    codes = {v["code"] for v in verify_candidate(broken, problem)["violations"]}
    assert "duplicate_participation_same_date" in codes, sorted(codes)
    repair = enumerate_search_neighborhood_repairs(
        broken, problem, scope={"age_group": "U10"}, dimensions=("participants",)
    )
    assert repair["options"], repair["rejected_candidates"]
    result = repair["result_candidates"][repair["options"][0]["option_id"]]
    assert verify_candidate(result, problem)["ok"] is True


# ---------------------------------------------------------------------------
# Architecture guards: one default path for participant maintenance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", ["intra_club_distribution", "home_representation"])
def test_sibling_families_have_no_private_coupled_search(module):
    source = (REPO / "repair" / f"{module}.py").read_text(encoding="utf-8")
    assert "_coupled_options" not in source and "_cycle_moves" not in source and "_diverse_units" not in source
    assert "tournament_scheduler.repair.sibling_search" in _imports(REPO / "repair" / f"{module}.py")
    assert "tournament_scheduler.stage3.optimizer" not in _imports(REPO / "repair" / f"{module}.py")


def test_only_placement_widening_still_imports_the_local_search_optimizer():
    users = sorted(
        path.relative_to(REPO).as_posix()
        for path in REPO.rglob("*.py")
        if "tournament_scheduler.stage3.optimizer.optimize_candidate" in _imports(path)
        and path.parent.name in {"repair", "hosting", "participation"}
    )
    assert users == ["hosting/balance_repair.py", "repair/search_neighborhood.py"]
