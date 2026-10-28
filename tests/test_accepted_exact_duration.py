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

import copy
from pathlib import Path

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

    # The accepted interval never blocks the accepted schedule: the whole verdict
    # is clean, and only the feasibility warning remains.
    assert verdict["ok"] is True, verdict["violations"]
    assert verdict["violations"] == []
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


@pytest.mark.parametrize("booked", [115, 100, 180])
def test_accepted_calendar_booking_is_not_demoted_by_its_own_event(tmp_path, booked):
    # Export placement normalization must not treat the tournament's own accepted
    # calendar event as an external conflict. That would demote an accepted booking
    # from the export and publish a schedule that differs from canonical state.
    from tournament_scheduler.placement.normalization import normalize_unplaced_placements

    root, problem, event, teams, result = _accept(tmp_path, planned=155, booked=booked)
    decisions = load_decisions("2026-2027", root=root)
    vp = project_overrides_into_problem(problem, decisions)
    vp[CALENDAR_BOOKING_ASSOCIATIONS_KEY] = decisions.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or []
    plan = copy.deepcopy(load_schedule("2026-2027", root=root)["plan"])

    report = normalize_unplaced_placements(plan, vp, approvals={})

    assert report["removed_tournament_ids"] == []
    assert [t["id"] for t in plan["tournaments"]] == ["rvv-0900"]


@pytest.mark.parametrize(
    ("planned", "booked"),
    [(120, 115), (155, 100), (155, 180)],
)
def test_accepted_booked_duration_is_exported_end_to_end(tmp_path, monkeypatch, planned, booked):
    # The real stage-4 export run, not only the projection layer: the export must
    # not refuse, must place the tournament at its exact booked interval in the
    # iCal artifact, and must leave the canonical override untouched.
    from tournament_scheduler.pipeline.stage4_export import run
    from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
    import tournament_scheduler.season.baseline_overlay as canonical_baseline

    root, problem, event, teams, result = _accept(tmp_path, planned=planned, booked=booked)
    decisions = load_decisions("2026-2027", root=root)
    projected = project_overrides_into_problem(problem, decisions)
    projected[CALENDAR_BOOKING_ASSOCIATIONS_KEY] = decisions.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or []
    plan = load_schedule("2026-2027", root=root)["plan"]

    monkeypatch.setattr(
        canonical_baseline,
        "resolve_canonical_state",
        lambda _config, _start, _end: {"season": "2026-2027", "root": str(root)},
    )
    state = PipelineState(tmp_path / "pipeline")
    state.write_stage(StageName.CONFIG, {"input_path": "fixture", "teams": []}, status=StageStatus.DONE)
    effective_config = {**projected, "canonical_season": "2026-2027", "canonical_season_root": str(root)}

    exported = run(
        {"plan": plan},
        state,
        export_dir=str(tmp_path / "export"),
        timestamped_export=False,
        verification_problem=projected,
        effective_config_override=effective_config,
        canonical_schedule_plan=plan,
    )

    ics = Path(exported["output_files"]["ical"]).read_text()
    dtstart = next(line for line in ics.splitlines() if line.startswith("DTSTART:"))
    dtend = next(line for line in ics.splitlines() if line.startswith("DTEND:"))
    assert dtstart == f"DTSTART:20270306T{START.replace(':', '')}00Z"
    assert dtend == f"DTEND:20270306T{_end(START, booked).replace(':', '')}00Z"
    assert active_overrides(load_decisions("2026-2027", root=root)) == {"rvv-0900": booked}


def test_independent_calendar_conflict_beside_accepted_booking_still_demotes(tmp_path):
    # Own-event exemption must not hide another fixed event on the same ice (#860 review P1).
    from tournament_scheduler.placement.normalization import normalize_unplaced_placements

    root, problem, event, teams, result = _accept(tmp_path, planned=155, booked=100)
    decisions = load_decisions("2026-2027", root=root)
    other = _event(date=DATE, start="18:00", end="18:30", title="Other club event")
    vp = project_overrides_into_problem({**problem, "club_busy_intervals": {"A": [event, other]}}, decisions)
    vp[CALENDAR_BOOKING_ASSOCIATIONS_KEY] = decisions.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or []
    plan = copy.deepcopy(load_schedule("2026-2027", root=root)["plan"])

    report = normalize_unplaced_placements(plan, vp, approvals={})

    assert report["removed_tournament_ids"] == ["rvv-0900"]
    assert report["unplaced"][0]["reason"] == "fixed_external_calendar_conflict"


def test_stale_association_does_not_hide_conflict_after_placement_moves(tmp_path):
    # The accepted booking is 17:20-19:00, but the placement now runs 17:20-19:20.
    # The association no longer matches the placement, so its own event must not
    # be excluded from conflict checks (#860 review P2).
    from tournament_scheduler.placement.normalization import normalize_unplaced_placements

    root, problem, event, teams, result = _accept(tmp_path, planned=155, booked=100)
    decisions = load_decisions("2026-2027", root=root)
    vp = project_overrides_into_problem(problem, decisions)
    vp["ice_time_minutes_overrides"] = {"rvv-0900": 120}
    vp[CALENDAR_BOOKING_ASSOCIATIONS_KEY] = decisions.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or []
    plan = copy.deepcopy(load_schedule("2026-2027", root=root)["plan"])

    report = normalize_unplaced_placements(plan, vp, approvals={})

    assert report["removed_tournament_ids"] == ["rvv-0900"]
    assert report["unplaced"][0]["reason"] == "fixed_external_calendar_conflict"
