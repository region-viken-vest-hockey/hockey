from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tournament_scheduler.application.canonical_season.config_reconciliation import reconcile_config
from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.cli.rvv_cli import _canonical_verification_problem
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.season_maintenance import repair_options
from tournament_scheduler.season_state import canonical_state_revision, load_decisions, load_schedule, schedule_fingerprint

YEAR = "2026-2027"


def _team(club: str, label: str, age_group: str = "U10") -> dict[str, str]:
    return {"club": club, "label": label, "age_group": age_group}


def _games(labels: list[str], rounds: int) -> list[dict[str, Any]]:
    return [
        {"home": labels[0], "away": labels[1], "parallel_slot": 0, "round_number": round_number}
        for round_number in range(1, rounds + 1)
    ]


def _tournament(tournament_id: str, age_group: str, date: str, rounds: int) -> dict[str, Any]:
    teams = [_team("Jar", f"Jar {age_group}", age_group), _team("Kongsberg", f"Kongsberg {age_group}", age_group)]
    return {
        "id": tournament_id,
        "date": date,
        "arena": "Jar Isforum",
        "age_group": age_group,
        "host_club": "Jar",
        "teams": teams,
        "games": _games([team["label"] for team in teams], rounds),
        "start_time": "10:00",
    }


def _write_season(root: Path, plan: dict[str, Any], problem: dict[str, Any]) -> None:
    season_dir = root / YEAR
    season_dir.mkdir(parents=True)
    fingerprint = schedule_fingerprint(plan)
    schedule = {
        "schema_version": 1,
        "season": YEAR,
        "revision": fingerprint,
        "fingerprint": fingerprint,
        "plan_schema_version": 1,
        "plan": plan,
        "verification_context": {"problem": problem},
    }
    decisions = {
        "schema_version": 1,
        "season": YEAR,
        "schedule_fingerprint": fingerprint,
        "decisions": {t["id"]: {"status": "approved", "placement_locked": True} for t in plan["tournaments"]},
    }
    decisions["canonical_state_revision"] = canonical_state_revision(schedule, decisions)
    (season_dir / "schedule.json").write_text(json.dumps(schedule, sort_keys=True), encoding="utf-8")
    (season_dir / "decisions.json").write_text(json.dumps(decisions, sort_keys=True), encoding="utf-8")


def _legacy_problem() -> dict[str, Any]:
    return {
        "teams": [
            _team("Jar", "Jar U10", "U10"),
            _team("Kongsberg", "Kongsberg U10", "U10"),
            _team("Jar", "Jar U12", "U12"),
            _team("Kongsberg", "Kongsberg U12", "U12"),
        ],
        "age_groups": ["U10", "U12"],
        "round_length_minutes": {"U10": 15, "U12": 15},
        "ice_time_minutes": {"U10": 115, "U12": 85},
        "rounds_per_tournament": {"U10": 5, "U12": 3},
    }


def test_reconcile_config_dry_run_preserves_legacy_effective_occupancy(tmp_path: Path) -> None:
    root = tmp_path / "season"
    plan = {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [_tournament("u10", "U10", "2026-10-10", 5), _tournament("u12", "U12", "2026-10-17", 3)],
    }
    _write_season(root, plan, _legacy_problem())

    report = reconcile_config(
        CanonicalSeasonService(root=root),
        season=YEAR,
        dry_run=True,
        current_config={"ice_time_minutes": {"U10": 140, "U12": 100}},
    )

    assert report["changed"] is True
    assert report["safe"] is True
    assert report["verification_ok"] is True
    migration = report["semantic_migrations"][0]
    assert migration["age_group_changes"] == {
        "U10": {"old_value": 115, "migrated_value": 140},
        "U12": {"old_value": 85, "migrated_value": 100},
    }
    by_id = {change["tournament_id"]: change for change in migration["changes"]}
    assert by_id["u10"]["legacy_effective_occupancy_minutes"] == 140
    assert by_id["u10"]["preserved_end_time_after_migration"] == "12:20"
    assert by_id["u12"]["legacy_effective_occupancy_minutes"] == 100
    assert load_schedule(YEAR, root=root)["verification_context"]["problem"]["ice_time_minutes"] == {"U10": 115, "U12": 85}


def test_reconcile_config_apply_is_atomic_and_audited(tmp_path: Path) -> None:
    root = tmp_path / "season"
    plan = {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [_tournament("u10", "U10", "2026-10-10", 5)],
    }
    problem = _legacy_problem()
    problem["age_groups"] = ["U10"]
    problem["ice_time_minutes"] = {"U10": 115}
    _write_season(root, plan, problem)
    before_schedule = load_schedule(YEAR, root=root)
    before_decisions = load_decisions(YEAR, root=root)

    report = reconcile_config(
        CanonicalSeasonService(root=root),
        season=YEAR,
        dry_run=False,
        actor="tester",
        note="migrate ice-time semantics",
        current_config={"ice_time_minutes": {"U10": 140}},
    )

    after_schedule = load_schedule(YEAR, root=root)
    after_decisions = load_decisions(YEAR, root=root)
    assert after_schedule["plan"] == before_schedule["plan"]
    assert after_schedule["verification_context"]["problem"]["ice_time_minutes"] == {"U10": 140}
    assert report["previous_canonical_state_revision"] != report["canonical_state_revision"]
    assert after_decisions["canonical_state_revision"] == report["canonical_state_revision"]
    assert after_decisions["decisions"] == before_decisions["decisions"]
    assert after_decisions["history"][-1]["event"] == "reconcile_config"


def test_reconcile_config_rederives_legacy_unresolved_obligation_durations(tmp_path: Path) -> None:
    root = tmp_path / "season"
    obligation = {
        "id": "unplaced_placement:U10:2026-12-19:1",
        "age_group": "U10",
        "date": "2026-12-19",
        "responsible_host": "Jar",
        "participant_teams": [_team("Jar", "Jar U10", "U10"), _team("Kongsberg", "Kongsberg U10", "U10")],
        "required_duration_minutes": 115,
        "configured_ice_time_minutes": 115,
    }
    plan = {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [_tournament("u10", "U10", "2026-10-10", 5)],
        "unresolved_tournament_placements": [obligation],
    }
    problem = _legacy_problem()
    problem["age_groups"] = ["U10"]
    problem["ice_time_minutes"] = {"U10": 115}
    _write_season(root, plan, problem)

    report = reconcile_config(
        CanonicalSeasonService(root=root),
        season=YEAR,
        dry_run=False,
        current_config={"ice_time_minutes": {"U10": 140}},
    )

    duration_report = report["unresolved_placement_duration_reconciliation"]
    assert duration_report["changes"][0]["old_required_duration_minutes"] == 115
    assert duration_report["changes"][0]["authoritative_duration_minutes"] == 140
    persisted = load_schedule(YEAR, root=root)["plan"]["unresolved_tournament_placements"][0]
    assert persisted["required_duration_minutes"] == 140
    assert persisted["configured_ice_time_minutes"] == 140
    assert persisted["duration_authority"] == "verification_context.ice_time_minutes"
    assert persisted["legacy_duration_evidence"]["required_duration_minutes"] == 115
    assert persisted["legacy_duration_evidence"]["non_authoritative_after_reconciliation"] is True

    option_report = repair_options(
        YEAR, "unplaced_placement:U10:2026-12-19:1", root=root
    )
    assert option_report["options"][0]["evidence"]["end_time"] == "12:20"


def test_cli_maintenance_uses_reconciled_canonical_problem_instead_of_stale_pipeline(tmp_path: Path) -> None:
    root = tmp_path / "season"
    plan = {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [_tournament("u10", "U10", "2026-10-10", 5)],
    }
    problem = _legacy_problem()
    problem["age_groups"] = ["U10"]
    problem["ice_time_minutes"] = {"U10": 140}
    _write_season(root, plan, problem)
    stale_work_dir = tmp_path / ".pipeline"
    stale_work_dir.mkdir()

    resolved = _canonical_verification_problem(str(stale_work_dir), YEAR, str(root))

    assert resolved is not None
    assert resolved["ice_time_minutes"] == {"U10": 140}


def test_reconcile_config_refuses_to_copy_current_minimum_that_does_not_preserve_legacy_meaning(tmp_path: Path) -> None:
    root = tmp_path / "season"
    plan = {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [_tournament("u10", "U10", "2026-10-10", 5)],
    }
    problem = _legacy_problem()
    problem["age_groups"] = ["U10"]
    problem["ice_time_minutes"] = {"U10": 115}
    _write_season(root, plan, problem)

    preview = reconcile_config(
        CanonicalSeasonService(root=root),
        season=YEAR,
        dry_run=True,
        current_config={"ice_time_minutes": {"U10": 120}},
    )

    assert preview["refused"] is True
    assert preview["changed"] is False
    assert preview["semantic_migrations"][0]["requires_operator"][0]["proposed_migrated_value"] == 140
    with pytest.raises(SeasonStateError):
        reconcile_config(
            CanonicalSeasonService(root=root),
            season=YEAR,
            dry_run=False,
            current_config={"ice_time_minutes": {"U10": 120}},
        )
    assert load_schedule(YEAR, root=root)["verification_context"]["problem"]["ice_time_minutes"] == {"U10": 115}
