"""Bounded cross-age coupled placement + roster repair tests.

These cover the planner-neutral candidate generator and its integration with
the existing ``season findings`` / ``repair-options`` / ``search`` /
``apply-repair`` boundary. No dedicated apply command is exercised: every
option is committed through the ordinary canonical candidate apply path.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pytest

from tournament_scheduler.coupled_placement_repair import (
    CoupledPlacementRepairError,
    apply_placement_swap,
    enumerate_coupled_placement_repairs,
    resolve_swap_fields,
)
from tournament_scheduler.planning_contract import build_planning_problem, verify_candidate
from tournament_scheduler.season_maintenance import (
    apply_repair,
    list_findings,
    repair_options,
    search,
)
from tournament_scheduler.season_state import load_schedule

YEAR = "2026-2027"


def _four_team_games(labels: List[str]) -> List[Dict[str, Any]]:
    return [
        {"home": labels[0], "away": labels[1], "parallel_slot": 0, "round_number": 1},
        {"home": labels[2], "away": labels[3], "parallel_slot": 1, "round_number": 1},
        {"home": labels[0], "away": labels[2], "parallel_slot": 0, "round_number": 2},
        {"home": labels[1], "away": labels[3], "parallel_slot": 1, "round_number": 2},
        {"home": labels[0], "away": labels[3], "parallel_slot": 0, "round_number": 3},
        {"home": labels[1], "away": labels[2], "parallel_slot": 1, "round_number": 3},
    ]


def _teams(club: str, label_prefix: str, age_group: str, count: int = 2) -> List[Dict[str, str]]:
    return [
        {"club": club, "label": f"{label_prefix}{index}", "age_group": age_group}
        for index in range(1, count + 1)
    ]


def _problem() -> Dict[str, Any]:
    teams = (
        _teams("Kongsberg", "K9-", "U9")
        + _teams("Solberg", "S9-", "U9")
        + _teams("Tønsberg", "T11-", "U11")
        + _teams("Frisk", "F11-", "U11")
        + _teams("Holmen", "H11-", "U11")
        + _teams("Jar", "J11-", "U11")
    )
    config: Dict[str, Any] = {
        "teams": teams,
        "age_groups": ["U9", "U11"],
        "parallel_games": {"U9": 2, "U11": 2},
        "round_length_minutes": {"U9": 30, "U11": 30},
        "ice_time_minutes": {"U9": 90, "U11": 90},
        "rounds_per_tournament": {"U9": 3, "U11": 3},
    }
    problem = build_planning_problem(config, None, date(2026, 9, 1), date(2027, 4, 30))
    problem["clubs"] = {
        "Kongsberg": "Kongsberg Arena",
        "Solberg": "Solberg Arena",
        "Tønsberg": "Tønsberg Arena",
        "Frisk": "Frisk Arena",
        "Holmen": "Holmen Arena",
        "Jar": "Jar Arena",
    }
    return problem


def _tournament(
    tournament_id: str,
    day: str,
    *,
    age_group: str,
    host: str,
    teams: List[Dict[str, str]],
) -> Dict[str, Any]:
    labels = [team["label"] for team in teams]
    return {
        "id": tournament_id,
        "date": day,
        "arena": f"{host} Arena",
        "age_group": age_group,
        "host_club": host,
        "teams": [dict(team) for team in teams],
        "games": _four_team_games(labels),
        "start_time": "10:00",
    }


def _coupled_plan() -> Dict[str, Any]:
    """Kongsberg U9 cluster plus a cross-age partner whose roster double-books."""

    u9 = _teams("Kongsberg", "K9-", "U9") + _teams("Solberg", "S9-", "U9")
    u11_pair = _teams("Tønsberg", "T11-", "U11") + _teams("Frisk", "F11-", "U11")
    u11_other = [
        {"club": "Tønsberg", "label": "T11-1", "age_group": "U11"},
        {"club": "Holmen", "label": "H11-1", "age_group": "U11"},
        {"club": "Holmen", "label": "H11-2", "age_group": "U11"},
        {"club": "Jar", "label": "J11-1", "age_group": "U11"},
    ]
    return {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [
            _tournament("rvv-0155", "2027-02-20", age_group="U9", host="Kongsberg", teams=u9),
            _tournament("rvv-0156", "2027-02-21", age_group="U9", host="Kongsberg", teams=u9),
            _tournament("rvv-0157", "2027-02-28", age_group="U9", host="Kongsberg", teams=u9),
            _tournament("rvv-0172", "2027-03-14", age_group="U11", host="Tønsberg", teams=u11_pair),
            _tournament("rvv-0190", "2027-02-21", age_group="U11", host="Holmen", teams=u11_other),
        ],
    }


def _write_season(root: Path, plan: Dict[str, Any], problem: Dict[str, Any]) -> None:
    season_dir = root / YEAR
    season_dir.mkdir(parents=True, exist_ok=True)
    revision = json.dumps(plan, sort_keys=True, ensure_ascii=False)
    schedule = {
        "schema_version": 1,
        "season": YEAR,
        "revision": revision,
        "fingerprint": revision,
        "plan_schema_version": 1,
        "plan": plan,
        "verification_context": {"problem": problem},
    }
    decisions = {
        "schema_version": 1,
        "season": YEAR,
        "schedule_fingerprint": revision,
        "decisions": {
            tournament["id"]: {
                "status": "pending_review",
                "placement_locked": False,
                "participants_locked": False,
                "approved_fingerprint": None,
            }
            for tournament in plan["tournaments"]
        },
    }
    (season_dir / "schedule.json").write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (season_dir / "decisions.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _season(tmp_path: Path):
    root = tmp_path / "season"
    plan = _coupled_plan()
    problem = _problem()
    _write_season(root, plan, problem)
    return root, plan, problem


# -- domain mutation ---------------------------------------------------------


def test_resolve_swap_fields_defaults_and_rejects_partial_arena_move() -> None:
    assert resolve_swap_fields(None) == ("date", "start_time")
    assert resolve_swap_fields(["start_time", "date"]) == ("date", "start_time")
    with pytest.raises(CoupledPlacementRepairError):
        resolve_swap_fields(["arena"])
    with pytest.raises(CoupledPlacementRepairError):
        resolve_swap_fields(["nope"])


def test_apply_placement_swap_preserves_identity_age_and_roster() -> None:
    plan = _coupled_plan()
    swapped = apply_placement_swap(plan, "rvv-0156", "rvv-0172")
    by_id = {t["id"]: t for t in swapped["tournaments"]}
    a, b = by_id["rvv-0156"], by_id["rvv-0172"]
    assert a["date"] == "2027-03-14"
    assert b["date"] == "2027-02-21"
    assert a["age_group"] == "U9" and b["age_group"] == "U11"
    assert a["teams"] == plan["tournaments"][1]["teams"]
    assert b["teams"] == plan["tournaments"][3]["teams"]
    assert plan["tournaments"][1]["date"] == "2027-02-21"


# -- coupled search ----------------------------------------------------------


def test_placement_only_conflict_is_repaired_by_same_age_roster_rotation() -> None:
    plan = _coupled_plan()
    problem = _problem()

    placement_only = apply_placement_swap(plan, "rvv-0156", "rvv-0172")
    verification = verify_candidate(placement_only, problem)
    assert verification["ok"] is False
    assert {v["code"] for v in verification["violations"]} == {
        "duplicate_participation_same_date"
    }

    repair_set = enumerate_coupled_placement_repairs(
        plan,
        problem,
        target_tournament_ids=["rvv-0156"],
        focus_team=("Kongsberg", "K9-1", "U9"),
        finding_id="temporal_clustering:Kongsberg:K9-1:U9",
    )
    options = repair_set["options"]
    coupled = [
        option
        for option in options
        if option["effects"]["swapped_tournament_ids"] == ["rvv-0156", "rvv-0172"]
    ]
    assert coupled, [option["effects"]["swapped_tournament_ids"] for option in options]
    chosen = coupled[0]
    assert chosen["effects"]["placement_only_verified"] is False
    assert chosen["effects"]["roster_repair_applied"] is True
    assert chosen["effects"]["roster_repair_tournament_ids"] == ["rvv-0172"]
    assert chosen["effects"]["participant_replacements"] == 1
    hosting = chosen["evidence"]["hosting"]
    assert hosting["preserved"] is True
    assert set(hosting["age_groups"]) == {"U9", "U11"}

    # The applied roster change is same-age and keeps host representation.
    arguments = chosen["arguments"]
    roster = arguments["roster_changes"]["rvv-0172"]["teams"]
    assert {team["age_group"] for team in roster} == {"U11"}
    assert "T11-1" not in {team["label"] for team in roster}
    assert "J11-2" in {team["label"] for team in roster}
    assert any(team["club"] == "Tønsberg" for team in roster)

    # The provider does not silently drop the placement-only failure: the
    # rejection evidence shows roster repair was actually attempted.
    assert all(
        rejection.get("reason") != "placement_conflict_not_roster_repairable"
        for rejection in repair_set["rejected_candidates"]
        if rejection.get("tournament_ids") == ["rvv-0156", "rvv-0172"]
    )


def test_provider_rejects_a_candidate_that_moves_hosting_responsibility(monkeypatch) -> None:
    import tournament_scheduler.coupled_placement_repair as module

    plan = _coupled_plan()
    problem = _problem()

    def _always_transfer(before, after, problem_):
        return [{"code": "unexplained_hosting_responsibility_transfer", "message": "transfer"}]

    monkeypatch.setattr(module, "unexplained_responsibility_transfers", _always_transfer)
    repair_set = enumerate_coupled_placement_repairs(
        plan,
        problem,
        target_tournament_ids=["rvv-0156"],
        finding_id="temporal_clustering:Kongsberg:K9-1:U9",
    )
    assert repair_set["options"] == []
    assert all(
        rejection.get("reason") == "unexplained_hosting_responsibility_transfer"
        for rejection in repair_set["rejected_candidates"]
    )


def test_no_target_tournament_yields_no_options() -> None:
    plan = _coupled_plan()
    problem = _problem()
    repair_set = enumerate_coupled_placement_repairs(plan, problem)
    assert repair_set["options"] == []
    assert repair_set["skipped"] == "no_target_tournament"


# -- finding-directed search / canonical apply -------------------------------


def test_clustering_finding_enumerates_coupled_repair_and_applies_canonically(
    tmp_path: Path,
) -> None:
    root, plan, _problem_dict = _season(tmp_path)

    findings = list_findings(YEAR, root=root)
    clustering = [
        finding
        for finding in findings["findings"]
        if finding["category"] == "temporal_clustering" and finding["club"] == "Kongsberg"
    ]
    assert clustering
    kong = clustering[0]
    assert kong["min_gap_days"] == 1
    assert "rvv-0156" in kong["tournament_ids"]

    report = repair_options(YEAR, kong["finding_id"], root=root)
    options = [
        option for option in report["options"] if option["family"] == "coupled_placement"
    ]
    assert options, report["families"]
    chosen = next(
        option
        for option in options
        if option["effects"]["swapped_tournament_ids"] == ["rvv-0156", "rvv-0172"]
    )
    assert chosen["effects"]["roster_repair_applied"] is True

    result = apply_repair(
        YEAR,
        chosen["option_id"],
        report["revision"],
        root=root,
        finding_id=kong["finding_id"],
    )
    assert result["ok"] is True, result

    schedule = load_schedule(YEAR, root=root)
    by_id = {t["id"]: t for t in schedule["plan"]["tournaments"]}
    assert by_id["rvv-0156"]["date"] == "2027-03-14"
    assert by_id["rvv-0172"]["date"] == "2027-02-21"
    assert by_id["rvv-0156"]["age_group"] == "U9"
    assert by_id["rvv-0172"]["age_group"] == "U11"
    # Games were regenerated canonically for the repaired roster.
    labels = {team["label"] for team in by_id["rvv-0172"]["teams"]}
    game_labels = {
        label
        for game in by_id["rvv-0172"]["games"]
        for label in (game["home"], game["away"])
    }
    assert game_labels <= labels

    fresh = list_findings(YEAR, root=root)
    assert not [
        finding
        for finding in fresh["findings"]
        if finding["category"] == "temporal_clustering" and finding["club"] == "Kongsberg"
    ]


def test_bounded_search_reports_exhaustion_not_infeasibility(
    tmp_path: Path, monkeypatch
) -> None:
    # When the configured bounded search runs and produces no verified option,
    # the result is exhaustion of *this* search, never proof of infeasibility.
    import tournament_scheduler.coupled_placement_repair as module

    root, _plan, _problem_dict = _season(tmp_path)
    monkeypatch.setattr(
        module,
        "enumerate_coupled_placement_repairs",
        lambda *args, **kwargs: {
            "options": [],
            "rejected_candidates": [
                {
                    "finding_id": kwargs.get("finding_id", ""),
                    "tournament_ids": ["rvv-0156", "rvv-0172"],
                    "reason": "no_verified_coupled_roster_repair",
                    "roster_repair_attempted": True,
                }
            ],
        },
    )

    finding = next(
        entry
        for entry in list_findings(YEAR, root=root)["findings"]
        if entry["category"] == "temporal_clustering" and entry["club"] == "Kongsberg"
    )
    report = search(YEAR, finding["finding_id"], root=root)
    assert report["option_count"] == 0
    coverage = report["finding"]["search_coverage"]
    assert coverage["status"] == "bounded_search_exhausted"
    assert coverage["proven_infeasible"] is False
    assert report["rejected_candidates"][0]["roster_repair_attempted"] is True
