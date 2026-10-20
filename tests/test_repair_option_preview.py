"""Repair-option previews through the shared consequence contract."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from tournament_scheduler.application.canonical_season.consequence_preview import (
    PREVIEW_CONTRACT,
    STATUS_BLOCKED,
    STATUS_SAFE_TO_APPLY,
    STATUS_STALE_REVISION,
    preview_canonical_operation,
)
from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.repair.apply import apply_repair
from tournament_scheduler.season.maintenance import repair_options
from tests.test_unplaced_placement_repair import (
    YEAR,
    _base_plan,
    _obligation,
    _problem,
    _teams,
    _write_season,
)

FINDING = "unplaced_placement:U10:2026-10-10:1"


def _season_with_unplaced_obligation(root: Path) -> dict:
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams, start=date(2026, 10, 1), end=date(2026, 10, 31))
    plan = _base_plan(
        [],
        _obligation(age_group="U10", day="2026-10-10", host="Sorby", roster=teams),
        start="2026-10-01",
        end="2026-10-31",
    )
    _write_season(root, plan, problem)
    return repair_options(YEAR, FINDING, root=root)


def _state_bytes(root: Path) -> tuple[bytes, bytes]:
    season_dir = root / YEAR
    return (
        (season_dir / "schedule.json").read_bytes(),
        (season_dir / "decisions.json").read_bytes(),
    )


def test_repair_preview_matches_dry_run_and_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "season"
    report = _season_with_unplaced_obligation(root)
    option_id = report["options"][0]["option_id"]
    before = _state_bytes(root)

    preview = preview_canonical_operation(
        CanonicalSeasonService(root=root),
        operation="repair",
        season=YEAR,
        expected_revision=report["revision"],
        option_id=option_id,
        finding_id=FINDING,
    )

    dry_run = apply_repair(YEAR, option_id, report["revision"], root=root, dry_run=True, finding_id=FINDING)
    assert dry_run["ok"] is True, dry_run

    assert preview["contract"] == PREVIEW_CONTRACT
    assert preview["operation"] == "repair"
    assert preview["writes_canonical_state"] is False
    assert preview["expected_revision"] == report["revision"]
    assert preview["verdict"]["status"] == STATUS_SAFE_TO_APPLY
    assert preview["verdict"]["applicable"] is True
    assert preview["verdict"]["checks"]["schedule_acceptable"] is True
    assert preview["verdict"]["checks"]["consequences_acceptable"] is True
    assert preview["candidate_fingerprint"]
    assert preview["changed_tournament_ids"]
    # Consequences are season-wide for a repair; no per-team rows are invented.
    assert preview["consequences"]["basis"] == "planned_schedule"
    assert preview["consequences"]["affected_teams"] == []
    assert _state_bytes(root) == before


def test_repair_preview_unknown_option_is_blocked_without_writing(tmp_path: Path) -> None:
    root = tmp_path / "season"
    report = _season_with_unplaced_obligation(root)
    before = _state_bytes(root)

    preview = preview_canonical_operation(
        CanonicalSeasonService(root=root),
        operation="repair",
        season=YEAR,
        expected_revision=report["revision"],
        option_id="not-a-real-option",
        finding_id=FINDING,
    )

    assert preview["verdict"]["status"] == STATUS_BLOCKED
    assert preview["verdict"]["applicable"] is False
    assert "unknown_or_stale_option" in preview["verdict"]["blockers"]
    assert preview["candidate_fingerprint"] is None
    assert _state_bytes(root) == before


def test_repair_preview_unknown_finding_is_blocked_without_writing(tmp_path: Path) -> None:
    root = tmp_path / "season"
    report = _season_with_unplaced_obligation(root)
    option_id = report["options"][0]["option_id"]
    before = _state_bytes(root)

    preview = preview_canonical_operation(
        CanonicalSeasonService(root=root),
        operation="repair",
        season=YEAR,
        expected_revision=report["revision"],
        option_id=option_id,
        finding_id="unplaced_placement:U10:2099-01-01:9",
    )

    assert preview["verdict"]["status"] == STATUS_BLOCKED
    assert preview["verdict"]["blockers"][0] == "unknown_or_stale_finding"
    assert _state_bytes(root) == before


def test_repair_preview_bound_to_a_stale_revision_is_not_evaluated(tmp_path: Path) -> None:
    root = tmp_path / "season"
    report = _season_with_unplaced_obligation(root)
    option_id = report["options"][0]["option_id"]
    before = _state_bytes(root)

    preview = preview_canonical_operation(
        CanonicalSeasonService(root=root),
        operation="repair",
        season=YEAR,
        expected_revision="0" * 64,
        option_id=option_id,
        finding_id=FINDING,
    )

    assert preview["verdict"]["status"] == STATUS_STALE_REVISION
    assert preview["verdict"]["applicable"] is False
    assert _state_bytes(root) == before


def test_season_preview_cli_reports_repair_verdict_without_writing(tmp_path: Path, capsys) -> None:
    from tournament_scheduler.cli.rvv_cli import main

    root = tmp_path / "season"
    report = _season_with_unplaced_obligation(root)
    option_id = report["options"][0]["option_id"]
    before = _state_bytes(root)

    rc = main(
        [
            "season",
            "preview",
            "--season",
            YEAR,
            "--root",
            str(root),
            "--operation",
            "repair",
            "--option-id",
            option_id,
            "--finding",
            FINDING,
            "--expected-revision",
            report["revision"],
            "--json",
        ]
    )

    assert rc == 0
    import json

    result = json.loads(capsys.readouterr().out)
    assert result["operation"] == "repair"
    assert result["verdict"]["status"] == STATUS_SAFE_TO_APPLY
    assert _state_bytes(root) == before
