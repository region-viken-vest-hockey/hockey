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
    assert preview["consequences"]["basis"] == "booked_reality"
    assert preview["consequences"]["provisional"] is True
    assert preview["consequences"]["affected_teams"]
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


def test_repair_preview_reports_writer_authorization_refusal_as_blocked(tmp_path: Path, monkeypatch) -> None:
    from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
    import tournament_scheduler.repair.apply as repair_apply

    root = tmp_path / "season"
    report = _season_with_unplaced_obligation(root)
    option_id = report["options"][0]["option_id"]
    before = _state_bytes(root)

    def refuse(**_kwargs):
        raise SeasonStateError("Refusing bounded repair: the operation does not change the schedule")

    monkeypatch.setattr(repair_apply, "authorize_bounded_repair", refuse)

    preview = preview_canonical_operation(
        CanonicalSeasonService(root=root),
        operation="repair",
        season=YEAR,
        expected_revision=report["revision"],
        option_id=option_id,
        finding_id=FINDING,
    )

    assert preview["verdict"]["status"] == STATUS_BLOCKED
    assert preview["verdict"]["applicable"] is False
    assert any("does not change the schedule" in item for item in preview["verdict"]["blockers"])
    assert _state_bytes(root) == before


def test_repair_preview_predicts_the_writer_outcome(tmp_path: Path) -> None:
    root = tmp_path / "season"
    report = _season_with_unplaced_obligation(root)
    option_id = report["options"][0]["option_id"]

    preview = preview_canonical_operation(
        CanonicalSeasonService(root=root),
        operation="repair",
        season=YEAR,
        expected_revision=report["revision"],
        option_id=option_id,
        finding_id=FINDING,
    )
    assert preview["verdict"]["status"] == STATUS_SAFE_TO_APPLY

    applied = apply_repair(YEAR, option_id, report["revision"], root=root, finding_id=FINDING)

    assert applied["ok"] is True, applied
    assert applied["delta"]["changed_tournament_ids"] == preview["changed_tournament_ids"]
    assert CanonicalSeasonService(root=root).load(YEAR).schedule["revision"] != report["revision"]


def test_repair_preview_lists_each_affected_team_with_its_planned_change(tmp_path: Path) -> None:
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

    assert preview["verdict"]["status"] == STATUS_SAFE_TO_APPLY
    planned = preview["consequences"]["planned_affected_teams"]
    # The materialized tournament hosts Sorby with Nordby: both rostered clubs gain one game.
    assert {row["club"] for row in planned} == {"Nordby", "Sorby"}
    for row in planned:
        assert row["before"]["planned"] == 0
        assert row["after"]["planned"] == 1
        assert row["delta"]["planned"] == 1
    # The same teams in booked terms: nothing is booked yet, and the fairness is provisional.
    booked = preview["consequences"]["affected_teams"]
    assert {row["club"] for row in booked} == {"Nordby", "Sorby"}
    for row in booked:
        assert row["before"]["booked"] == 0
        assert row["after"]["booked"] == 0
        assert row["provisional"] is True
    assert preview["consequences"]["basis"] == "booked_reality"
    assert _state_bytes(root) == before


def test_repair_team_consequences_keep_teams_of_a_tournament_the_repair_cancels() -> None:
    from tournament_scheduler.application.canonical_season.repair_consequences import (
        repair_team_consequences,
    )

    team = {"club": "Alfa", "label": "Alfa 1", "age_group": "U10"}
    tournament = {"id": "t1", "date": "2026-10-03", "age_group": "U10", "teams": [team], "games": []}
    before = {"tournaments": [dict(tournament)]}
    after = {"tournaments": [{**tournament, "cancelled": True}]}

    result = repair_team_consequences(before, after, None)

    rows = list(result["participation_counts"].values())
    assert len(rows) == 1
    assert rows[0]["identity"]["club"] == "Alfa"
    assert rows[0]["before"]["season"] == 1
    assert rows[0]["after"]["season"] == 0


def _team_plan(tournaments: list[dict]) -> dict:
    return {"start_date": "2026-10-01", "end_date": "2026-10-31", "tournaments": tournaments}


def _team_tournament(tournament_id: str, day: str, teams: list[dict]) -> dict:
    return {"id": tournament_id, "date": day, "age_group": "U10", "teams": teams, "games": []}


def test_repair_team_consequences_report_an_individual_team_regression() -> None:
    from tournament_scheduler.application.canonical_season.consequence_summary import build_consequences
    from tournament_scheduler.application.canonical_season.repair_consequences import (
        repair_team_consequences,
    )

    alfa = {"club": "Alfa", "label": "Alfa 1", "age_group": "U10"}
    bravo = {"club": "Bravo", "label": "Bravo 1", "age_group": "U10"}
    before = _team_plan([_team_tournament("t1", "2026-10-03", [alfa, bravo])])
    # The repair adds a second tournament for Alfa only, three days after its first:
    # a serious individual-team regression for Alfa, unrelated to Bravo.
    after = _team_plan(
        [
            _team_tournament("t1", "2026-10-03", [alfa, bravo]),
            _team_tournament("t2", "2026-10-06", [alfa]),
        ]
    )

    result = repair_team_consequences(before, after, None)

    alfa_key = "Alfa:Alfa 1:U10"
    codes = {item["code"] for item in result["team_consequences"][alfa_key]["material_regressions"]}
    assert "more_gaps_under_7_days" in codes
    assert all(
        not item.get("code") == "more_gaps_under_7_days"
        for item in result["team_consequences"].get("Bravo:Bravo 1:U10", {}).get("material_regressions", [])
    )

    # With no season-wide adoption regression, the team regression must still surface.
    details = {
        "participation_counts": result["participation_counts"],
        "team_consequences": result["team_consequences"],
        "adoption": {"material_regressions": []},
    }
    surfaced = build_consequences("repair", details)["material_regressions"]
    assert any(
        item["role"] == alfa_key and item["code"] == "more_gaps_under_7_days" for item in surfaced
    )


def test_repair_booked_fairness_drops_a_manual_booking_the_repair_moves(tmp_path: Path) -> None:
    from datetime import date as _date

    from tournament_scheduler.application.canonical_season.repair_consequences import (
        repair_team_consequences,
    )
    from tournament_scheduler.booking.calendar_bookings import new_manual_assertion_record
    from tests.test_unplaced_placement_repair import _problem as _repair_problem, _teams

    teams = _teams(["Nordby", "Sorby"])
    problem = _repair_problem(teams, start=_date(2026, 10, 1), end=_date(2026, 10, 31))

    def tournament(day: str) -> dict:
        return {
            "id": "t1",
            "date": day,
            "age_group": "U10",
            "host_club": "Sorby",
            "arena": "Sorby Arena",
            "start_time": "10:00",
            "teams": teams,
            "games": [],
        }

    before = _team_plan([tournament("2026-10-03")])
    after = _team_plan([tournament("2026-10-10")])
    # The club confirmed the 3 October slot; a repair that moves it to 10 October
    # must not carry that confirmation along.
    record = new_manual_assertion_record(
        tournament=before["tournaments"][0],
        booking_status="booked",
        problem=problem,
        actor="tester",
        note="club confirmed by email",
        reference="email-2026-10-01",
        source_scope="tournament",
        stated_interval=None,
        asserted_at="2026-10-01T00:00:00+00:00",
        source_revision="rev-test",
    )
    decisions = {"manual_booking_assertions": [record], "decisions": {}, "history": []}

    result = repair_team_consequences(before, after, problem, decisions)

    rows = result["booked_fairness"]["impacted_teams"]
    assert {row["club"] for row in rows} == {"Nordby", "Sorby"}
    for row in rows:
        assert row["before_booked"] == 1
        assert row["after_booked"] == 0
        assert row["delta"] == -1
