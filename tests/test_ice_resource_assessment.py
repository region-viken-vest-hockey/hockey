"""Skien-style multi-resource ice evidence in the booking assessment.

Bane 1 and Bane 2 are separately bookable resources of one hockey surface. A
miniputt tournament needs the whole surface, so only a booking covering both
resources can be its own booking. Single-resource bookings are real occupancy,
not competing candidates and not duplicates to collapse.
"""

from __future__ import annotations

from tournament_scheduler.booking.calendar_bookings import booking_assessment
from tournament_scheduler.booking.ice_resources import (
    footprint_coverage,
    interval_resource_fields,
    resource_ids_in_text,
    tournament_resource_footprint,
)
from tournament_scheduler.booking.interval_facts import event_fingerprint
from tournament_scheduler.models_calendar import SourceResource
from tournament_scheduler.pipeline.stage3_helpers import _build_club_busy_intervals

BOTH = ["bane-1", "bane-2"]
BANE_1 = ["bane-1"]
BANE_2 = ["bane-2"]


def _tournament():
    teams = [{"club": "Skien", "label": f"Skien{i}", "age_group": "U10"} for i in range(1, 5)]
    games = [
        {"home": "Skien1", "away": "Skien2", "parallel_slot": 0, "round_number": 1},
        {"home": "Skien3", "away": "Skien4", "parallel_slot": 0, "round_number": 1},
    ]
    return {
        "id": "skien-u10",
        "date": "2026-09-12",
        "arena": "Skien ishall",
        "age_group": "U10",
        "host_club": "Skien",
        "teams": teams,
        "games": games,
        "start_time": "10:00",
    }


def _event(start, end, *, resources=None, title="Skien Ishockeyklubb"):
    event = {
        "date": "2026-09-12",
        "start": start,
        "end": end,
        "availability": "fixed_busy",
        "calendar_event": title,
        "club": "Skien",
    }
    if resources is not None:
        event["resources"] = resources
    return event


def _assess(events):
    problem = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": [],
        "age_groups": ["U10"],
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 2},
        "parallel_games": {"U10": 2},
        "club_calendar_status": {"Skien": "known"},
        "club_busy_intervals": {"Skien": events},
    }
    plan = {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": [_tournament()]}
    return booking_assessment(
        problem=problem,
        plan=plan,
        decisions=None,
        canonical_state_revision="rev-1",
    )


def _row(result):
    return next(row for row in result["tournaments"] if row["tournament_id"] == "skien-u10")


def test_location_labels_normalize_to_stable_resource_ids():
    assert resource_ids_in_text("Ishockey - bane 1") == {"bane-1"}
    assert resource_ids_in_text("Ishockey - bane 1 + Ishockey - bane 2") == {"bane-1", "bane-2"}
    assert resource_ids_in_text("Askerhallen") == frozenset()


def test_only_footprint_clubs_store_resource_fields():
    bane_2 = SourceResource(id="363", label="Ishockey - bane 2")
    assert interval_resource_fields("Skien", [bane_2]) == {"resources": ["bane-2"]}
    assert interval_resource_fields("Frisk Asker", [SourceResource(id="1", label="Askerhallen - bane 1")]) == {}


def test_stage3_busy_intervals_carry_resource_ids_for_skien_only():
    scraping_result = {
        "events_by_club": {
            "Skien": [
                {
                    "date": "12.09.2026",
                    "name": "Hockeyskole",
                    "datetime": "2026-09-12T10:00:00",
                    "duration_hours": 2.0,
                    "location": "Ishockey - bane 1 + Ishockey - bane 2",
                    "resources": [
                        {"id": "362", "label": "Ishockey - bane 1"},
                        {"id": "363", "label": "Ishockey - bane 2"},
                    ],
                    "order_id": "1192286",
                }
            ]
        }
    }

    intervals = _build_club_busy_intervals(scraping_result)["Skien"]

    assert intervals[0]["resources"] == ["bane-1", "bane-2"]
    assert intervals[0]["start"] == "10:00"


def test_skien_tournament_requires_both_resources_from_registry():
    assert tournament_resource_footprint("Skien") == frozenset({"bane-1", "bane-2"})
    assert tournament_resource_footprint("Jar") == frozenset()


def test_footprint_coverage_distinguishes_covers_partial_and_disjoint():
    required = frozenset({"bane-1", "bane-2"})
    assert footprint_coverage(required, frozenset({"bane-1", "bane-2"})) == "covers"
    assert footprint_coverage(required, frozenset({"bane-1"})) == "partial"
    assert footprint_coverage(required, frozenset({"bane-3"})) == "disjoint"
    assert footprint_coverage(required, frozenset()) is None


def test_single_resource_bookings_on_both_banes_are_not_competing():
    result = _assess(
        [
            _event("10:00", "12:00", resources=BANE_1, title="Hockeyskole"),
            _event("10:00", "12:00", resources=BANE_2, title="U8/U9 (JU8)"),
        ]
    )

    row = _row(result)
    assert row["classification"] == "ambiguous"
    assert {c["resource_coverage"] for c in row["candidates"]} == {"partial"}
    assert all(c["actionable"] is False for c in row["candidates"])


def test_partial_resource_overlap_blocks_presumed_unscheduled():
    result = _assess([_event("10:00", "12:00", resources=BANE_1)])

    assert _row(result)["classification"] == "ambiguous"


def test_full_footprint_booking_is_the_proposed_match_despite_partial_neighbours():
    result = _assess(
        [
            _event("10:00", "12:00", resources=BOTH, title="Skien miniputt"),
            _event("10:00", "12:00", resources=BANE_1, title="Hockeyskole"),
        ]
    )

    row = _row(result)
    assert row["classification"] == "proposed_unchanged"
    actionable = [c for c in row["candidates"] if c["actionable"]]
    assert len(actionable) == 1
    assert "event_covers_resource_footprint" in actionable[0]["evidence"]


def test_same_title_on_different_resources_keeps_distinct_fingerprints():
    on_bane_1 = _event("10:00", "12:00", resources=BANE_1, title="Hockeyskole")
    on_bane_2 = _event("10:00", "12:00", resources=BANE_2, title="Hockeyskole")

    assert event_fingerprint(on_bane_1) != event_fingerprint(on_bane_2)


def test_events_without_resource_detail_keep_resource_agnostic_behaviour():
    result = _assess([_event("10:00", "12:00", title="Skien miniputt")])

    row = _row(result)
    assert row["classification"] == "proposed_unchanged"
    assert row["candidates"][0]["resource_coverage"] is None


def test_junior_age_group_does_not_match_plain_age_group():
    from tournament_scheduler.booking.assessment_candidates import _assessment_age_tokens

    assert _assessment_age_tokens("Skien Ishockeyklubb - JU10 11:15-13:35") == {"JU10"}
    assert _assessment_age_tokens("Skien Ishockeyklubb - U10") == {"U10"}


def test_event_with_other_age_group_is_not_a_candidate_for_junior_tournament():
    tournament = _tournament()
    tournament["age_group"] = "JU10"
    events = [_event("10:00", "12:00", resources=BOTH, title="Skien Ishockeyklubb - U10 10:00-12:00")]
    problem = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": [],
        "age_groups": ["JU10"],
        "ice_time_minutes": {"JU10": 120},
        "rounds_per_tournament": {"JU10": 2},
        "parallel_games": {"JU10": 2},
        "club_calendar_status": {"Skien": "known"},
        "club_busy_intervals": {"Skien": events},
    }
    result = booking_assessment(
        problem=problem,
        plan={"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": [tournament]},
        decisions=None,
        canonical_state_revision="rev-1",
    )

    row = _row(result)
    assert row["candidates"][0]["actionable"] is False
    assert "event_title_age_group_differs" in row["candidates"][0]["counterevidence"]
