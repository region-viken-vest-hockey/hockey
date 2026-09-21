from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.planning_contract import build_planning_problem
from tournament_scheduler.season_baseline import (
    IMPROVED,
    KNOWN,
    NEW,
    REGRESSED,
    RESOLVED,
    compare_findings_to_baseline,
    create_baseline_record,
)
from tournament_scheduler.season_state import (
    SeasonStateError,
    canonical_state_revision,
    load_decisions,
    promote_from_stage3,
    season_baseline_advance,
    season_baseline_create,
)
from tournament_scheduler.testing.reviewed_export import write_reviewed_stage4_export


def _finding(finding_id: str, *, code: str = "participation_deviation", deviation: int = -1) -> dict:
    return {
        "finding_id": finding_id,
        "code": code,
        "category": "participation",
        "severity": "strong_goal",
        "deviation": deviation,
        "actual": 2,
        "target": 3,
        "search_coverage": {"status": "bounded_search_exhausted"},
    }


def _baseline(findings: list[dict]) -> dict:
    return create_baseline_record(
        season="2026-2027",
        schedule={"plan": {"tournaments": []}},
        findings_report={"revision": "r1", "findings": findings},
        actor="tester",
        note="accepted",
    )


def test_comparison_classifies_unchanged_improved_resolved_regressed_and_new() -> None:
    baseline = _baseline(
        [
            _finding("known", deviation=-1),
            _finding("improved", deviation=-2),
            _finding("resolved", deviation=-1),
            _finding("regressed", deviation=-1),
        ]
    )

    comparison = compare_findings_to_baseline(
        baseline,
        [
            _finding("known", deviation=-1),
            _finding("improved", deviation=-1),
            _finding("regressed", deviation=-2),
            _finding("new", deviation=-1),
        ],
    )

    status_by_id = {entry["finding_id"]: entry["status"] for entry in comparison["entries"]}
    assert status_by_id == {
        "known": KNOWN,
        "improved": IMPROVED,
        "resolved": RESOLVED,
        "regressed": REGRESSED,
        "new": NEW,
    }
    assert comparison["summary"][REGRESSED] == 1
    assert comparison["summary"][NEW] == 1
    assert not comparison["ok_to_advance"]


def test_replaced_same_count_is_new_and_resolved_not_known() -> None:
    comparison = compare_findings_to_baseline(
        _baseline([_finding("old")]),
        [_finding("replacement")],
    )

    assert comparison["summary"][RESOLVED] == 1
    assert comparison["summary"][NEW] == 1
    assert comparison["summary"][KNOWN] == 0


def test_hard_findings_are_not_recorded_in_baseline() -> None:
    baseline = _baseline(
        [
            _finding("soft"),
            {"finding_id": "hard", "code": "double_booking", "category": "hard_violation", "severity": "hard"},
        ]
    )

    assert [entry["finding_id"] for entry in baseline["findings"]] == ["soft"]


def _candidate() -> dict:
    teams = [
        {"club": "A", "label": "A1", "age_group": "U10"},
        {"club": "B", "label": "B1", "age_group": "U10"},
        {"club": "C", "label": "C1", "age_group": "U10"},
        {"club": "D", "label": "D1", "age_group": "U10"},
    ]
    return {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [
            {
                "id": "u10-a-20260912",
                "date": "2026-09-12",
                "arena": "Arena A",
                "age_group": "U10",
                "host_club": "A",
                "teams": teams,
                "games": [
                    {"home": "A1", "away": "B1", "parallel_slot": 0, "round_number": 1},
                    {"home": "C1", "away": "D1", "parallel_slot": 1, "round_number": 1},
                    {"home": "A1", "away": "C1", "parallel_slot": 0, "round_number": 2},
                    {"home": "B1", "away": "D1", "parallel_slot": 1, "round_number": 2},
                    {"home": "A1", "away": "D1", "parallel_slot": 0, "round_number": 3},
                    {"home": "B1", "away": "C1", "parallel_slot": 1, "round_number": 3},
                ],
                "start_time": "10:00",
            }
        ],
    }


def _promote(tmp_path: Path, candidate: dict | None = None) -> Path:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    state.write_stage(StageName.PLANNING, {"plan": candidate or _candidate()}, status=StageStatus.DONE)
    write_reviewed_stage4_export(state)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    return root


def test_baseline_create_is_decision_only_and_advances_revision(tmp_path: Path) -> None:
    root = _promote(tmp_path)
    before_schedule = (root / "2026-2027" / "schedule.json").read_bytes()
    before_decisions = load_decisions("2026-2027", root=root)
    before_revision = canonical_state_revision(json.loads(before_schedule), before_decisions)

    result = season_baseline_create(season="2026-2027", root=root, actor="tester", note="accepted")

    assert result["baseline"]["note"] == "accepted"
    assert result["comparison"]["summary"]["NEW"] == 0
    assert (root / "2026-2027" / "schedule.json").read_bytes() == before_schedule
    after_decisions = load_decisions("2026-2027", root=root)
    assert canonical_state_revision(json.loads(before_schedule), after_decisions) != before_revision
    assert after_decisions["season_baseline"]["finding_count"] == result["baseline"]["finding_count"]


def test_baseline_advance_records_history_when_equal_or_better(tmp_path: Path) -> None:
    root = _promote(tmp_path)
    season_baseline_create(season="2026-2027", root=root, actor="tester", note="accepted")

    result = season_baseline_advance(season="2026-2027", root=root, actor="tester")

    assert result["advanced"] is True
    decisions = load_decisions("2026-2027", root=root)
    assert decisions["season_baseline_history"][0]["event"] == "advance"


def _two_club_plan() -> dict:
    teams = [
        {"club": club, "label": f"{club} {index}", "age_group": "U10"}
        for club in ("Nordby", "Sorby")
        for index in (1, 2)
    ]

    def tournament(tournament_id: str, day: str, host: str) -> dict:
        labels = [team["label"] for team in teams]
        return {
            "id": tournament_id,
            "date": day,
            "arena": f"{host} Arena",
            "age_group": "U10",
            "host_club": host,
            "teams": teams,
            "games": [
                {
                    "home": labels[index],
                    "away": labels[(index + 1) % len(labels)],
                    "parallel_slot": 0,
                    "round_number": 1,
                }
                for index in range(len(labels))
            ],
            "start_time": "10:00",
        }

    return {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [
            tournament("T1", "2026-10-10", "Nordby"),
            tournament("T2", "2026-11-14", "Nordby"),
        ],
    }


def _two_club_problem() -> dict:
    teams = [
        {"club": club, "label": f"{club} {index}", "age_group": "U10"}
        for club in ("Nordby", "Sorby")
        for index in (1, 2)
    ]
    problem = build_planning_problem(
        {
            "teams": teams,
            "age_groups": ["U10"],
            "parallel_games": {"U10": 2},
            "round_length_minutes": {"U10": 30},
            "ice_time_minutes": {"U10": 120},
            "rounds_per_tournament": {"U10": 3},
        },
        None,
        date(2026, 9, 1),
        date(2027, 4, 30),
    )
    problem["clubs"] = {club: f"{club} Arena" for club in ("Nordby", "Sorby")}
    return problem


def _seed_two_club_season(tmp_path: Path) -> Path:
    root = tmp_path / "season"
    season_dir = root / "2026-2027"
    season_dir.mkdir(parents=True)
    plan = _two_club_plan()
    revision = "rev-1"
    schedule = {
        "schema_version": 1,
        "season": "2026-2027",
        "revision": revision,
        "fingerprint": revision,
        "plan_schema_version": 1,
        "plan": plan,
        "verification_context": {"problem": _two_club_problem()},
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
    return root


def _introduce_hard_double_booking(root: Path) -> None:
    schedule_path = root / "2026-2027" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    plan = schedule["plan"]
    extra = json.loads(json.dumps(plan["tournaments"][0]))
    extra["id"] = "T3"
    extra["date"] = plan["tournaments"][0]["date"]
    extra["host_club"] = "Sorby"
    extra["arena"] = "Sorby Arena"
    plan["tournaments"].append(extra)
    schedule_path.write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_baseline_create_refuses_hard_verification_failures(tmp_path: Path) -> None:
    from tournament_scheduler.season_maintenance import list_findings

    root = _seed_two_club_season(tmp_path)
    _introduce_hard_double_booking(root)
    report = list_findings("2026-2027", root=root)
    assert report["verification_ok"] is False
    assert any(str(finding.get("severity")) == "hard" for finding in report["findings"])

    with pytest.raises(SeasonStateError, match="hard verification"):
        season_baseline_create(season="2026-2027", root=root, actor="tester")


def test_season_findings_keep_hard_failures_visible_with_active_baseline(tmp_path: Path, capsys) -> None:
    from tournament_scheduler.cli.rvv_cli import main

    root = _seed_two_club_season(tmp_path)
    assert main(["season", "baseline", "create", "--season", "2026-2027", "--root", str(root)]) == 0
    capsys.readouterr()
    _introduce_hard_double_booking(root)

    assert main(["season", "findings", "--season", "2026-2027", "--root", str(root)]) == 0
    output = capsys.readouterr().out
    # The default baseline view still surfaces the hard, never-baselineable finding.
    assert "hard_violation" in output
    assert "duplicate_participation_same_date" in output


def test_cli_baseline_create_then_findings_default_emphasizes_new(tmp_path: Path, capsys) -> None:
    from tournament_scheduler.cli.rvv_cli import main

    root = _seed_two_club_season(tmp_path)
    assert main(["season", "baseline", "create", "--season", "2026-2027", "--root", str(root), "--note", "accepted", "--json"]) == 0
    capsys.readouterr()

    # Same canonical state: every accepted finding is KNOWN and must not be
    # printed by the default (non---all) operator view.
    assert main(["season", "findings", "--season", "2026-2027", "--root", str(root)]) == 0
    output = capsys.readouterr().out
    assert "Baseline comparison" in output
    assert "No regressions relative to accepted baseline." in output
    assert "hosting_balance:U10:Sorby" not in output

    assert main(["season", "findings", "--season", "2026-2027", "--root", str(root), "--all"]) == 0
    assert "hosting_balance:U10:Sorby" in capsys.readouterr().out


def test_cli_findings_reports_new_finding_and_advance_refuses_it(tmp_path: Path, capsys) -> None:
    from tournament_scheduler.cli.rvv_cli import main

    root = _seed_two_club_season(tmp_path)
    assert main(["season", "baseline", "create", "--season", "2026-2027", "--root", str(root)]) == 0
    capsys.readouterr()

    # Introduce a genuinely new finding: a third tournament the day after T2
    # clusters every participating team's schedule without removing any
    # pre-existing hosting finding id.
    schedule_path = root / "2026-2027" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    plan = schedule["plan"]
    extra = json.loads(json.dumps(plan["tournaments"][1]))
    extra["id"] = "T3"
    extra["date"] = "2026-11-15"
    extra["host_club"] = "Sorby"
    extra["arena"] = "Sorby Arena"
    plan["tournaments"].append(extra)
    schedule_path.write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    assert main(["season", "findings", "--season", "2026-2027", "--root", str(root), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    comparison = report["baseline_comparison"]
    assert comparison["new_count"] + comparison["regression_count"] >= 1

    assert main(["season", "findings", "--season", "2026-2027", "--root", str(root)]) == 0
    output = capsys.readouterr().out
    assert "NEW" in output
    assert "NEW          0" not in output

    assert main(["season", "baseline", "advance", "--season", "2026-2027", "--root", str(root)]) == 1
    assert "NEW or REGRESSED" in capsys.readouterr().out

    # Only an explicit replacement accepts the genuinely worse state, and it
    # records the prior baseline as audit history.
    assert main([
        "season",
        "baseline",
        "replace",
        "--season",
        "2026-2027",
        "--root",
        str(root),
        "--note",
        "worse state deliberately accepted",
    ]) == 0
    assert "No NEW or REGRESSED findings relative to baseline." in capsys.readouterr().out
    decisions = load_decisions("2026-2027", root=root)
    assert decisions["season_baseline_history"][0]["event"] == "replace"
