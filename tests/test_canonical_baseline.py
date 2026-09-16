"""Unit tests for baseline-aware canonical planning (issue #355)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from tournament_scheduler.canonical_baseline import (
    build_canonical_baseline,
    change_cost,
    locked_dates,
    pinned_tournament_ids,
    verify_canonical_locks,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.planning_contract import build_planning_problem, verify_candidate
from tournament_scheduler.season_state import (
    SeasonStateError,
    apply_candidate,
    approve_tournament,
    load_decisions,
    load_schedule,
    promote_from_stage3,
)


def _teams(club_letters=("A", "B", "C", "D")):
    return [
        {"club": club, "label": f"{club}1", "age_group": "U10"}
        for club in club_letters
    ]


def _tournament(t_id, *, date_str="2026-09-12", arena="Arena A", host="A", start_time="10:00", teams=None):
    teams = teams if teams is not None else _teams()
    labels = [team["label"] for team in teams]
    games = [
        {"home": labels[i], "away": labels[j], "parallel_slot": 0, "round_number": 1}
        for i in range(len(labels))
        for j in range(i + 1, len(labels))
    ]
    return {
        "id": t_id,
        "date": date_str,
        "arena": arena,
        "age_group": "U10",
        "host_club": host,
        "teams": teams,
        "games": games,
        "start_time": start_time,
    }


def _plan(tournaments):
    return {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": tournaments}


def _schedule(tournaments, *, season="2026-2027", revision="rev-1"):
    return {"season": season, "revision": revision, "fingerprint": revision, "plan": _plan(tournaments)}


def test_build_canonical_baseline_marks_locks_and_dates():
    schedule = _schedule([_tournament("t1", date_str="2026-09-12"), _tournament("t2", date_str="2026-10-10")])
    decisions = {
        "decisions": {
            "t1": {"placement_locked": True, "participants_locked": False},
            "t2": {"placement_locked": False, "participants_locked": True},
        }
    }

    baseline = build_canonical_baseline(schedule, decisions)

    assert baseline["season"] == "2026-2027"
    assert baseline["revision"] == "rev-1"
    assert baseline["locks"] == {
        "t1": {"placement": True, "participants": False},
        "t2": {"placement": False, "participants": True},
    }
    assert pinned_tournament_ids(baseline) == ["t1", "t2"]
    assert locked_dates(baseline) == ["2026-09-12"]
    assert {snapshot["id"] for snapshot in baseline["tournaments"]} == {"t1", "t2"}


def test_verify_canonical_locks_reports_placement_participants_and_missing():
    schedule = _schedule([_tournament("t1"), _tournament("t2")])
    baseline = build_canonical_baseline(
        schedule,
        {"decisions": {"t1": {"placement_locked": True}, "t2": {"participants_locked": True}}},
    )

    moved = _plan([_tournament("t1", date_str="2026-09-19"), _tournament("t2")])
    codes = {v["code"] for v in verify_canonical_locks(baseline, moved)}
    assert "canonical_placement_locked" in codes

    swapped = _plan(
        [
            _tournament("t1"),
            _tournament("t2", teams=_teams(("A", "B", "C", "E"))),
        ]
    )
    codes = {v["code"] for v in verify_canonical_locks(baseline, swapped)}
    assert codes == {"canonical_participants_locked"}

    dropped = _plan([_tournament("t2")])
    codes = {v["code"] for v in verify_canonical_locks(baseline, dropped)}
    assert "canonical_locked_tournament_missing" in codes


def test_change_cost_classifies_each_tournament_once():
    baseline = build_canonical_baseline(
        _schedule(
            [
                _tournament("t1"),
                _tournament("t2", date_str="2026-10-10"),
                _tournament("t3", date_str="2026-11-07"),
                _tournament("t4", date_str="2026-12-05"),
            ]
        ),
        {"decisions": {}},
    )
    candidate = _plan(
        [
            _tournament("t1"),  # unchanged
            _tournament("t2", date_str="2026-10-10", teams=_teams(("A", "B", "C", "E"))),  # participants
            _tournament("t3", date_str="2026-11-14"),  # placement
            _tournament("t5", date_str="2027-01-09"),  # replacement (t4 removed, t5 new)
        ]
    )

    cost = change_cost(baseline, candidate)

    assert cost["counts"] == {"none": 1, "participants": 1, "placement": 1, "replacement": 2}
    assert cost["total"] == pytest.approx(1.0 + 3.0 + 2 * 10.0)


def test_change_cost_weights_are_configurable():
    baseline = build_canonical_baseline(_schedule([_tournament("t1")]), {"decisions": {}})
    candidate = _plan([_tournament("t1", date_str="2026-09-19")])

    cost = change_cost(baseline, candidate, weights={"placement": 0.5})

    assert cost["counts"]["placement"] == 1
    assert cost["total"] == pytest.approx(0.5)


def test_build_planning_problem_applies_canonical_baseline_and_verifies_locks():
    schedule = _schedule([_tournament("t1")])
    baseline = build_canonical_baseline(schedule, {"decisions": {"t1": {"placement_locked": True}}})
    config = {"teams": _teams()}

    problem = build_planning_problem(
        config, None, date(2026, 9, 1), date(2027, 4, 30), canonical_baseline=baseline
    )

    assert problem["canonical_baseline"] is baseline
    assert "t1" in problem["manual_adjustments"]["pinned_tournament_ids"]
    assert "2026-09-12" in problem["manual_adjustments"]["locked_dates"]

    moved = _plan([_tournament("t1", date_str="2026-09-19")])
    result = verify_candidate(moved, problem)
    assert not result["ok"]
    assert "canonical_placement_locked" in {v["code"] for v in result["violations"]}

    unchanged = _plan([_tournament("t1")])
    assert verify_candidate(unchanged, problem)["ok"]


def test_build_planning_problem_reads_baseline_from_config():
    schedule = _schedule([_tournament("t1")])
    baseline = build_canonical_baseline(schedule, {"decisions": {"t1": {"placement_locked": True}}})
    config = {"teams": _teams(), "canonical_baseline": baseline}

    problem = build_planning_problem(config, None, date(2026, 9, 1), date(2027, 4, 30))

    assert problem["canonical_baseline"] == baseline
    assert "t1" in problem["manual_adjustments"]["pinned_tournament_ids"]


def _promote(tmp_path, tournaments):
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    state.write_stage(StageName.PLANNING, {"plan": _plan(tournaments)}, status=StageStatus.DONE)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    return root


def test_apply_candidate_preserves_approvals_and_reconciles_records(tmp_path):
    root = _promote(tmp_path, [_tournament("t1"), _tournament("t2", date_str="2026-10-10")])
    approve_tournament(season="2026-2027", tournament_id="t1", root=root, actor="booker")

    candidate = _plan(
        [
            _tournament("t1"),
            _tournament("t2", date_str="2026-10-17"),
            _tournament("t3", date_str="2027-01-09"),
        ]
    )
    schedule, decisions, cost = apply_candidate(season="2026-2027", candidate=candidate, root=root)

    assert {t["id"] for t in schedule["plan"]["tournaments"]} == {"t1", "t2", "t3"}
    assert decisions["decisions"]["t1"]["status"] == "approved"
    assert decisions["decisions"]["t2"]["status"] == "pending_review"
    assert decisions["decisions"]["t3"]["status"] == "pending_review"
    assert cost["counts"]["none"] == 1
    assert cost["counts"]["placement"] == 1
    assert cost["counts"]["replacement"] == 1
    assert load_schedule("2026-2027", root=root)["revision"] == schedule["revision"]


def test_apply_candidate_rejects_locked_change_without_writing(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    approve_tournament(season="2026-2027", tournament_id="t1", root=root, actor="booker")
    schedule_file = root / "2026-2027" / "schedule.json"
    decisions_file = root / "2026-2027" / "decisions.json"
    before_schedule = schedule_file.read_bytes()
    before_decisions = decisions_file.read_bytes()

    moved = _plan([_tournament("t1", date_str="2026-09-19")])
    with pytest.raises(SeasonStateError):
        apply_candidate(season="2026-2027", candidate=moved, root=root)

    assert schedule_file.read_bytes() == before_schedule
    assert decisions_file.read_bytes() == before_decisions


def test_apply_candidate_rejects_hard_verification_failure(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    before = (root / "2026-2027" / "schedule.json").read_bytes()

    # Odd-sized tournament: a self-consistency hard violation.
    bad = _plan([_tournament("t1", teams=_teams(("A", "B", "C")))])
    with pytest.raises(SeasonStateError):
        apply_candidate(season="2026-2027", candidate=bad, root=root)

    assert (root / "2026-2027" / "schedule.json").read_bytes() == before
    assert load_decisions("2026-2027", root=root)["decisions"]["t1"]["status"] == "pending_review"


def test_replan_around_baseline_preserves_ids_and_reports_change_cost(tmp_path):
    from tournament_scheduler.canonical_replan import replan_around_baseline

    first = _teams(("A", "B", "C", "D"))
    second = [
        {"club": club, "label": f"{club}1", "age_group": "U10"} for club in ("E", "F", "G", "H")
    ]
    t1 = _tournament("t1", host="A", teams=first)
    t2 = _tournament("t2", date_str="2026-10-10", arena="Arena B", host="E", teams=second)
    root = _promote(tmp_path, [t1, t2])
    config = {"teams": first + second, "parallel_games": {"U10": 2}}

    result = replan_around_baseline(
        season="2026-2027",
        config=config,
        scraping_result=None,
        start_date=date(2026, 9, 1),
        end_date=date(2027, 4, 30),
        root=root,
        engine="local_search",
        request={"iterations": 200, "seed": 3},
    )

    assert result["lock_violations"] == []
    assert {t["id"] for t in result["candidate"]["tournaments"]} == {"t1", "t2"}
    assert result["change_cost"]["baseline_tournament_count"] == 2
    assert result["verification"]["ok"], result["verification"]["violations"]


def test_season_diff_cli_reports_change_cost(tmp_path, capsys):
    from tournament_scheduler.cli.rvv_cli import main

    root = _promote(tmp_path, [_tournament("t1")])
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(
        json.dumps(_plan([_tournament("t1", date_str="2026-09-19")])), encoding="utf-8"
    )

    rc = main(
        [
            "season",
            "diff",
            "--season",
            "2026-2027",
            "--candidate",
            str(candidate_path),
            "--root",
            str(root),
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "placement: 1" in output
    assert "total: 3.0" in output
