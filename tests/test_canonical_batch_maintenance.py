"""Atomic scoped batch maintenance for simultaneous request-constraint violations.

These are generic regressions: they build a small canonical season where two
independent ``team_unavailable`` constraints invalidate two different
tournaments, prove no single canonical mutation can make progress, and prove an
explicitly scoped in-memory batch repairs the whole set in one atomic commit
while an unaffected tournament stays byte-for-byte unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.season_state import (
    SeasonStateError,
    add_request_constraint,
    approve_tournament,
    batch_maintenance,
    load_decisions,
    load_schedule,
    move_tournament,
    promote_from_stage3,
    request_constraint_report,
    swap_participants,
)
from tournament_scheduler.testing.reviewed_export import (
    build_problem_from_candidate,
    write_reviewed_stage4_export,
)


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


def _stage_plan(
    state: PipelineState,
    candidate: dict | None = None,
    problem: dict | None = None,
) -> None:
    state.write_stage(
        StageName.PLANNING,
        {"plan": candidate or _candidate()},
        status=StageStatus.DONE,
    )
    write_reviewed_stage4_export(state, problem=problem)


def _promote(
    tmp_path: Path,
    *,
    candidate: dict | None = None,
    problem: dict | None = None,
) -> tuple[Path, Path]:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    _stage_plan(state, candidate, problem)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    return work_dir, root


def _add_unavailable(
    root: Path,
    request_id: str,
    date: str,
    *,
    club: str = "Kongsberg",
    label: str = "K1",
) -> None:
    add_request_constraint(
        season="2026-2027",
        type="team_unavailable",
        request_id=request_id,
        teams=[{"club": club, "label": label, "age_group": "U10"}],
        date_from=date,
        root=root,
        actor="tester",
    )


def _schedule_bytes(root: Path) -> bytes:
    return (root / "2026-2027" / "schedule.json").read_bytes()


def _decisions_bytes(root: Path) -> bytes:
    return (root / "2026-2027" / "decisions.json").read_bytes()


def _tournaments_by_id(root: Path) -> dict[str, dict]:
    schedule = load_schedule("2026-2027", root=root)
    return {tournament["id"]: tournament for tournament in schedule["plan"]["tournaments"]}


def test_individual_moves_deadlock_then_atomic_batch_repairs(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    _add_unavailable(root, "winter-A", "2026-09-12")
    _add_unavailable(root, "winter-B", "2026-09-20")
    assert request_constraint_report("2026-2027", root=root)["unsatisfied_count"] == 2

    before_schedule = _schedule_bytes(root)
    before_decisions = _decisions_bytes(root)
    c_before = _tournaments_by_id(root)["u10-c-20261018"]

    # 1. Moving A alone is refused because B still violates its constraint.
    with pytest.raises(SeasonStateError, match="violates an active request constraint"):
        move_tournament(
            season="2026-2027",
            tournament_id="u10-a-20260912",
            root=root,
            date="2026-09-13",
        )
    # 2. Moving B alone is refused because A still violates its constraint.
    with pytest.raises(SeasonStateError, match="violates an active request constraint"):
        move_tournament(
            season="2026-2027",
            tournament_id="u10-b-20260920",
            root=root,
            date="2026-09-21",
        )
    assert _schedule_bytes(root) == before_schedule
    assert _decisions_bytes(root) == before_decisions

    # 3./4./5. The atomic batch moves both and commits once.
    result = batch_maintenance(
        season="2026-2027",
        root=root,
        operations=[
            {"op": "move", "tournament_id": "u10-a-20260912", "date": "2026-09-13"},
            {"op": "move", "tournament_id": "u10-b-20260920", "date": "2026-09-21"},
        ],
        scope=["u10-a-20260912", "u10-b-20260920"],
        request_id="winter-repair-1",
        actor="tester",
        note="repair both independent availability requests together",
    )
    assert result["committed"] is True
    assert result["refused"] is False
    assert result["changed_outside_scope"] == []
    assert result["remaining_request_constraint_violations"] == []
    assert request_constraint_report("2026-2027", root=root)["unsatisfied_count"] == 0

    by_id = _tournaments_by_id(root)
    assert by_id["u10-a-20260912"]["date"] == "2026-09-13"
    assert by_id["u10-b-20260920"]["date"] == "2026-09-21"
    # 5. C is unchanged.
    assert by_id["u10-c-20261018"] == c_before

    decisions = load_decisions("2026-2027", root=root)
    assert decisions["canonical_state_revision"] == result["canonical_state_revision"]
    batches = [
        event
        for event in decisions.get("history", [])
        if event.get("event") == "batch_maintenance"
    ]
    assert len(batches) == 1
    assert batches[0]["details"]["request_id"] == "winter-repair-1"
    assert batches[0]["details"]["changed_tournament_ids"] == [
        "u10-a-20260912",
        "u10-b-20260920",
    ]


def test_batch_repairing_only_one_violation_is_refused_and_writes_nothing(
    tmp_path: Path,
) -> None:
    _work_dir, root = _promote(tmp_path)
    _add_unavailable(root, "winter-A", "2026-09-12")
    _add_unavailable(root, "winter-B", "2026-09-20")
    before_schedule = _schedule_bytes(root)
    before_decisions = _decisions_bytes(root)

    with pytest.raises(SeasonStateError, match="request-constraint"):
        batch_maintenance(
            season="2026-2027",
            root=root,
            operations=[
                {"op": "move", "tournament_id": "u10-a-20260912", "date": "2026-09-13"}
            ],
            scope=["u10-a-20260912"],
            request_id="partial-repair",
            actor="tester",
        )
    assert _schedule_bytes(root) == before_schedule
    assert _decisions_bytes(root) == before_decisions
    assert request_constraint_report("2026-2027", root=root)["unsatisfied_count"] == 2


def test_batch_operation_outside_declared_scope_is_refused(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    before_schedule = _schedule_bytes(root)
    before_decisions = _decisions_bytes(root)

    with pytest.raises(SeasonStateError, match="outside the declared scope"):
        batch_maintenance(
            season="2026-2027",
            root=root,
            operations=[
                {"op": "move", "tournament_id": "u10-c-20261018", "date": "2026-10-25"}
            ],
            scope=["u10-a-20260912", "u10-b-20260920"],
            request_id="out-of-scope",
            actor="tester",
        )
    assert _schedule_bytes(root) == before_schedule
    assert _decisions_bytes(root) == before_decisions


def test_batch_lock_conflict_refuses_without_partial_write(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    _add_unavailable(root, "winter-A", "2026-09-12")
    _add_unavailable(root, "winter-B", "2026-09-20")
    approve_tournament(
        season="2026-2027",
        tournament_id="u10-a-20260912",
        root=root,
        actor="booker",
        note="ice booked",
    )
    before_schedule = _schedule_bytes(root)
    before_decisions = _decisions_bytes(root)

    with pytest.raises(SeasonStateError, match="approval/lock"):
        batch_maintenance(
            season="2026-2027",
            root=root,
            operations=[
                {"op": "move", "tournament_id": "u10-a-20260912", "date": "2026-09-13"},
                {"op": "move", "tournament_id": "u10-b-20260920", "date": "2026-09-21"},
            ],
            scope=["u10-a-20260912", "u10-b-20260920"],
            request_id="locked-batch",
            actor="tester",
        )
    assert _schedule_bytes(root) == before_schedule
    assert _decisions_bytes(root) == before_decisions


def test_batch_protection_conflict_refuses_without_partial_write(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    accepted = batch_maintenance(
        season="2026-2027",
        root=root,
        operations=[
            {"op": "move", "tournament_id": "u10-a-20260912", "date": "2026-10-03"}
        ],
        scope=["u10-a-20260912"],
        request_id="accepted-move",
        actor="tester",
    )
    assert accepted["committed"] is True
    before_schedule = _schedule_bytes(root)
    before_decisions = _decisions_bytes(root)

    with pytest.raises(SeasonStateError, match="accepted change protection"):
        batch_maintenance(
            season="2026-2027",
            root=root,
            operations=[
                {"op": "move", "tournament_id": "u10-a-20260912", "date": "2026-10-10"}
            ],
            scope=["u10-a-20260912"],
            request_id="undo-move",
            actor="tester",
        )
    assert _schedule_bytes(root) == before_schedule
    assert _decisions_bytes(root) == before_decisions
    assert _tournaments_by_id(root)["u10-a-20260912"]["date"] == "2026-10-03"


def test_batch_hard_verification_failure_refuses_without_partial_write(
    tmp_path: Path,
) -> None:
    _work_dir, root = _promote(tmp_path)
    _add_unavailable(root, "winter-X", "2026-09-12", club="X", label="X1")
    _add_unavailable(root, "winter-W", "2026-09-20", club="W", label="W1")
    before_schedule = _schedule_bytes(root)
    before_decisions = _decisions_bytes(root)

    with pytest.raises(SeasonStateError, match="hard verification"):
        batch_maintenance(
            season="2026-2027",
            root=root,
            operations=[
                {"op": "move", "tournament_id": "u10-a-20260912", "date": "2026-10-25"},
                {"op": "move", "tournament_id": "u10-b-20260920", "date": "2026-10-25"},
            ],
            scope=["u10-a-20260912", "u10-b-20260920"],
            request_id="hard-failure",
            actor="tester",
        )
    assert _schedule_bytes(root) == before_schedule
    assert _decisions_bytes(root) == before_decisions


def test_batch_operational_acceptability_regression_refuses(tmp_path: Path) -> None:
    candidate = _candidate()
    problem = build_problem_from_candidate(candidate)
    problem["ice_time_minutes"] = {"U10": 120}
    problem["round_length_minutes"] = {"U10": 30}
    problem["club_calendar_status"] = {"Kongsberg": "known"}
    problem["club_busy_intervals"] = {
        "Kongsberg": [{"date": "2026-10-25", "start": "00:00", "end": "23:59"}]
    }
    _work_dir, root = _promote(tmp_path, candidate=candidate, problem=problem)
    before_schedule = _schedule_bytes(root)
    before_decisions = _decisions_bytes(root)

    with pytest.raises(SeasonStateError, match="operational placement work"):
        batch_maintenance(
            season="2026-2027",
            root=root,
            operations=[
                {"op": "move", "tournament_id": "u10-a-20260912", "date": "2026-10-25"}
            ],
            scope=["u10-a-20260912"],
            request_id="manual-placement",
            actor="tester",
        )
    assert _schedule_bytes(root) == before_schedule
    assert _decisions_bytes(root) == before_decisions


def test_batch_composes_participant_swap_and_move(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    _add_unavailable(root, "winter-A", "2026-09-12", club="Y", label="Y1")
    _add_unavailable(root, "winter-B", "2026-09-20")

    result = batch_maintenance(
        season="2026-2027",
        root=root,
        operations=[
            {
                "op": "swap_participants",
                "tournament_a": "u10-a-20260912",
                "team_a": "Y1",
                "tournament_b": "u10-b-20260920",
                "team_b": "W1",
            },
            {"op": "move", "tournament_id": "u10-b-20260920", "date": "2026-09-21"},
        ],
        scope=["u10-a-20260912", "u10-b-20260920"],
        request_id="swap-and-move",
        actor="tester",
    )
    assert result["committed"] is True
    assert result["remaining_request_constraint_violations"] == []
    assert request_constraint_report("2026-2027", root=root)["unsatisfied_count"] == 0

    by_id = _tournaments_by_id(root)
    assert {team["label"] for team in by_id["u10-a-20260912"]["teams"]} == {
        "K1",
        "X1",
        "W1",
        "Z1",
    }
    assert {team["label"] for team in by_id["u10-b-20260920"]["teams"]} == {
        "K1",
        "X1",
        "Y1",
        "V1",
    }
    assert by_id["u10-b-20260920"]["date"] == "2026-09-21"
    assert result["team_consequences"]
    assert result["consequence_acceptable"] is True


def test_existing_single_operations_keep_the_full_season_constraint_gate(
    tmp_path: Path,
) -> None:
    _work_dir, root = _promote(tmp_path)
    _add_unavailable(root, "winter-A", "2026-09-12")

    # Ordinary move still refuses while any active constraint is unsatisfied.
    with pytest.raises(SeasonStateError, match="violates an active request constraint"):
        move_tournament(
            season="2026-2027",
            tournament_id="u10-b-20260920",
            root=root,
            date="2026-09-21",
        )
    # A participant swap that does not resolve the active constraint is refused.
    with pytest.raises(SeasonStateError, match="violates an active request constraint"):
        swap_participants(
            season="2026-2027",
            tournament_a_id="u10-a-20260912",
            team_a_label="Y1",
            tournament_b_id="u10-b-20260920",
            team_b_label="W1",
            root=root,
        )


def test_batch_dry_run_reports_without_writing(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    _add_unavailable(root, "winter-A", "2026-09-12")
    _add_unavailable(root, "winter-B", "2026-09-20")
    before_schedule = _schedule_bytes(root)
    before_decisions = _decisions_bytes(root)

    preview = batch_maintenance(
        season="2026-2027",
        root=root,
        operations=[
            {"op": "move", "tournament_id": "u10-a-20260912", "date": "2026-09-13"},
            {"op": "move", "tournament_id": "u10-b-20260920", "date": "2026-09-21"},
        ],
        scope=["u10-a-20260912", "u10-b-20260920"],
        request_id="dry-run",
        actor="tester",
        dry_run=True,
    )
    assert preview["dry_run"] is True
    assert preview["committed"] is False
    assert preview["refused"] is False
    assert preview["declared_scope"] == ["u10-a-20260912", "u10-b-20260920"]
    assert preview["changed_tournament_ids"] == ["u10-a-20260912", "u10-b-20260920"]
    assert preview["changed_outside_scope"] == []
    assert preview["remaining_request_constraint_violations"] == []
    assert preview["hard_verification_ok"] is True
    assert preview["operational_acceptable"] is True
    assert preview["candidate_schedule_revision"] != preview["before_schedule_revision"]
    assert preview["candidate_canonical_revision"] != preview["before_canonical_revision"]
    assert _schedule_bytes(root) == before_schedule
    assert _decisions_bytes(root) == before_decisions


def test_batch_dry_run_reports_partial_repair_refusal(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    _add_unavailable(root, "winter-A", "2026-09-12")
    _add_unavailable(root, "winter-B", "2026-09-20")

    preview = batch_maintenance(
        season="2026-2027",
        root=root,
        operations=[
            {"op": "move", "tournament_id": "u10-a-20260912", "date": "2026-09-13"}
        ],
        scope=["u10-a-20260912"],
        request_id="dry-run-partial",
        actor="tester",
        dry_run=True,
    )
    assert preview["committed"] is False
    assert preview["refused"] is True
    assert preview["remaining_request_constraint_violations"]
    assert any("request-constraint" in reason for reason in preview["refusal_reasons"])


def test_batch_cancellation_is_a_supported_operation(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    result = batch_maintenance(
        season="2026-2027",
        root=root,
        operations=[
            {"op": "cancel", "tournament_id": "u10-c-20261018", "reason": "ice unavailable"}
        ],
        scope=["u10-c-20261018"],
        request_id="cancel-c",
        actor="tester",
    )
    assert result["committed"] is True
    by_id = _tournaments_by_id(root)
    assert by_id["u10-c-20261018"]["cancelled"] is True
    assert by_id["u10-c-20261018"]["cancellation_reason"] == "ice unavailable"


def test_batch_rejects_unknown_scope_id(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    with pytest.raises(SeasonStateError, match="unknown tournament ids"):
        batch_maintenance(
            season="2026-2027",
            root=root,
            operations=[
                {"op": "move", "tournament_id": "u10-a-20260912", "date": "2026-09-13"}
            ],
            scope=["u10-a-20260912", "does-not-exist"],
            request_id="unknown-scope",
            actor="tester",
        )
