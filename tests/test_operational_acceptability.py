"""Hard-valid vs automatically acceptable canonical repairs.

Hard verification deliberately represents some external-calendar collisions and
unresolved obligations as non-blocking manual work so scheduled-and-unresolved
seasons can be represented. These tests pin the separate acceptance policy: an
automatic canonical mutation may not *newly* introduce that work, while
pre-existing manual work stays visible and explicit operator opt-in is honoured.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tournament_scheduler.operational_acceptability import (
    CATEGORY_FIXED_BUSY_PLACEMENT,
    CATEGORY_HOST_CONFIRMATION_DEPENDENCY,
    CATEGORY_UNRESOLVED_PLACEMENT_OBLIGATION,
    check_operational_acceptability,
    operational_placement_profile,
)
from tournament_scheduler.planning_contract import verify_candidate
from tournament_scheduler.season_state import (
    SeasonStateError,
    apply_candidate,
    load_schedule,
    move_tournament,
)
from tests.test_season_maintenance import YEAR, _plan, _problem, _teams, _tournament, _write_season


def _profile(verification: dict, *, plan: dict | None = None) -> dict:
    return operational_placement_profile(plan or {"tournaments": []}, verification)


def test_new_fixed_busy_placement_is_a_regression_until_opted_in() -> None:
    before = {"manual_external_conflict_placements": [{"tournament_id": "t1"}]}
    after = {
        "manual_external_conflict_placements": [
            {"tournament_id": "t1"},
            {"tournament_id": "t2"},
        ]
    }

    rejected = check_operational_acceptability(None, before, None, after)

    assert rejected["ok"] is False
    assert [r["category"] for r in rejected["regressions"]] == [CATEGORY_FIXED_BUSY_PLACEMENT]
    assert rejected["regressions"][0]["tournament_ids"] == ["t2"]
    assert rejected["regressions"][0]["requires"] == "allow_manual_placement"
    # The pre-existing conflict is not re-reported as a regression.
    assert rejected["before_counts"][CATEGORY_FIXED_BUSY_PLACEMENT] == 1
    assert rejected["after_counts"][CATEGORY_FIXED_BUSY_PLACEMENT] == 2

    accepted = check_operational_acceptability(None, before, None, after, allow_manual_placement=True)
    assert accepted["ok"] is True
    assert accepted["added_by_category"][CATEGORY_FIXED_BUSY_PLACEMENT] == ["t2"]


def test_pre_existing_manual_work_is_never_a_new_regression() -> None:
    verification = {
        "manual_external_conflict_placements": [{"tournament_id": "t1"}],
        "manual_calendar_placements": [{"tournament_id": "t2"}],
    }
    result = check_operational_acceptability(None, verification, None, verification)
    assert result["ok"] is True
    assert result["regressions"] == []


def test_new_unresolved_placement_obligation_requires_opt_in() -> None:
    before_plan = {"tournaments": [], "unresolved_tournament_placements": []}
    after_plan = {
        "tournaments": [],
        "unresolved_tournament_placements": [{"id": "unplaced_placement:U9:2027-02-21"}],
    }
    rejected = check_operational_acceptability(before_plan, {}, after_plan, {})
    assert rejected["ok"] is False
    assert [r["category"] for r in rejected["regressions"]] == [CATEGORY_UNRESOLVED_PLACEMENT_OBLIGATION]


def test_new_host_confirmation_dependency_requires_its_own_opt_in() -> None:
    before = {"movable_allocations_used": []}
    after = {"movable_allocations_used": [{"tournament_id": "t1"}]}

    rejected = check_operational_acceptability(None, before, None, after)
    assert rejected["ok"] is False
    assert [r["category"] for r in rejected["regressions"]] == [CATEGORY_HOST_CONFIRMATION_DEPENDENCY]
    assert rejected["regressions"][0]["requires"] == "allow_host_confirmation"

    # A movable_busy placement is not covered by the manual-placement opt-in.
    still_rejected = check_operational_acceptability(None, before, None, after, allow_manual_placement=True)
    assert still_rejected["ok"] is False

    accepted = check_operational_acceptability(None, before, None, after, allow_host_confirmation=True)
    assert accepted["ok"] is True


def test_profile_reads_plan_borne_host_confirmation_flag() -> None:
    plan = {
        "tournaments": [
            {"id": "t1", "requires_host_confirmation": True},
            {"id": "t2"},
        ]
    }
    profile = _profile({}, plan=plan)
    assert profile[CATEGORY_HOST_CONFIRMATION_DEPENDENCY] == ["t1"]


# -- canonical mutation boundary --------------------------------------------


def _fixed_busy_season(tmp_path: Path, *, busy_date: str, host: str = "Nordby"):
    teams = _teams([host, "Sorby"])
    problem = _problem(teams)
    problem["club_calendar_status"] = {host: "known", "Sorby": "known"}
    problem["club_busy_intervals"] = {host: [{"date": busy_date, "start": "07:00", "end": "14:00"}]}
    plan = _plan(
        [
            _tournament("T1", "2026-10-10", host, teams),
            _tournament("T2", "2026-11-14", "Sorby", teams),
        ]
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)
    return root, plan, problem


def test_move_onto_fixed_busy_ice_is_rejected_by_default(tmp_path: Path) -> None:
    """Production shape: the candidate is hard-valid (the collision is
    represented as manual work) but must not be accepted as an automatic move."""

    root, plan, problem = _fixed_busy_season(tmp_path, busy_date="2026-10-17")
    schedule_file = root / YEAR / "schedule.json"
    decisions_file = root / YEAR / "decisions.json"
    before_schedule = schedule_file.read_bytes()
    before_decisions = decisions_file.read_bytes()

    candidate = copy.deepcopy(plan)
    candidate["tournaments"][0]["date"] = "2026-10-17"
    verification = verify_candidate(candidate, problem)
    assert verification["ok"] is True
    assert any(placement["tournament_id"] == "T1" for placement in verification["manual_external_conflict_placements"])

    with pytest.raises(SeasonStateError, match="operational placement work"):
        move_tournament(
            season=YEAR,
            tournament_id="T1",
            root=root,
            date="2026-10-17",
            problem=problem,
        )
    assert schedule_file.read_bytes() == before_schedule
    assert decisions_file.read_bytes() == before_decisions

    preview = move_tournament(
        season=YEAR,
        tournament_id="T1",
        root=root,
        date="2026-10-17",
        problem=problem,
        dry_run=True,
    )
    assert preview["move_preview"]["operational_acceptability"]["ok"] is False

    applied = move_tournament(
        season=YEAR,
        tournament_id="T1",
        root=root,
        date="2026-10-17",
        problem=problem,
        allow_manual_placement=True,
    )
    assert applied["plan"]["tournaments"][0]["date"] == "2026-10-17"


def test_apply_candidate_rechecks_operational_acceptability(tmp_path: Path) -> None:
    root, plan, problem = _fixed_busy_season(tmp_path, busy_date="2026-10-17")
    schedule_file = root / YEAR / "schedule.json"
    before_schedule = schedule_file.read_bytes()

    candidate = copy.deepcopy(plan)
    candidate["tournaments"][0]["date"] = "2026-10-17"

    with pytest.raises(SeasonStateError, match="operational placement work"):
        apply_candidate(season=YEAR, candidate=candidate, root=root, problem=problem)
    assert schedule_file.read_bytes() == before_schedule

    schedule, _decisions, _cost = apply_candidate(
        season=YEAR,
        candidate=candidate,
        root=root,
        problem=problem,
        allow_manual_placement=True,
    )
    assert schedule["plan"]["tournaments"][0]["date"] == "2026-10-17"
    assert load_schedule(YEAR, root=root)["plan"]["tournaments"][0]["date"] == "2026-10-17"
