"""Promoted-season finding/repair maintenance loop tests.

These exercise the Ringerike-style production regression on a synthetic
promoted season: fresh findings, finding-directed repair options, an atomic
revision-bound apply, and a deterministic before/after delta. They never set up
a ``.pipeline`` work directory, which is the point -- repairing an already
promoted season must not require re-running Stage 1/2.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tournament_scheduler.participation_deviation_repair import _classification
from tournament_scheduler.planning_contract import build_planning_problem, verify_candidate
from tournament_scheduler.season_maintenance import (
    apply_repair,
    list_findings,
    repair_options,
    search,
)
from tournament_scheduler.season_state import schedule_fingerprint

YEAR = "2026-2027"


def _teams(clubs: Iterable[str]) -> List[Dict[str, str]]:
    return [
        {"club": club, "label": f"{club} {index}", "age_group": "U10"}
        for club in clubs
        for index in (1, 2)
    ]


def _problem(
    teams: List[Dict[str, str]],
    *,
    parallel_games: int = 2,
    participation_targets: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config: Dict[str, Any] = {
        "teams": teams,
        "age_groups": ["U10"],
        "parallel_games": {"U10": parallel_games},
        "round_length_minutes": {"U10": 30},
        "ice_time_minutes": {"U10": 90},
        "rounds_per_tournament": {"U10": 3},
    }
    if participation_targets is not None:
        config["participation_targets_by_age_group"] = participation_targets
    problem = build_planning_problem(config, None, date(2026, 9, 1), date(2027, 4, 30))
    problem["clubs"] = {club: f"{club} Arena" for club in {team["club"] for team in teams}}
    return problem


def _tournament(
    tournament_id: str,
    day: str,
    host: str,
    teams: List[Dict[str, str]],
) -> Dict[str, Any]:
    labels = [team["label"] for team in teams]
    games = [
        {
            "home": labels[index],
            "away": labels[(index + 1) % len(labels)],
            "parallel_slot": 0,
            "round_number": 1,
        }
        for index in range(len(labels))
    ]
    return {
        "id": tournament_id,
        "date": day,
        "arena": f"{host} Arena",
        "age_group": "U10",
        "host_club": host,
        "teams": teams,
        "games": games,
        "start_time": "10:00",
    }


def _plan(tournaments: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": tournaments,
    }


def _write_season(
    root: Path,
    plan: Dict[str, Any],
    problem: Dict[str, Any],
    *,
    approved: Optional[Iterable[str]] = None,
) -> str:
    season_dir = root / YEAR
    season_dir.mkdir(parents=True, exist_ok=True)
    revision = schedule_fingerprint(plan)
    approved_ids = set(approved or ())
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
                "status": "approved" if tournament["id"] in approved_ids else "pending_review",
                "placement_locked": tournament["id"] in approved_ids,
                "participants_locked": False,
                "approved_fingerprint": None,
            }
            for tournament in plan["tournaments"]
        },
    }
    (season_dir / "schedule.json").write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (season_dir / "decisions.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return revision


def _two_club_season(tmp_path: Path, *, approved: Optional[Iterable[str]] = None):
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams)
    plan = _plan(
        [
            _tournament("T1", "2026-10-10", "Nordby", teams),
            _tournament("T2", "2026-11-14", "Nordby", teams),
        ]
    )
    root = tmp_path / "season"
    revision = _write_season(root, plan, problem, approved=approved)
    return root, plan, problem, revision


def test_findings_expose_unresolved_hosting_obligation_bound_to_revision(tmp_path: Path) -> None:
    root, _plan, _problem_dict, revision = _two_club_season(tmp_path)

    findings = list_findings(YEAR, root=root)

    assert findings["revision"] == revision
    hosting = [finding for finding in findings["findings"] if finding["category"] == "hosting"]
    assert [finding["finding_id"] for finding in hosting] == ["hosting_balance:U10:Sorby"]
    assert hosting[0]["code"] == "unresolved_hosting_obligation"
    assert hosting[0]["club"] == "Sorby"
    assert hosting[0]["deficit"] == 1


def test_unrelated_findings_do_not_impose_queue_order(tmp_path: Path) -> None:
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(
        teams,
        participation_targets={"U10": {"before_christmas": 3, "after_christmas": 3}},
    )
    plan = _plan(
        [
            _tournament("T1", "2026-10-10", "Nordby", teams),
            _tournament("T2", "2026-11-14", "Nordby", teams),
        ]
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    findings = list_findings(YEAR, root=root)

    # A hosting finding and a participation finding coexist; either may be
    # selected directly without first processing the other.
    categories = {finding["category"] for finding in findings["findings"]}
    assert {"hosting", "participation"} <= categories
    hosting = repair_options(YEAR, "hosting_balance:U10:Sorby", root=root)
    assert hosting["option_count"] >= 1
    participation = [f for f in findings["findings"] if f["category"] == "participation"]
    assert participation
    assert search(YEAR, participation[0]["finding_id"], root=root)["finding"]["finding_id"] == participation[0]["finding_id"]


def test_repair_options_offer_surplus_donor_and_preserve_unrelated(tmp_path: Path) -> None:
    root, plan, _problem_dict, revision = _two_club_season(tmp_path)

    options = repair_options(YEAR, "hosting_balance:U10:Sorby", root=root)

    assert options["revision"] == revision
    assert options["options"]
    assert all(option["family"] == "hosting_balance" for option in options["options"])
    assert {option["effects"]["donor_host"] for option in options["options"]} == {"Nordby"}
    assert all(option["effects"]["donor_host_excess_before"] == 1 for option in options["options"])
    # Only one tournament changes; the other must survive byte-identically.
    assert all(option["effects"]["changed_tournament_count"] == 1 for option in options["options"])
    assert plan["tournaments"][1]["id"] == "T2"


def test_apply_repair_is_revision_bound_and_returns_fresh_delta(tmp_path: Path) -> None:
    root, plan, _problem_dict, revision = _two_club_season(tmp_path)
    options = repair_options(YEAR, "hosting_balance:U10:Sorby", root=root)
    chosen = options["options"][0]

    result = apply_repair(
        YEAR,
        chosen["option_id"],
        revision,
        root=root,
        finding_id="hosting_balance:U10:Sorby",
    )

    assert result["ok"] is True
    assert result["revision_before"] == revision
    assert result["revision_after"] and result["revision_after"] != revision
    delta = result["delta"]
    assert delta["hard_violations_after"] == 0
    assert (delta["unresolved_hosting_obligations_before"], delta["unresolved_hosting_obligations_after"]) == (1, 0)
    assert (delta["hosting_balance_imbalances_before"], delta["hosting_balance_imbalances_after"]) == (2, 0)
    assert delta["changed_tournament_count"] == 1
    # Fresh, revision-consistent evidence: no findings remain on the new revision.
    assert result["fresh_findings"]["revision"] == result["revision_after"]
    assert result["fresh_findings"]["finding_count"] == 0


def test_search_then_apply_with_explicit_dimensions(tmp_path: Path) -> None:
    root, _plan_dict, _problem_dict, revision = _two_club_season(tmp_path)

    result = search(
        YEAR,
        "hosting_balance:U10:Sorby",
        root=root,
        dimensions=("participants", "host"),
    )

    assert result["requested_dimensions"] == ["host", "participants"]
    assert result["options"]
    chosen = result["options"][0]
    applied = apply_repair(
        YEAR,
        chosen["option_id"],
        revision,
        root=root,
        finding_id="hosting_balance:U10:Sorby",
        dimensions=("participants", "host"),
    )
    assert applied["ok"] is True
    assert applied["delta"]["unresolved_hosting_obligations_after"] == 0


def test_stale_revision_is_rejected_without_mutating_canonical_state(tmp_path: Path) -> None:
    root, _plan, _problem_dict, revision = _two_club_season(tmp_path)
    options = repair_options(YEAR, "hosting_balance:U10:Sorby", root=root)
    option_id = options["options"][0]["option_id"]
    schedule_file = root / YEAR / "schedule.json"
    before = schedule_file.read_bytes()

    result = apply_repair(YEAR, option_id, "deadbeef", root=root)

    assert result["ok"] is False
    assert result["reason"] == "stale_canonical_revision"
    assert result["canonical_revision_unchanged"] is True
    assert schedule_file.read_bytes() == before
    assert list_findings(YEAR, root=root)["counts_by_code"].get("unresolved_hosting_obligation") == 1


def test_placement_locked_donor_is_not_moved(tmp_path: Path) -> None:
    root, _plan, _problem_dict, _revision = _two_club_season(tmp_path, approved=["T2"])

    options = repair_options(YEAR, "hosting_balance:U10:Sorby", root=root)

    assert [option["tournament_id"] for option in options["options"]] == ["T1"]
    rejected = [entry for entry in options["rejected_candidates"] if entry.get("tournament_id") == "T2"]
    assert rejected and "canonical_placement_locked" in rejected[0]["violation_codes"]


def test_rehost_that_creates_another_deficit_is_rejected(tmp_path: Path) -> None:
    teams = _teams(["Alpha", "Bravo", "Charlie"])
    problem = _problem(teams, parallel_games=3)
    plan = _plan(
        [
            _tournament("A1", "2026-10-10", "Alpha", teams),
            _tournament("A2", "2026-11-14", "Alpha", teams),
            _tournament("B1", "2026-12-05", "Bravo", teams),
        ]
    )
    assert verify_candidate(plan, problem)["ok"]
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    options = repair_options(YEAR, "hosting_balance:U10:Charlie", root=root)

    # Only the surplus club's (Alpha) tournaments may satisfy Charlie; moving
    # Bravo's tournament would merely relocate the shortfall.
    assert {option["tournament_id"] for option in options["options"]} == {"A1", "A2"}
    rejected = [entry for entry in options["rejected_candidates"] if entry.get("tournament_id") == "B1"]
    assert rejected and rejected[0]["reason"] == "no_deficit_improvement"


def test_participation_finding_exposes_avoidability(tmp_path: Path) -> None:
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(
        teams,
        participation_targets={"U10": {"before_christmas": 3, "after_christmas": 3}},
    )
    plan = _plan(
        [
            _tournament("T1", "2026-10-10", "Nordby", teams),
            _tournament("T2", "2026-11-14", "Nordby", teams),
        ]
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    findings = list_findings(YEAR, root=root)
    participation = [f for f in findings["findings"] if f["category"] == "participation"]

    assert participation
    assert all("avoidability" in finding for finding in participation)
    assert all(finding["proven_infeasible"] == (finding["avoidability"] == "proven_infeasible") for finding in participation)


def test_bounded_search_exhausted_is_not_proven_infeasible() -> None:
    classification = _classification({"avoidability": "bounded_search_exhausted"})

    assert classification["proven_infeasible"] is False
    assert "not proof of infeasibility" in classification["note"]

    proven = _classification({"avoidability": "proven_infeasible"})
    assert proven["proven_infeasible"] is True


def test_hard_finding_reuses_the_common_stage3_repair_providers(tmp_path: Path) -> None:
    teams = _teams(["Nordby", "Sorby", "Tredje"])
    problem = _problem(teams)
    participants = teams[:4]
    plan = _plan(
        [
            _tournament("T1", "2026-10-10", "Tredje", participants),
            _tournament("T2", "2026-11-14", "Nordby", participants),
        ]
    )
    root = tmp_path / "season"
    revision = _write_season(root, plan, problem)

    findings = list_findings(YEAR, root=root)
    hard = [
        finding
        for finding in findings["findings"]
        if finding["category"] == "hard_violation" and finding["code"] == "host_team_missing"
    ]
    assert [finding["finding_id"] for finding in hard] == ["host_team_missing:T1"]

    options = repair_options(YEAR, "host_team_missing:T1", root=root)
    assert options["option_count"] > 0
    assert {option["family"] for option in options["options"]} == {"host_team_missing"}

    applied = apply_repair(
        YEAR,
        options["options"][0]["option_id"],
        revision,
        root=root,
        finding_id="host_team_missing:T1",
    )
    assert applied["ok"] is True
    assert applied["delta"]["hard_violations_before"] == 1
    assert applied["delta"]["hard_violations_after"] == 0
