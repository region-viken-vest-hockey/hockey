"""Regression coverage for movable-capacity participant/date repair."""

import json
from copy import deepcopy
from pathlib import Path

from tournament_scheduler.local_repair_options import (
    apply_local_repair_option,
    enumerate_local_repair_options,
)
from tournament_scheduler.movable_capacity_repair import (
    enumerate_movable_capacity_repairs,
)
from tournament_scheduler.season_maintenance import (
    apply_repair,
    list_findings,
    repair_options,
)
from tournament_scheduler.season_state import load_schedule, schedule_fingerprint

SEASON = "2026-2027"


def _promoted_season(
    tmp_path: Path, candidate, problem, *, season_root: str = "season"
) -> tuple[Path, str]:
    """Promote a candidate/problem pair into canonical season state for tests."""

    root = tmp_path / season_root
    season_dir = root / SEASON
    season_dir.mkdir(parents=True, exist_ok=True)
    revision = schedule_fingerprint(candidate)
    schedule = {
        "schema_version": 1,
        "season": SEASON,
        "revision": revision,
        "fingerprint": revision,
        "plan_schema_version": 1,
        "plan": candidate,
        "verification_context": {"problem": problem},
    }
    decisions = {
        "schema_version": 1,
        "season": SEASON,
        "schedule_fingerprint": revision,
        "decisions": {
            tournament["id"]: {
                "status": "pending_review",
                "placement_locked": False,
                "participants_locked": False,
                "approved_fingerprint": None,
            }
            for tournament in candidate["tournaments"]
        },
    }
    (season_dir / "schedule.json").write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (season_dir / "decisions.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root, revision


def _team(club, label, age="U11"):
    return {"club": club, "label": label, "age_group": age}


def _games(teams):
    labels = [team["label"] for team in teams]
    return [
        {"home": labels[0], "away": labels[1], "parallel_slot": 0, "round_number": 1},
        {"home": labels[2], "away": labels[3], "parallel_slot": 1, "round_number": 1},
        {"home": labels[0], "away": labels[2], "parallel_slot": 0, "round_number": 2},
        {"home": labels[1], "away": labels[3], "parallel_slot": 1, "round_number": 2},
        {"home": labels[0], "away": labels[3], "parallel_slot": 0, "round_number": 3},
        {"home": labels[1], "away": labels[2], "parallel_slot": 1, "round_number": 3},
    ]


def _tournament(tid, host, teams, date, arena):
    return {
        "id": tid,
        "date": date,
        "age_group": "U11",
        "host_club": host,
        "arena": arena,
        "start_time": "10:00",
        "duration_minutes": 120,
        "teams": teams,
        "games": _games(teams),
    }


def _problem():
    teams = [
        _team("Kongsberg", "Kongsberg"),
        _team("Jar", "Jar Oransje"),
        _team("Jar", "Jar Hvit"),
        _team("Holmen", "Holmen 1"),
        _team("Holmen", "Holmen 2"),
        _team("Ringerike", "Ringerike"),
        _team("Tønsberg", "Tønsberg Grå"),
        _team("Tønsberg", "Tønsberg Rød"),
    ]
    clubs = {team["club"]: f"{team['club']} Arena" for team in teams}
    clubs["Kongsberg"] = "Kongsberghallen"
    return {
        "teams": teams,
        "parallel_games": {"U11": 2},
        "rounds_per_tournament": {"U11": 3},
        "round_length_minutes": {"U11": 20},
        "ice_time_minutes": {"U11": 120},
        "clubs": clubs,
        "club_calendar_status": {club: "known" for club in clubs},
        "club_busy_intervals": {
            "Kongsberg": [
                {
                    "date": "2026-11-21",
                    "start": "09:00",
                    "end": "14:00",
                    "kind": "club_controlled",
                    "availability": "movable_busy",
                    "classification_source": "configured",
                    "calendar_event": "Åpen ishall",
                    "reason": "host-controlled open ice",
                }
            ]
        },
        "start_date": "2026-10-09",
        "end_date": "2027-03-28",
        "christmas_split_date": "2026-12-24",
        "allow_cross_half_moves": False,
    }


def _candidate():
    manual = _tournament(
        "k-u11",
        "Kongsberg",
        [
            _team("Kongsberg", "Kongsberg"),
            _team("Jar", "Jar Oransje"),
            _team("Holmen", "Holmen 1"),
            _team("Tønsberg", "Tønsberg Grå"),
        ],
        "2026-11-14",
        "Kongsberghallen",
    )
    manual["manual_booking_reason"] = (
        "Ingen verifisert ledig istid for Kongsberg 2026-11-14 — "
        "turneringen må plasseres manuelt."
    )
    occupied = _tournament(
        "other-u11",
        "Ringerike",
        [
            _team("Jar", "Jar Oransje"),
            _team("Holmen", "Holmen 2"),
            _team("Ringerike", "Ringerike"),
            _team("Tønsberg", "Tønsberg Rød"),
        ],
        "2026-11-21",
        "Ringerike Arena",
    )
    return {
        "schema_version": 1,
        "unresolved_tournament_placements": [
            {
                "age_group": "U11",
                "date": "2026-11-14",
                "category": "manual_tournament_placement",
            }
        ],
        "tournaments": [manual, occupied],
    }


def test_kongsberg_movable_weekend_can_reselect_conflicting_participant():
    candidate = _candidate()
    problem = _problem()

    repair_set = enumerate_movable_capacity_repairs(candidate, problem, run_id="r1")

    option = next(
        option
        for option in repair_set["options"]
        if option["action"] == "move_to_movable_capacity_reselect_participants"
        and option["arguments"]["date"] == "2026-11-21"
    )
    assert option["evidence"]["availability"] == "movable_busy"
    assert option["evidence"]["requires_host_confirmation"] is True
    assert option["evidence"]["calendar_event"] == "Åpen ishall"
    assert {team["label"] for team in option["arguments"]["removed_teams"]} == {
        "Jar Oransje"
    }
    assert "Jar Oransje" not in {
        team["label"] for team in option["arguments"]["roster"]
    }


def test_generic_repair_boundary_applies_movable_capacity_option_atomically():
    candidate = _candidate()
    original = deepcopy(candidate)
    problem = _problem()

    repair_set = enumerate_local_repair_options(candidate, problem, run_id="r1")
    option = next(
        option
        for option in repair_set["options"]
        if option["family"] == "movable_capacity"
        and option["arguments"]["date"] == "2026-11-21"
    )

    applied = apply_local_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=repair_set["candidate_fingerprint"],
        run_id="r1",
    )

    assert candidate == original
    assert applied["ok"]
    assert applied["family"] == "movable_capacity"
    assert applied["verification"]["ok"]
    repaired = next(
        tournament
        for tournament in applied["candidate"]["tournaments"]
        if tournament["id"] == "k-u11"
    )
    assert repaired["host_club"] == "Kongsberg"
    assert repaired["arena"] == "Kongsberghallen"
    assert repaired["date"] == "2026-11-21"
    assert repaired["manual_booking_reason"] is None
    assert "Jar Oransje" not in {team["label"] for team in repaired["teams"]}
    assert applied["candidate"]["unresolved_tournament_placements"] == []


def test_promoted_season_exposes_movable_capacity_finding_and_applies_atomically(
    tmp_path: Path,
) -> None:
    """The blocked placement and its movable ice are selectable without Stage 1/2."""

    candidate = _candidate()
    problem = _problem()
    root, revision = _promoted_season(tmp_path, candidate, problem)

    findings = list_findings(SEASON, root=str(root))
    by_code = {finding["code"]: finding for finding in findings["findings"]}

    # A plan-level manual marker is a first-class finding even though the
    # scheduled slot happens to be calendar-free (verification alone cannot
    # rediscover the unconfirmed booking).
    manual = by_code["manual_placement"]
    assert manual["finding_id"] == "manual_placement:k-u11"
    assert manual["category"] == "manual_placement"

    movable = by_code["movable_capacity_opportunity"]
    assert movable["category"] == "movable_capacity"
    assert movable["finding_id"] == "movable_capacity:k-u11"
    assert movable["tournament_id"] == "k-u11"
    assert movable["requires_host_confirmation"] is True
    assert movable["earliest_movable_date"] == "2026-11-21"

    report = repair_options(SEASON, movable["finding_id"], root=str(root))
    option = next(entry for entry in report["options"] if entry["family"] == "movable_capacity")
    assert option["evidence"]["availability"] == "movable_busy"
    assert option["evidence"]["requires_host_confirmation"] is True

    result = apply_repair(
        SEASON,
        option["option_id"],
        findings["revision"],
        root=str(root),
        finding_id=movable["finding_id"],
    )

    assert result["ok"], result
    assert result["revision_before"] == revision
    assert result["revision_after"] != revision
    assert result["delta"]["changed_tournament_count"] == 1
    assert result["delta"]["changed_tournament_ids"] == ["k-u11"]
    updated = load_schedule(SEASON, root=str(root))
    repaired = next(t for t in updated["plan"]["tournaments"] if t["id"] == "k-u11")
    assert repaired["host_club"] == "Kongsberg"
    assert repaired["arena"] == "Kongsberghallen"
    assert repaired["date"] == "2026-11-21"
    assert repaired["manual_booking_reason"] is None
    counts = result["fresh_findings"]["counts_by_code"]
    assert "movable_capacity_opportunity" not in counts
    assert "manual_placement" not in counts


def test_movable_capacity_finding_is_absent_without_trusted_host_calendar(
    tmp_path: Path,
) -> None:
    """An untrusted host calendar yields the manual finding but no ice promise."""

    candidate = _candidate()
    problem = _problem()
    problem["club_calendar_status"]["Kongsberg"] = "unknown"
    root, _revision = _promoted_season(tmp_path, candidate, problem)

    counts = list_findings(SEASON, root=str(root))["counts_by_code"]

    assert counts.get("manual_placement") == 1
    assert "movable_capacity_opportunity" not in counts


def test_stale_movable_capacity_option_is_rejected_without_mutating_state(
    tmp_path: Path,
) -> None:
    candidate = _candidate()
    problem = _problem()
    root, _revision = _promoted_season(tmp_path, candidate, problem)
    findings = list_findings(SEASON, root=str(root))
    movable = next(
        finding
        for finding in findings["findings"]
        if finding["code"] == "movable_capacity_opportunity"
    )
    option = repair_options(SEASON, movable["finding_id"], root=str(root))["options"][0]

    result = apply_repair(
        SEASON,
        option["option_id"],
        "not-the-current-revision",
        root=str(root),
        finding_id=movable["finding_id"],
    )

    assert result["ok"] is False
    assert result["reason"] == "stale_canonical_revision"
    assert result["canonical_revision_unchanged"] is True
    assert load_schedule(SEASON, root=str(root))["revision"] == findings["revision"]
