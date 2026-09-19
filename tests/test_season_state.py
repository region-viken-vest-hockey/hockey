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
    change_protection_report,
    load_decisions,
    load_schedule,
    move_tournament,
    swap_participants,
    normalize_placements,
    planning_checkpoint_from_schedule,
    promote_from_stage3,
    release_change_protections,
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


def _legacy_unplaced_candidate() -> dict:
    candidate = _candidate()
    candidate["tournaments"][0]["manual_booking_reason"] = (
        "Ingen verifisert ledig istid for A 2026-09-12 — turneringen må plasseres manuelt."
    )
    candidate["unresolved_tournament_placements"] = [
        {
            "age_group": "U10",
            "date": "2026-09-12",
            "category": "manual_tournament_placement",
            "candidate_hosts": ["A", "B"],
            "search_hosts_tried": ["A", "B"],
        }
    ]
    return candidate


def test_normalize_placements_demotes_a_genuine_slot_failure(tmp_path: Path) -> None:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    _stage_plan(state, _legacy_unplaced_candidate())
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")

    schedule, _ = normalize_placements(season="2026-2027", root=root, actor="tester")

    assert schedule["plan"]["tournaments"] == []
    obligations = schedule["plan"]["unresolved_tournament_placements"]
    assert len(obligations) == 1
    assert obligations[0]["id"] == "unplaced_placement:U10:2026-09-12:1"
    # The richer pre-existing search evidence survives the normalization.
    assert obligations[0]["search_hosts_tried"] == ["A", "B"]
    # The canonical revision advanced and the corrected state is durable.
    assert load_schedule("2026-2027", root=root)["plan"]["tournaments"] == []


def test_normalize_placements_is_a_noop_for_a_valid_plan(tmp_path: Path) -> None:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    _stage_plan(state)
    before, _ = promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    before_bytes = (root / "2026-2027" / "schedule.json").read_bytes()

    after, _ = normalize_placements(season="2026-2027", root=root, actor="tester")

    assert after["plan"]["tournaments"] == before["plan"]["tournaments"]
    assert (root / "2026-2027" / "schedule.json").read_bytes() == before_bytes


def test_participant_swap_is_verified_atomic_and_dry_runnable(tmp_path: Path) -> None:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    candidate = _candidate()

    def four_team_games(labels: list[str]) -> list[dict]:
        return [
            {"home": labels[0], "away": labels[1], "parallel_slot": 0, "round_number": 1},
            {"home": labels[2], "away": labels[3], "parallel_slot": 1, "round_number": 1},
            {"home": labels[0], "away": labels[2], "parallel_slot": 0, "round_number": 2},
            {"home": labels[1], "away": labels[3], "parallel_slot": 1, "round_number": 2},
            {"home": labels[0], "away": labels[3], "parallel_slot": 0, "round_number": 3},
            {"home": labels[1], "away": labels[2], "parallel_slot": 1, "round_number": 3},
        ]

    tournament_b = {
        "id": "u10-b-20260920",
        "date": "2026-09-20",
        "arena": "Arena B",
        "age_group": "U10",
        "host_club": "B",
        "teams": [
            {"club": "B", "label": "B2", "age_group": "U10"},
            {"club": "E", "label": "E1", "age_group": "U10"},
            {"club": "F", "label": "F1", "age_group": "U10"},
            {"club": "G", "label": "G1", "age_group": "U10"},
        ],
        "games": four_team_games(["B2", "E1", "F1", "G1"]),
        "start_time": "12:00",
    }
    candidate["tournaments"].append(tournament_b)
    _stage_plan(state, candidate)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")

    before_schedule = (root / "2026-2027" / "schedule.json").read_bytes()
    before_decisions = (root / "2026-2027" / "decisions.json").read_bytes()
    preview = swap_participants(
        season="2026-2027",
        tournament_a_id="u10-a-20260912",
        team_a_label="D1",
        tournament_b_id="u10-b-20260920",
        team_b_label="E1",
        root=root,
        actor="tester",
        note="spread dates",
        dry_run=True,
    )
    assert preview["dry_run"] is True
    assert preview["verification_result"]["ok"] is True
    assert preview["swap"]["consequence_acceptable"] is True
    assert preview["swap"]["team_consequences"]["team_a"]["acceptable"] is True
    assert preview["swap"]["team_consequences"]["team_b"]["acceptable"] is True
    assert preview["candidate_revision"] != preview["current_revision"]
    assert (root / "2026-2027" / "schedule.json").read_bytes() == before_schedule
    assert (root / "2026-2027" / "decisions.json").read_bytes() == before_decisions

    result = swap_participants(
        season="2026-2027",
        tournament_a_id="u10-a-20260912",
        team_a_label="D1",
        tournament_b_id="u10-b-20260920",
        team_b_label="E1",
        root=root,
        actor="tester",
        note="spread dates",
    )
    assert result["dry_run"] is False
    assert result["verification_result"]["ok"] is True

    updated = load_schedule("2026-2027", root=root)
    by_id = {tournament["id"]: tournament for tournament in updated["plan"]["tournaments"]}
    assert {team["label"] for team in by_id["u10-a-20260912"]["teams"]} == {
        "A1",
        "B1",
        "C1",
        "E1",
    }
    assert {team["label"] for team in by_id["u10-b-20260920"]["teams"]} == {
        "B2",
        "D1",
        "F1",
        "G1",
    }
    assert all(
        game["home"] != "D1" and game["away"] != "D1"
        for game in by_id["u10-a-20260912"]["games"]
    )
    assert any(
        game["home"] == "D1" or game["away"] == "D1"
        for game in by_id["u10-b-20260920"]["games"]
    )

    decisions = load_decisions("2026-2027", root=root)
    events = [
        event
        for event in decisions.get("history", [])
        if event.get("event") == "participant_swap"
    ]
    assert len(events) == 1
    assert events[0]["actor"] == "tester"
    assert events[0]["note"] == "spread dates"
    assert events[0]["details"]["tournament_b_id"] == "u10-b-20260920"

    approve_tournament(
        season="2026-2027",
        tournament_id="u10-a-20260912",
        root=root,
        actor="booker",
        participants_locked=True,
    )
    locked_schedule = (root / "2026-2027" / "schedule.json").read_bytes()
    with pytest.raises(SeasonStateError, match="participant lock"):
        swap_participants(
            season="2026-2027",
            tournament_a_id="u10-a-20260912",
            team_a_label="E1",
            tournament_b_id="u10-b-20260920",
            team_b_label="D1",
            root=root,
        )
    assert (root / "2026-2027" / "schedule.json").read_bytes() == locked_schedule


def test_participant_swap_protects_request_intent_until_explicit_release(tmp_path: Path) -> None:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    candidate = _candidate()
    second = {
        "id": "u10-b-20260920",
        "date": "2026-09-20",
        "arena": "Arena B",
        "age_group": "U10",
        "host_club": "B",
        "teams": [
            {"club": "B", "label": "B2", "age_group": "U10"},
            {"club": "E", "label": "E1", "age_group": "U10"},
            {"club": "F", "label": "F1", "age_group": "U10"},
            {"club": "G", "label": "G1", "age_group": "U10"},
        ],
        "games": [],
        "start_time": "12:00",
    }
    candidate["tournaments"].append(second)
    _stage_plan(state, candidate)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")

    first = swap_participants(
        season="2026-2027",
        tournament_a_id="u10-a-20260912",
        team_a_label="D1",
        tournament_b_id="u10-b-20260920",
        team_b_label="E1",
        root=root,
        actor="tester",
        note="club asked to spread dates",
        request_id="club-request-17",
    )
    assert first["dry_run"] is False
    report = change_protection_report("2026-2027", root=root)
    assert report["active_count"] == 4
    assert {item["request_id"] for item in report["protections"]} == {"club-request-17"}

    preview = swap_participants(
        season="2026-2027",
        tournament_a_id="u10-a-20260912",
        team_a_label="E1",
        tournament_b_id="u10-b-20260920",
        team_b_label="D1",
        root=root,
        dry_run=True,
    )
    assert preview["swap"]["change_protection_acceptable"] is False
    assert preview["swap"]["existing_change_protection_violations"]

    with pytest.raises(SeasonStateError, match="undo an accepted change"):
        swap_participants(
            season="2026-2027",
            tournament_a_id="u10-a-20260912",
            team_a_label="E1",
            tournament_b_id="u10-b-20260920",
            team_b_label="D1",
            root=root,
        )

    released = release_change_protections(
        season="2026-2027",
        root=root,
        request_id="club-request-17",
        actor="tester",
        note="superseded by club-request-18",
    )
    assert len(released["released_protection_ids"]) == 4
    assert released["active_count"] == 0
    all_report = change_protection_report(
        "2026-2027",
        root=root,
        include_released=True,
    )
    assert len(all_report["protections"]) == 4
    assert {item["status"] for item in all_report["protections"]} == {"released"}

    reversed_result = swap_participants(
        season="2026-2027",
        tournament_a_id="u10-a-20260912",
        team_a_label="E1",
        tournament_b_id="u10-b-20260920",
        team_b_label="D1",
        root=root,
        actor="tester",
        request_id="club-request-18",
        note="new request explicitly supersedes the earlier change",
    )
    assert reversed_result["dry_run"] is False
    assert change_protection_report("2026-2027", root=root)["active_count"] == 4
