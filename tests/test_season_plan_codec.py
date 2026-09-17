from datetime import date

import pytest

from tournament_scheduler.models import Game, SeasonPlan, Team, Tournament
from tournament_scheduler.serialization.season_plan import (
    SEASON_PLAN_SCHEMA_VERSION,
    SeasonPlanCodec,
    season_plan_from_dict,
    season_plan_to_dict,
)


def _representative_plan() -> SeasonPlan:
    jar_u10 = Team("Jar", "Jar 1", "U10")
    jar_u10_b = Team("Jar", "Jar 2", "U10")
    frisk_u10 = Team("Frisk Asker", "Frisk Asker 1", "U10")
    tonsberg_u11 = Team("Tønsberg", "Tønsberg 1", "U11")
    kongsberg_u11 = Team("Kongsberg", "Kongsberg 1", "U11")
    return SeasonPlan(
        tournaments=[
            Tournament(
                id="t-u10",
                derived_from=["legacy-u10"],
                date=date(2026, 1, 10),
                arena="Jar Isforum",
                age_group="U10",
                host_club="Jar",
                teams=[jar_u10, jar_u10_b, frisk_u10],
                games=[
                    Game(jar_u10, frisk_u10, parallel_slot=0, round_number=1),
                    Game(jar_u10_b, frisk_u10, parallel_slot=1, round_number=2),
                ],
                start_time="09:15",
                preferanse_vekt=1.25,
                scoring_weight_term=0.5,
                manual_booking_reason="calendar source blocked",
                requires_host_confirmation=True,
                host_confirmation_reason="Åpen ishall — host-controlled open ice",
            ),
            Tournament(
                id="t-u11-cancelled",
                date=date(2026, 2, 7),
                arena="Kongsberghallen",
                age_group="U11",
                host_club="Kongsberg",
                teams=[tonsberg_u11, kongsberg_u11],
                games=[Game(tonsberg_u11, kongsberg_u11, round_number=1)],
                cancelled=True,
                cancellation_reason="arena unavailable",
                start_time="11:00",
            ),
        ],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 3, 31),
        diversity_score=0.7,
        pairwise_matchup_score=0.8,
        month_balance_score=0.9,
        arena_counts={"Jar Isforum": 1, "Kongsberghallen": 1},
        team_game_counts={"Jar 1": 1, "Jar 2": 1, "Frisk Asker 1": 2, "Tønsberg 1": 1},
        game_count_spread=1,
        game_count_spread_by_age_group={"U10": 1, "U11": 0},
        fairness_gate={"status": "warn", "findings": [{"rule": "balance"}]},
        team_last_game_dates={"Jar 1": date(2026, 1, 10)},
        skipped_age_groups=[{"age_group": "U9", "team_count": 2, "reason": "too few teams"}],
        same_date_capacity_evidence=[{"age_group": "U10", "category": "same_date_uniqueness_limit"}],
        manual_adjustments={"locked_dates": ["2026-01-10"], "pinned_tournament_ids": ["t-u10"]},
        arena_day_collisions=[{"date": "2026-01-10", "arena": "Jar Isforum"}],
        date_preference_weights=[{"fra": "2026-02-01", "til": "2026-02-08", "vekt": 2.0}],
        unresolved_hosting_obligations=[{"club": "Jar", "age_group": "U11", "reason": "no slot"}],
        cross_age_hosting_repairs=[{"club": "Jar", "age_group": "U11", "status": "unresolved"}],
        targeted_roster_repairs=[{"tournament_id": "t-u10", "added": ["Jar 2"]}],
        same_age_hosting_repairs=[{"club": "Jar", "age_group": "U10", "status": "repaired"}],
        unresolved_external_conflicts=[{"tournament_id": "t-u10", "reason": "manual follow-up"}],
        unresolved_participation_shortfalls=[{"label": "Jar 1", "actual": "1", "target": "2"}],
        participation_targets_by_age_group={"U10": {"before_christmas": 2, "after_christmas": 2}},
        shared_host_decisions=[{"registration": "Kongsberg/Tønsberg", "age_group": "U11", "chosen_club": "Tønsberg"}],
        operator_waivers=[{"waiver_id": "w1", "rule": "participation_target_exceeded"}],
        operator_waived_violations=[{"rule": "participation_target_exceeded", "waiver_id": "w1"}],
        unresolved_tournament_placements=[
            {
                "age_group": "U10",
                "date": "2026-01-17",
                "participant_teams": [{"club": "Jar", "label": "Jar 1", "age_group": "U10"}],
                "alternate_roster_attempted": True,
            }
        ],
        club_participation_fairness=[{"age_group": "U10", "club": "Jar", "sibling_spread": 1}],
        calendar_interpretations=[
            {
                "club": "Kongsberg",
                "date": "2026-02-07",
                "start": "10:00",
                "end": "12:00",
                "calendar_event": "Ukjent arrangement",
                "reason": "controller-inferred host-controlled interval",
            }
        ],
    )


def test_codec_round_trip_is_stable_for_representative_plan():
    encoded = season_plan_to_dict(_representative_plan())
    restored = season_plan_from_dict(encoded)
    assert season_plan_to_dict(restored) == encoded


def test_explicit_schema_version_is_available_without_changing_default_payload():
    plain = season_plan_to_dict(_representative_plan())
    versioned = season_plan_to_dict(_representative_plan(), include_schema_version=True)

    assert "schema_version" not in plain
    assert versioned["schema_version"] == SEASON_PLAN_SCHEMA_VERSION
    assert {k: v for k, v in versioned.items() if k != "schema_version"} == plain
    assert SeasonPlanCodec.from_dict(versioned).tournaments[0].id == "t-u10"


def test_legacy_unversioned_checkpoint_payload_still_loads():
    payload = {"tournaments": [], "game_count_spread": 0}
    assert season_plan_from_dict(payload).tournaments == []


def test_unknown_schema_version_is_rejected():
    with pytest.raises(ValueError, match="Unsupported SeasonPlan schema_version"):
        season_plan_from_dict({"schema_version": 999, "tournaments": []})
