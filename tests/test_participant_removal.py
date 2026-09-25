"""Scoped participant removal / season-withdrawal regression tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.canonical_state import canonical_state_revision, schedule_fingerprint
from tournament_scheduler.infrastructure.canonical_season_store import (
    DECISIONS_SCHEMA_VERSION,
    SEASON_STATE_SCHEMA_VERSION,
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
    SeasonStateError,
)
from tournament_scheduler.planning_contract import build_planning_problem
from tournament_scheduler.pipeline.export_projection_guard import tournament_projection
from tournament_scheduler.published_mutation_history import reconcile_published_baseline
from tournament_scheduler.season_state import (
    batch_maintenance,
    load_decisions,
    load_schedule,
    remove_participant,
)


def _problem() -> dict:
    teams = [
        {"club": club, "label": f"{club} 1", "age_group": "U10"}
        for club in ("Alfa", "Bravo", "Charlie", "Delta", "Echo")
    ]
    config = {
        "teams": teams,
        "age_groups": ["U10"],
        "parallel_games": {"U10": 4},
        "round_length_minutes": {"U10": 30},
        "ice_time_minutes": {"U10": 200},
        "rounds_per_tournament": {"U10": 5},
    }
    problem = build_planning_problem(config, None, date(2026, 10, 1), date(2026, 10, 31))
    problem["clubs"] = {team["club"]: f"{team['club']} Arena" for team in teams}
    problem["club_calendar_status"] = {team["club"]: "known" for team in teams}
    return problem


def _tournament(tournament_id: str, tournament_date: str, host: str, teams: list[dict]) -> dict:
    return {
        "id": tournament_id,
        "date": tournament_date,
        "arena": f"{host} Arena",
        "age_group": "U10",
        "host_club": host,
        "teams": [dict(team) for team in teams],
        "games": [],
        "start_time": "10:00",
    }


def _write_canonical(root: Path, *, sealed: bool) -> None:
    problem = _problem()
    teams = [dict(team) for team in problem["teams"]]
    plan = {
        "schema_version": 1,
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
        "tournaments": [
            _tournament("u10-a", "2026-10-03", "Alfa", teams),
            _tournament("u10-b", "2026-10-10", "Bravo", teams),
        ],
    }
    now = "2026-09-22T00:00:00+00:00"
    fingerprint = schedule_fingerprint(plan)
    schedule = {
        "schema_version": SEASON_STATE_SCHEMA_VERSION,
        "season": "2026-2027",
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
        "season": "2026-2027",
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
        CanonicalSeasonSnapshot(season="2026-2027", schedule=schedule, decisions=decisions)
    )
    if sealed:
        service = CanonicalSeasonService(root=root)
        projection = tournament_projection(plan, problem)
        service.seal_published_season(
            season="2026-2027",
            publication_id="2026-09-22T0908",
            canonical_revision="rev-published",
            published_at="2026-09-22T09:14:53+00:00",
            published_projection=projection,
            publication_canonical_projection=projection,
            materializations=[],
            actor="tester",
        )


def test_removal_without_withdrawal_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, sealed=False)

    with pytest.raises(SeasonStateError, match="avoidably underfilled"):
        remove_participant(
            season="2026-2027",
            tournament_ids=["u10-a"],
            remove_team_label="Echo 1",
            root=root,
            request_id="absent-echo-a",
            actor="tester",
        )


def test_withdrawal_reconciles_pool_and_records_provenance(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, sealed=False)

    preview = remove_participant(
        season="2026-2027",
        tournament_ids=["u10-a", "u10-b"],
        remove_team_label="Echo 1",
        reconcile_withdrawal=True,
        root=root,
        request_id="withdraw-echo",
        actor="tester",
        dry_run=True,
    )
    assert preview["dry_run"] is True
    assert preview["removal"]["can_apply_unchanged"] is True
    assert preview["removal"]["reconcile_withdrawal"] is True
    assert len(preview["removal"]["withdrawals_to_add"]) == 2
    assert preview["removal"]["verification_result"]["ok"] is True
    # Remaining teams are analysed for consequences; the withdrawn team's own
    # shortfall is reported separately and never treated as a regression.
    assert "Echo|Echo 1|U10" not in preview["removal"]["team_consequences"]
    assert preview["removal"]["removed_team_consequence"]["membership_role"] == "removed"

    schedule_file = root / "2026-2027" / "schedule.json"
    decisions_file = root / "2026-2027" / "decisions.json"
    before_apply = (schedule_file.read_bytes(), decisions_file.read_bytes())

    result = remove_participant(
        season="2026-2027",
        tournament_ids=["u10-a", "u10-b"],
        remove_team_label="Echo 1",
        reconcile_withdrawal=True,
        root=root,
        request_id="withdraw-echo",
        actor="tester",
        note="Echo withdrew from U10",
    )
    assert result["dry_run"] is False

    updated = load_schedule("2026-2027", root=root)
    for tournament in updated["plan"]["tournaments"]:
        assert "Echo 1" not in {team["label"] for team in tournament["teams"]}
        assert all(game["home"] != "Echo 1" and game["away"] != "Echo 1" for game in tournament["games"])

    decisions = load_decisions("2026-2027", root=root)
    withdrawals = decisions["participation_withdrawals"]
    assert {record["tournament_id"] for record in withdrawals} == {"u10-a", "u10-b"}
    assert all(record["team"]["label"] == "Echo 1" for record in withdrawals)
    assert all(record["status"] == "active" for record in withdrawals)
    assert all(record["request_id"] == "withdraw-echo" for record in withdrawals)
    assert all(record["source_revision"] for record in withdrawals)

    events = [event for event in decisions["history"] if event["event"] == "participant_removal"]
    assert len(events) == 1
    assert events[0]["details"]["request_id"] == "withdraw-echo"

    # The withdrawal must be part of the canonical-state identity.
    assert canonical_state_revision(updated, decisions) == result["canonical_state_revision"]

    # A dry-run never writes anything.
    assert before_apply != (schedule_file.read_bytes(), decisions_file.read_bytes())


def test_sealed_withdrawal_reconciles_against_published_baseline(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, sealed=True)
    service = CanonicalSeasonService(root=root)

    result = service.remove_participant(
        season="2026-2027",
        tournament_ids=["u10-a", "u10-b"],
        remove_team_label="Echo 1",
        reconcile_withdrawal=True,
        request_id="sealed-withdraw-echo",
        actor="tester",
        note="Echo withdrew from U10",
    )
    assert result["dry_run"] is False

    snapshot = service.load("2026-2027")
    report = service.verify_sealed_reconciliation("2026-2027")
    assert report["ok"] is True
    assert snapshot.decisions["history"][-1]["event"] == "participant_removal"


def test_removal_fails_when_participant_lock_active(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, sealed=False)
    service = CanonicalSeasonService(root=root)
    service.approve_tournament(
        season="2026-2027",
        tournament_id="u10-a",
        actor="tester",
        note="ice booked",
        participants_locked=True,
        problem=_problem(),
    )

    with pytest.raises(SeasonStateError, match="participant lock"):
        service.remove_participant(
            season="2026-2027",
            tournament_ids=["u10-a"],
            remove_team_label="Echo 1",
            reconcile_withdrawal=True,
            request_id="locked-echo",
            actor="tester",
        )


def test_batch_removal_reconciles_withdrawal_and_commits_once(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, sealed=False)

    result = batch_maintenance(
        season="2026-2027",
        root=root,
        operations=[
            {
                "op": "remove_participant",
                "tournament_id": "u10-a",
                "remove_team": "Echo 1",
                "reconcile_withdrawal": True,
            },
            {
                "op": "remove_participant",
                "tournament_id": "u10-b",
                "remove_team": "Echo 1",
                "reconcile_withdrawal": True,
            },
        ],
        scope=["u10-a", "u10-b"],
        request_id="batch-withdraw-echo",
        actor="tester",
        note="Echo withdrew from U10",
    )
    assert result["committed"] is True
    assert result["refused"] is False
    assert result["changed_outside_scope"] == []
    assert {removal["tournament_id"] for removal in result["removals"]} == {
        "u10-a",
        "u10-b",
    }

    updated = load_schedule("2026-2027", root=root)
    for tournament in updated["plan"]["tournaments"]:
        assert "Echo 1" not in {team["label"] for team in tournament["teams"]}
        assert all(game["home"] != "Echo 1" and game["away"] != "Echo 1" for game in tournament["games"])

    decisions = load_decisions("2026-2027", root=root)
    withdrawals = decisions["participation_withdrawals"]
    assert {record["tournament_id"] for record in withdrawals} == {"u10-a", "u10-b"}
    assert all(record["request_id"] == "batch-withdraw-echo" for record in withdrawals)
    assert decisions["canonical_state_revision"] == result["canonical_state_revision"]


def test_batch_removal_without_withdrawal_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, sealed=False)
    schedule_file = root / "2026-2027" / "schedule.json"
    decisions_file = root / "2026-2027" / "decisions.json"
    before = (schedule_file.read_bytes(), decisions_file.read_bytes())

    preview = batch_maintenance(
        season="2026-2027",
        root=root,
        operations=[{"op": "remove_participant", "tournament_id": "u10-a", "remove_team": "Echo 1"}],
        scope=["u10-a"],
        request_id="batch-absence",
        actor="tester",
        dry_run=True,
    )
    assert preview["refused"] is True
    assert preview["hard_verification_ok"] is False
    assert (schedule_file.read_bytes(), decisions_file.read_bytes()) == before

    with pytest.raises(SeasonStateError, match="hard verification"):
        batch_maintenance(
            season="2026-2027",
            root=root,
            operations=[{"op": "remove_participant", "tournament_id": "u10-a", "remove_team": "Echo 1"}],
            scope=["u10-a"],
            request_id="batch-absence",
            actor="tester",
        )
    assert (schedule_file.read_bytes(), decisions_file.read_bytes()) == before


def test_replay_participant_removal_history() -> None:
    problem = _problem()
    teams = [dict(team) for team in problem["teams"]]
    before = {
        "tournaments": [
            _tournament("u10-a", "2026-10-03", "Alfa", teams),
        ]
    }
    after_problem = dict(problem)
    projection_before = tournament_projection(before, after_problem)
    after = {
        "tournaments": [
            _tournament("u10-a", "2026-10-03", "Alfa", [team for team in teams if team["label"] != "Echo 1"]),
        ]
    }
    projection_after = tournament_projection(after, after_problem)
    history = [
        {
            "event": "participant_removal",
            "tournament_id": "u10-a",
            "details": {"removed_team": {"club": "Echo", "label": "Echo 1"}, "tournament_ids": ["u10-a"]},
        }
    ]
    report = reconcile_published_baseline(
        published_projection=projection_before,
        current_projection=projection_after,
        history=history,
        attested_additions={},
    )
    assert report["ok"] is True
    assert report["applied_mutation_count"] == 1
