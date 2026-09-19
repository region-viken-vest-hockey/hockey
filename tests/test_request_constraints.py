from __future__ import annotations

from pathlib import Path

import pytest

from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.request_constraints import (
    RequestConstraintError,
    constraint_id,
    constraint_violations,
    validate_and_normalize,
)
from tournament_scheduler.season_state import (
    SeasonStateError,
    add_request_constraint,
    apply_candidate,
    canonical_state_revision,
    load_decisions,
    load_schedule,
    move_tournament,
    promote_from_stage3,
    release_request_constraints,
    request_constraint_report,
    swap_participants,
)
from tournament_scheduler.testing.reviewed_export import write_reviewed_stage4_export


def _round_robin_games(labels: list[str]) -> list[dict]:
    games: list[dict] = []
    pairs = [
        (labels[0], labels[1]),
        (labels[2], labels[3]),
        (labels[0], labels[2]),
        (labels[1], labels[3]),
        (labels[0], labels[3]),
        (labels[1], labels[2]),
    ]
    for index, (home, away) in enumerate(pairs):
        games.append(
            {
                "home": home,
                "away": away,
                "parallel_slot": index % 2,
                "round_number": index // 2 + 1,
            }
        )
    return games


def _tournament(
    tournament_id: str,
    date: str,
    host_club: str,
    arena: str,
    teams: list[tuple[str, str]],
    start_time: str,
) -> dict:
    roster = [
        {"club": club, "label": label, "age_group": "U10"} for club, label in teams
    ]
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
    """Two U10 tournaments sharing Kongsberg K1 and X X1 across dates."""
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
        ],
    }


def _stage_plan(state: PipelineState, candidate: dict | None = None) -> None:
    state.write_stage(
        StageName.PLANNING,
        {"plan": candidate or _candidate()},
        status=StageStatus.DONE,
    )
    write_reviewed_stage4_export(state)


def _promote(tmp_path: Path) -> tuple[Path, Path]:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    _stage_plan(state)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    return work_dir, root


def _unit_plan() -> dict:
    return {
        "tournaments": [
            _tournament(
                "t-a",
                "2027-02-21",
                "Kongsberg",
                "Arena A",
                [("Kongsberg", "K1"), ("X", "X1"), ("Y", "Y1"), ("Z", "Z1")],
                "10:00",
            ),
            _tournament(
                "t-b",
                "2027-02-28",
                "X",
                "Arena B",
                [("Kongsberg", "K1"), ("X", "X1"), ("W", "W1"), ("V", "V1")],
                "12:00",
            ),
        ]
    }


# -- unit: typed model ------------------------------------------------------


def test_constraint_id_is_deterministic_and_request_scoped() -> None:
    plan = _unit_plan()
    first = validate_and_normalize(
        {
            "type": "team_unavailable",
            "request_id": "club-1",
            "teams": [{"club": "Kongsberg", "label": "K1", "age_group": "U10"}],
            "date_from": "2027-02-21",
        },
        plan,
    )
    retry = validate_and_normalize(
        {
            "type": "team_unavailable",
            "request_id": "club-1",
            "teams": [{"club": "Kongsberg", "label": "K1", "age_group": "U10"}],
            "date_from": "2027-02-21",
        },
        plan,
    )
    other_request = validate_and_normalize(
        {
            "type": "team_unavailable",
            "request_id": "club-2",
            "teams": [{"club": "Kongsberg", "label": "K1", "age_group": "U10"}],
            "date_from": "2027-02-21",
        },
        plan,
    )
    assert first == retry
    assert first["id"] == constraint_id(first)
    assert other_request["id"] != first["id"]


def test_validation_rejects_malformed_and_ambiguous_constraints() -> None:
    plan = _unit_plan()
    with pytest.raises(RequestConstraintError, match="Unsupported"):
        validate_and_normalize({"type": "unknown", "request_id": "r"}, plan)
    with pytest.raises(RequestConstraintError, match="Unknown team identity"):
        validate_and_normalize(
            {
                "type": "team_unavailable",
                "request_id": "r",
                "teams": [{"club": "Nobody", "label": "N1", "age_group": "U10"}],
                "date_from": "2027-02-21",
            },
            plan,
        )
    ambiguous_plan = {
        "tournaments": [
            _tournament(
                "t-u10",
                "2027-02-21",
                "Kongsberg",
                "Arena A",
                [("Kongsberg", "K1"), ("X", "X1"), ("Y", "Y1"), ("Z", "Z1")],
                "10:00",
            ),
            {
                **_tournament(
                    "t-u12",
                    "2027-02-28",
                    "Kongsberg",
                    "Arena B",
                    [("Kongsberg", "K1"), ("X", "X1"), ("Y", "Y1"), ("Z", "Z1")],
                    "12:00",
                ),
                "age_group": "U12",
            },
        ]
    }
    # The same club+label exists in two age groups, so omitting the age group
    # is ambiguous and must be rejected rather than guessed.
    ambiguous_plan["tournaments"][1]["teams"] = [
        {"club": club, "label": label, "age_group": "U12"}
        for club, label in [("Kongsberg", "K1"), ("X", "X1"), ("Y", "Y1"), ("Z", "Z1")]
    ]
    with pytest.raises(RequestConstraintError, match="Ambiguous"):
        validate_and_normalize(
            {
                "type": "team_unavailable",
                "request_id": "r",
                "teams": [{"club": "Kongsberg", "label": "K1"}],
                "date_from": "2027-02-21",
            },
            ambiguous_plan,
        )
    with pytest.raises(RequestConstraintError, match="Invalid date range"):
        validate_and_normalize(
            {
                "type": "team_unavailable",
                "request_id": "r",
                "teams": [{"club": "Kongsberg", "label": "K1", "age_group": "U10"}],
                "date_from": "2027-02-28",
                "date_to": "2027-02-21",
            },
            plan,
        )
    with pytest.raises(RequestConstraintError, match="positive"):
        validate_and_normalize(
            {
                "type": "minimum_gap",
                "request_id": "r",
                "teams": [{"club": "Kongsberg", "label": "K1", "age_group": "U10"}],
                "min_days": 0,
            },
            plan,
        )
    with pytest.raises(RequestConstraintError, match="distinct"):
        validate_and_normalize(
            {
                "type": "opponent_avoidance",
                "request_id": "r",
                "teams": [
                    {"club": "Kongsberg", "label": "K1", "age_group": "U10"},
                    {"club": "Kongsberg", "label": "K1", "age_group": "U10"},
                ],
                "date_from": "2027-02-21",
            },
            plan,
        )


def test_team_unavailable_violation_is_derived_from_the_plan() -> None:
    plan = _unit_plan()
    constraint = validate_and_normalize(
        {
            "type": "team_unavailable",
            "request_id": "r",
            "teams": [{"club": "Kongsberg", "label": "K1", "age_group": "U10"}],
            "date_from": "2027-02-21",
            "date_to": "2027-02-21",
        },
        plan,
    )
    violations = constraint_violations(plan, constraint)
    assert len(violations) == 1
    assert violations[0]["code"] == "team_unavailable"
    assert violations[0]["tournament_id"] == "t-a"


def test_minimum_gap_violation_reports_the_pair() -> None:
    plan = _unit_plan()
    constraint = validate_and_normalize(
        {
            "type": "minimum_gap",
            "request_id": "r",
            "teams": [{"club": "Kongsberg", "label": "K1", "age_group": "U10"}],
            "min_days": 21,
        },
        plan,
    )
    violations = constraint_violations(plan, constraint)
    assert len(violations) == 1
    assert violations[0]["code"] == "minimum_gap"
    assert violations[0]["tournament_ids"] == ["t-a", "t-b"]
    assert violations[0]["gap_days"] == 7


def test_opponent_avoidance_violation_is_scoped_by_date() -> None:
    plan = _unit_plan()
    constraint = validate_and_normalize(
        {
            "type": "opponent_avoidance",
            "request_id": "r",
            "teams": [
                {"club": "Kongsberg", "label": "K1", "age_group": "U10"},
                {"club": "X", "label": "X1", "age_group": "U10"},
            ],
            "date_from": "2027-02-28",
            "date_to": "2027-02-28",
        },
        plan,
    )
    violations = constraint_violations(plan, constraint)
    assert len(violations) == 1
    assert violations[0]["code"] == "opponent_avoidance"
    assert violations[0]["tournament_id"] == "t-b"


# -- integration: canonical lifecycle --------------------------------------


def test_add_constraint_allows_current_violation_and_advances_revision(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    schedule_before = (root / "2026-2027" / "schedule.json").read_bytes()
    decisions_before = load_decisions("2026-2027", root=root)
    revision_before = canonical_state_revision(
        load_schedule("2026-2027", root=root), decisions_before
    )

    result = add_request_constraint(
        season="2026-2027",
        type="team_unavailable",
        request_id="club-request-42",
        teams=[{"club": "Kongsberg", "label": "K1", "age_group": "U10"}],
        date_from="2026-09-12",
        actor="tester",
        note="Kongsberg cannot play 12 September",
        root=root,
    )

    assert result["created"] is True
    assert result["constraint"]["satisfied"] is False
    assert result["constraint"]["violations"]
    assert result["canonical_state_revision"] != revision_before
    # A decision-only write must not touch the schedule.
    assert (root / "2026-2027" / "schedule.json").read_bytes() == schedule_before

    report = request_constraint_report("2026-2027", root=root)
    assert report["active_count"] == 1
    assert report["unsatisfied_count"] == 1
    assert report["constraints"][0]["request_id"] == "club-request-42"

    updated_decisions = load_decisions("2026-2027", root=root)
    assert updated_decisions["canonical_state_revision"] == result["canonical_state_revision"]
    events = [
        event
        for event in updated_decisions.get("history", [])
        if event.get("event") == "add_request_constraint"
    ]
    assert len(events) == 1


def test_add_constraint_is_idempotent_for_the_same_request(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    definition = {
        "season": "2026-2027",
        "type": "minimum_gap",
        "request_id": "club-request-7",
        "teams": [{"club": "Kongsberg", "label": "K1", "age_group": "U10"}],
        "min_days": 14,
        "root": root,
        "actor": "tester",
    }
    first = add_request_constraint(**definition)
    second = add_request_constraint(**definition)

    assert first["created"] is True
    assert second["created"] is False
    assert first["constraint"]["id"] == second["constraint"]["id"]
    assert request_constraint_report("2026-2027", root=root)["active_count"] == 1
    decisions = load_decisions("2026-2027", root=root)
    assert len(decisions["request_constraints"]) == 1


def test_move_blocked_by_active_constraint_until_released(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    add_request_constraint(
        season="2026-2027",
        type="team_unavailable",
        request_id="club-request-unavailable",
        teams=[{"club": "Kongsberg", "label": "K1", "age_group": "U10"}],
        date_from="2026-09-13",
        root=root,
        actor="tester",
    )
    schedule_before = (root / "2026-2027" / "schedule.json").read_bytes()

    preview = move_tournament(
        season="2026-2027",
        tournament_id="u10-a-20260912",
        root=root,
        date="2026-09-13",
        dry_run=True,
    )
    assert preview["move_preview"]["request_constraint_acceptable"] is False
    assert preview["move_preview"]["request_constraint_violations"]

    with pytest.raises(SeasonStateError, match="violates an active request constraint"):
        move_tournament(
            season="2026-2027",
            tournament_id="u10-a-20260912",
            root=root,
            date="2026-09-13",
        )
    assert (root / "2026-2027" / "schedule.json").read_bytes() == schedule_before

    release_request_constraints(
        season="2026-2027",
        root=root,
        request_id="club-request-unavailable",
        actor="tester",
        note="superseded by club-request-new",
    )
    moved = move_tournament(
        season="2026-2027",
        tournament_id="u10-a-20260912",
        root=root,
        date="2026-09-13",
        request_id="club-request-new",
    )
    assert moved["plan"]["tournaments"][0]["date"] == "2026-09-13"


def test_minimum_gap_constraint_blocks_still_violating_move_and_allows_repair(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    add_request_constraint(
        season="2026-2027",
        type="minimum_gap",
        request_id="club-request-gap",
        teams=[{"club": "Kongsberg", "label": "K1", "age_group": "U10"}],
        min_days=21,
        root=root,
        actor="tester",
    )

    with pytest.raises(SeasonStateError, match="violates an active request constraint"):
        move_tournament(
            season="2026-2027",
            tournament_id="u10-a-20260912",
            root=root,
            date="2026-09-13",
        )

    moved = move_tournament(
        season="2026-2027",
        tournament_id="u10-b-20260920",
        root=root,
        date="2026-10-25",
        request_id="club-request-gap-repair",
    )
    assert moved["plan"]["tournaments"][1]["date"] == "2026-10-25"
    report = request_constraint_report("2026-2027", root=root)
    assert report["unsatisfied_count"] == 0
    assert report["constraints"][0]["satisfied"] is True


def test_opponent_avoidance_blocks_a_conflicting_move(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    add_request_constraint(
        season="2026-2027",
        type="opponent_avoidance",
        request_id="club-request-avoid",
        teams=[
            {"club": "Kongsberg", "label": "K1", "age_group": "U10"},
            {"club": "X", "label": "X1", "age_group": "U10"},
        ],
        date_from="2026-10-25",
        date_to="2026-10-25",
        root=root,
        actor="tester",
    )
    assert request_constraint_report("2026-2027", root=root)["unsatisfied_count"] == 0

    with pytest.raises(SeasonStateError, match="violates an active request constraint"):
        move_tournament(
            season="2026-2027",
            tournament_id="u10-b-20260920",
            root=root,
            date="2026-10-25",
        )


def test_swap_and_generic_apply_are_blocked_by_active_constraint(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    add_request_constraint(
        season="2026-2027",
        type="team_unavailable",
        request_id="club-request-block",
        teams=[{"club": "Kongsberg", "label": "K1", "age_group": "U10"}],
        date_from="2026-09-12",
        root=root,
        actor="tester",
    )

    with pytest.raises(SeasonStateError, match="violates an active request constraint"):
        swap_participants(
            season="2026-2027",
            tournament_a_id="u10-a-20260912",
            team_a_label="Y1",
            tournament_b_id="u10-b-20260920",
            team_b_label="W1",
            root=root,
        )

    schedule = load_schedule("2026-2027", root=root)
    with pytest.raises(SeasonStateError, match="violates an active request constraint"):
        apply_candidate(
            season="2026-2027",
            candidate=schedule["plan"],
            root=root,
        )


def test_release_by_constraint_id_keeps_audit_history(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    added = add_request_constraint(
        season="2026-2027",
        type="team_unavailable",
        request_id="club-request-audit",
        teams=[{"club": "Kongsberg", "label": "K1", "age_group": "U10"}],
        date_from="2026-09-12",
        root=root,
        actor="tester",
    )
    constraint_identifier = added["constraint"]["id"]

    released = release_request_constraints(
        season="2026-2027",
        root=root,
        constraint_ids=[constraint_identifier],
        actor="tester",
        note="club withdrew the request",
    )
    assert released["released_constraint_ids"] == [constraint_identifier]
    assert released["active_count"] == 0

    active_report = request_constraint_report("2026-2027", root=root)
    assert active_report["active_count"] == 0
    full_report = request_constraint_report("2026-2027", root=root, include_released=True)
    assert full_report["constraints"][0]["status"] == "released"
    assert full_report["constraints"][0]["satisfied"] is None
    assert full_report["constraints"][0]["release_reason"] == "club withdrew the request"

    decisions = load_decisions("2026-2027", root=root)
    events = [
        event
        for event in decisions.get("history", [])
        if event.get("event") == "release_request_constraint"
    ]
    assert events and events[0]["details"]["released_constraint_ids"] == [constraint_identifier]

    # A released constraint no longer blocks a schedule change.
    with pytest.raises(SeasonStateError, match="No active request constraints"):
        release_request_constraints(
            season="2026-2027",
            root=root,
            constraint_ids=[constraint_identifier],
        )


def test_missing_release_selector_is_rejected(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    with pytest.raises(SeasonStateError, match="provide --constraint-id"):
        release_request_constraints(season="2026-2027", root=root)
