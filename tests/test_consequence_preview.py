"""Shared consequence preview for scoped canonical operations."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tournament_scheduler.application.canonical_season.consequence_preview import (
    PREVIEW_CONTRACT,
    STATUS_BLOCKED,
    STATUS_SAFE_TO_APPLY,
    STATUS_STALE_REVISION,
    preview_canonical_operation,
)
from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.infrastructure.canonical_season_store import (
    DECISIONS_SCHEMA_VERSION,
    SEASON_STATE_SCHEMA_VERSION,
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
)
from tournament_scheduler.planning.contract import build_planning_problem
from tournament_scheduler.season.state import canonical_state_revision, schedule_fingerprint

SEASON = "2026-2027"
CLUBS = ("Alfa", "Bravo", "Charlie", "Delta", "Echo")


def _problem() -> dict:
    teams = [{"club": club, "label": f"{club} 1", "age_group": "U10"} for club in CLUBS]
    config = {
        "teams": teams,
        "age_groups": ["U10"],
        "parallel_games": {"U10": 4},
        "round_length_minutes": {"U10": 30},
        "ice_time_minutes": {"U10": 200},
        "rounds_per_tournament": {"U10": 5},
    }
    problem = build_planning_problem(config, None, date(2026, 10, 1), date(2026, 10, 31))
    problem["clubs"] = {club: f"{club} Arena" for club in CLUBS}
    problem["club_calendar_status"] = {club: "known" for club in CLUBS}
    return problem


def _tournament(tournament_id: str, tournament_date: str, host: str) -> dict:
    teams = [{"club": club, "label": f"{club} 1", "age_group": "U10"} for club in CLUBS]
    return {
        "id": tournament_id,
        "date": tournament_date,
        "arena": f"{host} Arena",
        "age_group": "U10",
        "host_club": host,
        "teams": teams,
        "games": [],
        "start_time": "10:00",
    }


def _write(root: Path) -> None:
    problem = _problem()
    plan = {
        "schema_version": 1,
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
        "tournaments": [
            _tournament("u10-a", "2026-10-03", "Alfa"),
            _tournament("u10-b", "2026-10-10", "Bravo"),
        ],
    }
    now = "2026-09-22T00:00:00+00:00"
    fingerprint = schedule_fingerprint(plan)
    schedule = {
        "schema_version": SEASON_STATE_SCHEMA_VERSION,
        "season": SEASON,
        "created_at": now,
        "updated_at": now,
        "revision": fingerprint,
        "fingerprint": fingerprint,
        "plan_schema_version": 1,
        "plan": plan,
        "verification_context": {"problem": problem},
    }
    decisions = {
        "schema_version": DECISIONS_SCHEMA_VERSION,
        "season": SEASON,
        "created_at": now,
        "updated_at": now,
        "schedule_fingerprint": fingerprint,
        "actor": "tester",
        "decisions": {
            tournament["id"]: {
                "status": "pending_review",
                "placement_locked": False,
                "participants_locked": False,
                "approved_fingerprint": None,
            }
            for tournament in plan["tournaments"]
        },
        "history": [],
    }
    CanonicalSeasonStore(root).write(
        CanonicalSeasonSnapshot(season=SEASON, schedule=schedule, decisions=decisions)
    )


def _state_bytes(root: Path) -> tuple[bytes, bytes]:
    season_dir = root / SEASON
    return (
        (season_dir / "schedule.json").read_bytes(),
        (season_dir / "decisions.json").read_bytes(),
    )


def _assert_contract(preview: dict, root: Path) -> None:
    snapshot = CanonicalSeasonService(root=root).load(SEASON)
    assert preview["contract"] == PREVIEW_CONTRACT
    assert preview["writes_canonical_state"] is False
    assert preview["expected_revision"] == canonical_state_revision(snapshot.schedule, snapshot.decisions)
    assert preview["verdict"]["status"] in {STATUS_SAFE_TO_APPLY, STATUS_BLOCKED}
    assert preview["verdict"]["applicable"] is (preview["verdict"]["status"] == STATUS_SAFE_TO_APPLY)


def test_removal_preview_is_safe_and_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root)
    before = _state_bytes(root)
    service = CanonicalSeasonService(root=root)

    preview = preview_canonical_operation(service, 
        operation="remove",
        season=SEASON,
        tournament_ids=["u10-a", "u10-b"],
        remove_team_label="Echo 1",
        reconcile_withdrawal=True,
        actor="tester",
        request_id="preview-echo",
    )

    _assert_contract(preview, root)
    assert preview["operation"] == "remove"
    assert preview["changed_tournament_ids"] == ["u10-a", "u10-b"]
    assert preview["candidate_fingerprint"]
    assert preview["verdict"]["status"] == STATUS_SAFE_TO_APPLY
    assert preview["verdict"]["checks"]["schedule_acceptable"] is True
    assert preview["verdict"]["checks"]["consequences_acceptable"] is True
    assert _state_bytes(root) == before


def test_move_preview_reports_candidate_fingerprint_and_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root)
    before = _state_bytes(root)
    service = CanonicalSeasonService(root=root)

    preview = preview_canonical_operation(service, 
        operation="move",
        season=SEASON,
        tournament_id="u10-a",
        date="2026-10-05",
        actor="tester",
        request_id="preview-move",
    )

    _assert_contract(preview, root)
    assert preview["changed_tournament_ids"] == ["u10-a"]
    assert preview["candidate_fingerprint"]
    assert _state_bytes(root) == before


def test_swap_refusal_becomes_blocked_verdict_without_writing(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root)
    before = _state_bytes(root)
    service = CanonicalSeasonService(root=root)

    # Every tournament already fields every club, so this swap is refused by the
    # domain. The preview must report that refusal, not raise.
    preview = preview_canonical_operation(service, 
        operation="swap",
        season=SEASON,
        tournament_a_id="u10-a",
        team_a_label="Alfa 1",
        tournament_b_id="u10-b",
        team_b_label="Charlie 1",
        actor="tester",
        request_id="preview-swap",
    )

    _assert_contract(preview, root)
    assert preview["verdict"]["status"] == STATUS_BLOCKED
    assert preview["verdict"]["blockers"]
    assert preview["candidate_fingerprint"] is None
    assert preview["changed_tournament_ids"] == ["u10-a", "u10-b"]
    assert _state_bytes(root) == before


def test_unknown_tournament_is_blocked_without_writing(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root)
    before = _state_bytes(root)
    service = CanonicalSeasonService(root=root)

    preview = preview_canonical_operation(service, 
        operation="replace",
        season=SEASON,
        tournament_id="u10-missing",
        remove_team_label="Alfa 1",
        add_team_label="Echo 1",
        actor="tester",
    )

    _assert_contract(preview, root)
    assert preview["verdict"]["status"] == STATUS_BLOCKED
    assert preview["verdict"]["blockers"]
    assert preview["candidate_fingerprint"] is None
    assert _state_bytes(root) == before


def test_removal_preview_reports_planned_consequences_for_affected_teams(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root)
    service = CanonicalSeasonService(root=root)

    preview = preview_canonical_operation(service,
        operation="remove",
        season=SEASON,
        tournament_ids=["u10-a", "u10-b"],
        remove_team_label="Echo 1",
        reconcile_withdrawal=True,
        actor="tester",
        request_id="preview-echo",
    )

    consequences = preview["consequences"]
    assert consequences["basis"] == "planned_schedule"
    assert consequences["provisional"] is None
    assert isinstance(consequences["material_regressions"], list)
    rows = {(row["club"], row["team"]): row for row in consequences["affected_teams"]}
    echo = rows[("Echo", "Echo 1")]
    assert echo["after"]["planned"] == echo["before"]["planned"] - 2
    for side in ("before", "after"):
        counts = echo[side]
        assert counts["planned"] == counts["before_christmas"] + counts["after_christmas"] + counts["unsplit"]
    assert echo["delta"]["planned"] == -2


def test_stale_expected_revision_is_refused_without_dry_run(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root)
    before = _state_bytes(root)
    service = CanonicalSeasonService(root=root)

    preview = preview_canonical_operation(service, 
        operation="remove",
        season=SEASON,
        tournament_ids=["u10-a", "u10-b"],
        remove_team_label="Echo 1",
        reconcile_withdrawal=True,
        expected_revision="not-the-current-revision",
        actor="tester",
        request_id="preview-stale",
    )

    assert preview["verdict"]["status"] == STATUS_STALE_REVISION
    assert preview["verdict"]["applicable"] is False
    assert preview["candidate_fingerprint"] is None
    assert preview["consequences"] == {
        "basis": None,
        "provisional": None,
        "affected_teams": [],
        "material_regressions": [],
    }
    assert _state_bytes(root) == before


def test_matching_expected_revision_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root)
    service = CanonicalSeasonService(root=root)
    current = service.load(SEASON)
    revision = canonical_state_revision(current.schedule, current.decisions)

    preview = preview_canonical_operation(service, 
        operation="remove",
        season=SEASON,
        tournament_ids=["u10-a", "u10-b"],
        remove_team_label="Echo 1",
        reconcile_withdrawal=True,
        expected_revision=revision,
        actor="tester",
        request_id="preview-current",
    )

    assert preview["verdict"]["status"] != STATUS_STALE_REVISION
    assert preview["expected_revision"] == revision


def test_unsupported_operation_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root)
    service = CanonicalSeasonService(root=root)

    with pytest.raises(ValueError, match="Unsupported preview operation"):
        preview_canonical_operation(service, operation="delete_everything", season=SEASON)


def test_cli_preview_emits_contract_and_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import json

    from tournament_scheduler.cli.rvv_cli import main as cli_main

    root = tmp_path / "season"
    _write(root)
    before = _state_bytes(root)
    base = ["season", "preview", "--season", SEASON, "--root", str(root), "--json"]

    assert (
        cli_main(
            [
                *base,
                "--operation", "remove",
                "--tournament-ids", "u10-a", "u10-b",
                "--remove-team", "Echo 1",
                "--reconcile-withdrawal",
                "--request-id", "cli-echo",
                "--fail-on-blocked",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["contract"] == PREVIEW_CONTRACT
    assert payload["verdict"]["status"] == STATUS_SAFE_TO_APPLY

    assert (
        cli_main(
            [
                *base,
                "--operation", "swap",
                "--tournament-a-id", "u10-a", "--team-a", "Alfa 1",
                "--tournament-b-id", "u10-b", "--team-b", "Charlie 1",
                "--request-id", "cli-swap",
                "--fail-on-blocked",
            ]
        )
        == 3
    )
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["verdict"]["status"] == STATUS_BLOCKED

    assert _state_bytes(root) == before


def test_cli_preview_rejects_missing_operation_arguments(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tournament_scheduler.cli.rvv_cli import main as cli_main

    root = tmp_path / "season"
    _write(root)

    assert (
        cli_main(["season", "preview", "--season", SEASON, "--root", str(root), "--operation", "replace"])
        == 2
    )
    assert "replace preview needs" in capsys.readouterr().out


def _fixed_busy_root(tmp_path: Path) -> tuple[Path, dict]:
    """Host Nordby is busy on the target date, so a move there needs a manual-placement opt-in."""

    from tests.test_season_maintenance import _plan, _problem, _teams, _tournament, _write_season

    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams)
    problem["club_calendar_status"] = {"Nordby": "known", "Sorby": "known"}
    problem["club_busy_intervals"] = {"Nordby": [{"date": "2026-10-17", "start": "07:00", "end": "14:00"}]}
    plan = _plan(
        [
            _tournament("T1", "2026-10-10", "Nordby", teams),
            _tournament("T2", "2026-11-14", "Sorby", teams),
        ]
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)
    return root, problem


def test_move_preview_opt_in_matches_apply(tmp_path: Path) -> None:
    root, problem = _fixed_busy_root(tmp_path)
    service = CanonicalSeasonService(root=root)
    move = {"operation": "move", "season": "2026-2027", "tournament_id": "T1", "date": "2026-10-17", "problem": problem}

    blocked = preview_canonical_operation(service, **move)
    assert blocked["verdict"]["status"] == STATUS_BLOCKED
    assert blocked["verdict"]["checks"]["consequences_acceptable"] is False

    opted_in = preview_canonical_operation(service, **move, allow_manual_placement=True)
    assert opted_in["verdict"]["status"] == STATUS_SAFE_TO_APPLY

    applied = service.move_tournament(
        season="2026-2027",
        tournament_id="T1",
        date="2026-10-17",
        problem=problem,
        allow_manual_placement=True,
    )
    assert applied["plan"]["tournaments"][0]["date"] == "2026-10-17"


def test_cli_move_preview_forwards_manual_placement_opt_in(tmp_path: Path) -> None:
    from tournament_scheduler.cli.rvv_cli import main as cli_main

    root, _problem_unused = _fixed_busy_root(tmp_path)
    base = [
        "season", "preview", "--season", "2026-2027", "--root", str(root),
        "--operation", "move", "--tournament-id", "T1", "--date", "2026-10-17",
        "--fail-on-blocked", "--json",
    ]

    assert cli_main(base) == 3
    assert cli_main([*base, "--allow-manual-placement"]) == 0
