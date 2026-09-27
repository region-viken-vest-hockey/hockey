"""Per-tournament host-confirmed ice-time override (canonical occupancy).

These tests exercise the decision-only override at its canonical owner: the
durable decision record, its projection into the verification problem, the
arena-interval conflict check and the calendar-booking coverage check.
"""

from __future__ import annotations

import json

import pytest

from tournament_scheduler.canonical_ice_time_overrides import (
    active_overrides,
    override_for_tournament,
    overrides_from_problem,
    project_overrides_into_problem,
)
from tournament_scheduler.canonical_state import canonical_state_revision
from tournament_scheduler.calendar_bookings import tournament_occupancy_interval_facts
from tournament_scheduler.occupancy import effective_ice_time_minutes, tournament_end_time
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.serialization.season_plan import tournament_from_dict
from tournament_scheduler.testing.reviewed_export import write_reviewed_stage4_export
from tournament_scheduler.season_state import (
    SeasonStateError,
    clear_ice_time_minutes,
    confirm_calendar_booking,
    ice_time_override_report,
    load_decisions,
    load_schedule,
    move_tournament,
    promote_from_stage3,
    set_ice_time_minutes,
)


def _teams(club_letters=("A", "B", "C", "D")):
    return [
        {"club": club, "label": f"{club}1", "age_group": "U12"}
        for club in club_letters
    ]


def _round_robin_games(labels, rounds=3):
    """Return a valid circle-method round robin with one game per team per round."""

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


def _tournament(t_id, *, date_str="2026-09-12", arena="Arena A", host="A", start="10:00", teams=None):
    teams = teams if teams is not None else _teams((host, "B", "C", "D"))
    labels = [team["label"] for team in teams]
    return {
        "id": t_id,
        "date": date_str,
        "arena": arena,
        "age_group": "U12",
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


def _problem(*, ice=100, round_length=15, teams=None):
    return {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": teams if teams is not None else _teams(),
        "age_groups": ["U12"],
        "ice_time_minutes": {"U12": ice},
        "round_length_minutes": {"U12": round_length},
        "rounds_per_tournament": {"U12": 3},
        "parallel_games": {"U12": 2},
        "club_calendar_status": {"A": "known"},
        "club_busy_intervals": {"A": []},
    }


def _revision(root):
    return canonical_state_revision(
        load_schedule("2026-2027", root=root), load_decisions("2026-2027", root=root)
    )


def test_set_persists_projects_and_advances_revision(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _problem(ice=100)
    before = _revision(root)

    result = set_ice_time_minutes(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        minutes=60,
        request_id="host:holmen:shorter-window",
        actor="operator",
        reference="email:2026-09-27:Holmen",
        problem=problem,
    )
    assert result["changed"] is True
    assert result["override"]["minutes"] == 60
    assert result["override"]["default_minutes"] == 100
    assert result["canonical_state_revision"] == _revision(root)
    assert _revision(root) != before

    decisions = load_decisions("2026-2027", root=root)
    assert active_overrides(decisions) == {"t1": 60}
    record = override_for_tournament(decisions, "t1")
    assert record["status"] == "active"
    assert any(entry["event"] == "set_ice_time_minutes" for entry in decisions["history"])

    projected = project_overrides_into_problem(problem, decisions)
    assert overrides_from_problem(projected) == {"t1": 60}

    # The effective occupied interval shrinks for the override and only there.
    interval = tournament_occupancy_interval_facts(
        load_schedule("2026-2027", root=root)["plan"]["tournaments"][0], projected
    )
    assert interval["duration_minutes"] == "60"
    assert interval["end_time"] == "11:00"


def test_override_only_applies_to_the_named_tournament_instance(tmp_path):
    root = _promote(
        tmp_path,
        [
            _tournament("t1"),
            _tournament(
                "t2",
                start="12:00",
                host="E",
                teams=[
                    {"club": club, "label": f"{club}1", "age_group": "U12"}
                    for club in ("E", "F", "G", "H")
                ],
            ),
        ],
    )
    problem = _problem(ice=100)
    set_ice_time_minutes(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        minutes=60,
        request_id="host:shorter-t1",
        note="host confirmed a shorter window for t1 only",
        problem=problem,
    )
    decisions = load_decisions("2026-2027", root=root)
    projected = project_overrides_into_problem(problem, decisions)
    t1 = tournament_from_dict(load_schedule("2026-2027", root=root)["plan"]["tournaments"][0])
    t2 = tournament_from_dict(load_schedule("2026-2027", root=root)["plan"]["tournaments"][1])
    assert effective_ice_time_minutes(t1, {"U12": 100}, overrides_from_problem(projected)) == 60
    assert tournament_end_time(t1, {"U12": 100}, overrides_by_tournament=overrides_from_problem(projected)) == "11:00"
    assert effective_ice_time_minutes(t2, {"U12": 100}, overrides_from_problem(projected)) == 100
    assert tournament_end_time(t2, {"U12": 100}, overrides_by_tournament=overrides_from_problem(projected)) == "13:40"


def test_set_below_format_or_governing_floor_is_rejected(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _problem(ice=100, round_length=15)
    # Three rounds at 15 minutes plus a 5-minute changeover = 60-minute floor.
    with pytest.raises(SeasonStateError) as excinfo:
        set_ice_time_minutes(
            season="2026-2027",
            root=root,
            tournament_id="t1",
            minutes=50,
            request_id="host:too-short",
            note="below the format minimum",
            problem=problem,
        )
    assert "below the required minimum" in str(excinfo.value)
    assert active_overrides(load_decisions("2026-2027", root=root)) == {}


def test_set_requires_traceable_provenance(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _problem()
    with pytest.raises(SeasonStateError):
        set_ice_time_minutes(
            season="2026-2027",
            root=root,
            tournament_id="t1",
            minutes=60,
            request_id="host:no-provenance",
            problem=problem,
        )


def test_set_is_idempotent_and_a_new_decision_supersedes(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _problem()
    first = set_ice_time_minutes(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        minutes=60,
        request_id="host:first",
        note="first host confirmation",
        problem=problem,
    )
    again = set_ice_time_minutes(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        minutes=60,
        request_id="host:first",
        note="first host confirmation",
        problem=problem,
    )
    assert again["idempotent"] is True
    assert again["changed"] is False

    second = set_ice_time_minutes(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        minutes=70,
        request_id="host:second",
        note="host revised the window",
        problem=problem,
    )
    assert second["changed"] is True
    assert second["previous_override"]["id"] == first["override"]["id"]
    decisions = load_decisions("2026-2027", root=root)
    records = decisions["ice_time_minutes_overrides"]
    statuses = {record["id"]: record["status"] for record in records}
    assert statuses[first["override"]["id"]] == "released"
    assert active_overrides(decisions) == {"t1": 70}


def test_clear_restores_age_default_and_is_idempotent(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _problem()
    set_ice_time_minutes(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        minutes=60,
        request_id="host:temporary",
        note="host confirmed",
        problem=problem,
    )
    cleared = clear_ice_time_minutes(
        season="2026-2027", root=root, tournament_id="t1", actor="operator", note="window reverted"
    )
    assert cleared["changed"] is True
    assert active_overrides(load_decisions("2026-2027", root=root)) == {}
    again = clear_ice_time_minutes(
        season="2026-2027", root=root, tournament_id="t1", actor="operator", note="again"
    )
    assert again["changed"] is False


def test_arena_conflict_move_blocked_by_default_and_unblocked_by_override(tmp_path):
    """The exact issue failure: two host-confirmed slots overlap only under the default."""

    root = _promote(
        tmp_path,
        [
            _tournament("rvv-0003", start="14:00"),
            _tournament(
                "rvv-0004",
                start="10:00",
                host="E",
                teams=[
                    {"club": club, "label": f"{club}1", "age_group": "U12"}
                    for club in ("E", "F", "G", "H")
                ],
            ),
        ],
    )
    problem = _problem(
        ice=100,
        teams=[
            *[{"club": club, "label": f"{club}1", "age_group": "U12"} for club in ("A", "B", "C", "D")],
            *[{"club": club, "label": f"{club}1", "age_group": "U12"} for club in ("E", "F", "G", "H")],
        ],
    )

    # Moving rvv-0004 to the host-reported 13:00 start double-books Arena A
    # under the canonical 100-minute default.
    with pytest.raises(SeasonStateError) as excinfo:
        move_tournament(
            season="2026-2027",
            tournament_id="rvv-0004",
            root=root,
            start_time="13:00",
            problem=problem,
            actor="operator",
        )
    assert "Arena conflict" in str(excinfo.value)

    # The host confirmed both 12-team, 3-round windows are 60 minutes.
    set_ice_time_minutes(
        season="2026-2027",
        root=root,
        tournament_id="rvv-0004",
        minutes=60,
        request_id="holmen:2026-10-10-shorter-window",
        note="Holmen confirmed two 60-minute U12 windows",
        problem=problem,
    )
    moved = move_tournament(
        season="2026-2027",
        tournament_id="rvv-0004",
        root=root,
        start_time="13:00",
        problem=problem,
        actor="operator",
    )
    placement = next(
        t for t in moved["plan"]["tournaments"] if t["id"] == "rvv-0004"
    )
    assert placement["start_time"] == "13:00"


def test_confirm_calendar_booking_accepts_host_confirmed_shorter_window(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", start="14:00")])
    problem = _problem(ice=100)
    problem["club_busy_intervals"] = {
        "A": [
            {
                "date": "2026-09-12",
                "start": "13:30",
                "end": "15:00",
                "kind": "external",
                "availability": "fixed_busy",
                "calendar_event": "Serieturneringer U12",
            }
        ]
    }
    from tournament_scheduler.season_state import calendar_booking_candidates

    candidates = calendar_booking_candidates(
        season="2026-2027", root=root, club="A", problem=problem
    )
    event_fp = candidates["booking_candidates"][0]["calendar_event"]["fingerprint"]

    # Canonical 100-minute window runs to 15:40, which the event does not cover.
    with pytest.raises(SeasonStateError) as excinfo:
        confirm_calendar_booking(
            season="2026-2027",
            root=root,
            event_fingerprint=event_fp,
            tournament_id="t1",
            actor="booker",
            note="host calendar event",
            problem=problem,
        )
    assert "interval_mismatch" in str(excinfo.value)

    set_ice_time_minutes(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        minutes=60,
        request_id="host:confirmed-60",
        note="host confirmed a 60-minute window",
        problem=problem,
    )
    confirmed = confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fp,
        tournament_id="t1",
        actor="booker",
        note="host confirmed a 60-minute window matching the calendar event",
        problem=problem,
    )
    assert confirmed["association"]["tournament_interval"]["duration_minutes"] == "60"


def test_override_report_lists_active_and_released(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _problem()
    set_ice_time_minutes(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        minutes=60,
        request_id="host:report",
        note="confirmed",
        problem=problem,
    )
    report = ice_time_override_report(season="2026-2027", root=root, problem=problem)
    assert report["active_count"] == 1
    assert report["overrides"][0]["minutes"] == 60
    assert report["overrides"][0]["default_minutes"] == 100

    clear_ice_time_minutes(season="2026-2027", root=root, tournament_id="t1", note="done")
    active = ice_time_override_report(season="2026-2027", root=root, problem=problem)
    assert active["active_count"] == 0
    all_rows = ice_time_override_report(
        season="2026-2027", root=root, problem=problem, include_released=True
    )
    assert [row["status"] for row in all_rows["overrides"]] == ["released"]


def test_set_rejects_non_positive_minutes_and_unknown_tournament(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _problem()
    with pytest.raises(SeasonStateError):
        set_ice_time_minutes(
            season="2026-2027",
            root=root,
            tournament_id="t1",
            minutes=0,
            request_id="host:zero",
            note="nonsense",
            problem=problem,
        )
    with pytest.raises(SeasonStateError):
        set_ice_time_minutes(
            season="2026-2027",
            root=root,
            tournament_id="rvv-9999",
            minutes=60,
            request_id="host:missing",
            note="unknown",
            problem=problem,
        )


def test_verifier_applies_override_to_format_minimum():
    """A projected override must not bypass the hard format/governing floor."""

    from tournament_scheduler.planning_contract import verify_candidate

    problem = _problem(ice=100, round_length=15)
    problem["ice_time_minutes_overrides"] = {"t1": 50}
    result = verify_candidate(_plan([_tournament("t1")]), problem)
    codes = {violation.get("code") for violation in result.get("violations", [])}
    assert "ice_time_playing_minimum" in codes


def test_override_reaches_export_projection_and_end_times(tmp_path):
    """Exports/projection must render the same interval the verifier uses."""

    from tournament_scheduler.pipeline.export_projection_guard import tournament_projection
    from tournament_scheduler.serialization.season_plan import season_plan_from_dict
    from tournament_scheduler.canonical_ice_time_overrides import apply_overrides_to_plan

    root = _promote(tmp_path, [_tournament("t1", start="14:00")])
    problem = _problem(ice=100)
    set_ice_time_minutes(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        minutes=60,
        request_id="host:export",
        note="host confirmed 60 minutes",
        problem=problem,
    )
    schedule = load_schedule("2026-2027", root=root)
    projected = project_overrides_into_problem(problem, load_decisions("2026-2027", root=root))

    projection = tournament_projection(schedule["plan"], projected)
    assert projection["t1"]["duration_minutes"] == 60
    assert projection["t1"]["end_time"] == "15:00"

    plan = season_plan_from_dict(schedule["plan"])
    apply_overrides_to_plan(plan, projected)
    assert tournament_end_time(plan.tournaments[0], {"U12": 100}) == "15:00"


def test_cli_set_report_clear(tmp_path, capsys):
    from tournament_scheduler.cli.rvv_cli import main

    root = _promote(tmp_path, [_tournament("t1")])
    assert main(
        [
            "season", "set-ice-time-minutes",
            "--season", "2026-2027",
            "--root", str(root),
            "--tournament-id", "t1",
            "--minutes", "60",
            "--request-id", "host:cli",
            "--note", "host confirmed",
            "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["override"]["minutes"] == 60

    assert main(
        [
            "season", "ice-time-overrides",
            "--season", "2026-2027",
            "--root", str(root),
            "--json",
        ]
    ) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["active_count"] == 1

    assert main(
        [
            "season", "clear-ice-time-minutes",
            "--season", "2026-2027",
            "--root", str(root),
            "--tournament-id", "t1",
            "--note", "reverted",
        ]
    ) == 0
    capsys.readouterr()
    assert active_overrides(load_decisions("2026-2027", root=root)) == {}
