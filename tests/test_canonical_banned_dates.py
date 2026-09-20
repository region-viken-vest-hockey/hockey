"""Canonical operator banned-date lifecycle (issue #406).

Generic regressions: a small promoted canonical season, a global date ban
recorded as a policy-only write, and proof that the existing banned-date
verifier path plus every automatic date-changing consumer respects it.
"""

from __future__ import annotations

from datetime import date as _date
from pathlib import Path

import pytest

from tournament_scheduler.canonical_banned_dates import (
    active_banned_dates,
    banned_date_records,
    project_banned_dates_into_problem,
)
from tournament_scheduler.canonical_state import canonical_state_revision
from tournament_scheduler.date_policy import problem_forbidden_dates
from tournament_scheduler.host_placement_repair import _candidate_weekend_dates
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.planning_contract import verify_candidate
from tournament_scheduler.season_maintenance import apply_repair, load_context
from tournament_scheduler.season_state import (
    SeasonStateError,
    add_banned_date,
    banned_date_report,
    batch_maintenance,
    load_decisions,
    load_schedule,
    move_tournament,
    promote_from_stage3,
    release_banned_dates,
)
from tournament_scheduler.testing.reviewed_export import write_reviewed_stage4_export


def _round_robin_games(labels: list[str]) -> list[dict]:
    pairs = [
        (labels[0], labels[1]),
        (labels[2], labels[3]),
        (labels[0], labels[2]),
        (labels[1], labels[3]),
        (labels[0], labels[3]),
        (labels[1], labels[2]),
    ]
    return [
        {
            "home": home,
            "away": away,
            "parallel_slot": index % 2,
            "round_number": index // 2 + 1,
        }
        for index, (home, away) in enumerate(pairs)
    ]


def _tournament(
    tournament_id: str,
    date: str,
    host_club: str,
    arena: str,
    teams: list[tuple[str, str]],
    start_time: str,
) -> dict:
    roster = [{"club": club, "label": label, "age_group": "U10"} for club, label in teams]
    return {
        "id": tournament_id,
        "date": date,
        "arena": arena,
        "age_group": "U10",
        "host_club": host_club,
        "start_time": start_time,
        "teams": roster,
        "games": _round_robin_games([label for _club, label in teams]),
    }


def _candidate() -> dict:
    return {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [
            _tournament(
                "u10-a-20260912",
                "2026-09-12",
                "Kongsberg",
                "Arena A",
                [("Kongsberg", "K1"), ("X", "X1"), ("Y", "Y1"), ("Z", "Z1")],
                "10:00",
            ),
            _tournament(
                "u10-b-20260920",
                "2026-09-20",
                "X",
                "Arena B",
                [("Kongsberg", "K1"), ("X", "X1"), ("W", "W1"), ("V", "V1")],
                "12:00",
            ),
            _tournament(
                "u10-c-20261018",
                "2026-10-18",
                "C",
                "Arena C",
                [("C", "C1"), ("C", "C2"), ("D", "D1"), ("D", "D2")],
                "14:00",
            ),
        ],
    }


def _promote(tmp_path: Path) -> tuple[Path, Path]:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    state.write_stage(StageName.PLANNING, {"plan": _candidate()}, status=StageStatus.DONE)
    write_reviewed_stage4_export(state)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    return work_dir, root


def _schedule_bytes(root: Path) -> bytes:
    return (root / "2026-2027" / "schedule.json").read_bytes()


def _revision(root: Path) -> str:
    schedule = load_schedule("2026-2027", root=root)
    decisions = load_decisions("2026-2027", root=root)
    return canonical_state_revision(schedule, decisions)


def test_add_unused_banned_date_succeeds_without_touching_schedule(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    before_schedule = _schedule_bytes(root)

    result = add_banned_date(
        season="2026-2027",
        date="2026-10-25",
        request_id="operator:policy:1",
        root=root,
        actor="tester",
        note="ice not available",
    )
    assert result["created"] is True
    assert result["banned_date"]["date"] == "2026-10-25"
    assert result["banned_date"]["satisfied"] is True
    assert result["banned_date"]["affected_tournament_ids"] == []
    assert _schedule_bytes(root) == before_schedule
    assert active_banned_dates(load_decisions("2026-2027", root=root)) == ["2026-10-25"]


def test_add_currently_used_banned_date_is_a_policy_write(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    before_schedule = _schedule_bytes(root)
    before_revision = _revision(root)

    result = add_banned_date(
        season="2026-2027",
        date="2026-09-12",
        request_id="operator:policy:2",
        root=root,
        actor="tester",
    )
    assert result["created"] is True
    assert result["banned_date"]["satisfied"] is False
    assert result["banned_date"]["affected_tournament_ids"] == ["u10-a-20260912"]
    # Policy write: the schedule is untouched even though it now violates.
    assert _schedule_bytes(root) == before_schedule
    assert _revision(root) != before_revision


def test_banned_date_report_exposes_repair_scope(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    add_banned_date(
        season="2026-2027",
        date="2026-09-12",
        request_id="operator:policy:3",
        root=root,
    )
    add_banned_date(
        season="2026-2027",
        date="2026-09-20",
        request_id="operator:policy:4",
        root=root,
    )

    report = banned_date_report("2026-2027", root=root)
    assert report["active_count"] == 2
    assert report["unsatisfied_count"] == 2
    assert report["affected_tournament_ids"] == [
        "u10-a-20260912",
        "u10-b-20260920",
    ]
    by_date = {entry["date"]: entry for entry in report["banned_dates"]}
    assert by_date["2026-09-12"]["satisfied"] is False
    assert by_date["2026-09-12"]["affected_tournament_ids"] == ["u10-a-20260912"]
    assert by_date["2026-09-20"]["request_id"] == "operator:policy:4"


def test_adding_same_date_again_is_idempotent(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    first = add_banned_date(
        season="2026-2027",
        date="2026-10-25",
        request_id="operator:policy:idem",
        root=root,
    )
    revision_after_first = _revision(root)
    second = add_banned_date(
        season="2026-2027",
        date="2026-10-25",
        request_id="operator:policy:idem",
        root=root,
    )
    assert first["created"] is True
    assert second["created"] is False
    assert second["banned_date"]["id"] == first["banned_date"]["id"]
    assert len(banned_date_records(load_decisions("2026-2027", root=root))) == 1
    assert _revision(root) == revision_after_first


def test_canonical_revision_advances_on_ban_and_unban(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    revision_before = _revision(root)
    add_banned_date(
        season="2026-2027",
        date="2026-10-25",
        request_id="operator:policy:rev",
        root=root,
    )
    revision_banned = _revision(root)
    assert revision_banned != revision_before
    release_banned_dates(
        season="2026-2027",
        root=root,
        dates=["2026-10-25"],
        note="policy withdrawn",
    )
    assert _revision(root) != revision_banned


def test_tournament_on_active_banned_date_fails_canonical_verification(
    tmp_path: Path,
) -> None:
    _work_dir, root = _promote(tmp_path)
    add_banned_date(
        season="2026-2027",
        date="2026-09-12",
        request_id="operator:policy:verify",
        root=root,
    )
    schedule, decisions, plan, problem = load_context("2026-2027", root=root)
    result = verify_candidate(plan, problem)
    codes = {violation.get("code") for violation in result.get("violations", [])}
    assert "banned_date_used" in codes
    # The promoted schedule file itself is unchanged; the violation is derived.
    assert load_decisions("2026-2027", root=root)["banned_dates"]


def test_direct_move_onto_banned_date_is_rejected(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    add_banned_date(
        season="2026-2027",
        date="2026-10-25",
        request_id="operator:policy:move",
        root=root,
    )
    before = _schedule_bytes(root)
    with pytest.raises(SeasonStateError, match="banned date"):
        move_tournament(
            season="2026-2027",
            tournament_id="u10-b-20260920",
            root=root,
            date="2026-10-25",
        )
    assert _schedule_bytes(root) == before


def test_batch_move_onto_banned_date_is_refused(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    add_banned_date(
        season="2026-2027",
        date="2026-10-25",
        request_id="operator:policy:batch",
        root=root,
    )
    before = _schedule_bytes(root)
    with pytest.raises(SeasonStateError, match="hard verification"):
        batch_maintenance(
            season="2026-2027",
            root=root,
            operations=[
                {"op": "move", "tournament_id": "u10-b-20260920", "date": "2026-10-25"}
            ],
            scope=["u10-b-20260920"],
            request_id="operator:policy:batch-repair",
        )
    assert _schedule_bytes(root) == before


def test_candidate_weekend_enumeration_excludes_banned_date(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    add_banned_date(
        season="2026-2027",
        date="2026-10-25",
        request_id="operator:policy:weekends",
        root=root,
    )
    _schedule, _decisions, _plan, problem = load_context("2026-2027", root=root)
    forbidden = problem_forbidden_dates(problem)
    assert _date(2026, 10, 25) in forbidden
    weekend_dates = {
        value.isoformat()
        for value in _candidate_weekend_dates(problem, _date(2026, 9, 20))
    }
    assert "2026-10-25" not in weekend_dates


def test_projection_unions_with_existing_problem_bans() -> None:
    decisions = {
        "banned_dates": [
            {"date": "2026-10-25", "status": "active", "request_id": "r1"},
        ]
    }
    problem = {"manual_adjustments": {"banned_dates": ["2026-11-01"]}}
    projected = project_banned_dates_into_problem(problem, decisions)
    assert projected["manual_adjustments"]["banned_dates"] == [
        "2026-10-25",
        "2026-11-01",
    ]
    # The input problem is not mutated.
    assert problem["manual_adjustments"]["banned_dates"] == ["2026-11-01"]


def test_unban_restores_date_eligibility(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    add_banned_date(
        season="2026-2027",
        date="2026-10-25",
        request_id="operator:policy:unban",
        root=root,
    )
    release_banned_dates(
        season="2026-2027",
        root=root,
        dates=["2026-10-25"],
        note="ban no longer applies",
    )
    moved = move_tournament(
        season="2026-2027",
        tournament_id="u10-b-20260920",
        root=root,
        date="2026-10-25",
    )
    by_id = {t["id"]: t for t in moved["plan"]["tournaments"]}
    assert by_id["u10-b-20260920"]["date"] == "2026-10-25"
    assert banned_date_report("2026-2027", root=root)["active_count"] == 0


def test_released_bans_are_listed_only_with_all(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    add_banned_date(
        season="2026-2027",
        date="2026-10-25",
        request_id="operator:policy:history",
        root=root,
    )
    release_banned_dates(season="2026-2027", root=root, dates=["2026-10-25"])
    assert banned_date_report("2026-2027", root=root)["active_count"] == 0
    history = banned_date_report("2026-2027", root=root, include_released=True)
    assert history["banned_dates"][0]["status"] == "released"
    assert history["banned_dates"][0]["release_reason"] == ""


def test_stale_repair_option_is_rejected_after_ban(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    revision_before = _revision(root)
    add_banned_date(
        season="2026-2027",
        date="2026-10-25",
        request_id="operator:policy:stale",
        root=root,
    )
    result = apply_repair(
        "2026-2027",
        "some-option-from-before-the-ban",
        revision_before,
        root=root,
    )
    assert result["ok"] is False
    assert result["reason"] == "stale_canonical_revision"


def test_multiple_banned_dates_work_together(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    for index, day in enumerate(("2026-10-24", "2026-10-25")):
        add_banned_date(
            season="2026-2027",
            date=day,
            request_id=f"operator:policy:multi:{index}",
            root=root,
        )
    report = banned_date_report("2026-2027", root=root)
    assert report["active_count"] == 2
    assert report["unsatisfied_count"] == 0
    assert [entry["date"] for entry in report["banned_dates"]] == [
        "2026-10-24",
        "2026-10-25",
    ]
    assert active_banned_dates(load_decisions("2026-2027", root=root)) == [
        "2026-10-24",
        "2026-10-25",
    ]


def test_replan_problem_carries_active_bans(tmp_path: Path) -> None:
    from tournament_scheduler.canonical_replan import replan_around_baseline

    _work_dir, root = _promote(tmp_path)
    add_banned_date(
        season="2026-2027",
        date="2026-10-25",
        request_id="operator:policy:replan",
        root=root,
    )
    teams = [
        dict(team)
        for tournament in _candidate()["tournaments"]
        for team in tournament["teams"]
    ]
    result = replan_around_baseline(
        season="2026-2027",
        config={"teams": teams, "parallel_games": {"U10": 2}},
        scraping_result=None,
        start_date=_date(2026, 9, 1),
        end_date=_date(2027, 4, 30),
        root=root,
        engine="local_search",
        request={"iterations": 50, "seed": 1},
    )
    assert "2026-10-25" in (
        result["problem"]["manual_adjustments"]["banned_dates"]
    )
    # No generated candidate may schedule on the banned date.
    assert all(
        tournament.get("date") != "2026-10-25"
        for tournament in result["candidate"].get("tournaments", [])
    )


def test_season_ban_date_cli_lifecycle(tmp_path: Path, capsys) -> None:
    import json

    from tournament_scheduler.cli.rvv_cli import main

    _work_dir, root = _promote(tmp_path)

    rc = main(
        [
            "season",
            "ban-date",
            "--season",
            "2026-2027",
            "--root",
            str(root),
            "--date",
            "2026-09-12",
            "--request-id",
            "operator:policy:cli",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["created"] is True
    assert payload["banned_date"]["affected_tournament_ids"] == ["u10-a-20260912"]

    rc = main(
        [
            "season",
            "banned-dates",
            "--season",
            "2026-2027",
            "--root",
            str(root),
            "--json",
        ]
    )
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["active_count"] == 1
    assert report["affected_tournament_ids"] == ["u10-a-20260912"]

    rc = main(
        [
            "season",
            "unban-date",
            "--season",
            "2026-2027",
            "--root",
            str(root),
            "--date",
            "2026-09-12",
            "--json",
        ]
    )
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["released_dates"] == ["2026-09-12"]
    assert result["active_count"] == 0
