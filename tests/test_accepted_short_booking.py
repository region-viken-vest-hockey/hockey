"""Accepted authoritative short bookings replace the planned interval.

Regression coverage for the Jutul reconciliation defect: once an operator
accepts a scraped calendar event as the actual booking, the canonical
date/start/end/duration must become the exact event interval. The previous
planned interval is retained only as typed history/provenance, and a short
booking stays booked with the planned-round/governing shortfall surfaced as a
durable (non-blocking) feasibility finding. Recording the accepted interval is
not an approval of its format: even an operationally unusable interval is
recorded exactly and its playing-time concern remains a separate finding.

Hermetic fixtures (``rvv-0116`` U9 60 min, ``rvv-0173`` U12 80 min, a
representative 15-minute start offset and a 10-minute sub-round interval); the
live season is never mutated.
"""

from __future__ import annotations

from tournament_scheduler.canonical_exception_policy import (
    reclassify_accepted_exceptions,
)
from tournament_scheduler.calendar_bookings import (
    REJECTED_BOOKING_EVIDENCE_KEY,
    event_fingerprint,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.season_state import (
    booking_status_report,
    calendar_booking_assessment,
    confirm_calendar_booking,
    load_decisions,
    load_schedule,
    promote_from_stage3,
)
from tournament_scheduler.testing.reviewed_export import write_reviewed_stage4_export


def _round_robin_games(labels):
    """Return a circle-method round robin (``len(labels) - 1`` rounds)."""

    rotation = list(labels)
    games: list[dict[str, object]] = []
    team_count = len(rotation)
    for round_index in range(team_count - 1):
        for pairing in range(team_count // 2):
            home = rotation[pairing]
            away = rotation[team_count - 1 - pairing]
            games.append(
                {
                    "home": home,
                    "away": away,
                    "parallel_slot": 0,
                    "round_number": round_index + 1,
                }
            )
        rotation = [rotation[0], rotation[-1], *rotation[1:-1]]
    return games


def _teams(age_group, club_letters=("A", "B", "C", "D", "E", "F")):
    return [
        {"club": club, "label": f"{club}1", "age_group": age_group}
        for club in club_letters
    ]


def _tournament(t_id, *, age_group, start, date_str="2026-11-21", host="A", teams):
    labels = [team["label"] for team in teams]
    return {
        "id": t_id,
        "date": date_str,
        "arena": "Baerum ishall",
        "age_group": age_group,
        "host_club": host,
        "teams": teams,
        "games": _round_robin_games(labels),
        "start_time": start,
    }


def _plan(tournaments):
    return {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": tournaments,
    }


def _promote(tmp_path, tournaments):
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    state.write_stage(StageName.PLANNING, {"plan": _plan(tournaments)}, status=StageStatus.DONE)
    write_reviewed_stage4_export(state)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    return root


def _problem(*, age_group, ice, teams, events, parallel_games):
    return {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": teams,
        "age_groups": [age_group],
        "ice_time_minutes": {age_group: ice},
        "round_length_minutes": {age_group: 15},
        "rounds_per_tournament": {age_group: 5},
        "parallel_games": {age_group: parallel_games},
        "club_calendar_status": {"A": "known"},
        "club_busy_intervals": {"A": events},
    }


def _event(*, date, start, end, title):
    return {
        "date": date,
        "start": start,
        "end": end,
        "kind": "external",
        "availability": "fixed_busy",
        "calendar_event": title,
    }


def _confirm(root, problem, event, tournament_id, note="accepted host calendar booking"):
    return confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fingerprint({**event, "club": "A"}),
        tournament_id=tournament_id,
        actor="operator",
        note=note,
        problem=problem,
    )


def _u9_setup(tmp_path):
    teams = _teams("U9")
    tournament = _tournament("rvv-0116", age_group="U9", start="17:20", date_str="2026-11-21", teams=teams)
    root = _promote(tmp_path, [tournament])
    event = _event(date="2026-11-21", start="17:30", end="18:30", title="TURNERING U9")
    problem = _problem(age_group="U9", ice=155, teams=teams, events=[event], parallel_games=3)
    return root, problem, event


def test_accepted_shorter_u9_booking_replaces_planned_interval(tmp_path):
    """Jutul rvv-0116: 60-minute event supersedes the 155-minute planned window."""

    root, problem, event = _u9_setup(tmp_path)

    result = _confirm(root, problem, event, "rvv-0116")

    assert result["interval_alignment"]["previous_interval"]["duration_minutes"] == "155"
    assert result["interval_alignment"]["previous_interval"]["start_time"] == "17:20"
    assert result["interval_alignment"]["accepted_calendar_interval"] == {
        "date": "2026-11-21",
        "start_time": "17:30",
        "duration_minutes": 60,
        "end_time": "18:30",
    }
    assert result["ice_time_override"]["minutes"] == 60
    # The planned-round and governing shortfalls are separate durable findings.
    assert {warning["code"] for warning in result["booking_feasibility_warnings"]} == {
        "ice_time_governing_minimum",
        "ice_time_playing_minimum",
    }

    schedule = load_schedule("2026-2027", root=root)["plan"]
    placement = next(t for t in schedule["tournaments"] if t["id"] == "rvv-0116")
    assert placement["start_time"] == "17:30"
    decisions = load_decisions("2026-2027", root=root)
    assert decisions["ice_time_minutes_overrides"][-1]["minutes"] == 60

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = next(r for r in report["tournaments"] if r["tournament_id"] == "rvv-0116")
    assert row["status"] == "confirmed_booked"
    assert row["operational_state"] == "booked"

    # The accepted event is the current canonical interval: the superseded
    # planned interval must not re-enter matching or coverage evidence.
    assessment = calendar_booking_assessment(
        season="2026-2027", root=root, club="A", problem=problem
    )
    assessment_row = next(
        r for r in assessment["tournaments"] if r["tournament_id"] == "rvv-0116"
    )
    assert assessment_row["classification"] == "associated"
    assert assessment_row["canonical_interval"]["start_time"] == "17:30"
    accepted = next(
        c
        for c in assessment_row["candidates"]
        if c["event_fingerprint"] == event_fingerprint({**event, "club": "A"})
    )
    assert accepted["covers_current_interval"] is True
    assert "canonical_slot_not_covered" not in accepted["counterevidence"]


def test_accepted_changed_start_keeps_effective_duration(tmp_path):
    """Jutul rvv-0173: an 80-minute event starts 10 minutes later than planned."""

    teams = _teams("U12", ("A", "B", "C", "D"))
    tournament = _tournament("rvv-0173", age_group="U12", start="14:20", date_str="2027-03-06", teams=teams)
    root = _promote(tmp_path, [tournament])
    event = _event(date="2027-03-06", start="14:30", end="15:50", title="TURNERING U12")
    problem = _problem(age_group="U12", ice=100, teams=teams, events=[event], parallel_games=2)

    result = _confirm(root, problem, event, "rvv-0173")

    assert result["interval_alignment"]["accepted_calendar_interval"] == {
        "date": "2027-03-06",
        "start_time": "14:30",
        "duration_minutes": 80,
        "end_time": "15:50",
    }
    schedule = load_schedule("2026-2027", root=root)["plan"]
    placement = next(t for t in schedule["tournaments"] if t["id"] == "rvv-0173")
    assert placement["start_time"] == "14:30"
    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = next(r for r in report["tournaments"] if r["tournament_id"] == "rvv-0173")
    assert row["operational_state"] == "booked"


def test_accepted_start_offset_with_unchanged_duration(tmp_path):
    """A representative 15-minute Jutul start offset is adopted exactly."""

    teams = _teams("U12", ("A", "B", "C", "D"))
    tournament = _tournament("rvv-0173", age_group="U12", start="13:00", date_str="2027-03-06", teams=teams)
    root = _promote(tmp_path, [tournament])
    event = _event(date="2027-03-06", start="13:15", end="14:55", title="TURNERING U12")
    problem = _problem(age_group="U12", ice=100, teams=teams, events=[event], parallel_games=2)

    result = _confirm(root, problem, event, "rvv-0173")

    assert result["interval_alignment"]["accepted_calendar_interval"]["start_time"] == "13:15"
    schedule = load_schedule("2026-2027", root=root)["plan"]
    assert schedule["tournaments"][0]["start_time"] == "13:15"


def test_accepted_sub_round_booking_records_reality_and_surfaces_finding(tmp_path):
    """An accepted sub-round interval is recorded exactly, not rejected.

    Recording the authoritative interval is not an approval of its format: the
    playing-time shortfall stays a separate durable finding even when the
    interval is operationally unusable.
    """

    teams = _teams("U9")
    tournament = _tournament("rvv-0116", age_group="U9", start="17:20", date_str="2026-11-21", teams=teams)
    root = _promote(tmp_path, [tournament])
    event = _event(date="2026-11-21", start="17:30", end="17:40", title="TURNERING U9")
    problem = _problem(age_group="U9", ice=155, teams=teams, events=[event], parallel_games=3)

    result = _confirm(root, problem, event, "rvv-0116")

    assert result["interval_alignment"]["accepted_calendar_interval"] == {
        "date": "2026-11-21",
        "start_time": "17:30",
        "duration_minutes": 10,
        "end_time": "17:40",
    }
    assert {w["code"] for w in result["booking_feasibility_warnings"]} == {
        "ice_time_governing_minimum",
        "ice_time_playing_minimum",
    }
    schedule = load_schedule("2026-2027", root=root)["plan"]
    assert schedule["tournaments"][0]["start_time"] == "17:30"
    decisions = load_decisions("2026-2027", root=root)
    assert decisions["ice_time_minutes_overrides"][-1]["minutes"] == 10
    assert not decisions.get(REJECTED_BOOKING_EVIDENCE_KEY)
    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = next(r for r in report["tournaments"] if r["tournament_id"] == "rvv-0116")
    assert row["operational_state"] == "booked"


def _accepted_interval_problem():
    return {
        "ice_time_minutes": {"U9": 155},
        "ice_time_minutes_overrides": {"rvv-0116": 60},
        "calendar_booking_associations": [
            {
                "id": "calendar_booking:event:rvv-0116",
                "status": "active",
                "event_fingerprint": "event",
                "tournament_id": "rvv-0116",
                "club": "A",
                "date": "2026-11-21",
                "start": "17:30",
                "end": "18:30",
                "tournament_facts": {
                    "host_club": "A",
                    "arena": "Baerum ishall",
                    "date": "2026-11-21",
                    "start_time": "17:30",
                    "age_group": "U9",
                },
                "tournament_interval": {
                    "date": "2026-11-21",
                    "start_time": "17:30",
                    "duration_minutes": "60",
                    "end_time": "18:30",
                },
            }
        ],
    }


def _playing_minimum_violation(minutes: int):
    return {
        "code": "ice_time_playing_minimum",
        "message": f"Tournament rvv-0116 (U9) has ice_time_minutes={minutes}",
        "tournament_id": "rvv-0116",
        "age_group": "U9",
        "configured_ice_time_minutes": minutes,
        "minimum_required_minutes": 100,
        "round_count": 5,
        "round_length_minutes": 15,
    }


def test_playing_minimum_finding_keeps_rule_identity_and_is_non_blocking():
    """The accepted playing-round shortfall is a durable, labelled finding."""

    candidate = {
        "tournaments": [
            {
                "id": "rvv-0116",
                "date": "2026-11-21",
                "arena": "Baerum ishall",
                "host_club": "A",
                "age_group": "U9",
                "start_time": "17:30",
                "teams": [],
            }
        ]
    }

    classified = reclassify_accepted_exceptions(
        _accepted_interval_problem(), candidate, [_playing_minimum_violation(60)]
    )

    assert classified["blocking_violations"] == []
    finding = classified["accepted_exceptions"][0]
    assert finding["code"] == "ice_time_playing_minimum"
    assert finding["accepted_booking_interval"] is True
    assert finding["accepted_exception"]["rule"] == "ice_time_playing_minimum"


def test_playing_minimum_finding_is_reclassified_even_below_one_round():
    """Recording reality does not depend on the interval being playable."""

    candidate = {
        "tournaments": [
            {
                "id": "rvv-0116",
                "date": "2026-11-21",
                "arena": "Baerum ishall",
                "host_club": "A",
                "age_group": "U9",
                "start_time": "17:30",
                "teams": [],
            }
        ]
    }

    classified = reclassify_accepted_exceptions(
        _accepted_interval_problem(), candidate, [_playing_minimum_violation(10)]
    )

    assert classified["blocking_violations"] == []
    finding = classified["accepted_exceptions"][0]
    assert finding["code"] == "ice_time_playing_minimum"
    assert finding["accepted_booking_interval"] is True
    assert finding["accepted_exception"]["rule"] == "ice_time_playing_minimum"
