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
from typing import Any, Dict, List

import pytest

from tournament_scheduler.coupled_placement_repair import (
    CoupledPlacementRepairError,
    apply_coupled_placement_repair_option,
    apply_placement_swap,
    enumerate_coupled_placement_repairs,
    placement_swap_consequences,
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


def _busy_problem() -> Dict[str, Any]:
    """Same coupling scene, but Kongsberg's own arena is ``fixed_busy`` on the
    date a coupled exchange would move its clustered U9 tournament to."""

    problem = _problem()
    problem["club_calendar_status"] = {
        club: "known"
        for club in ("Kongsberg", "Solberg", "Tønsberg", "Frisk", "Holmen", "Jar")
    }
    problem["club_busy_intervals"] = {
        "Kongsberg": [{"date": "2027-03-14", "start": "07:00", "end": "14:00"}]
    }
    return problem


def _busy_season(tmp_path: Path):
    root = tmp_path / "season"
    plan = _coupled_plan()
    problem = _busy_problem()
    _write_season(root, plan, problem)
    return root, plan, problem


def _clustering_finding(root: Path) -> Dict[str, Any]:
    return next(
        entry
        for entry in list_findings(YEAR, root=root)["findings"]
        if entry["category"] == "temporal_clustering" and entry["club"] == "Kongsberg"
    )


def _coupled_exchange_option(report: Dict[str, Any]) -> Dict[str, Any]:
    return next(
        option
        for option in report["options"]
        if (option.get("effects") or {}).get("swapped_tournament_ids")
        == ["rvv-0156", "rvv-0172"]
    )


def test_coupled_candidate_on_fixed_busy_ice_is_classified_and_rejected_at_apply(
    tmp_path: Path,
) -> None:
    """A coupled exchange can be hard-valid yet newly place a tournament on
    known fixed_busy ice (represented as manual work). It must be classified as
    requiring an explicit opt-in, kept off the auto-applicable Pareto front,
    and refused at the apply boundary."""

    root, _plan_dict, _problem_dict = _busy_season(tmp_path)
    finding = _clustering_finding(root)

    report = repair_options(YEAR, finding["finding_id"], root=root)
    option = _coupled_exchange_option(report)
    # Hard-valid (the option would not reproduce otherwise) but not acceptable
    # as an automatic repair: it moves rvv-0156 onto Kongsberg's fixed_busy ice.
    assert option["operational_acceptable"] is False
    assert option["operational_work_added"] == {"fixed_busy_placement": ["rvv-0156"]}
    assert option["requires_operational_opt_in"] == ["allow_manual_placement"]
    assert option["option_id"] in report["pareto"]["operational_rejected_option_ids"]
    assert option["option_id"] not in report["pareto"]["non_dominated_option_ids"]

    schedule_file = root / YEAR / "schedule.json"
    before = schedule_file.read_bytes()
    result = apply_repair(
        YEAR,
        option["option_id"],
        report["revision"],
        root=root,
        finding_id=finding["finding_id"],
    )
    assert result["ok"] is False
    assert result["reason"] == "operational_acceptability_regression"
    assert result["required_opt_in_flags"] == ["allow_manual_placement"]
    assert schedule_file.read_bytes() == before


def test_coupled_candidate_can_be_opted_into_explicitly(tmp_path: Path) -> None:
    root, _plan_dict, _problem_dict = _busy_season(tmp_path)
    finding = _clustering_finding(root)

    report = repair_options(
        YEAR,
        finding["finding_id"],
        root=root,
        allow_manual_placement=True,
    )
    option = _coupled_exchange_option(report)
    assert option["operational_acceptable"] is True
    assert option["option_id"] in report["pareto"]["non_dominated_option_ids"]

    result = apply_repair(
        YEAR,
        option["option_id"],
        report["revision"],
        root=root,
        finding_id=finding["finding_id"],
        allow_manual_placement=True,
    )
    assert result["ok"] is True, result
    schedule = load_schedule(YEAR, root=root)
    by_id = {t["id"]: t for t in schedule["plan"]["tournaments"]}
    assert by_id["rvv-0156"]["date"] == "2027-03-14"


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


# -- production-shaped consequence coverage ----------------------------------


def _production_u9_teams() -> List[Dict[str, str]]:
    return (
        _teams("Kongsberg", "K9-", "U9", 1)
        + _teams("Solberg", "S9-", "U9", 1)
        + _teams("Tønsberg", "T9-", "U9", 1)
        + _teams("Frisk", "F9-", "U9", 1)
        + _teams("Holmen", "H9-", "U9", 1)
        + _teams("Jar", "J9-", "U9", 1)
        + _teams("Jutul", "JU9-", "U9", 1)
        + _teams("Ringerike", "R9-", "U9", 1)
    )


def _production_u11_fixed() -> List[Dict[str, str]]:
    return [
        {"club": "Holmen", "label": "Holmen Rød", "age_group": "U11"},
        {"club": "Frisk", "label": "Frisk Asker 3", "age_group": "U11"},
        {"club": "Frisk", "label": "Frisk Asker 4", "age_group": "U11"},
        {"club": "Jar", "label": "Jar Hvit", "age_group": "U11"},
        {"club": "Jar", "label": "Jar Blå", "age_group": "U11"},
        {"club": "Ringerike", "label": "Ringerike 2", "age_group": "U11"},
    ]


def _production_u11_busy() -> List[Dict[str, str]]:
    """U11 teams already booked on 2027-02-20, so moving rvv-0145 there
    double-books Ringerike 2 and leaves Frisk Asker 1 as the only free
    same-age replacement."""

    return [
        {"club": "Ringerike", "label": "Ringerike 2", "age_group": "U11"},
        {"club": "Ringerike", "label": "Ringerike 1", "age_group": "U11"},
        {"club": "Jar", "label": "Jar Rød", "age_group": "U11"},
        {"club": "Holmen", "label": "Holmen Hvit", "age_group": "U11"},
        {"club": "Tønsberg", "label": "Tønsberg 1", "age_group": "U11"},
        {"club": "Tønsberg", "label": "Tønsberg 2", "age_group": "U11"},
    ]


def _production_problem() -> Dict[str, Any]:
    teams = (
        _production_u9_teams()
        + _production_u11_fixed()
        + _production_u11_busy()
        + [{"club": "Frisk", "label": "Frisk Asker 1", "age_group": "U11"}]
    )
    config: Dict[str, Any] = {
        "teams": teams,
        "age_groups": ["U9", "U11"],
        "parallel_games": {"U9": 4, "U11": 3},
        "round_length_minutes": {"U9": 30, "U11": 30},
        "ice_time_minutes": {"U9": 120, "U11": 120},
        "rounds_per_tournament": {"U9": 3, "U11": 3},
    }
    problem = build_planning_problem(config, None, date(2026, 9, 1), date(2027, 4, 30))
    problem["clubs"] = {
        club: f"{club} Arena" for club in {team["club"] for team in teams}
    }
    problem["participation_targets_by_age_group"] = {
        "U9": {"before_christmas": 2, "after_christmas": 2},
        "U11": {"before_christmas": 2, "after_christmas": 2},
    }
    return problem


def _production_plan(problem: Dict[str, Any]) -> Dict[str, Any]:
    from tournament_scheduler.host_team_missing_repair import _regenerate_games

    u9 = _production_u9_teams()
    u11_fixed = _production_u11_fixed()
    u11_busy = _production_u11_busy()
    union = {team["label"]: team for team in (*u11_fixed, *u11_busy)}

    def pick(labels: List[str]) -> List[Dict[str, str]]:
        return [union[label] for label in labels]

    def tournament(tournament_id, day, age_group, host, roster):
        payload = {
            "id": tournament_id,
            "date": day,
            "arena": f"{host} Arena",
            "age_group": age_group,
            "host_club": host,
            "teams": [dict(team) for team in roster],
            "start_time": "10:00",
        }
        _regenerate_games(payload, problem)
        return payload

    return {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [
            tournament("rvv-0184", "2027-02-20", "U9", "Kongsberg", u9),
            tournament("rvv-0156", "2027-02-21", "U9", "Kongsberg", u9),
            tournament("rvv-0142", "2027-02-28", "U9", "Kongsberg", u9),
            tournament("rvv-0145", "2027-02-14", "U11", "Holmen", u11_fixed),
            tournament(
                "rvv-0200",
                "2027-02-21",
                "U11",
                "Frisk",
                pick(
                    [
                        "Frisk Asker 3",
                        "Jar Blå",
                        "Jar Hvit",
                        "Frisk Asker 4",
                        "Holmen Rød",
                        "Ringerike 1",
                    ]
                ),
            ),
            tournament("rvv-0201", "2027-02-20", "U11", "Jar", u11_busy),
            tournament(
                "rvv-0202",
                "2027-02-07",
                "U11",
                "Holmen",
                pick(
                    [
                        "Frisk Asker 3",
                        "Jar Rød",
                        "Ringerike 1",
                        "Holmen Hvit",
                        "Tønsberg 1",
                        "Tønsberg 2",
                    ]
                ),
            ),
            tournament(
                "rvv-0203",
                "2027-02-27",
                "U11",
                "Tønsberg",
                pick(
                    [
                        "Frisk Asker 3",
                        "Jar Blå",
                        "Jar Hvit",
                        "Frisk Asker 4",
                        "Tønsberg 1",
                        "Holmen Hvit",
                    ]
                ),
            ),
            tournament(
                "rvv-0204",
                "2027-01-30",
                "U11",
                "Holmen",
                pick(
                    [
                        "Jar Blå",
                        "Frisk Asker 3",
                        "Frisk Asker 4",
                        "Holmen Rød",
                        "Tønsberg 1",
                        "Tønsberg 2",
                    ]
                ),
            ),
            # Two before-Christmas tournaments make Ringerike 2 exactly on
            # target before the repair, so losing rvv-0145 creates a genuine
            # participation shortfall.
            tournament(
                "rvv-0205",
                "2026-10-17",
                "U11",
                "Ringerike",
                pick(
                    [
                        "Ringerike 2",
                        "Ringerike 1",
                        "Jar Rød",
                        "Holmen Hvit",
                        "Tønsberg 1",
                        "Tønsberg 2",
                    ]
                ),
            ),
            tournament(
                "rvv-0206",
                "2026-11-14",
                "U11",
                "Ringerike",
                pick(
                    [
                        "Ringerike 2",
                        "Ringerike 1",
                        "Jar Rød",
                        "Holmen Hvit",
                        "Tønsberg 1",
                        "Tønsberg 2",
                    ]
                ),
            ),
        ],
    }


def _production_roster_repaired_after(
    plan: Dict[str, Any], problem: Dict[str, Any]
) -> Dict[str, Any]:
    """The production coupled mutation: swap rvv-0184/rvv-0145 and repair the
    double-booked U11 roster by replacing Ringerike 2 with Frisk Asker 1."""

    from tournament_scheduler.host_team_missing_repair import _regenerate_games

    after = apply_placement_swap(plan, "rvv-0184", "rvv-0145")
    by_id = {tournament["id"]: tournament for tournament in after["tournaments"]}
    u11 = by_id["rvv-0145"]
    u11["teams"] = [team for team in u11["teams"] if team["label"] != "Ringerike 2"]
    u11["teams"].append({"club": "Frisk", "label": "Frisk Asker 1", "age_group": "U11"})
    _regenerate_games(u11, problem)
    return after


def _production_season(tmp_path: Path):
    root = tmp_path / "season"
    problem = _production_problem()
    plan = _production_plan(problem)
    assert verify_candidate(plan, problem)["ok"] is True
    _write_season(root, plan, problem)
    return root, plan, problem


def test_production_shaped_consequences_cover_added_removed_and_all_teams() -> None:
    problem = _production_problem()
    plan = _production_plan(problem)
    after = _production_roster_repaired_after(plan, problem)

    consequences = placement_swap_consequences(
        plan,
        after,
        "rvv-0184",
        "rvv-0145",
        problem=problem,
        focus_team=("Kongsberg", "K9-1", "U9"),
    )

    assert consequences["consequence_acceptable"] is False
    # Eight U9 participants, six pre-repair U11 participants and the one U11
    # identity introduced only by the roster repair.
    assert consequences["team_consequence_count"] == 15

    u11 = {key.split("|")[1]: value for key, value in consequences["team_consequences"].items()}

    def codes(label: str) -> set[str]:
        return {regression["code"] for regression in u11[label]["material_regressions"]}

    assert "more_gaps_under_7_days" in codes("Frisk Asker 3")
    assert "more_gaps_under_7_days" in codes("Jar Blå")
    assert u11["Ringerike 2"]["membership_role"] == "removed"
    assert "participation_shortfall_worsened" in codes("Ringerike 2")
    assert u11["Frisk Asker 1"]["membership_role"] == "added"
    assert "Frisk Asker 1" in u11

    # A display bound may truncate the rendered detail, but never the set the
    # acceptance decision is computed from.
    capped = placement_swap_consequences(
        plan,
        after,
        "rvv-0184",
        "rvv-0145",
        problem=problem,
        focus_team=("Kongsberg", "K9-1", "U9"),
        max_teams=1,
    )
    assert capped["consequence_acceptable"] is False
    assert capped["team_consequence_count"] == 15
    assert capped["display_team_keys"] == ["Kongsberg|K9-1|U9"]


def test_production_shaped_coupled_repair_is_classified_and_rejected(
    tmp_path: Path,
) -> None:
    root, _plan, _problem_dict = _production_season(tmp_path)

    finding = next(
        entry
        for entry in list_findings(YEAR, root=root)["findings"]
        if entry["category"] == "temporal_clustering" and entry["club"] == "Kongsberg"
    )
    report = repair_options(YEAR, finding["finding_id"], root=root)
    option = next(
        entry
        for entry in report["options"]
        if entry.get("family") == "coupled_placement"
        and (entry.get("effects") or {}).get("swapped_tournament_ids")
        == ["rvv-0184", "rvv-0145"]
    )
    assert option["effects"]["consequence_acceptable"] is False
    assert option["effects"]["roster_repair_applied"] is True
    assert option["option_id"] in report["pareto"]["consequence_rejected_option_ids"]
    assert option["option_id"] not in report["pareto"]["non_dominated_option_ids"]

    schedule_file = root / YEAR / "schedule.json"
    before = schedule_file.read_bytes()
    result = apply_repair(
        YEAR,
        option["option_id"],
        report["revision"],
        root=root,
        finding_id=finding["finding_id"],
    )
    assert result["ok"] is False
    assert result["reason"] == "team_schedule_regression"
    assert schedule_file.read_bytes() == before


def test_production_apply_rejects_material_team_regression_directly() -> None:
    problem = _production_problem()
    plan = _production_plan(problem)
    after = _production_roster_repaired_after(plan, problem)
    after_by_id = {tournament["id"]: tournament for tournament in after["tournaments"]}
    arguments = {
        "tournament_a_id": "rvv-0184",
        "tournament_b_id": "rvv-0145",
        "fields": ["date", "start_time"],
        "roster_changes": {
            "rvv-0145": {
                "teams": [dict(team) for team in after_by_id["rvv-0145"]["teams"]],
                "removed": ["Ringerike 2"],
                "added": ["Frisk Asker 1"],
            }
        },
    }
    from tournament_scheduler.host_team_missing_repair import candidate_fingerprint

    applied = apply_coupled_placement_repair_option(
        plan,
        problem,
        option_id="coupled_placement:test",
        expected_fingerprint=candidate_fingerprint(plan),
        arguments=arguments,
    )
    assert applied["ok"] is False
    assert applied["reason"] == "team_schedule_regression"
    assert applied["consequences"]["consequence_acceptable"] is False


# -- bounded selection / ranking --------------------------------------------


def test_bounded_selection_keeps_safe_candidate_ahead_of_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    # The provider truncates to a bounded display set after ranking, so the
    # ranking itself must prefer automatic acceptability: a cheaper, larger
    # focus-improvement candidate that is consequence-rejected must never take
    # the slot a generated safe candidate needs.
    import tournament_scheduler.coupled_placement_repair as module
    from tournament_scheduler.host_team_missing_repair import RepairOption

    _root, plan, problem = _season(tmp_path)
    calls = {"n": 0}

    def fake_build(_plan, _problem, **kwargs):
        calls["n"] += 1
        safe = calls["n"] == 1
        tournament_a_id = kwargs["tournament_a_id"]
        tournament_b_id = kwargs["tournament_b_id"]
        return (
            RepairOption(
                option_id=f"coupled_placement:{tournament_a_id}:{tournament_b_id}",
                finding_id=kwargs.get("finding_id", ""),
                action="coupled_placement_repair",
                tournament_id=tournament_a_id,
                arguments={},
                hard_feasible=True,
                effects={
                    "consequence_acceptable": safe,
                    # The rejected candidates are cheaper and improve the focus
                    # team more, so cost/focus ordering alone would rank every
                    # one of them ahead of the safe candidate.
                    "change_cost_total": 100.0 if safe else 1.0,
                    "focus_team": {
                        "min_gap_before": 0,
                        "min_gap_after": 30 if safe else 60,
                    },
                },
                evidence={},
            ),
            {},
        )

    monkeypatch.setattr(module, "_build_coupled_option", fake_build)
    result = module.enumerate_coupled_placement_repairs(
        plan,
        problem,
        target_tournament_ids=[t["id"] for t in plan["tournaments"]],
        finding_id="finding-1",
        max_options=6,
    )

    # More verified candidates than the display bound, so truncation order is
    # what decides membership.
    assert result["verified_candidate_count"] > 6
    assert len(result["options"]) == 6

    safe = [
        option
        for option in result["options"]
        if option["effects"]["consequence_acceptable"] is True
    ]
    assert len(safe) == 1
    assert safe[0]["effects"]["rank"] == 1

    # Rejected candidates stay visible as evidence inside the bound rather than
    # being hidden, but they cannot crowd the safe choice out.
    assert any(
        option["effects"]["consequence_acceptable"] is False
        for option in result["options"]
    )


def test_option_rank_orders_acceptability_before_cost() -> None:
    # The full class ordering: automatic-acceptability dominates the #401
    # operational opt-in class, which in turn dominates consequence-rejected
    # candidates even when those are cheaper and improve the focus team more.
    import tournament_scheduler.coupled_placement_repair as module
    from tournament_scheduler.host_team_missing_repair import RepairOption

    def option(name: str, *, consequence: bool, opt_in: bool) -> RepairOption:
        return RepairOption(
            option_id=name,
            finding_id="finding-1",
            action="coupled_placement_repair",
            tournament_id="rvv-0001",
            arguments={},
            hard_feasible=True,
            effects={
                "consequence_acceptable": consequence,
                "requires_operational_opt_in": opt_in,
                "change_cost_total": 1.0,
                "focus_team": {"min_gap_before": 0, "min_gap_after": 60},
            },
        )

    ranked = sorted(
        [
            option("rejected-opt-in", consequence=False, opt_in=True),
            option("rejected-auto", consequence=False, opt_in=False),
            option("safe-opt-in", consequence=True, opt_in=True),
            option("safe-auto", consequence=True, opt_in=False),
        ],
        key=module._option_rank,
    )
    assert [entry.option_id for entry in ranked] == [
        "safe-auto",
        "safe-opt-in",
        "rejected-auto",
        "rejected-opt-in",
    ]


# -- soft consequence-driven same-club sibling repair ------------------------


def _u9_problem() -> Dict[str, Any]:
    """One-team Kongsberg plus a three-team Jar U9 pool.

    The participation target is intentionally small so the sibling pool exists
    as an operational fact without adding unrelated per-team shortfalls.
    """

    teams = [
        {"club": "Kongsberg", "label": "K9-1", "age_group": "U9"},
        {"club": "Solberg", "label": "S9-1", "age_group": "U9"},
        {"club": "Tønsberg", "label": "T9-1", "age_group": "U9"},
        {"club": "Frisk", "label": "F9-1", "age_group": "U9"},
        {"club": "Jar", "label": "Jar Rød", "age_group": "U9"},
        {"club": "Jar", "label": "Jar Blå", "age_group": "U9"},
        {"club": "Jar", "label": "Jar Hvit", "age_group": "U9"},
    ]
    config: Dict[str, Any] = {
        "teams": teams,
        "age_groups": ["U9"],
        "parallel_games": {"U9": 2},
        "round_length_minutes": {"U9": 30},
        "ice_time_minutes": {"U9": 90},
        "rounds_per_tournament": {"U9": 3},
    }
    problem = build_planning_problem(config, None, date(2026, 9, 1), date(2027, 4, 30))
    problem["clubs"] = {
        club: f"{club} Arena"
        for club in ("Kongsberg", "Solberg", "Tønsberg", "Frisk", "Jar")
    }
    problem["participation_targets_by_age_group"] = {
        "U9": {"before_christmas": 1, "after_christmas": 1}
    }
    return problem


def _u9_plan(problem: Dict[str, Any], *, siblings_busy: bool) -> Dict[str, Any]:
    """Kongsberg's own U9 cluster plus a Jar tournament that moves into it.

    Swapping ``rvv-K2`` (2027-02-21) with ``rvv-J1`` (2027-02-07) spreads
    Kongsberg's cluster to 2027-02-07/20/28, but gives Jar Blå a 2027-02-15 /
    2027-02-21 / 2027-02-27 schedule with two sub-7-day gaps. Jar's siblings
    are free in the positive scene and already clustered in the negative one.
    """

    from tournament_scheduler.host_team_missing_repair import _regenerate_games

    def team(club: str, label: str) -> Dict[str, str]:
        return {"club": club, "label": label, "age_group": "U9"}

    k, s = team("Kongsberg", "K9-1"), team("Solberg", "S9-1")
    t, f = team("Tønsberg", "T9-1"), team("Frisk", "F9-1")
    rod = team("Jar", "Jar Rød")
    bla = team("Jar", "Jar Blå")
    hvit = team("Jar", "Jar Hvit")

    def tournament(tournament_id: str, day: str, host: str, roster: List[Dict[str, str]]) -> Dict[str, Any]:
        payload = {
            "id": tournament_id,
            "date": day,
            "arena": f"{host} Arena",
            "age_group": "U9",
            "host_club": host,
            "teams": [dict(member) for member in roster],
            "start_time": "10:00",
        }
        _regenerate_games(payload, problem)
        return payload

    jar_second = [bla, rod, s, t] if siblings_busy else [bla, s, t, f]
    jar_third = [bla, hvit, t, f] if siblings_busy else [bla, s, t, f]
    return {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [
            tournament("rvv-K1", "2027-02-20", "Kongsberg", [k, s, t, f]),
            tournament("rvv-K2", "2027-02-21", "Kongsberg", [k, s, t, f]),
            tournament("rvv-K3", "2027-02-28", "Kongsberg", [k, s, t, f]),
            tournament("rvv-J1", "2027-02-07", "Jar", [bla, s, t, f]),
            tournament("rvv-J2", "2027-02-15", "Jar", jar_second),
            tournament("rvv-J3", "2027-02-27", "Jar", jar_third),
        ],
    }


def _u9_season(tmp_path: Path, *, siblings_busy: bool):
    root = tmp_path / "season"
    problem = _u9_problem()
    plan = _u9_plan(problem, siblings_busy=siblings_busy)
    assert verify_candidate(plan, problem)["ok"] is True
    _write_season(root, plan, problem)
    return root, plan, problem


def _pair_option(report: Dict[str, Any], pair: List[str]) -> Dict[str, Any]:
    return next(
        option
        for option in report["options"]
        if (option.get("effects") or {}).get("swapped_tournament_ids") == pair
    )


def _kong_clustering_finding(root: Path) -> Dict[str, Any]:
    return next(
        entry
        for entry in list_findings(YEAR, root=root)["findings"]
        if entry["category"] == "temporal_clustering" and entry["club"] == "Kongsberg"
    )


def test_soft_consequence_regression_is_repaired_by_same_club_sibling(
    tmp_path: Path,
) -> None:
    """The placement exchange alone gives Jar Blå two sub-7-day gaps; the
    provider rotates the load to a free same-club, same-age sibling instead of
    rejecting the repair or relaxing the gap rule."""

    root, plan, problem = _u9_season(tmp_path, siblings_busy=False)
    finding = _kong_clustering_finding(root)
    report = repair_options(YEAR, finding["finding_id"], root=root)
    option = _pair_option(report, ["rvv-K2", "rvv-J1"])

    effects = option["effects"]
    assert effects["placement_only_verified"] is True
    assert effects["roster_repair_trigger"] == "consequence_regression"
    assert effects["roster_repair_applied"] is True
    assert effects["consequence_acceptable"] is True
    assert option["operational_acceptable"] is True
    # Acceptability, not Pareto membership, is what must hold: this specific
    # exchange may be dominated by a cheaper acceptable alternative.
    assert option.get("objectives") is not None
    assert option["option_id"] not in report["pareto"]["consequence_rejected_option_ids"]

    # The substituted team is a same-club, same-age sibling, and the
    # single-team Kongsberg side is untouched.
    substitution = effects["sibling_substitution"]
    assert substitution["outcome"] == "sibling_repaired"
    applied = substitution["applied"]["rvv-J1"]
    assert applied["removed"] == [
        {"club": "Jar", "label": "Jar Blå", "age_group": "U9"}
    ]
    assert applied["added"][0]["club"] == "Jar"
    assert applied["added"][0]["age_group"] == "U9"
    assert applied["added"][0]["label"] in {"Jar Rød", "Jar Hvit"}
    assert effects["roster_repair_applied"] is True
    assert "rvv-K2" not in substitution["applied"]
    assert {tuple(pair) for pair in substitution["affected_club_age_pairs"]} == {
        ("Jar", "U9")
    }
    assert substitution["registered_sibling_counts"]["rvv-J1"][0][
        "available_sibling_count"
    ] == 2

    # Before/after club-pool evidence is reported and not materially worsened.
    before_pool = {
        (row["scope"]): (row["club_pool_actual"], row["classification"])
        for row in effects["club_pool_before"]
    }
    after_pool = {
        (row["scope"]): (row["club_pool_actual"], row["classification"])
        for row in effects["club_pool_after"]
    }
    assert before_pool == after_pool
    assert before_pool["season"] == (3, "material_club_pool_shortfall")

    # The complete changed-team consequence evidence is retained.
    consequences = option["evidence"]["consequences"]
    assert consequences["consequence_acceptable"] is True
    assert "Jar Blå" in {
        row["label"] for row in consequences["affected_teams"]
    }

    result = apply_repair(
        YEAR,
        option["option_id"],
        report["revision"],
        root=root,
        finding_id=finding["finding_id"],
    )
    assert result["ok"] is True, result
    schedule = load_schedule(YEAR, root=root)
    by_id = {tournament["id"]: tournament for tournament in schedule["plan"]["tournaments"]}
    assert by_id["rvv-K2"]["date"] == "2027-02-07"
    assert by_id["rvv-J1"]["date"] == "2027-02-21"
    labels = {team["label"] for team in by_id["rvv-J1"]["teams"]}
    assert "Jar Blå" not in labels
    assert labels & {"Jar Rød", "Jar Hvit"}


def test_soft_repair_is_rejected_when_no_sibling_can_absorb(
    tmp_path: Path,
) -> None:
    """If every Jar sibling would itself become materially regressive, the
    candidate stays consequence-rejected rather than being accepted by
    relaxing the individual teams' temporal rule."""

    root, _plan, _problem = _u9_season(tmp_path, siblings_busy=True)
    finding = _kong_clustering_finding(root)
    report = repair_options(YEAR, finding["finding_id"], root=root)
    option = _pair_option(report, ["rvv-K2", "rvv-J1"])

    effects = option["effects"]
    assert effects["placement_only_verified"] is True
    assert effects["consequence_acceptable"] is False
    assert effects["roster_repair_applied"] is False
    substitution = effects["sibling_substitution"]
    assert substitution["outcome"] == "no_acceptable_sibling_substitution"
    assert substitution["registered_sibling_counts"]["rvv-J1"][0][
        "available_sibling_count"
    ] == 2
    assert option["option_id"] in report["pareto"]["consequence_rejected_option_ids"]
    assert option["option_id"] not in report["pareto"]["non_dominated_option_ids"]

    schedule_file = root / YEAR / "schedule.json"
    before = schedule_file.read_bytes()
    result = apply_repair(
        YEAR,
        option["option_id"],
        report["revision"],
        root=root,
        finding_id=finding["finding_id"],
    )
    assert result["ok"] is False
    assert result["reason"] == "team_schedule_regression"
    assert schedule_file.read_bytes() == before
