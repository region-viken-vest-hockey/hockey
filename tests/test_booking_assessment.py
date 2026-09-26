"""Read-only, revision/source-bound booking crosswalk assessment.

The assessment proposes tournament<->calendar-event relations and keeps
competing or unresolved cases visible.  It never mutates canonical state, never
confirms a booking from a bare overlap, and never claims that calendar absence
proves a tournament is unbooked.
"""

from __future__ import annotations

import json

from tournament_scheduler.calendar_bookings import booking_assessment
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.season_state import (
    calendar_booking_assessment,
    confirm_calendar_booking,
    load_decisions,
    load_schedule,
    set_manual_booking_assertion,
)
from tournament_scheduler.testing.reviewed_export import write_reviewed_stage4_export


def _teams(club_letters=("A", "B", "C", "D")):
    return [{"club": club, "label": f"{club}1", "age_group": "U10"} for club in club_letters]


def _tournament(t_id, *, date_str="2026-09-12", arena="Arena A", host="A", start="10:00", teams=None):
    teams = teams if teams is not None else _teams((host, "B", "C", "D"))
    labels = [team["label"] for team in teams]
    games = [
        {"home": labels[i], "away": labels[j], "parallel_slot": 0, "round_number": 1}
        for i in range(len(labels))
        for j in range(i + 1, len(labels))
    ]
    return {
        "id": t_id,
        "date": date_str,
        "arena": arena,
        "age_group": "U10",
        "host_club": host,
        "teams": teams,
        "games": games,
        "start_time": start,
    }


def _plan(tournaments):
    return {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": tournaments}


def _promote(tmp_path, tournaments):
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    state.write_stage(StageName.PLANNING, {"plan": _plan(tournaments)}, status=StageStatus.DONE)
    write_reviewed_stage4_export(state)
    from tournament_scheduler.season_state import promote_from_stage3

    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    return root


def _problem(events, *, status="known"):
    return {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": _teams(),
        "age_groups": ["U10"],
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
        "parallel_games": {"U10": 2},
        "club_calendar_status": {"A": status},
        "club_busy_intervals": {"A": events},
    }


def _event(date_str, start, end, *, title="Miniputt U10", club="A"):
    return {
        "date": date_str,
        "start": start,
        "end": end,
        "availability": "fixed_busy",
        "calendar_event": title,
        "club": club,
    }


def _assess(root, problem, **kwargs):
    return calendar_booking_assessment(
        season="2026-2027", root=root, problem=problem, **kwargs
    )


def _tournament_row(result, tournament_id):
    return next(row for row in result["tournaments"] if row["tournament_id"] == tournament_id)


def test_adjacent_day_event_is_a_changed_slot_candidate(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12")])
    result = _assess(root, _problem([_event("2026-09-13", "10:00", "12:00")]))

    row = _tournament_row(result, "t1")
    assert row["classification"] == "proposed_changed_slot"
    assert row["proposal_is_binding"] is False
    candidate = row["candidates"][0]
    assert candidate["relation"] == "proximate_date_shift"
    assert candidate["date_delta_days"] == 1
    assert candidate["covers_current_interval"] is False


def test_same_day_non_overlapping_event_is_a_changed_slot_candidate(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12", start="10:00")])
    result = _assess(root, _problem([_event("2026-09-12", "13:00", "15:00")]))

    row = _tournament_row(result, "t1")
    assert row["classification"] == "proposed_changed_slot"
    assert row["candidates"][0]["relation"] == "same_date_time_shift"


def test_one_event_competing_for_two_tournaments_stays_visible(tmp_path):
    root = _promote(
        tmp_path,
        [
            _tournament("t1", date_str="2026-09-12", start="10:00", arena="Arena A"),
            _tournament("t2", date_str="2026-09-13", start="10:00", arena="Arena B"),
        ],
    )
    result = _assess(root, _problem([_event("2026-09-12", "10:00", "12:00")]))

    assert _tournament_row(result, "t1")["classification"] == "competing_candidates"
    assert _tournament_row(result, "t2")["classification"] == "competing_candidates"
    event_row = result["events"][0]
    assert event_row["one_to_many"] is True
    assert {c["tournament_id"] for c in event_row["candidate_tournaments"]} == {"t1", "t2"}
    assert event_row["event_fingerprint"] in result["unresolved"]["event_fingerprints"]


def test_group_booking_covering_two_tournaments_is_flagged(tmp_path):
    root = _promote(
        tmp_path,
        [
            _tournament("t1", date_str="2026-09-12", start="10:00", arena="Arena A"),
            _tournament(
                "t2",
                date_str="2026-09-12",
                start="12:00",
                arena="Arena B",
                teams=[
                    {"club": "A", "label": "A2", "age_group": "U10"},
                    {"club": "E", "label": "E1", "age_group": "U10"},
                    {"club": "F", "label": "F1", "age_group": "U10"},
                    {"club": "G", "label": "G1", "age_group": "U10"},
                ],
            ),
        ],
    )
    result = _assess(root, _problem([_event("2026-09-12", "09:00", "14:00")]))

    event_row = result["events"][0]
    assert event_row["group_booking"] is True
    assert event_row["covered_tournament_ids"] == ["t1", "t2"]


def test_unmatched_tournament_and_event_are_not_negative_claims(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12")])
    result = _assess(root, _problem([_event("2026-10-30", "10:00", "12:00")]))

    assert _tournament_row(result, "t1")["classification"] == "unmatched"
    event_row = result["events"][0]
    assert event_row["unmatched"] is True
    assert event_row["candidate_count"] == 0
    assert result["sources"]["A"]["trusted_for_negative_claim"] is False
    assert result["counts"]["not_checkable"] == 0
    # The assessment never emits a negative booking conclusion from absence.
    assert "confirmed_not_booked" not in json.dumps(result)


def test_untrusted_source_fails_closed_to_not_checkable(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12")])
    result = _assess(root, _problem([], status="untrusted"))

    assert _tournament_row(result, "t1")["classification"] == "not_checkable"
    source = result["sources"]["A"]
    assert source["source_trust"] == "untrusted"
    assert source["source_review_required"] is True


def test_wrong_age_title_is_visible_counterevidence(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12")])
    result = _assess(root, _problem([_event("2026-09-12", "10:00", "12:00", title="Miniputt U12")]))

    candidate = _tournament_row(result, "t1")["candidates"][0]
    assert candidate["age_group_conflict"] is True
    assert "event_title_age_group_differs" in candidate["counterevidence"]


def test_explicit_association_and_manual_assertion_are_reported_as_authority(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12")])
    problem = _problem([_event("2026-09-12", "10:00", "12:00")])
    event_fp = _assess(root, problem)["events"][0]["event_fingerprint"]

    confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fp,
        tournament_id="t1",
        actor="booker",
        note="matched booking",
        problem=problem,
    )
    associated = _tournament_row(_assess(root, problem), "t1")
    assert associated["classification"] == "associated"
    assert associated["associated_event_fingerprint"] == event_fp
    assert associated["proposal_is_binding"] is False

    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="not-booked",
        actor="booker",
        reference="email:1",
        problem=problem,
    )
    manual = _tournament_row(_assess(root, problem), "t1")
    assert manual["classification"] == "manually_asserted"
    assert manual["manual_authority"] == "manual_club_confirmation"


def test_stale_association_is_surfaced_by_assessment(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12")])
    problem = _problem([_event("2026-09-12", "10:00", "12:00")])
    event_fp = _assess(root, problem)["events"][0]["event_fingerprint"]
    confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fp,
        tournament_id="t1",
        actor="booker",
        note="matched booking",
        problem=problem,
    )

    schedule_path = root / "2026-2027" / "schedule.json"
    saved = json.loads(schedule_path.read_text(encoding="utf-8"))
    saved["plan"]["tournaments"][0]["date"] = "2026-09-19"
    schedule_path.write_text(json.dumps(saved), encoding="utf-8")

    result = _assess(root, problem)
    assert result["counts"]["stale_associations"] == 1
    assert _tournament_row(result, "t1")["classification"] != "associated"


def test_assessment_fingerprint_is_deterministic_and_revision_bound(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12")])
    problem = _problem([_event("2026-09-13", "10:00", "12:00")])

    first = _assess(root, problem)
    second = _assess(root, problem)
    assert first["assessment_fingerprint"] == second["assessment_fingerprint"]
    assert first["canonical_state_revision"] == second["canonical_state_revision"]

    plan = load_schedule("2026-2027", root=root)["plan"]
    decisions = load_decisions("2026-2027", root=root)
    other_revision = booking_assessment(
        problem=problem,
        plan=plan,
        decisions=decisions,
        canonical_state_revision="different-revision",
        season="2026-2027",
    )
    assert other_revision["assessment_fingerprint"] != first["assessment_fingerprint"]


def test_assessment_is_read_only(tmp_path):
    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12")])
    schedule_path = root / "2026-2027" / "schedule.json"
    decisions_path = root / "2026-2027" / "decisions.json"
    before_schedule = schedule_path.read_text(encoding="utf-8")
    before_decisions = decisions_path.read_text(encoding="utf-8")

    _assess(root, _problem([_event("2026-09-12", "10:00", "12:00")]))

    assert schedule_path.read_text(encoding="utf-8") == before_schedule
    assert decisions_path.read_text(encoding="utf-8") == before_decisions


def test_booking_assessment_cli_is_read_only_and_structured(tmp_path, capsys):
    from tournament_scheduler.cli.rvv_cli import main

    root = _promote(tmp_path, [_tournament("t1", date_str="2026-09-12")])
    assert (
        main(
            [
                "season",
                "booking-assessment",
                "--season",
                "2026-2027",
                "--root",
                str(root),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["canonical_state_revision"]
    assert payload["counts"]["unresolved_tournaments"] == 1
    assert load_decisions("2026-2027", root=root)["decisions"]["t1"]["status"] == "pending_review"
