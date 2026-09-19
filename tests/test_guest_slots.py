"""Reserved guest slots: canonical lifecycle, verification and export.

Covers the acceptance criteria for JU10/JU12 guest opportunities:
reserve -> optimize/export, fill, release; capacity vs RVV participation;
metrics not distorted; the canonical repair vocabulary cannot consume a
reserved place; and the HTML/audit surface distinguishes open/filled places.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.guest_slots import (
    active_guest_slot_count,
    guest_slot_summary,
    open_guest_slot_count,
    rvv_team_count,
)
from tournament_scheduler.game_generation import generate_tournament_games
from tournament_scheduler.html.html_exporter import HtmlExporter
from tournament_scheduler.models import Team, Tournament
from tournament_scheduler.participation_targets import evaluate_participation
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.planning_contract import verify_candidate
from tournament_scheduler.serialization.season_plan import season_plan_from_dict, season_plan_to_dict
from tournament_scheduler.season_state import (
    SeasonStateError,
    apply_candidate,
    fill_guest_slot,
    guest_slot_candidates,
    guest_slot_report,
    load_schedule,
    promote_from_stage3,
    release_guest_slot,
    reserve_guest_slot,
)
from tournament_scheduler.testing.reviewed_export import (
    build_problem_from_candidate,
    write_reviewed_stage4_export,
)
from tournament_scheduler.underfilled_roster_repair import enumerate_underfilled_roster_repairs


def _round_robin(teams: list[dict]) -> list[dict]:
    team_objects = [
        Team(club=team["club"], label=team["label"], age_group=team["age_group"])
        for team in teams
    ]
    return [
        {
            "home": game.home.label,
            "away": game.away.label,
            "parallel_slot": game.parallel_slot,
            "round_number": game.round_number,
        }
        for game in generate_tournament_games(team_objects, parallel_games=2)
    ]


def _candidate(team_labels: list[str], *, tournament_id: str = "ju12-a-20261010") -> dict:
    teams = [
        {"club": label[0], "label": label, "age_group": "JU12"} for label in team_labels
    ]
    return {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [
            {
                "id": tournament_id,
                "date": "2026-10-10",
                "arena": "Arena A",
                "age_group": "JU12",
                "host_club": "A",
                "teams": teams,
                "games": _round_robin(teams),
                "start_time": "10:00",
            }
        ],
    }


def _promote(tmp_path: Path, candidate: dict, *, parallel_games: int = 2):
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    state.write_stage(StageName.PLANNING, {"plan": candidate}, status=StageStatus.DONE)
    problem = build_problem_from_candidate(candidate)
    problem["parallel_games"] = {candidate["tournaments"][0]["age_group"]: parallel_games}
    write_reviewed_stage4_export(state, problem=problem)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    return work_dir, root, problem


def _spare_capacity_case(tmp_path: Path):
    """JU12 with only three registered teams: a reserved place fits naturally."""

    candidate = _candidate(["A1", "B1", "C1"])
    return _promote(tmp_path, candidate)


def _full_capacity_case(tmp_path: Path):
    """JU12 with a full four-team field: reserving requires a displacement."""

    candidate = _candidate(["A1", "B1", "C1", "D1"])
    return _promote(tmp_path, candidate)


def test_guest_slot_helpers_and_codec_round_trip() -> None:
    tournament = Tournament(
        date=__import__("datetime").date(2026, 10, 10),
        arena="Arena A",
        age_group="JU12",
        host_club="A",
        teams=[
            Team(club="A", label="A1", age_group="JU12"),
            Team(club="B", label="B1", age_group="JU12"),
            Team(club="X", label="X1", age_group="JU12", guest=True),
        ],
        guest_slots=[
            {"id": "guest:1", "status": "filled", "external_team": {"club": "X", "label": "X1"}},
            {"id": "guest:2", "status": "open"},
        ],
    )
    assert active_guest_slot_count(tournament) == 2
    assert open_guest_slot_count(tournament) == 1
    assert rvv_team_count(tournament) == 2
    summary = guest_slot_summary(tournament)
    assert summary["reserved"] == 2
    assert summary["filled"] == 1
    assert summary["open"] == 1
    assert summary["released"] == 0

    payload = season_plan_to_dict(
        __import__("tournament_scheduler.models", fromlist=["SeasonPlan"]).SeasonPlan(
            tournaments=[tournament]
        )
    )
    stored = payload["tournaments"][0]
    assert stored["reserved_guest_slots"] == 2
    assert stored["teams"][2]["guest"] is True
    restored = season_plan_from_dict(payload).tournaments[0]
    assert restored.reserved_guest_slots == 2
    assert restored.teams[2].guest is True
    assert open_guest_slot_count(restored) == 1


def test_codec_accepts_simple_reserved_guest_slots_integer() -> None:
    payload = {
        "tournaments": [
            {
                "id": "t1",
                "date": "2026-10-10",
                "arena": "Arena A",
                "age_group": "JU10",
                "host_club": "A",
                "teams": [],
                "games": [],
                "reserved_guest_slots": 1,
            }
        ]
    }
    plan = season_plan_from_dict(payload)
    assert plan.tournaments[0].reserved_guest_slots == 1
    assert open_guest_slot_count(plan.tournaments[0]) == 1


def test_reserve_uses_spare_capacity_without_removing_a_team(tmp_path: Path) -> None:
    _work_dir, root, problem = _spare_capacity_case(tmp_path)

    schedule = reserve_guest_slot(
        season="2026-2027",
        tournament_id="ju12-a-20261010",
        root=root,
        problem=problem,
        actor="planner",
        note="external league team may apply",
    )
    tournament = schedule["plan"]["tournaments"][0]
    assert rvv_team_count(tournament) == 3
    assert open_guest_slot_count(tournament) == 1
    assert tournament["reserved_guest_slots"] == 1

    report = guest_slot_report("2026-2027", root=root)
    assert report["open_total"] == 1
    assert report["filled_total"] == 0
    assert report["tournaments"][0]["rvv_team_count"] == 3

    decisions = json.loads((root / "2026-2027" / "decisions.json").read_text(encoding="utf-8"))
    events = [entry["event"] for entry in decisions["history"]]
    assert "reserve_guest_slot" in events


def test_reserve_requires_displacement_on_a_full_tournament(tmp_path: Path) -> None:
    _work_dir, root, problem = _full_capacity_case(tmp_path)

    with pytest.raises(SeasonStateError) as excinfo:
        reserve_guest_slot(
            season="2026-2027",
            tournament_id="ju12-a-20261010",
            root=root,
            problem=problem,
        )
    assert "free place" in str(excinfo.value)

    with pytest.raises(SeasonStateError):
        reserve_guest_slot(
            season="2026-2027",
            tournament_id="ju12-a-20261010",
            root=root,
            problem=problem,
            displaced_teams=["A1"],  # host club's only team
        )

    schedule = reserve_guest_slot(
        season="2026-2027",
        tournament_id="ju12-a-20261010",
        root=root,
        problem=problem,
        displaced_teams=["B1"],
        actor="planner",
    )
    tournament = schedule["plan"]["tournaments"][0]
    assert rvv_team_count(tournament) == 3
    assert open_guest_slot_count(tournament) == 1
    # Host representation survives: A1 is still present.
    assert any(team["label"] == "A1" for team in tournament["teams"] if not team.get("guest"))


def test_reserved_place_cannot_be_consumed_by_roster_repair(tmp_path: Path) -> None:
    _work_dir, root, problem = _spare_capacity_case(tmp_path)
    schedule = reserve_guest_slot(
        season="2026-2027",
        tournament_id="ju12-a-20261010",
        root=root,
        problem=problem,
    )

    verification = verify_candidate(schedule["plan"], problem)
    assert "bye_team_not_allowed" not in {v["code"] for v in verification["violations"]}

    repair = enumerate_underfilled_roster_repairs(schedule["plan"], problem)
    assert repair["options"] == []


def test_reserved_slots_do_not_distort_participation_metrics() -> None:
    candidate = _candidate(["A1", "B1", "C1"])
    candidate["tournaments"][0]["teams"].append(
        {"club": "X", "label": "X1", "age_group": "JU12", "guest": True}
    )
    candidate["tournaments"][0]["guest_slots"] = [
        {"id": "guest:1", "status": "filled", "external_team": {"club": "X", "label": "X1"}}
    ]
    problem = build_problem_from_candidate(
        _candidate(["A1", "B1", "C1", "D1"])
    )
    problem["participation_targets_by_age_group"] = {
        "JU12": {"before_christmas": 1, "after_christmas": 1}
    }
    evaluation = evaluate_participation(candidate, problem)
    counted = {
        (entry["club"], entry["label"])
        for entry in evaluation.teams
    }
    assert ("X", "X1") not in counted
    assert ("A", "A1") in counted


def test_filled_guest_is_not_a_registered_rvv_team_and_games_regenerate(tmp_path: Path) -> None:
    _work_dir, root, problem = _spare_capacity_case(tmp_path)
    reserve_guest_slot(
        season="2026-2027",
        tournament_id="ju12-a-20261010",
        root=root,
        problem=problem,
    )

    schedule = fill_guest_slot(
        season="2026-2027",
        tournament_id="ju12-a-20261010",
        slot_id=None,
        external_team={"club": "External IF", "label": "External IF 1"},
        root=root,
        problem=problem,
        actor="planner",
        note="external team accepted",
    )
    tournament = schedule["plan"]["tournaments"][0]
    assert open_guest_slot_count(tournament) == 0
    assert guest_slot_summary(tournament)["filled"] == 1
    guests = [team for team in tournament["teams"] if team.get("guest")]
    assert len(guests) == 1 and guests[0]["label"] == "External IF 1"
    # A complete four-team round robin now exists, including the guest.
    assert len(tournament["games"]) == 6
    assert any(
        "External IF 1" in (game["home"], game["away"]) for game in tournament["games"]
    )

    verification = verify_candidate(schedule["plan"], problem)
    assert verification["ok"], verification["violations"]
    assert "unregistered_team" not in {v["code"] for v in verification["violations"]}

    # The filled guest is not an RVV season participation.
    assert rvv_team_count(tournament) == 3


def test_release_can_replacement_fill_and_verifies(tmp_path: Path) -> None:
    _work_dir, root, problem = _full_capacity_case(tmp_path)
    reserve_guest_slot(
        season="2026-2027",
        tournament_id="ju12-a-20261010",
        root=root,
        problem=problem,
        displaced_teams=["B1"],
    )

    # Releasing without a replacement would leave an underfilled tournament.
    with pytest.raises(SeasonStateError):
        release_guest_slot(
            season="2026-2027",
            tournament_id="ju12-a-20261010",
            root=root,
            problem=problem,
        )

    schedule = release_guest_slot(
        season="2026-2027",
        tournament_id="ju12-a-20261010",
        root=root,
        problem=problem,
        replacement_team={"club": "B", "label": "B1", "age_group": "JU12"},
        note="no external team applied",
    )
    tournament = schedule["plan"]["tournaments"][0]
    assert active_guest_slot_count(tournament) == 0
    assert rvv_team_count(tournament) == 4
    verification = verify_candidate(schedule["plan"], problem)
    assert verification["ok"], verification["violations"]


def test_release_removes_filled_guest(tmp_path: Path) -> None:
    _work_dir, root, problem = _spare_capacity_case(tmp_path)
    reserve_guest_slot(
        season="2026-2027",
        tournament_id="ju12-a-20261010",
        root=root,
        problem=problem,
    )
    fill_guest_slot(
        season="2026-2027",
        tournament_id="ju12-a-20261010",
        slot_id=None,
        external_team={"club": "External IF", "label": "External IF 1"},
        root=root,
        problem=problem,
    )
    schedule = release_guest_slot(
        season="2026-2027",
        tournament_id="ju12-a-20261010",
        root=root,
        problem=problem,
        note="guest team withdrew",
    )
    tournament = schedule["plan"]["tournaments"][0]
    assert not any(team.get("guest") for team in tournament["teams"])
    assert guest_slot_summary(tournament)["released"] == 1
    assert active_guest_slot_count(tournament) == 0
    verification = verify_candidate(schedule["plan"], problem)
    assert verification["ok"], verification["violations"]


def test_apply_candidate_cannot_silently_drop_reservations(tmp_path: Path) -> None:
    _work_dir, root, problem = _spare_capacity_case(tmp_path)
    reserve_guest_slot(
        season="2026-2027",
        tournament_id="ju12-a-20261010",
        root=root,
        problem=problem,
    )
    schedule = load_schedule("2026-2027", root=root)
    stripped = json.loads(json.dumps(schedule["plan"]))
    stripped["tournaments"][0].pop("guest_slots", None)
    stripped["tournaments"][0].pop("reserved_guest_slots", None)

    with pytest.raises(SeasonStateError) as excinfo:
        apply_candidate(
            season="2026-2027",
            candidate=stripped,
            root=root,
            problem=problem,
        )
    assert "reserved guest slots" in str(excinfo.value)


def test_guest_slot_candidates_exposes_deterministic_legal_facts(tmp_path: Path) -> None:
    _work_dir, root, problem = _spare_capacity_case(tmp_path)
    report = guest_slot_candidates(
        season="2026-2027",
        root=root,
        problem=problem,
        age_groups=["JU12"],
    )
    assert report["age_groups"] == ["JU12"]
    candidate = report["candidates"][0]
    assert candidate["tournament_id"] == "ju12-a-20261010"
    assert candidate["free_places"] == 1
    assert candidate["legal"] is True
    assert candidate["rank"] == 1


def test_canonical_service_candidate_facts_and_locked_refusal(tmp_path: Path) -> None:
    _work_dir, root, problem = _spare_capacity_case(tmp_path)
    service = CanonicalSeasonService(root=root)
    service.approve_tournament(
        season="2026-2027",
        tournament_id="ju12-a-20261010",
        placement_locked=True,
        participants_locked=True,
        problem=problem,
    )
    report = service.guest_slot_candidates(season="2026-2027", age_groups=["JU12"], problem=problem)
    assert report["candidates"][0]["legal"] is False
    assert report["candidates"][0]["participants_locked"] is True

    with pytest.raises(SeasonStateError):
        service.reserve_guest_slot(
            season="2026-2027",
            tournament_id="ju12-a-20261010",
            problem=problem,
        )


def test_html_export_shows_open_and_filled_guest_places(tmp_path: Path) -> None:
    _work_dir, root, problem = _spare_capacity_case(tmp_path)
    schedule = reserve_guest_slot(
        season="2026-2027",
        tournament_id="ju12-a-20261010",
        root=root,
        problem=problem,
    )
    plan = season_plan_from_dict(schedule["plan"])
    payload = json.loads(HtmlExporter._plan_to_json(plan))
    assert payload[0]["gs"]["o"] == 1
    assert payload[0]["gs"]["r"] == 1

    # The rendered page ships the Norwegian label for an open guest place.
    template = (
        Path(__file__).resolve().parents[1]
        / "tournament_scheduler"
        / "html"
        / "templates"
        / "script_schedule.js"
    ).read_text(encoding="utf-8")
    assert "ledig gjesteplass" in template


def test_final_verification_accepts_open_reservation(tmp_path: Path) -> None:
    from tournament_scheduler.final_verification import verify_final_candidate

    _work_dir, root, problem = _spare_capacity_case(tmp_path)
    schedule = reserve_guest_slot(
        season="2026-2027",
        tournament_id="ju12-a-20261010",
        root=root,
        problem=problem,
    )
    result = verify_final_candidate(schedule["plan"], problem)
    assert result["ok"], result["violations"]


def test_cli_guest_operations_round_trip(tmp_path: Path, capsys) -> None:
    work_dir, root, _problem = _spare_capacity_case(tmp_path)
    from tournament_scheduler.cli.rvv_cli import main

    def run(*args: str) -> dict:
        argv = ["season", *args, "--root", str(root)]
        if args and args[0] != "guest-report":
            argv += ["--work-dir", str(work_dir)]
        argv.append("--json")
        code = main(argv)
        assert code == 0, capsys.readouterr().out
        return json.loads(capsys.readouterr().out)

    candidates = run("guest-candidates", "--season", "2026-2027", "--age-groups", "JU12")
    assert candidates["candidates"][0]["legal"] is True

    reserved = run(
        "guest-reserve",
        "--season",
        "2026-2027",
        "--tournament-id",
        "ju12-a-20261010",
        "--note",
        "external league",
    )
    assert reserved["plan"]["tournaments"][0]["reserved_guest_slots"] == 1

    report = run("guest-report", "--season", "2026-2027")
    assert report["open_total"] == 1
