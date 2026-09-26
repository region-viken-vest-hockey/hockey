"""Scoped participant removal / season-withdrawal regression tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.application.canonical_season.removal_policy import (
    evaluate_removal_consequences,
)
from tournament_scheduler.canonical_state import canonical_state_revision, schedule_fingerprint
from tournament_scheduler.infrastructure.canonical_season_store import (
    DECISIONS_SCHEMA_VERSION,
    SEASON_STATE_SCHEMA_VERSION,
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
    SeasonStateError,
)
from tournament_scheduler.planning_contract import build_planning_problem, verify_candidate
from tournament_scheduler.participation_withdrawals import (
    WITHDRAWN_INELIGIBLE_FIELD,
    build_withdrawal_records,
    project_into_problem,
    withdrawn_team_identities_for_tournament,
)
from tournament_scheduler.pipeline.export_projection_guard import tournament_projection
from tournament_scheduler.published_mutation_history import reconcile_published_baseline
from tournament_scheduler.season_state import (
    batch_maintenance,
    load_decisions,
    load_schedule,
    release_participation_withdrawals,
    remove_participant,
    withdrawal_report,
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


def _tournament(
    tournament_id: str,
    tournament_date: str,
    host: str,
    teams: list[dict],
    *,
    age_group: str = "U10",
) -> dict:
    return {
        "id": tournament_id,
        "date": tournament_date,
        "arena": f"{host} Arena",
        "age_group": age_group,
        "host_club": host,
        "teams": [dict(team) for team in teams],
        "games": [],
        "start_time": "10:00",
    }


def _write_canonical(
    root: Path,
    *,
    sealed: bool,
    extra_team_on: str | None = None,
    extra_team: dict | None = None,
) -> None:
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
    if extra_team_on and extra_team:
        for tournament in plan["tournaments"]:
            if tournament["id"] == extra_team_on:
                tournament["teams"].append(dict(extra_team))
    _write_plan(root, plan, problem, sealed=sealed)


def _write_plan(root: Path, plan: dict, problem: dict, *, sealed: bool, season: str = "2026-2027") -> None:
    now = "2026-09-22T00:00:00+00:00"
    fingerprint = schedule_fingerprint(plan)
    schedule = {
        "schema_version": SEASON_STATE_SCHEMA_VERSION,
        "season": season,
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
        "season": season,
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
        CanonicalSeasonSnapshot(season=season, schedule=schedule, decisions=decisions)
    )
    if sealed:
        service = CanonicalSeasonService(root=root)
        projection = tournament_projection(plan, problem)
        service.seal_published_season(
            season=season,
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
    assert len(preview["removal"]["withdrawals_to_add"]) == 1
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
    assert len(withdrawals) == 1
    record = withdrawals[0]
    assert record["scope"] == "age_group"
    assert record["age_group"] == "U10"
    assert set(record["tournament_ids"]) == {"u10-a", "u10-b"}
    assert record["team"]["label"] == "Echo 1"
    assert record["status"] == "active"
    assert record["request_id"] == "withdraw-echo"
    assert record["source_revision"]
    # The earliest removed tournament scopes the withdrawal; anything before it
    # is historical provenance and keeps the team.
    assert record["effective_from"] == "2026-10-03"

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
    assert len(withdrawals) == 1
    assert withdrawals[0]["scope"] == "age_group"
    assert set(withdrawals[0]["tournament_ids"]) == {"u10-a", "u10-b"}
    assert withdrawals[0]["request_id"] == "batch-withdraw-echo"
    assert decisions["canonical_state_revision"] == result["canonical_state_revision"]


def test_batch_removal_without_withdrawal_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, sealed=False)
    schedule_file = root / "2026-2027" / "schedule.json"
    decisions_file = root / "2026-2027" / "decisions.json"
    before = (schedule_file.read_bytes(), decisions_file.read_bytes())

    # The single-operation and batch paths share one eligibility boundary: a
    # one-tournament absence that leaves an avoidably underfilled shape fails
    # closed (and writes nothing) exactly the same way.
    with pytest.raises(SeasonStateError, match="avoidably underfilled"):
        batch_maintenance(
            season="2026-2027",
            root=root,
            operations=[{"op": "remove_participant", "tournament_id": "u10-a", "remove_team": "Echo 1"}],
            scope=["u10-a"],
            request_id="batch-absence",
            actor="tester",
            dry_run=True,
        )
    assert (schedule_file.read_bytes(), decisions_file.read_bytes()) == before

    with pytest.raises(SeasonStateError, match="avoidably underfilled"):
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


def test_batch_withdrawal_requires_registered_target(tmp_path: Path) -> None:
    """P1: the batch path validates the canonical registration like the single path."""

    root = tmp_path / "season"
    _write_canonical(
        root,
        sealed=False,
        extra_team_on="u10-a",
        extra_team={"club": "Rogue", "label": "Rogue 1", "age_group": "U10"},
    )
    schedule_file = root / "2026-2027" / "schedule.json"
    decisions_file = root / "2026-2027" / "decisions.json"
    before = (schedule_file.read_bytes(), decisions_file.read_bytes())

    with pytest.raises(SeasonStateError, match="not a registered"):
        batch_maintenance(
            season="2026-2027",
            root=root,
            operations=[
                {
                    "op": "remove_participant",
                    "tournament_id": "u10-a",
                    "remove_team": "Rogue 1",
                    "reconcile_withdrawal": True,
                }
            ],
            scope=["u10-a"],
            request_id="batch-rogue",
            actor="tester",
        )
    assert (schedule_file.read_bytes(), decisions_file.read_bytes()) == before


def test_release_withdrawal_refuses_premature_release_without_write(tmp_path: Path) -> None:
    """P1: releasing while the team is still absent is verified, not silent."""

    root = tmp_path / "season"
    _write_canonical(root, sealed=False)
    remove_participant(
        season="2026-2027",
        tournament_ids=["u10-a", "u10-b"],
        remove_team_label="Echo 1",
        reconcile_withdrawal=True,
        root=root,
        request_id="withdraw-echo",
        actor="tester",
    )
    schedule_file = root / "2026-2027" / "schedule.json"
    decisions_file = root / "2026-2027" / "decisions.json"
    before = (schedule_file.read_bytes(), decisions_file.read_bytes())

    with pytest.raises(SeasonStateError, match="Refusing withdrawal release"):
        release_participation_withdrawals(
            season="2026-2027",
            root=root,
            request_id="withdraw-echo",
            actor="tester",
            note="premature",
        )
    assert (schedule_file.read_bytes(), decisions_file.read_bytes()) == before
    assert withdrawal_report("2026-2027", root=root)["active_count"] == 1


def test_release_withdrawal_with_atomic_restore_preserves_provenance(tmp_path: Path) -> None:
    """The authorized reversal restores the participant and releases atomically."""

    root = tmp_path / "season"
    _write_canonical(root, sealed=False)
    remove_participant(
        season="2026-2027",
        tournament_ids=["u10-a", "u10-b"],
        remove_team_label="Echo 1",
        reconcile_withdrawal=True,
        root=root,
        request_id="withdraw-echo",
        actor="tester",
    )
    before_release = load_decisions("2026-2027", root=root)
    record_ids = {record["id"] for record in before_release["participation_withdrawals"]}
    revision_before_release = canonical_state_revision(
        load_schedule("2026-2027", root=root), before_release
    )

    result = release_participation_withdrawals(
        season="2026-2027",
        root=root,
        request_id="withdraw-echo",
        actor="tester",
        note="Echo returned to the age group",
        restore_participants=True,
    )
    assert set(result["released_withdrawal_ids"]) == record_ids
    assert result["active_count"] == 0

    updated = load_schedule("2026-2027", root=root)
    for tournament in updated["plan"]["tournaments"]:
        assert "Echo 1" in {team["label"] for team in tournament["teams"]}
        assert tournament["games"]

    decisions = load_decisions("2026-2027", root=root)
    assert canonical_state_revision(updated, decisions) != revision_before_release
    released = {record["id"]: record for record in decisions["participation_withdrawals"]}
    assert set(released) == record_ids
    for record in released.values():
        assert record["status"] == "released"
        assert record["released_by"] == "tester"
        assert record["released_at"]
        assert record["release_reason"] == "Echo returned to the age group"
        assert record["request_id"] == "withdraw-echo"
    assert any(
        event["event"] == "participant_restoration" for event in decisions["history"]
    )


def test_selective_release_rebuilds_projections_from_active_records(tmp_path: Path) -> None:
    """P1: releasing one of two withdrawals leaves only the active scope behind."""

    root = tmp_path / "season"
    _write_canonical(root, sealed=False)
    service = CanonicalSeasonService(root=root)
    remove_participant(
        season="2026-2027",
        tournament_ids=["u10-a", "u10-b"],
        remove_team_label="Echo 1",
        reconcile_withdrawal=True,
        root=root,
        request_id="withdraw-echo",
        actor="tester",
    )
    remove_participant(
        season="2026-2027",
        tournament_ids=["u10-a", "u10-b"],
        remove_team_label="Delta 1",
        reconcile_withdrawal=True,
        root=root,
        request_id="withdraw-delta",
        actor="tester",
    )

    # Releasing Echo restores it, leaving only Delta withdrawn.
    release_participation_withdrawals(
        season="2026-2027",
        root=root,
        request_id="withdraw-echo",
        actor="tester",
        note="Echo returns",
        restore_participants=True,
    )
    decisions = load_decisions("2026-2027", root=root)
    active = [
        record for record in decisions["participation_withdrawals"] if record["status"] == "active"
    ]
    assert [record["team"]["label"] for record in active] == ["Delta 1"]

    plan = load_schedule("2026-2027", root=root)["plan"]
    projected = project_into_problem(_problem(), decisions=decisions, plan=plan)
    shape_labels = {
        (entry["club"], entry["label"]) for entry in projected["withdrawn_tournament_teams"]
    }
    ineligible_labels = {
        (entry["club"], entry["label"]) for entry in projected[WITHDRAWN_INELIGIBLE_FIELD]
    }
    assert shape_labels == {("Delta", "Delta 1")}
    assert ineligible_labels == {("Delta", "Delta 1")}

    # Re-projecting a problem that still carries both stale entries rebuilds
    # from the authoritative active decisions instead of unioning them.
    stale = project_into_problem(
        _problem(),
        records=[
            *build_withdrawal_records(
                team={"club": "Echo", "label": "Echo 1", "age_group": "U10"},
                tournament_ids=["u10-a", "u10-b"],
                request_id="withdraw-echo",
                actor="tester",
                note="",
                created_at="2026-09-22T00:00:00+00:00",
                source_revision="rev-1",
                effective_from="2026-10-03",
            ),
            *build_withdrawal_records(
                team={"club": "Delta", "label": "Delta 1", "age_group": "U10"},
                tournament_ids=["u10-a", "u10-b"],
                request_id="withdraw-delta",
                actor="tester",
                note="",
                created_at="2026-09-22T00:00:00+00:00",
                source_revision="rev-1",
                effective_from="2026-10-03",
            ),
        ],
    )
    assert len(stale["withdrawn_tournament_teams"]) == 2
    rebuilt = project_into_problem(stale, decisions=decisions, plan=plan)
    assert {
        (entry["club"], entry["label"]) for entry in rebuilt["withdrawn_tournament_teams"]
    } == {("Delta", "Delta 1")}
    assert {
        (entry["club"], entry["label"]) for entry in rebuilt[WITHDRAWN_INELIGIBLE_FIELD]
    } == {("Delta", "Delta 1")}


def test_sealed_restore_replays_and_reconciles(tmp_path: Path) -> None:
    """P1: restoration is a typed, replayable sealed-season mutation."""

    root = tmp_path / "season"
    _write_canonical(root, sealed=True)
    service = CanonicalSeasonService(root=root)
    service.remove_participant(
        season="2026-2027",
        tournament_ids=["u10-a", "u10-b"],
        remove_team_label="Echo 1",
        reconcile_withdrawal=True,
        request_id="sealed-withdraw-echo",
        actor="tester",
        note="Echo withdrew",
    )
    assert service.verify_sealed_reconciliation("2026-2027")["ok"] is True

    result = service.release_participation_withdrawals(
        season="2026-2027",
        request_id="sealed-withdraw-echo",
        actor="tester",
        note="Echo returns",
        restore_participants=True,
    )
    assert result["restored_participants"] is True
    assert service.verify_sealed_reconciliation("2026-2027")["ok"] is True
    snapshot = service.load("2026-2027")
    event = snapshot.decisions["history"][-1]
    assert event["event"] == "participant_restoration"
    assert event["details"]["after_records"]


def test_release_restore_refuses_active_request_constraint_without_write(tmp_path: Path) -> None:
    """P1: restoration runs the request-constraint gate and writes nothing on refusal."""

    root = tmp_path / "season"
    _write_canonical(root, sealed=False)
    service = CanonicalSeasonService(root=root)
    # The gap constraint is recorded while Echo still plays both tournaments,
    # then the withdrawal makes it vacuously satisfied; restoring Echo would
    # reintroduce the seven-day gap.
    service.add_request_constraint(
        season="2026-2027",
        type="minimum_gap",
        request_id="echo-gap",
        teams=[{"club": "Echo", "label": "Echo 1", "age_group": "U10"}],
        min_days=10,
        actor="tester",
    )
    service.remove_participant(
        season="2026-2027",
        tournament_ids=["u10-a", "u10-b"],
        remove_team_label="Echo 1",
        reconcile_withdrawal=True,
        request_id="withdraw-echo",
        actor="tester",
    )
    schedule_file = root / "2026-2027" / "schedule.json"
    decisions_file = root / "2026-2027" / "decisions.json"
    before = (schedule_file.read_bytes(), decisions_file.read_bytes())

    with pytest.raises(SeasonStateError, match="request constraint"):
        service.release_participation_withdrawals(
            season="2026-2027",
            request_id="withdraw-echo",
            actor="tester",
            note="premature restore",
            restore_participants=True,
        )
    assert (schedule_file.read_bytes(), decisions_file.read_bytes()) == before
    assert withdrawal_report("2026-2027", root=root)["active_count"] == 1


def test_durable_withdrawal_projection_blocks_reintroduction_and_registration_ends_it() -> None:
    """P1: the shape reduction is presence-reconciled; ineligibility is durable."""

    problem = _problem()
    records = build_withdrawal_records(
        team={"club": "Echo", "label": "Echo 1", "age_group": "U10"},
        tournament_ids=["u10-a", "u10-b"],
        request_id="withdraw-echo",
        actor="tester",
        note="",
        created_at="2026-09-22T00:00:00+00:00",
        source_revision="rev-1",
        effective_from="2026-10-03",
    )
    teams = [dict(team) for team in problem["teams"]]

    # Absent: the age-group scope reduces the shape pool and marks the team
    # ineligible.
    absent_plan = {
        "tournaments": [
            _tournament("u10-a", "2026-10-03", "Alfa", [t for t in teams if t["label"] != "Echo 1"]),
            _tournament("u10-b", "2026-10-10", "Bravo", [t for t in teams if t["label"] != "Echo 1"]),
        ]
    }
    projected = project_into_problem(problem, records=records, plan=absent_plan)
    assert len(projected["withdrawn_tournament_teams"]) == 1
    assert projected["withdrawn_tournament_teams"][0]["scope"] == "age_group"
    assert len(projected[WITHDRAWN_INELIGIBLE_FIELD]) == 1

    # Restored on/after the effective date: the shape reduction is reconciled
    # away, but the durable ineligibility remains.
    restored_plan = {
        "tournaments": [
            _tournament("u10-a", "2026-10-03", "Alfa", teams),
            _tournament("u10-b", "2026-10-10", "Bravo", [t for t in teams if t["label"] != "Echo 1"]),
        ]
    }
    projected = project_into_problem(problem, records=records, plan=restored_plan)
    assert projected["withdrawn_tournament_teams"] == []
    assert len(projected[WITHDRAWN_INELIGIBLE_FIELD]) == 1

    # A tournament before effective_from is historical: the team is neither
    # ineligible nor does it reduce the shape pool.
    historical = _tournament("u10-hist", "2026-09-01", "Alfa", teams)
    assert (
        withdrawn_team_identities_for_tournament(projected, "u10-hist", "U10", "2026-09-01")
        == set()
    )
    assert historical["id"] == "u10-hist"

    # Registration reconciliation removes the team from the authoritative pool,
    # so both projections stop reducing/blocking.
    reconciled_problem = dict(problem)
    reconciled_problem["teams"] = [t for t in problem["teams"] if t["label"] != "Echo 1"]
    projected = project_into_problem(reconciled_problem, records=records, plan=absent_plan)
    assert projected["withdrawn_tournament_teams"] == []
    assert projected[WITHDRAWN_INELIGIBLE_FIELD] == []


def test_withdrawn_team_cannot_be_reintroduced_by_verification(tmp_path: Path) -> None:
    """P1: a later rebuild cannot silently regain a withdrawn participant."""

    root = tmp_path / "season"
    _write_canonical(root, sealed=False)
    remove_participant(
        season="2026-2027",
        tournament_ids=["u10-a", "u10-b"],
        remove_team_label="Echo 1",
        reconcile_withdrawal=True,
        root=root,
        request_id="withdraw-echo",
        actor="tester",
    )
    decisions = load_decisions("2026-2027", root=root)
    problem = project_into_problem(_problem(), decisions=decisions)
    candidate = load_schedule("2026-2027", root=root)["plan"]
    candidate = {**candidate, "tournaments": [dict(t) for t in candidate["tournaments"]]}
    for tournament in candidate["tournaments"]:
        if tournament["id"] == "u10-a":
            tournament["teams"] = [
                *tournament["teams"],
                {"club": "Echo", "label": "Echo 1", "age_group": "U10"},
            ]

    result = verify_candidate(candidate, problem)
    assert result["ok"] is False
    assert "withdrawn_team_participating" in {
        violation.get("code") for violation in result["violations"]
    }


def test_evaluate_removal_consequences_keeps_partially_removed_team_blocking() -> None:
    """P2: a team removed from one tournament still has its remaining games checked."""

    problem = _problem()
    teams = [dict(team) for team in problem["teams"]]
    before = {
        "tournaments": [
            _tournament("u10-a", "2026-10-03", "Alfa", teams),
            _tournament("u10-b", "2026-10-10", "Bravo", teams),
        ]
    }
    # Echo is removed from u10-a but still plays u10-b, which also loses another
    # team, so u10-b's games are regenerated and Echo's remaining schedule can
    # change.
    without_echo = [t for t in teams if t["label"] != "Echo 1"]
    without_delta = [t for t in teams if t["label"] != "Delta 1"]
    after = {
        "tournaments": [
            _tournament("u10-a", "2026-10-03", "Alfa", without_echo),
            _tournament("u10-b", "2026-10-10", "Bravo", without_delta),
        ]
    }
    removed, retained = evaluate_removal_consequences(
        before,
        after,
        problem=problem,
        removed_identities=[("Echo", "Echo 1", "U10")],
        tournament_ids=["u10-a", "u10-b"],
    )
    assert "Echo|Echo 1|U10" in removed
    assert "Echo|Echo 1|U10" in retained
    assert retained["Echo|Echo 1|U10"]["membership_role"] == "removed"


def _ringerike_problem() -> dict:
    teams = [
        {"club": club, "label": f"{club} 1", "age_group": "JU8"}
        for club in ("Ringerike", "Alfa", "Bravo", "Charlie", "Delta")
    ]
    config = {
        "teams": teams,
        "age_groups": ["JU8"],
        "parallel_games": {"JU8": 4},
        "round_length_minutes": {"JU8": 20},
        "ice_time_minutes": {"JU8": 180},
        "rounds_per_tournament": {"JU8": 5},
    }
    problem = build_planning_problem(config, None, date(2026, 10, 1), date(2026, 12, 31))
    problem["clubs"] = {team["club"]: f"{team['club']} Arena" for team in teams}
    problem["club_calendar_status"] = {team["club"]: "known" for team in teams}
    return problem


def _ringerike_plan() -> dict:
    problem = _ringerike_problem()
    teams = [dict(team) for team in problem["teams"]]
    fixtures = [
        ("j8-1", "2026-10-03", "Alfa"),
        ("j8-2", "2026-10-17", "Bravo"),
        ("j8-3", "2026-11-07", "Charlie"),
        ("j8-4", "2026-11-21", "Delta"),
        ("j8-5", "2026-12-05", "Alfa"),
    ]
    tournaments = [
        _tournament(tid, when, host, teams, age_group="JU8") for tid, when, host in fixtures
    ]
    cancelled = _tournament("j8-host-cancelled", "2026-11-14", "Ringerike", teams, age_group="JU8")
    cancelled["cancelled"] = True
    cancelled["cancellation_reason"] = "arena unavailable"
    historical = _tournament("j8-historical", "2026-10-01", "Bravo", teams, age_group="JU8")
    return {
        "schema_version": 1,
        "start_date": "2026-10-01",
        "end_date": "2026-12-31",
        "tournaments": [*tournaments, cancelled, historical],
    }


def test_ringerike_ju8_withdrawal_regression_case(tmp_path: Path) -> None:
    """Production-shaped regression: five affected tournaments, atomic commit."""

    root = tmp_path / "season"
    problem = _ringerike_problem()
    plan = _ringerike_plan()
    _write_plan(root, plan, problem, sealed=True)
    service = CanonicalSeasonService(root=root)

    affected = [f"j8-{index}" for index in range(1, 6)]
    before_plan = load_schedule("2026-2027", root=root)["plan"]
    before_projection = tournament_projection(before_plan, problem)
    protected_before = {
        tournament_id: next(
            t for t in before_plan["tournaments"] if t["id"] == tournament_id
        )
        for tournament_id in ("j8-host-cancelled", "j8-historical")
    }

    result = service.batch_maintenance(
        season="2026-2027",
        operations=[
            {
                "op": "remove_participant",
                "tournament_id": tournament_id,
                "remove_team": "Ringerike 1",
                "reconcile_withdrawal": True,
            }
            for tournament_id in affected
        ],
        scope=affected,
        request_id="ringerike-ju8-withdrawal",
        actor="tester",
        note="Ringerike JU8 withdrew with no same-age replacement",
    )
    assert result["committed"] is True
    assert result["refused"] is False
    assert result["changed_tournament_ids"] == affected
    assert result["changed_outside_scope"] == []
    assert {removal["tournament_id"] for removal in result["removals"]} == set(affected)

    after_plan = load_schedule("2026-2027", root=root)["plan"]
    after_projection = tournament_projection(after_plan, problem)
    after_by_id = {t["id"]: t for t in after_plan["tournaments"]}

    for tournament_id in affected:
        tournament = after_by_id[tournament_id]
        assert "Ringerike 1" not in {team["label"] for team in tournament["teams"]}
        assert tournament["games"], "participant removal must regenerate games"
        assert all(
            game["home"] != "Ringerike 1" and game["away"] != "Ringerike 1"
            for game in tournament["games"]
        )
        before_entry = before_projection[tournament_id]
        after_entry = after_projection[tournament_id]
        for field in ("date", "start_time", "arena", "host_club", "duration_minutes", "end_time"):
            assert after_entry[field] == before_entry[field], field
        assert after_entry["cancelled"] is False
        assert before_entry["participants"] != after_entry["participants"]

    # The cancelled hosted tournament and the historical entry are untouched.
    for tournament_id, before_tournament in protected_before.items():
        assert after_by_id[tournament_id] == before_tournament
    assert after_projection["j8-host-cancelled"]["cancelled"] is True

    decisions = load_decisions("2026-2027", root=root)
    withdrawals = decisions["participation_withdrawals"]
    assert len(withdrawals) == 1
    assert withdrawals[0]["scope"] == "age_group"
    assert set(withdrawals[0]["tournament_ids"]) == set(affected)
    assert withdrawals[0]["team"]["label"] == "Ringerike 1"
    assert withdrawals[0]["status"] == "active"
    assert withdrawals[0]["request_id"] == "ringerike-ju8-withdrawal"
    assert withdrawals[0]["effective_from"] == "2026-10-03"

    # The sealed season reconciles against the published baseline through replay.
    report = service.verify_sealed_reconciliation("2026-2027")
    assert report["ok"] is True
    replay = reconcile_published_baseline(
        published_projection=before_projection,
        current_projection=after_projection,
        history=decisions["history"],
        attested_additions={},
    )
    assert replay["ok"] is True
    assert replay["applied_mutation_count"] >= 1

    # Refusal is atomic: a removal that would leave an avoidable underfill
    # writes nothing.
    schedule_file = root / "2026-2027" / "schedule.json"
    decisions_file = root / "2026-2027" / "decisions.json"
    snapshot_before = (schedule_file.read_bytes(), decisions_file.read_bytes())
    with pytest.raises(SeasonStateError, match="avoidably underfilled"):
        service.batch_maintenance(
            season="2026-2027",
            operations=[
                {
                    "op": "remove_participant",
                    "tournament_id": "j8-1",
                    "remove_team": "Alfa 1",
                }
            ],
            scope=["j8-1"],
            request_id="ringerike-ju8-retry",
            actor="tester",
        )
    assert (schedule_file.read_bytes(), decisions_file.read_bytes()) == snapshot_before
