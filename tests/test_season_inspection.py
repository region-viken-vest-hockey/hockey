"""Read-only canonical-season inspection projections (issue #586).

These tests characterize the supported investigation path: inspect a tournament
and its roster/status/constraints, inspect constraints filtered by domain
identity, list registered replacement candidates, and feed a candidate into the
verified replacement dry-run. None of the inspection calls mutate canonical
state.
"""

from __future__ import annotations

import json
from pathlib import Path

from tournament_scheduler.cli.args import build_parser
from tournament_scheduler.cli.rvv_cli import _cmd_season
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.season_state import (
    SeasonStateError,
    add_request_constraint,
    constraint_inspection,
    load_schedule,
    promote_from_stage3,
    replace_participant,
    replacement_candidates,
    tournament_inspection,
)
from tournament_scheduler.testing.reviewed_export import (
    build_problem_from_candidate,
    write_reviewed_stage4_export,
)

import pytest


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


def _tournament(tournament_id: str, date: str, host: str, arena: str, teams, start: str) -> dict:
    roster = [
        {"club": club, "label": label, "age_group": "U10"} for club, label in teams
    ]
    return {
        "id": tournament_id,
        "date": date,
        "arena": arena,
        "age_group": "U10",
        "host_club": host,
        "start_time": start,
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
                "t1",
                "2026-09-12",
                "A",
                "Arena A",
                [("A", "A1"), ("B", "B1"), ("C", "C1"), ("D", "D1")],
                "10:00",
            ),
            _tournament(
                "t2",
                "2026-09-12",
                "B",
                "Arena B",
                [("B", "B2"), ("E", "E1"), ("E", "E2"), ("H", "H1")],
                "12:00",
            ),
            _tournament(
                "t3",
                "2026-10-10",
                "A",
                "Arena A",
                [("A", "A1"), ("B", "B1"), ("C", "C1"), ("G", "G1")],
                "10:00",
            ),
        ],
    }


def _problem_with_registered_pool(candidate: dict) -> dict:
    problem = build_problem_from_candidate(candidate)
    problem["teams"] = list(problem["teams"]) + [
        {"club": "F", "label": "F1", "age_group": "U12"}
    ]
    return problem


def _promote(tmp_path: Path) -> Path:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    candidate = _candidate()
    state.write_stage(StageName.PLANNING, {"plan": candidate}, status=StageStatus.DONE)
    write_reviewed_stage4_export(
        state, problem=_problem_with_registered_pool(candidate)
    )
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    return root


def test_tournament_inspection_projects_roster_status_and_constraints(
    tmp_path: Path,
) -> None:
    root = _promote(tmp_path)
    add_request_constraint(
        season="2026-2027",
        type="team_unavailable",
        request_id="club-feedback:a:a1",
        teams=[{"club": "A", "label": "A1", "age_group": "U10"}],
        date_from="2026-09-12",
        date_to="2026-09-12",
        root=root,
    )

    report = tournament_inspection(
        season="2026-2027", tournament_id="t1", root=root
    )

    tournament = report["tournament"]
    assert tournament["id"] == "t1"
    assert tournament["age_group"] == "U10"
    assert {team["label"] for team in tournament["teams"]} == {
        "A1",
        "B1",
        "C1",
        "D1",
    }
    assert report["approval"]["status"] == "pending_review"
    assert report["approval"]["approved"] is False
    assert report["canonical_state_revision"]
    constraint_ids = [item["id"] for item in report["constraints"]["constraints"]]
    assert constraint_ids
    # The inspection is read-only.
    assert load_schedule("2026-2027", root=root)["revision"] == report["revision"]


def test_tournament_inspection_excludes_unrelated_same_date_team_constraint(
    tmp_path: Path,
) -> None:
    """A team-scoped constraint follows its team, not the tournament date."""

    root = _promote(tmp_path)
    # E1 plays t2 on 2026-09-12 (same date as t1) but is not a t1 participant,
    # so this constraint must not appear as relevant to t1.
    add_request_constraint(
        season="2026-2027",
        type="team_unavailable",
        request_id="club-feedback:e:e1",
        teams=[{"club": "E", "label": "E1", "age_group": "U10"}],
        date_from="2026-09-12",
        date_to="2026-09-12",
        root=root,
    )

    t1 = tournament_inspection(season="2026-2027", tournament_id="t1", root=root)
    assert t1["constraints"]["count"] == 0
    t1_filtered = constraint_inspection(
        season="2026-2027", tournament_id="t1", root=root
    )
    assert t1_filtered["count"] == 0

    t2 = tournament_inspection(season="2026-2027", tournament_id="t2", root=root)
    assert [item["request_id"] for item in t2["constraints"]["constraints"]] == [
        "club-feedback:e:e1"
    ]


def test_tournament_inspection_rejects_unknown_tournament(tmp_path: Path) -> None:
    root = _promote(tmp_path)
    with pytest.raises(SeasonStateError, match="Unknown tournament id"):
        tournament_inspection(season="2026-2027", tournament_id="does-not-exist", root=root)


def test_constraint_inspection_filters_by_team_tournament_and_date(
    tmp_path: Path,
) -> None:
    root = _promote(tmp_path)
    add_request_constraint(
        season="2026-2027",
        type="team_unavailable",
        request_id="club-feedback:a:a1",
        teams=[{"club": "A", "label": "A1", "age_group": "U10"}],
        date_from="2026-09-12",
        date_to="2026-09-12",
        root=root,
    )
    add_request_constraint(
        season="2026-2027",
        type="team_unavailable",
        request_id="club-feedback:b:b1",
        teams=[{"club": "B", "label": "B1", "age_group": "U10"}],
        date_from="2026-09-12",
        date_to="2026-09-12",
        root=root,
    )

    by_team = constraint_inspection(season="2026-2027", team="A1", root=root)
    assert [item["type"] for item in by_team["constraints"]] == ["team_unavailable"]
    assert by_team["count"] == 1

    by_tournament = constraint_inspection(
        season="2026-2027", tournament_id="t1", root=root
    )
    assert {item["request_id"] for item in by_tournament["constraints"]} == {
        "club-feedback:a:a1",
        "club-feedback:b:b1",
    }

    by_date = constraint_inspection(season="2026-2027", date="2026-09-12", root=root)
    assert by_date["count"] == 2

    outside_window = constraint_inspection(
        season="2026-2027", date="2026-10-10", root=root
    )
    assert outside_window["count"] == 0


def test_replacement_candidates_exclude_participants_and_other_age_groups(
    tmp_path: Path,
) -> None:
    root = _promote(tmp_path)

    report = replacement_candidates(
        season="2026-2027", tournament_id="t1", root=root
    )

    labels = {candidate["label"] for candidate in report["candidates"]}
    assert labels == {"B2", "E1", "E2", "H1", "G1"}
    assert "F1" not in labels  # other age group
    assert "D1" not in labels  # already a participant
    # A team already playing on the tournament date is annotated, not silently
    # recommended as a free replacement.
    e1 = next(item for item in report["candidates"] if item["label"] == "E1")
    assert e1["plays_on_tournament_date"] is True
    assert e1["same_date_tournament_ids"] == ["t2"]
    g1 = next(item for item in report["candidates"] if item["label"] == "G1")
    assert g1["plays_on_tournament_date"] is False
    assert report["age_group"] == "U10"
    assert report["validated"] is False
    assert all("verdict" not in candidate for candidate in report["candidates"])


def test_replacement_candidate_discovery_validates_legality(tmp_path: Path) -> None:
    root = _promote(tmp_path)

    report = replacement_candidates(
        season="2026-2027",
        tournament_id="t1",
        replace_team_label="D1",
        root=root,
    )

    assert report["validated"] is True
    assert report["replace_team"] == "D1"
    by_label = {candidate["label"]: candidate for candidate in report["candidates"]}
    assert by_label["G1"]["verdict"]["applicable"] is True
    assert by_label["G1"]["verdict"]["status"] == "safe_to_apply"
    # A same-date participant is a deterministic hard conflict, reported as a
    # domain verdict instead of a green-looking candidate.
    assert by_label["E1"]["verdict"]["applicable"] is False
    assert "same_date_participation" in by_label["E1"]["verdict"]["blockers"]

    legal = replacement_candidates(
        season="2026-2027",
        tournament_id="t1",
        replace_team_label="D1",
        legal_only=True,
        root=root,
    )
    assert [candidate["label"] for candidate in legal["candidates"]] == ["G1"]

    with pytest.raises(SeasonStateError, match="not a participant"):
        replacement_candidates(
            season="2026-2027",
            tournament_id="t1",
            replace_team_label="Not In Tournament",
            root=root,
        )


def test_inspection_to_replacement_is_the_supported_path(tmp_path: Path) -> None:
    root = _promote(tmp_path)

    inspected = tournament_inspection(
        season="2026-2027", tournament_id="t1", root=root
    )
    assert inspected["tournament"]["team_count"] == 4

    # Replacement discovery is a read-only domain operation with its own
    # verdict; the harness never probes the mutation command to find a legal
    # candidate.
    candidates = replacement_candidates(
        season="2026-2027",
        tournament_id="t1",
        replace_team_label="D1",
        legal_only=True,
        root=root,
    )
    legal = candidates["candidates"]
    assert [candidate["label"] for candidate in legal] == ["G1"]
    free = legal[0]

    preview = replace_participant(
        season="2026-2027",
        tournament_id="t1",
        remove_team_label="D1",
        add_team_label=free["label"],
        root=root,
        dry_run=True,
        actor="tester",
    )

    assert preview["dry_run"] is True
    assert preview["verdict"]["applicable"] is True
    assert preview["verdict"]["status"] == "safe_to_apply"
    assert all(preview["verdict"]["checks"].values())
    assert preview["replacement"]["removed_team"]["label"] == "D1"
    assert preview["replacement"]["added_team"]["label"] == free["label"]
    # The dry-run did not mutate canonical state.
    assert load_schedule("2026-2027", root=root)["revision"] == inspected["revision"]


def test_season_inspect_tournament_cli_emits_domain_json(tmp_path: Path, capsys) -> None:
    root = _promote(tmp_path)

    args = build_parser().parse_args(
        [
            "season",
            "inspect",
            "tournament",
            "--season",
            "2026-2027",
            "--tournament-id",
            "t1",
            "--root",
            str(root),
            "--json",
        ]
    )
    rc = _cmd_season(args)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tournament"]["id"] == "t1"
    assert len(payload["tournament"]["teams"]) == 4


def test_season_inspect_candidates_cli_returns_legal_verdicts(
    tmp_path: Path, capsys
) -> None:
    root = _promote(tmp_path)

    args = build_parser().parse_args(
        [
            "season",
            "inspect",
            "candidates",
            "--season",
            "2026-2027",
            "--tournament-id",
            "t1",
            "--replace",
            "D1",
            "--legal-only",
            "--root",
            str(root),
            "--json",
        ]
    )
    rc = _cmd_season(args)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["validated"] is True
    assert [candidate["label"] for candidate in payload["candidates"]] == ["G1"]
    assert payload["candidates"][0]["verdict"]["applicable"] is True
