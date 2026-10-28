"""Accepted booked duration is exact, even below the planning minimum (#860).

A calendar-confirmed booking is the booked reality. Its duration must be kept
exactly: shorter than the plan, longer than the plan, and below the planner's
governing minimum. The planner's minimum governs new proposals only. A shortfall
is reported as a separate, non-blocking feasibility warning and never rejects,
extends, rounds or re-approves the accepted booking.

Each case is checked through reconciliation, canonical persistence, the repeat
audit, and final verification of the accepted plan.
"""

from __future__ import annotations

import pytest

from tournament_scheduler.application.canonical_season.store_facade import (
    booking_status_report,
    load_decisions,
    load_schedule,
)
from tournament_scheduler.booking.calendar_bookings import CALENDAR_BOOKING_ASSOCIATIONS_KEY
from tournament_scheduler.planning.verification import verify_final_candidate
from tournament_scheduler.season.ice_time_overrides import active_overrides, project_overrides_into_problem
from tests.test_accepted_short_booking import _confirm, _event, _problem, _promote, _teams, _tournament

START = "17:20"
DATE = "2027-03-06"


def _accept(tmp_path, *, planned: int, booked: int):
    teams = _teams("U10", ("A", "B", "C", "D"))
    tournament = _tournament("rvv-0900", age_group="U10", start=START, date_str=DATE, teams=teams)
    root = _promote(tmp_path, [tournament])
    end_minutes = int(START[:2]) * 60 + int(START[3:]) + booked
    end = f"{end_minutes // 60:02d}:{end_minutes % 60:02d}"
    event = _event(date=DATE, start=START, end=end, title="TURNERING U10")
    problem = _problem(age_group="U10", ice=planned, teams=teams, events=[event], parallel_games=2)
    result = _confirm(root, problem, event, "rvv-0900")
    return root, problem, event, teams, result


@pytest.mark.parametrize(
    ("planned", "booked"),
    [
        (120, 115),  # shorter than plan, below the 120-minute governing minimum
        (155, 100),  # well below the planning minimum
        (155, 180),  # longer than plan
    ],
)
def test_accepted_booked_duration_is_exact_through_reconciliation_and_persistence(tmp_path, planned, booked):
    root, problem, event, teams, result = _accept(tmp_path, planned=planned, booked=booked)

    accepted = result["interval_alignment"]["accepted_calendar_interval"]
    assert accepted["duration_minutes"] == booked
    assert accepted["start_time"] == START
    assert result["ice_time_override"]["minutes"] == booked
    # Nothing rejects or rounds the accepted booking: it is the stored truth.
    assert active_overrides(load_decisions("2026-2027", root=root)) == {"rvv-0900": booked}
    placement = next(t for t in load_schedule("2026-2027", root=root)["plan"]["tournaments"] if t["id"] == "rvv-0900")
    assert placement["start_time"] == START


@pytest.mark.parametrize(
    ("planned", "booked"),
    [(120, 115), (155, 100), (155, 180)],
)
def test_accepted_booked_duration_is_stable_across_repeat_audits(tmp_path, planned, booked):
    root, problem, event, teams, result = _accept(tmp_path, planned=planned, booked=booked)

    first = booking_status_report(season="2026-2027", root=root, problem=problem)
    second = booking_status_report(season="2026-2027", root=root, problem=problem)

    row = next(r for r in first["tournaments"] if r["tournament_id"] == "rvv-0900")
    assert row["operational_state"] == "booked"
    assert row["interval_comparison"]["status"] == "matches"
    assert row["interval_comparison"]["planned"]["end"] == _end(START, booked)
    buckets = first["calendar_audit"]["buckets"]
    assert "rvv-0900" in buckets["confirmed_matching"]
    assert first["calendar_audit"]["material_mismatches"] == []
    assert second["calendar_audit"]["buckets"] == buckets
    assert second["tournaments"] == first["tournaments"]


@pytest.mark.parametrize(
    ("planned", "booked", "shortfall"),
    [
        (120, 115, True),
        (155, 100, True),
        (155, 180, False),
    ],
)
def test_accepted_booked_duration_verifies_with_shortfall_as_warning_only(tmp_path, planned, booked, shortfall):
    root, problem, event, teams, result = _accept(tmp_path, planned=planned, booked=booked)

    decisions = load_decisions("2026-2027", root=root)
    verification_problem = project_overrides_into_problem(problem, decisions)
    # The accepted calendar association is the authority for the booked interval.
    verification_problem[CALENDAR_BOOKING_ASSOCIATIONS_KEY] = decisions.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or []
    candidate = load_schedule("2026-2027", root=root)["plan"]

    verdict = verify_final_candidate(candidate, verification_problem)

    # The accepted interval never causes a hard violation on its own.
    assert not [v for v in verdict["violations"] if "ice_time" in v["code"]], verdict["violations"]
    warning_codes = {w["code"] for w in verdict["booking_feasibility_warnings"]}
    assert ("ice_time_governing_minimum" in warning_codes) is shortfall


def _end(start: str, minutes: int) -> str:
    total = int(start[:2]) * 60 + int(start[3:]) + minutes
    return f"{total // 60:02d}:{total % 60:02d}"


@pytest.mark.parametrize(
    ("planned", "booked"),
    [(120, 115), (155, 100), (155, 180)],
)
def test_accepted_booked_duration_reaches_export_projection(tmp_path, planned, booked):
    from tournament_scheduler.publication.projection_guard import tournament_projection
    from tournament_scheduler.season.ice_time_overrides import apply_overrides_to_plan
    from tournament_scheduler.serialization.season_plan import season_plan_from_dict
    from tournament_scheduler.shape.occupancy import tournament_end_time

    root, problem, event, teams, result = _accept(tmp_path, planned=planned, booked=booked)
    decisions = load_decisions("2026-2027", root=root)
    projected = project_overrides_into_problem(problem, decisions)
    schedule = load_schedule("2026-2027", root=root)

    projection = tournament_projection(schedule["plan"], projected)
    assert projection["rvv-0900"]["duration_minutes"] == booked
    assert projection["rvv-0900"]["end_time"] == _end(START, booked)

    plan = season_plan_from_dict(schedule["plan"])
    apply_overrides_to_plan(plan, projected)
    assert tournament_end_time(plan.tournaments[0], {"U10": planned}) == _end(START, booked)
