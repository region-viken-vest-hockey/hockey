"""Canonical holiday/date-admissibility policy regression tests.

The repository's holiday exclusions must be one deterministic hard invariant
shared by the initial scheduler, every repair/optimizer date move and the
independent planning-contract verifier. These tests pin that contract at the
owner boundaries: the canonical policy, the normalized planning problem, the
verifier, the local-search date move and the promoted-season repair surface.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

from tournament_scheduler.date_policy import (
    holiday_excluded_dates,
)
from tournament_scheduler.date_policy_relocation import (
    apply_date_policy_relocation_option,
    enumerate_date_policy_relocations,
    relocate_excluded_date_tournaments,
)
from tournament_scheduler.planning_contract import build_planning_problem, verify_candidate
from tournament_scheduler.search_neighborhood_repair import (
    DATE_MOVE_VIOLATION_CODES,
    enumerate_search_neighborhood_repairs,
    findings_are_locally_searchable,
)
from tournament_scheduler.season_maintenance import (
    apply_repair,
    list_findings,
    load_context,
    search,
    supported_dimensions_for_finding,
)
from tournament_scheduler.season_state import schedule_fingerprint
from tournament_scheduler.stage3_optimizer import optimize_candidate

WINDOW_START = date(2026, 9, 1)
WINDOW_END = date(2027, 4, 30)


def _teams(clubs: Iterable[str]) -> List[Dict[str, str]]:
    return [
        {"club": club, "label": f"{club} {index}", "age_group": "U10"}
        for club in clubs
        for index in (1, 2)
    ]


def _problem(teams: List[Dict[str, str]]) -> Dict[str, Any]:
    config: Dict[str, Any] = {
        "teams": teams,
        "age_groups": ["U10"],
        "parallel_games": {"U10": 2},
        "round_length_minutes": {"U10": 30},
        "ice_time_minutes": {"U10": 90},
        "rounds_per_tournament": {"U10": 3},
    }
    problem = build_planning_problem(config, None, WINDOW_START, WINDOW_END)
    problem["clubs"] = {club: f"{club} Arena" for club in {team["club"] for team in teams}}
    return problem


def _tournament(
    tournament_id: str, day: str, host: str, teams: List[Dict[str, str]]
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
        "start_date": WINDOW_START.isoformat(),
        "end_date": WINDOW_END.isoformat(),
        "tournaments": tournaments,
    }


def _write_season(root: Path, plan: Dict[str, Any], problem: Dict[str, Any]) -> None:
    season_dir = root / "2026-2027"
    season_dir.mkdir(parents=True, exist_ok=True)
    revision = schedule_fingerprint(plan)
    schedule = {
        "schema_version": 1,
        "season": "2026-2027",
        "revision": revision,
        "fingerprint": revision,
        "plan_schema_version": 1,
        "plan": plan,
        "verification_context": {"problem": problem},
    }
    decisions = {
        "schema_version": 1,
        "season": "2026-2027",
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
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (season_dir / "decisions.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_canonical_policy_matches_initial_scheduler() -> None:
    """The shared policy and the initial scheduler's checker agree exactly."""
    from tournament_scheduler.conflict_checkers.holiday_checker import HolidayConflictChecker
    from tournament_scheduler.scheduler import TournamentScheduler
    from tournament_scheduler.utils.date_parser import DateParser

    scheduler = TournamentScheduler(
        calendar_sources=[],
        conflict_checkers=[HolidayConflictChecker()],
        date_parser=DateParser(),
    )
    result = scheduler.find_available_dates(
        datetime.combine(WINDOW_START, datetime.min.time()),
        datetime.combine(WINDOW_END, datetime.min.time()),
    )
    assert set(result.excluded_dates) == {
        excluded
        for excluded in holiday_excluded_dates(WINDOW_START, WINDOW_END)
        if excluded.weekday() in (5, 6)
    }
    # The policy must actually exclude the published Christmas/New Year dates.
    assert date(2026, 12, 26) in result.excluded_dates
    assert date(2027, 1, 2) in result.excluded_dates


def test_planning_problem_carries_canonical_date_exclusions() -> None:
    problem = _problem(_teams(["Nordby", "Sorby"]))
    exclusions = {
        entry["date"]: entry for entry in problem["date_exclusions"]
    }
    assert exclusions
    assert all(entry["source"] == "holiday_policy" for entry in exclusions.values())
    assert "2026-12-26" in exclusions
    assert "2027-01-02" in exclusions


def test_verify_candidate_rejects_excluded_holiday_date() -> None:
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams)
    candidate = _plan([_tournament("T1", "2026-12-26", "Nordby", teams)])

    result = verify_candidate(candidate, problem)

    holiday_violations = [v for v in result["violations"] if v["code"] == "holiday_date_used"]
    assert holiday_violations
    assert holiday_violations[0]["tournament_id"] == "T1"
    assert result["ok"] is False


def test_verify_candidate_accepts_admissible_date() -> None:
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams)
    candidate = _plan([_tournament("T1", "2026-12-13", "Nordby", teams)])

    result = verify_candidate(candidate, problem)

    assert "holiday_date_used" not in {v["code"] for v in result["violations"]}


def test_optimizer_never_moves_onto_excluded_holiday_date() -> None:
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams)
    candidate = _plan(
        [
            _tournament("T1", "2026-10-10", "Nordby", teams),
            _tournament("T2", "2026-11-14", "Nordby", teams),
        ]
    )
    excluded = holiday_excluded_dates(WINDOW_START, WINDOW_END)

    optimized = optimize_candidate(
        candidate,
        problem,
        iterations=3000,
        seed=0,
        move_dates=True,
        move_dates_within_half=True,
    )

    landed = {date.fromisoformat(t["date"]) for t in optimized["tournaments"]}
    assert not (landed & excluded)
    assert landed  # the search did not silently drop every tournament


def test_search_neighborhood_declares_and_repairs_holiday_date() -> None:
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams)
    candidate = _plan([_tournament("T1", "2026-12-26", "Nordby", teams)])

    assert "holiday_date_used" in DATE_MOVE_VIOLATION_CODES
    assert findings_are_locally_searchable({"holiday_date_used"})
    assert "date" in supported_dimensions_for_finding(
        {"code": "holiday_date_used", "category": "hard_violation"}
    )

    repair_set = enumerate_search_neighborhood_repairs(
        candidate, problem, scope={"tournament_id": "T1"}
    )
    assert repair_set["applicable"] is True
    excluded = holiday_excluded_dates(WINDOW_START, WINDOW_END)
    for result_candidate in repair_set["result_candidates"].values():
        for tournament in result_candidate["tournaments"]:
            moved = date.fromisoformat(tournament["date"])
            assert moved not in excluded


def test_promoted_season_search_relocates_holiday_tournament(tmp_path: Path) -> None:
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams)
    plan = _plan(
        [
            _tournament("T1", "2026-12-26", "Nordby", teams),
            _tournament("T2", "2026-11-14", "Nordby", teams),
        ]
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    findings = list_findings("2026-2027", root=root)
    holiday_findings = [f for f in findings["findings"] if f["code"] == "holiday_date_used"]
    assert holiday_findings
    assert holiday_findings[0]["tournament_id"] == "T1"

    result = search("2026-2027", holiday_findings[0]["finding_id"], root=root)
    assert result["option_count"] >= 1

    applied = apply_repair(
        "2026-2027",
        result["options"][0]["option_id"],
        result["revision"],
        root=root,
    )
    assert applied.get("applied") or applied.get("ok")

    _schedule, _decisions, repaired_plan, loaded_problem = load_context("2026-2027", root=root)
    assert loaded_problem.get("date_exclusions")
    excluded = holiday_excluded_dates(WINDOW_START, WINDOW_END)
    for tournament in repaired_plan["tournaments"]:
        assert date.fromisoformat(tournament["date"]) not in excluded
    repaired_t1 = next(t for t in repaired_plan["tournaments"] if t["id"] == "T1")
    assert repaired_t1["date"] != "2026-12-26"


def test_batch_relocation_provider_returns_verified_option() -> None:
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams)
    candidate = _plan(
        [
            _tournament("T1", "2026-12-26", "Nordby", teams),
            _tournament("T2", "2026-11-14", "Nordby", teams),
        ]
    )

    repair_set = enumerate_date_policy_relocations(candidate, problem)
    assert repair_set["options"]
    option = repair_set["options"][0]

    applied = apply_date_policy_relocation_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=repair_set["candidate_fingerprint"],
    )

    assert applied["ok"] is True
    assert applied["verification"]["ok"] is True
    excluded = holiday_excluded_dates(WINDOW_START, WINDOW_END)
    for tournament in applied["candidate"]["tournaments"]:
        assert date.fromisoformat(tournament["date"]) not in excluded


def test_batch_relocation_demotes_when_no_admissible_date() -> None:
    teams = _teams(["Nordby", "Sorby"])
    holiday = date(2026, 12, 26)
    config: Dict[str, Any] = {
        "teams": teams,
        "age_groups": ["U10"],
        "parallel_games": {"U10": 2},
        "round_length_minutes": {"U10": 30},
        "ice_time_minutes": {"U10": 90},
        "rounds_per_tournament": {"U10": 3},
    }
    problem = build_planning_problem(config, None, holiday, holiday)
    plan = {
        "schema_version": 1,
        "start_date": holiday.isoformat(),
        "end_date": holiday.isoformat(),
        "tournaments": [_tournament("T1", holiday.isoformat(), "Nordby", teams)],
    }

    result = relocate_excluded_date_tournaments(plan, problem)

    assert result["moves"] == []
    assert result["unresolved"]
    assert result["demotions"]
    assert result["verification"]["ok"] is True
    obligation_ids = [
        str(entry.get("source_tournament_id")) for entry in result["candidate"]["unresolved_tournament_placements"]
    ]
    assert "T1" in obligation_ids
