from __future__ import annotations

import json
from pathlib import Path

from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.testing.reviewed_export import write_reviewed_stage4_export
import pytest

from tournament_scheduler.season_state import (
    SeasonStateError,
    approve_tournament,
    canonical_state_revision,
    load_decisions,
    load_schedule,
    move_tournament,
    planning_checkpoint_from_schedule,
    promote_from_stage3,
)


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


def _stage_plan(state, candidate: dict | None = None) -> None:
    state.write_stage(StageName.PLANNING, {"plan": candidate or _candidate()}, status=StageStatus.DONE)
    write_reviewed_stage4_export(state)


def _two_tournament_candidate() -> dict:
    candidate = _candidate()
    second = dict(candidate["tournaments"][0])
    second.update(
        {
            "id": "u10-b-20260920",
            "date": "2026-09-20",
            "arena": "Arena B",
            "host_club": "B",
            "start_time": "12:00",
        }
    )
    candidate["tournaments"] = [candidate["tournaments"][0], second]
    return candidate


def test_promote_writes_separate_deterministic_schedule_and_decisions(tmp_path: Path) -> None:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    _stage_plan(state)

    schedule, decisions = promote_from_stage3(work_dir=work_dir, root=root, actor="tester")

    assert schedule["season"] == "2026-2027"
    assert schedule["plan"]["tournaments"][0]["id"] == "u10-a-20260912"
    assert decisions["decisions"]["u10-a-20260912"]["status"] == "pending_review"
    assert "decisions" not in schedule
    assert "plan" not in decisions

    first_schedule_bytes = (root / "2026-2027" / "schedule.json").read_bytes()
    first_decision_bytes = (root / "2026-2027" / "decisions.json").read_bytes()

    promote_from_stage3(work_dir=work_dir, root=root, actor="tester", force=True)

    second_schedule = json.loads((root / "2026-2027" / "schedule.json").read_text(encoding="utf-8"))
    assert second_schedule["fingerprint"] == schedule["fingerprint"]
    assert json.loads(first_schedule_bytes.decode("utf-8"))["plan"] == second_schedule["plan"]
    assert json.loads(first_decision_bytes.decode("utf-8"))["decisions"] == load_decisions("2026-2027", root=root)["decisions"]


def test_approval_updates_only_decisions_file(tmp_path: Path) -> None:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    _stage_plan(state)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    schedule_file = root / "2026-2027" / "schedule.json"
    before_schedule = schedule_file.read_bytes()

    approve_tournament(
        season="2026-2027",
        tournament_id="u10-a-20260912",
        root=root,
        actor="ice-booker",
        note="ice booked",
        participants_locked=True,
    )

    assert schedule_file.read_bytes() == before_schedule
    decisions = load_decisions("2026-2027", root=root)
    record = decisions["decisions"]["u10-a-20260912"]
    assert record["status"] == "approved"
    assert record["placement_locked"] is True
    assert record["participants_locked"] is True
    assert record["approved_by"] == "ice-booker"
    assert record["note"] == "ice booked"
    assert load_schedule("2026-2027", root=root)["plan"]["tournaments"][0]["date"] == "2026-09-12"


def test_approval_advances_canonical_revision_without_touching_schedule(tmp_path: Path) -> None:
    """A decision-only approval is still an effective-state change."""
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    _stage_plan(state)
    schedule, decisions = promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    before_revision = canonical_state_revision(schedule, decisions)
    schedule_file = root / "2026-2027" / "schedule.json"
    before_schedule = schedule_file.read_bytes()

    approve_tournament(
        season="2026-2027",
        tournament_id="u10-a-20260912",
        root=root,
        actor="ice-booker",
        note="ice booked",
    )

    updated_schedule = load_schedule("2026-2027", root=root)
    updated_decisions = load_decisions("2026-2027", root=root)
    after_revision = canonical_state_revision(updated_schedule, updated_decisions)
    assert after_revision != before_revision
    assert updated_decisions["canonical_state_revision"] == after_revision
    assert schedule_file.read_bytes() == before_schedule


def test_move_mutates_schedule_when_unlocked_and_rejects_locked_moves(tmp_path: Path) -> None:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    _stage_plan(state)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")

    moved = move_tournament(
        season="2026-2027",
        tournament_id="u10-a-20260912",
        root=root,
        date="2026-09-13",
        arena="Arena B",
        start_time="11:00",
        actor="mover",
        note="requested slot",
    )
    tournament = moved["plan"]["tournaments"][0]
    assert tournament["id"] == "u10-a-20260912"
    assert tournament["date"] == "2026-09-13"
    assert tournament["arena"] == "Arena B"
    assert tournament["start_time"] == "11:00"
    decisions = load_decisions("2026-2027", root=root)
    move_events = [event for event in decisions.get("history", []) if event.get("event") == "move"]
    assert len(move_events) == 1
    assert move_events[0]["actor"] == "mover"
    assert move_events[0]["note"] == "requested slot"
    assert move_events[0]["details"]["old_placement"]["date"] == "2026-09-12"
    assert move_events[0]["details"]["new_placement"]["date"] == "2026-09-13"

    approve_tournament(season="2026-2027", tournament_id="u10-a-20260912", root=root, actor="booker")
    before = (root / "2026-2027" / "schedule.json").read_bytes()
    before_decisions = (root / "2026-2027" / "decisions.json").read_bytes()
    with pytest.raises(SeasonStateError):
        move_tournament(season="2026-2027", tournament_id="u10-a-20260912", root=root, date="2026-09-14")
    assert (root / "2026-2027" / "schedule.json").read_bytes() == before
    assert (root / "2026-2027" / "decisions.json").read_bytes() == before_decisions


def test_move_clears_stale_movable_host_confirmation(tmp_path: Path) -> None:
    """issue #373: a placement that required the host to move a movable event
    must not keep claiming that confirmation after it is moved elsewhere."""
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    candidate = _candidate()
    candidate["tournaments"][0]["requires_host_confirmation"] = True
    candidate["tournaments"][0]["host_confirmation_reason"] = "Åpen ishall — open ice"
    _stage_plan(state, candidate)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    assert load_schedule("2026-2027", root=root)["plan"]["tournaments"][0]["requires_host_confirmation"] is True

    moved = move_tournament(
        season="2026-2027",
        tournament_id="u10-a-20260912",
        root=root,
        start_time="11:00",
    )["plan"]["tournaments"][0]

    assert "requires_host_confirmation" not in moved
    assert "host_confirmation_reason" not in moved


def test_move_changes_only_intended_fields(tmp_path: Path) -> None:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    _stage_plan(state)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    original = load_schedule("2026-2027", root=root)["plan"]["tournaments"][0]

    moved = move_tournament(
        season="2026-2027",
        tournament_id="u10-a-20260912",
        root=root,
        arena="Arena B",
    )["plan"]["tournaments"][0]

    for field in ("id", "date", "age_group", "host_club", "start_time", "teams", "games"):
        assert moved[field] == original[field], field
    assert moved["arena"] == "Arena B"


def test_unknown_or_invalid_move_leaves_canonical_files_unchanged(tmp_path: Path) -> None:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    _stage_plan(state)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    before = (root / "2026-2027" / "schedule.json").read_bytes()
    before_decisions = (root / "2026-2027" / "decisions.json").read_bytes()

    with pytest.raises(SeasonStateError):
        move_tournament(season="2026-2027", tournament_id="does-not-exist", root=root, date="2026-09-13")

    assert (root / "2026-2027" / "schedule.json").read_bytes() == before
    assert (root / "2026-2027" / "decisions.json").read_bytes() == before_decisions


def test_dry_run_move_verifies_without_writing(tmp_path: Path) -> None:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    _stage_plan(state)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    before = (root / "2026-2027" / "schedule.json").read_bytes()
    before_decisions = (root / "2026-2027" / "decisions.json").read_bytes()

    preview = move_tournament(
        season="2026-2027",
        tournament_id="u10-a-20260912",
        root=root,
        date="2026-09-13",
        dry_run=True,
    )

    assert preview["dry_run"] is True
    assert preview["move_preview"]["old_placement"]["date"] == "2026-09-12"
    assert preview["move_preview"]["new_placement"]["date"] == "2026-09-13"
    assert (root / "2026-2027" / "schedule.json").read_bytes() == before
    assert (root / "2026-2027" / "decisions.json").read_bytes() == before_decisions


def test_move_rejects_team_date_collision_and_cross_half_without_mutation(tmp_path: Path) -> None:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    _stage_plan(state, _two_tournament_candidate())
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    before = (root / "2026-2027" / "schedule.json").read_bytes()
    before_decisions = (root / "2026-2027" / "decisions.json").read_bytes()

    with pytest.raises(SeasonStateError) as collision:
        move_tournament(season="2026-2027", tournament_id="u10-a-20260912", root=root, date="2026-09-20")
    assert "scheduled in 2 tournaments" in str(collision.value)

    with pytest.raises(SeasonStateError) as cross_half:
        move_tournament(season="2026-2027", tournament_id="u10-a-20260912", root=root, date="2027-01-10")
    assert "crosses planning half" in str(cross_half.value)
    assert (root / "2026-2027" / "schedule.json").read_bytes() == before
    assert (root / "2026-2027" / "decisions.json").read_bytes() == before_decisions


def test_canonical_state_survives_pipeline_deletion_and_reloads(tmp_path: Path) -> None:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    _stage_plan(state)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")

    import shutil

    shutil.rmtree(work_dir)

    schedule = load_schedule("2026-2027", root=root)
    decisions = load_decisions("2026-2027", root=root)
    checkpoint = planning_checkpoint_from_schedule(schedule)

    assert checkpoint["plan"] == schedule["plan"]
    assert checkpoint["plan"]["tournaments"][0]["id"] == "u10-a-20260912"
    assert decisions["decisions"]["u10-a-20260912"]["status"] == "pending_review"


def test_repeated_noop_promotion_keeps_ids_and_order(tmp_path: Path) -> None:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    _stage_plan(state)

    first, _ = promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    second, _ = promote_from_stage3(work_dir=work_dir, root=root, actor="tester", force=True)

    first_ids = [t["id"] for t in first["plan"]["tournaments"]]
    second_ids = [t["id"] for t in second["plan"]["tournaments"]]
    assert first_ids == second_ids
    assert first["fingerprint"] == second["fingerprint"]
