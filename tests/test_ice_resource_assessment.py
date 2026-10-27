"""Skien-style multi-resource ice evidence in the booking assessment.

Bane 1 and Bane 2 are separately bookable resources of one hockey surface. A
miniputt tournament needs the whole surface, so only a booking covering both
resources can be its own booking. Single-resource bookings are real occupancy,
not competing candidates and not duplicates to collapse.
"""

from __future__ import annotations

from datetime import date, datetime

from tournament_scheduler.booking.calendar_bookings import booking_assessment
from tournament_scheduler.booking.ice_resources import (
    footprint_coverage,
    interval_resource_fields,
    resource_ids_in_text,
    tournament_resource_footprint,
)
from tournament_scheduler.booking.interval_facts import event_fingerprint
from tournament_scheduler.models_calendar import CalendarEvent, SourceResource
from tournament_scheduler.pipeline.scraper_event_helpers import _events_to_dicts
from tournament_scheduler.planning.contract import external_calendar_conflict
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


def test_planning_conflict_uses_same_profile_footprint_as_reconciliation():
    intervals = {
        "Skien": [
            _event("10:00", "12:00", resources=BANE_1, title="Bane 1 occupancy"),
            _event("10:00", "12:00", resources=["bane-3"], title="Other resource"),
        ]
    }

    # One occupied half makes the complete miniputt footprint unavailable.
    assert external_calendar_conflict(intervals, "Skien", date(2026, 9, 12), "10:00", 120)
    # A disjoint source resource does not block either required rink half.
    assert not external_calendar_conflict(
        {"Skien": [intervals["Skien"][1]]},
        "Skien",
        date(2026, 9, 12),
        "10:00",
        120,
    )


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


def test_disjoint_resource_booking_stays_visible_as_non_actionable_context():
    other_hall_resource = ["bane-3"]
    result = _assess([_event("10:00", "12:00", resources=other_hall_resource, title="Skien miniputt")])

    row = _row(result)
    assert row["candidate_count"] == 1
    candidate = row["candidates"][0]
    assert candidate["resource_coverage"] == "disjoint"
    assert candidate["actionable"] is False
    assert "event_occupies_other_resources_only" in candidate["counterevidence"]


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


def test_same_host_full_footprint_event_months_away_stays_a_candidate():
    # A tournament moved from autumn to spring must still find its booking.
    moved = _event("10:00", "12:00", resources=BOTH, title="Skien Ishockeyklubb - U10")
    moved["date"] = "2027-03-20"

    row = _row(_assess([moved]))

    assert row["candidate_count"] == 1
    candidate = row["candidates"][0]
    assert candidate["date_delta_days"] == 189
    assert candidate["actionable"] is True
    assert "event_covers_resource_footprint" in candidate["evidence"]
    assert row["classification"] == "proposed_changed_slot"


def test_far_generic_booking_without_identity_evidence_stays_visible_as_context():
    # No age token in the title and far from the plan: the harness still sees
    # it, but it is context only and never an actionable proposal.
    generic = _event("10:00", "12:00", resources=BOTH, title="Skien Ishockeyklubb")
    generic["date"] = "2027-03-20"

    row = _row(_assess([generic]))

    assert row["candidate_count"] == 1
    candidate = row["candidates"][0]
    assert candidate["season_context_only"] is True
    assert candidate["actionable"] is False
    assert "event_lacks_identity_evidence" in candidate["counterevidence"]
    assert row["classification"] != "proposed_changed_slot"


def test_nearby_generic_full_footprint_bookings_do_not_contest_exact_match():
    # Live Skien shape: one exact-slot full-footprint block plus many other
    # same-week full-footprint blocks with no age token. Only the exact block
    # is a plausible association; the rest stay visible as context.
    exact = _event("10:00", "12:00", resources=BOTH, title="Skien Ishockeyklubb")
    same_day_other_slots = [
        _event("13:00", "15:00", resources=BOTH, title="Skien Ishockeyklubb"),
        _event("16:00", "18:00", resources=BOTH, title="Skien Ishockeyklubb"),
    ]
    same_slot_other_days = []
    for day in ["2026-09-10", "2026-09-14", "2026-09-15"]:
        other_day = _event("10:00", "12:00", resources=BOTH, title="Skien Ishockeyklubb")
        other_day["date"] = day
        same_slot_other_days.append(other_day)
    events = [exact, *same_day_other_slots, *same_slot_other_days]

    row = _row(_assess(events))

    assert row["classification"] == "proposed_unchanged"
    assert row["candidate_count"] == len(events)
    actionable = [c for c in row["candidates"] if c["actionable"]]
    assert [c["start"] for c in actionable] == ["10:00"]
    assert [c["date"] for c in actionable] == ["2026-09-12"]
    context = [c for c in row["candidates"] if c["season_context_only"]]
    assert len(context) == len(events) - 1
    assert all("event_lacks_identity_evidence" in c["counterevidence"] for c in context)


def test_rvv_0050_shaped_exact_minute_source_interval_is_matchable():
    event = CalendarEvent(
        date="15.11.2026",
        name="JU12 11:15-12:55",
        datetime=datetime(2026, 11, 15, 11, 15),
        duration_hours=(12 + 55 / 60) - (11 + 15 / 60),
        resources=(
            SourceResource(id="362", label="Ishockey - bane 1"),
            SourceResource(id="363", label="Ishockey - bane 2"),
        ),
    )
    intervals = _build_club_busy_intervals(
        {"events_by_club": {"Skien": _events_to_dicts([event])}}
    )["Skien"]
    tournament = _tournament()
    tournament.update({"id": "rvv-0050", "date": "2026-11-15", "start_time": "11:15", "age_group": "JU12"})
    problem = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": [],
        "age_groups": ["JU12"],
        "ice_time_minutes": {"JU12": 100},
        "rounds_per_tournament": {"JU12": 5},
        "parallel_games": {"JU12": 2},
        "club_calendar_status": {"Skien": "known"},
        "club_busy_intervals": {"Skien": intervals},
    }

    result = booking_assessment(
        problem=problem,
        plan={"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": [tournament]},
        decisions=None,
        canonical_state_revision="rev-1",
    )

    assert intervals[0]["start"] == "11:15"
    assert intervals[0]["end"] == "12:55"
    row = next(row for row in result["tournaments"] if row["tournament_id"] == "rvv-0050")
    assert row["classification"] == "proposed_unchanged"
    assert row["candidates"][0]["actionable"] is True


def test_near_exact_interval_with_one_minute_end_defect_stays_a_candidate():
    # rvv-0050 shape: the booking ends one minute before the canonical slot.
    # The defect is counterevidence, not a reason to lose the match.
    row = _row(_assess([_event("10:00", "11:59", resources=BOTH, title="Skien Ishockeyklubb")]))

    candidate = row["candidates"][0]
    assert candidate["actionable"] is True
    assert candidate["season_context_only"] is False
    assert "event_interval_within_tolerance_of_canonical" in candidate["evidence"]
    assert "canonical_slot_not_covered" in candidate["counterevidence"]
    assert row["classification"] == "proposed_changed_slot"


def test_interval_outside_tolerance_without_identity_is_context_only():
    # Same date and footprint, but a 2h-shifted slot with no age token is an
    # unrelated booking, not the tournament's changed slot.
    row = _row(_assess([_event("14:00", "16:00", resources=BOTH, title="Skien Ishockeyklubb")]))

    candidate = row["candidates"][0]
    assert candidate["season_context_only"] is True
    assert candidate["actionable"] is False
    assert row["classification"] != "proposed_changed_slot"


def test_generic_overlap_on_booked_resources_contests_without_identity_evidence():
    # #853: a standing block overlapping the booked slot on the same ice is a venue
    # contest, even with no age token and no near-exact interval.
    overlapping = _event("10:30", "11:30", resources=BOTH, title="Skien Ishockeyklubb")
    overlapping["date"] = "2026-09-12"

    row = _row(_assess([overlapping]))

    candidate = row["candidates"][0]
    assert candidate["season_context_only"] is False


def test_generic_overlap_on_disjoint_resources_stays_context():
    # Ice outside the required footprint does not occupy the booked resources.
    elsewhere = _event("10:30", "11:30", resources=["bane-3"], title="Skien Ishockeyklubb")
    elsewhere["date"] = "2026-09-12"

    row = _row(_assess([elsewhere]))

    assert row["candidates"][0]["season_context_only"] is True
