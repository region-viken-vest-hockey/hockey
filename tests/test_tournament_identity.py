from datetime import date

from tournament_scheduler.models import SeasonPlan, Team, Tournament
from tournament_scheduler.serialization.season_plan import season_plan_to_dict
from tournament_scheduler.pipeline.state import PipelineState
from tournament_scheduler.pipeline.tournament_updater import TournamentUpdater
from tournament_scheduler.planning_contract import verify_candidate
from tournament_scheduler.tournament_identity import allocate_tournament_id


def _team(label: str, club: str = "Club") -> Team:
    return Team(club=club, label=label, age_group="U10")


def test_existing_tournament_retains_id_after_date_host_and_participant_edit(tmp_path) -> None:
    teams = [_team("A", "A"), _team("B", "B"), _team("C", "C")]
    tournament = Tournament(
        id="stable-1",
        date=date(2026, 1, 10),
        arena="Old arena",
        age_group="U10",
        host_club="A",
        teams=list(teams),
    )
    plan = SeasonPlan(tournaments=[tournament])
    updater = TournamentUpdater(PipelineState(tmp_path))

    updater.move_date("stable-1", date(2026, 1, 17), plan, force=True)
    updater.set_host_club("stable-1", "B", plan)
    updater.drop_team("stable-1", "C", plan)

    assert plan.tournaments[0].id == "stable-1"


def test_new_tournament_receives_unique_durable_id(tmp_path) -> None:
    teams = [_team("A", "A"), _team("B", "B"), _team("C", "C")]
    plan = SeasonPlan(
        tournaments=[
            Tournament(
                id="rvv-0001",
                date=date(2026, 1, 10),
                arena="Arena",
                age_group="U10",
                teams=teams,
            )
        ]
    )
    updater = TournamentUpdater(PipelineState(tmp_path))

    result = updater.add_tournament(
        plan,
        age_group="U10",
        team_labels=["A", "B", "C"],
        tournament_date=date(2026, 1, 17),
        arena="Arena",
        force=True,
    )

    assert result.success
    assert result.tournament_id == "rvv-0002"
    assert {t.id for t in plan.tournaments} == {"rvv-0001", "rvv-0002"}


def test_duplicate_missing_and_unknown_identity_refs_are_rejected() -> None:
    candidate = {
        "tournaments": [
            {"id": "dup", "date": "2026-01-10", "arena": "A", "age_group": "U10", "teams": []},
            {"id": "dup", "date": "2026-01-17", "arena": "A", "age_group": "U10", "teams": []},
            {"id": "", "date": "2026-01-24", "arena": "A", "age_group": "U10", "teams": []},
        ],
        "manual_adjustments": {"pinned_tournament_ids": ["missing"]},
        "operator_waivers": [{"scope": {"tournament_id": "also-missing"}}],
    }

    result = verify_candidate(candidate)
    codes = {violation["code"] for violation in result["violations"]}

    assert "duplicate_tournament_id" in codes
    assert "missing_tournament_id" in codes
    assert "unknown_pinned_tournament_id" in codes
    assert "unknown_waiver_tournament_id" in codes


def test_lineage_round_trips_and_is_validated_against_identity_registry() -> None:
    plan = SeasonPlan(
        tournaments=[
            Tournament(
                id="rvv-0002",
                derived_from=["rvv-0001"],
                date=date(2026, 1, 17),
                arena="Arena",
                age_group="U10",
                teams=[],
            )
        ]
    )

    candidate = season_plan_to_dict(plan)

    assert candidate["tournaments"][0]["derived_from"] == ["rvv-0001"]
    assert "rvv-0001" in candidate["identity_registry"]["known_tournament_ids"]
    assert not [v for v in verify_candidate(candidate)["violations"] if v["code"].startswith("unknown_")]


def test_allocator_is_deterministic_and_independent_of_existing_id_order() -> None:
    assert allocate_tournament_id(["rvv-0002", "rvv-0001"]) == "rvv-0003"
    assert allocate_tournament_id(["rvv-0001", "rvv-0002"]) == "rvv-0003"
