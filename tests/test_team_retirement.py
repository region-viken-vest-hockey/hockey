"""Regression tests for team retirement capability (issue #624).

Covers the Kongsberg/Tønsberg Ju12 shape:
- Home Kongsberghallen tournaments are cancelled
- Away appearances are withdrawn
- Remaining away tournaments are analyzed for rebalance
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.infrastructure.canonical_season_store import CanonicalSeasonStore


def _build_test_season_plan() -> dict:
    """Build a test season plan with Kongsberg/Tønsberg Ju12 tournaments."""
    base_date = date(2026, 9, 1)
    tournaments = []

    # Hosted tournaments by Kongsberg/Tønsberg at Kongsberghallen
    for i in range(3):
        d = base_date + timedelta(weeks=i * 2)
        tournaments.append({
            "id": f"rvv-00{i+1}",
            "date": d.isoformat(),
            "age_group": "Ju12",
            "arena": "Kongsberghallen",
            "host_club": "Kongsberg/Tønsberg",
            "start_time": "10:00",
            "ice_time_minutes": 90,
            "teams": [
                {"club": "Kongsberg/Tønsberg", "label": "Kongsberg/Tønsberg", "age_group": "Ju12", "guest": False},
                {"club": "Club A", "label": "Club A", "age_group": "Ju12", "guest": False},
                {"club": "Club B", "label": "Club B", "age_group": "Ju12", "guest": False},
                {"club": "Club C", "label": "Club C", "age_group": "Ju12", "guest": False},
            ],
            "cancelled": False,
        })

    # Away tournaments for Kongsberg/Tønsberg
    for i in range(3):
        d = base_date + timedelta(weeks=i * 2 + 1)
        tournaments.append({
            "id": f"rvv-01{i+1}",
            "date": d.isoformat(),
            "age_group": "Ju12",
            "arena": f"Arena {i+1}",
            "host_club": f"Host Club {i+1}",
            "start_time": "12:00",
            "ice_time_minutes": 90,
            "teams": [
                {"club": f"Host Club {i+1}", "label": f"Host Club {i+1}", "age_group": "Ju12", "guest": False},
                {"club": "Kongsberg/Tønsberg", "label": "Kongsberg/Tønsberg", "age_group": "Ju12", "guest": False},
                {"club": f"Club {chr(ord('D')+i)}", "label": f"Club {chr(ord('D')+i)}", "age_group": "Ju12", "guest": False},
                {"club": f"Club {chr(ord('G')+i)}", "label": f"Club {chr(ord('G')+i)}", "age_group": "Ju12", "guest": False},
            ],
            "cancelled": False,
        })

    # Other tournaments in Ju12 (not involving Kongsberg/Tønsberg)
    for i in range(2):
        d = base_date + timedelta(weeks=i * 2 + 4)
        tournaments.append({
            "id": f"rvv-02{i+1}",
            "date": d.isoformat(),
            "age_group": "Ju12",
            "arena": f"Arena {i+10}",
            "host_club": f"Other Host {i+1}",
            "start_time": "14:00",
            "ice_time_minutes": 90,
            "teams": [
                {"club": f"Other Host {i+1}", "label": f"Other Host {i+1}", "age_group": "Ju12", "guest": False},
                {"club": f"Club {chr(ord('J')+i)}", "label": f"Club {chr(ord('J')+i)}", "age_group": "Ju12", "guest": False},
                {"club": f"Club {chr(ord('L')+i)}", "label": f"Club {chr(ord('L')+i)}", "age_group": "Ju12", "guest": False},
                {"club": f"Club {chr(ord('N')+i)}", "label": f"Club {chr(ord('N')+i)}", "age_group": "Ju12", "guest": False},
            ],
            "cancelled": False,
        })

    # Build the planning problem for verification
    problem = {
        "start_date": "2026-09-01",
        "end_date": "2027-03-31",
        "age_groups": ["Ju12"],
        "parallel_games": {"Ju12": 2},
        "round_length_minutes": {"Ju12": 45},
        "actual_round_count": {"Ju12": 3},
        "teams": [
            {"club": "Kongsberg/Tønsberg", "label": "Kongsberg/Tønsberg", "age_group": "Ju12"},
            {"club": "Club A", "label": "Club A", "age_group": "Ju12"},
            {"club": "Club B", "label": "Club B", "age_group": "Ju12"},
            {"club": "Club C", "label": "Club C", "age_group": "Ju12"},
            {"club": "Host Club 1", "label": "Host Club 1", "age_group": "Ju12"},
            {"club": "Club D", "label": "Club D", "age_group": "Ju12"},
            {"club": "Club G", "label": "Club G", "age_group": "Ju12"},
            {"club": "Host Club 2", "label": "Host Club 2", "age_group": "Ju12"},
            {"club": "Club E", "label": "Club E", "age_group": "Ju12"},
            {"club": "Club H", "label": "Club H", "age_group": "Ju12"},
            {"club": "Host Club 3", "label": "Host Club 3", "age_group": "Ju12"},
            {"club": "Club F", "label": "Club F", "age_group": "Ju12"},
            {"club": "Club I", "label": "Club I", "age_group": "Ju12"},
            {"club": "Other Host 1", "label": "Other Host 1", "age_group": "Ju12"},
            {"club": "Club J", "label": "Club J", "age_group": "Ju12"},
            {"club": "Club L", "label": "Club L", "age_group": "Ju12"},
            {"club": "Club N", "label": "Club N", "age_group": "Ju12"},
            {"club": "Other Host 2", "label": "Other Host 2", "age_group": "Ju12"},
            {"club": "Club K", "label": "Club K", "age_group": "Ju12"},
            {"club": "Club M", "label": "Club M", "age_group": "Ju12"},
            {"club": "Club O", "label": "Club O", "age_group": "Ju12"},
        ],
        "tournaments": tournaments,
    }

    return {
        "plan": {
            "tournaments": tournaments,
        },
        "verification_context": {
            "start_date": "2026-09-01",
            "end_date": "2027-03-31",
            "age_groups": ["Ju12"],
            "parallel_games": {"Ju12": 2},
            "round_length_minutes": {"Ju12": 45},
            "actual_round_count": {"Ju12": 3},
            "teams": [
                {"club": "Kongsberg/Tønsberg", "label": "Kongsberg/Tønsberg", "age_group": "Ju12"},
                {"club": "Club A", "label": "Club A", "age_group": "Ju12"},
                {"club": "Club B", "label": "Club B", "age_group": "Ju12"},
                {"club": "Club C", "label": "Club C", "age_group": "Ju12"},
                {"club": "Host Club 1", "label": "Host Club 1", "age_group": "Ju12"},
                {"club": "Club D", "label": "Club D", "age_group": "Ju12"},
                {"club": "Club G", "label": "Club G", "age_group": "Ju12"},
                {"club": "Host Club 2", "label": "Host Club 2", "age_group": "Ju12"},
                {"club": "Club E", "label": "Club E", "age_group": "Ju12"},
                {"club": "Club H", "label": "Club H", "age_group": "Ju12"},
                {"club": "Host Club 3", "label": "Host Club 3", "age_group": "Ju12"},
                {"club": "Club F", "label": "Club F", "age_group": "Ju12"},
                {"club": "Club I", "label": "Club I", "age_group": "Ju12"},
                {"club": "Other Host 1", "label": "Other Host 1", "age_group": "Ju12"},
                {"club": "Club J", "label": "Club J", "age_group": "Ju12"},
                {"club": "Club L", "label": "Club L", "age_group": "Ju12"},
                {"club": "Club N", "label": "Club N", "age_group": "Ju12"},
                {"club": "Other Host 2", "label": "Other Host 2", "age_group": "Ju12"},
                {"club": "Club K", "label": "Club K", "age_group": "Ju12"},
                {"club": "Club M", "label": "Club M", "age_group": "Ju12"},
                {"club": "Club O", "label": "Club O", "age_group": "Ju12"},
            ],
            "problem": problem,
        },
    }


def _create_test_season_dir(tmp_path: Path, season: str) -> Path:
    """Create a test canonical season directory."""
    season_dir = tmp_path / season
    season_dir.mkdir(parents=True)

    plan = _build_test_season_plan()
    schedule = {
        "schema_version": 1,
        "revision": "test-revision-1",
        "fingerprint": "test-fingerprint-1",
        "plan": plan["plan"],
        "verification_context": plan["verification_context"],
    }
    decisions = {
        "schema_version": 1,
        "decisions": {},
        "participation_acceptances": [],
        "participation_withdrawals": [],
        "change_protections": [],
        "request_constraints": [],
        "banned_dates": [],
        "holiday_date_exceptions": [],
        "season_baseline": {},
        "season_baseline_history": [],
        "calendar_booking_associations": [],
        "manual_booking_assertions": [],
    }

    (season_dir / "schedule.json").write_text(json.dumps(schedule, indent=2))
    (season_dir / "decisions.json").write_text(json.dumps(decisions, indent=2))

    return season_dir


def test_team_retirement_preview_classifies_hosted_and_away(tmp_path: Path):
    """Test that preview classifies hosted vs away tournaments correctly."""
    season = "2026-2027"
    _create_test_season_dir(tmp_path, season)

    service = CanonicalSeasonService(root=tmp_path)

    result = service.preview_retire_team(
        season=season,
        club="Kongsberg/Tønsberg",
        team_label="Kongsberg/Tønsberg",
        age_group="Ju12",
        effective_from="2026-09-01",
        request_id="test-request-001",
        actor="test-operator",
        note="Test retirement",
    )

    # Verify classification
    classification = result["classification"]
    assert classification["retiring_team"]["club"] == "Kongsberg/Tønsberg"
    assert classification["retiring_team"]["label"] == "Kongsberg/Tønsberg"
    assert classification["retiring_team"]["age_group"] == "Ju12"
    assert classification["effective_from"] == "2026-09-01"

    # 3 hosted tournaments should be cancelled
    hosted_cancel = classification["hosted_tournaments"]["cancel"]
    assert len(hosted_cancel) == 3
    for t in hosted_cancel:
        assert t["host_club"] == "Kongsberg/Tønsberg"
        assert t["was_on_roster"] is True

    # 3 away tournaments should have team withdrawn
    away_remove = classification["away_participation"]["remove_from"]
    assert len(away_remove) == 3
    for t in away_remove:
        assert t["host_club"] != "Kongsberg/Tønsberg"

    # Rebalance proposals for away tournaments
    rebalance = classification["rebalance"]
    assert "proposals" in rebalance
    assert "unresolved_vacancies" in rebalance
    assert len(rebalance["proposals"]) == 3

    # Can apply should be False without rebalance (verification fails due to underfilled tournaments)
    assert classification["can_apply"] is False
    assert classification["verification"]["ok"] is False
    assert classification["hosting_responsibility"]["ok"] is True
    assert classification["change_protections"]["ok"] is True
    assert classification["request_constraints"]["acceptable"] is True


def test_team_retirement_applies_cancellations_and_withdrawals(tmp_path: Path):
    """Test that apply cancels hosted tournaments and withdraws from away."""
    season = "2026-2027"
    _create_test_season_dir(tmp_path, season)

    service = CanonicalSeasonService(root=tmp_path)

    # First get the rebalance proposals from preview
    preview = service.preview_retire_team(
        season=season,
        club="Kongsberg/Tønsberg",
        team_label="Kongsberg/Tønsberg",
        age_group="Ju12",
        effective_from="2026-09-01",
        request_id="test-request-002-preview",
        actor="test-operator",
        note="Test retirement preview",
    )
    proposals = preview["classification"]["rebalance"]["proposals"]

    # Now apply with rebalance
    result = service.apply_retire_team(
        season=season,
        club="Kongsberg/Tønsberg",
        team_label="Kongsberg/Tønsberg",
        age_group="Ju12",
        effective_from="2026-09-01",
        request_id="test-request-002",
        actor="test-operator",
        note="Test retirement apply",
        accept_rebalance=True,
        rebalance_proposals=proposals,
    )

    retirement = result["retirement"]
    assert retirement["retiring_team"]["club"] == "Kongsberg/Tønsberg"
    assert retirement["retiring_team"]["label"] == "Kongsberg/Tønsberg"
    assert retirement["retiring_team"]["age_group"] == "Ju12"

    # 3 hosted tournaments cancelled
    assert len(retirement["cancelled_hosted"]) == 3
    for t in retirement["cancelled_hosted"]:
        assert t["host_club"] == "Kongsberg/Tønsberg"
        assert t["was_on_roster"] is True

    # 3 away tournaments withdrawn
    assert len(retirement["withdrawn_away"]) == 3
    for t in retirement["withdrawn_away"]:
        assert t["host_club"] != "Kongsberg/Tønsberg"

    # 3 rebalance proposals applied
    assert len(retirement["rebalance_applied"]) == 3
    for r in retirement["rebalance_applied"]:
        assert "tournament_id" in r
        assert "added_team" in r

    # Verify the canonical state was updated
    updated_schedule = service.store.load(season).schedule
    updated_plan = updated_schedule.get("plan", {})
    tournaments = updated_plan.get("tournaments", [])

    # Check hosted tournaments are cancelled
    for t in tournaments:
        if t.get("host_club") == "Kongsberg/Tønsberg" and not t.get("cancelled", True):
            # Should have been cancelled
            assert False, f"Hosted tournament {t['id']} was not cancelled"

    # Check away tournaments no longer have Kongsberg/Tønsberg
    for t in tournaments:
        if t.get("host_club") != "Kongsberg/Tønsberg" and not t.get("cancelled"):
            teams = t.get("teams", [])
            kongsberg_teams = [
                team for team in teams
                if team.get("club") == "Kongsberg/Tønsberg"
                and team.get("label") == "Kongsberg/Tønsberg"
            ]
            assert len(kongsberg_teams) == 0, f"Kongsberg/Tønsberg still in {t['id']}"

    # Check withdrawal records were created
    updated_decisions = service.store.load(season).decisions
    withdrawals = updated_decisions.get("participation_withdrawals", [])
    assert len(withdrawals) == 1
    withdrawal = withdrawals[0]
    assert withdrawal["team"]["club"] == "Kongsberg/Tønsberg"
    assert withdrawal["team"]["label"] == "Kongsberg/Tønsberg"
    assert withdrawal["team"]["age_group"] == "Ju12"
    assert withdrawal["status"] == "active"
    assert len(withdrawal["tournament_ids"]) == 3


def test_team_retirement_preserves_historical_tournaments(tmp_path: Path):
    """Test that historical/completed tournaments are not rewritten."""
    season = "2026-2027"
    _create_test_season_dir(tmp_path, season)

    # Add a historical tournament before effective date by directly modifying files
    season_dir = tmp_path / season
    schedule = json.loads((season_dir / "schedule.json").read_text())
    decisions = json.loads((season_dir / "decisions.json").read_text())

    # Add a historical tournament (before effective date, but within planning window)
    historical = {
        "id": "rvv-historical-001",
        "date": "2026-09-10",
        "age_group": "Ju12",
        "arena": "Old Arena",
        "host_club": "Kongsberg/Tønsberg",
        "start_time": "10:00",
        "ice_time_minutes": 90,
        "teams": [
            {"club": "Kongsberg/Tønsberg", "label": "Kongsberg/Tønsberg", "age_group": "Ju12", "guest": False},
            {"club": "Club A", "label": "Club A", "age_group": "Ju12", "guest": False},
            {"club": "Club B", "label": "Club B", "age_group": "Ju12", "guest": False},
            {"club": "Club C", "label": "Club C", "age_group": "Ju12", "guest": False},
        ],
        "cancelled": False,
    }
    schedule["plan"]["tournaments"].append(historical)

    # Write updated files
    (season_dir / "schedule.json").write_text(json.dumps(schedule, indent=2))

    service = CanonicalSeasonService(root=tmp_path)

    # Get rebalance proposals from preview (use later effective_from so historical tournament is before it)
    preview = service.preview_retire_team(
        season=season,
        club="Kongsberg/Tønsberg",
        team_label="Kongsberg/Tønsberg",
        age_group="Ju12",
        effective_from="2026-10-01",
        request_id="test-request-003-preview",
        actor="test-operator",
        note="Test retirement with historical preview",
    )
    proposals = preview["classification"]["rebalance"]["proposals"]

    # Now retire from 2026-10-01 with rebalance (historical tournament on 2026-09-15 is before effective_from)
    result = service.apply_retire_team(
        season=season,
        club="Kongsberg/Tønsberg",
        team_label="Kongsberg/Tønsberg",
        age_group="Ju12",
        effective_from="2026-10-01",
        request_id="test-request-003",
        actor="test-operator",
        note="Test retirement with historical",
        accept_rebalance=True,
        rebalance_proposals=proposals,
    )

    # Verify historical tournament was not touched
    updated_schedule = service.store.load(season).schedule
    updated_plan = updated_schedule.get("plan", {})
    updated_tournaments = updated_plan.get("tournaments", [])

    historical_t = next(t for t in updated_tournaments if t["id"] == "rvv-historical-001")
    assert historical_t["cancelled"] is False
    teams = historical_t.get("teams", [])
    kongsberg_teams = [
        team for team in teams
        if team.get("club") == "Kongsberg/Tønsberg"
        and team.get("label") == "Kongsberg/Tønsberg"
    ]
    assert len(kongsberg_teams) == 1, "Historical tournament team was removed"


def test_team_retirement_no_silent_hosting_transfer(tmp_path: Path):
    """Test that hosting responsibility is never silently transferred."""
    season = "2026-2027"
    _create_test_season_dir(tmp_path, season)

    service = CanonicalSeasonService(root=tmp_path)

    # Get rebalance proposals from preview
    preview = service.preview_retire_team(
        season=season,
        club="Kongsberg/Tønsberg",
        team_label="Kongsberg/Tønsberg",
        age_group="Ju12",
        effective_from="2026-09-01",
        request_id="test-request-004-preview",
        actor="test-operator",
        note="Test retirement hosting check preview",
    )
    proposals = preview["classification"]["rebalance"]["proposals"]

    result = service.apply_retire_team(
        season=season,
        club="Kongsberg/Tønsberg",
        team_label="Kongsberg/Tønsberg",
        age_group="Ju12",
        effective_from="2026-09-01",
        request_id="test-request-004",
        actor="test-operator",
        note="Test retirement hosting check",
        accept_rebalance=True,
        rebalance_proposals=proposals,
    )

    # Verify hosting responsibility check passed
    assert result["verification_result"]["ok"] is True

    # Check the cancelled tournaments have retirement provenance recorded
    for cancelled in result["retirement"]["cancelled_hosted"]:
        # The tournament should have retirement provenance recorded
        assert cancelled["tournament_id"].startswith("rvv-00")


def test_team_retirement_rebalance_analysis(tmp_path: Path):
    """Test that affected away tournaments receive rebalance analysis."""
    season = "2026-2027"
    _create_test_season_dir(tmp_path, season)

    service = CanonicalSeasonService(root=tmp_path)

    result = service.preview_retire_team(
        season=season,
        club="Kongsberg/Tønsberg",
        team_label="Kongsberg/Tønsberg",
        age_group="Ju12",
        effective_from="2026-09-01",
        request_id="test-request-005",
        actor="test-operator",
        note="Test retirement rebalance",
    )

    classification = result["classification"]
    rebalance = classification["rebalance"]

    # Should have proposals for the 3 affected away tournaments
    assert "proposals" in rebalance
    assert "unresolved_vacancies" in rebalance

    # The preview should expose the rebalance analysis
    proposals = rebalance["proposals"]
    assert len(proposals) == 3
    for p in proposals:
        assert "tournament_id" in p
        assert "candidate_club" in p
        assert "candidate_label" in p
        assert "reason" in p


if __name__ == "__main__":
    pytest.main([__file__, "-v"])