"""End-to-end approval/lock lifecycle tests."""

from __future__ import annotations

import json
from datetime import date

import pytest

from tournament_scheduler.canonical_baseline import (
    build_canonical_baseline,
    resolve_canonical_baseline,
    verify_canonical_locks,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.testing.reviewed_export import write_reviewed_stage4_export
from tournament_scheduler.season_state import (
    SeasonStateError,
    apply_candidate,
    approval_report,
    approve_tournament,
    booking_status_report,
    calendar_booking_candidates,
    calendar_booking_findings,
    confirm_calendar_booking,
    reconcile_calendar_bookings,
    load_decisions,
    release_calendar_booking,
    load_schedule,
    move_tournament,
    promote_from_stage3,
    unapprove_tournament,
)


def _teams(club_letters=("A", "B", "C", "D")):
    return [
        {"club": club, "label": f"{club}1", "age_group": "U10"}
        for club in club_letters
    ]


def _tournament(t_id, *, date_str="2026-09-12", arena="Arena A", host="A", teams=None):
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
        "start_time": "10:00",
    }


def _plan(tournaments):
    return {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": tournaments}


def _promote(tmp_path, tournaments):
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    state.write_stage(StageName.PLANNING, {"plan": _plan(tournaments)}, status=StageStatus.DONE)
    write_reviewed_stage4_export(state)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    return root


def test_unapprove_restores_editability_and_clears_locks(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    approve_tournament(season="2026-2027", tournament_id="t1", root=root, actor="booker")

    decisions = unapprove_tournament(
        season="2026-2027", tournament_id="t1", root=root, actor="booker", note="host rebooked"
    )
    record = decisions["decisions"]["t1"]
    assert record["status"] == "pending_review"
    assert record["placement_locked"] is False
    assert record["participants_locked"] is False
    assert record["approved_fingerprint"] is None
    assert record["unapproved_by"] == "booker"
    assert any(entry["event"] == "unapprove" for entry in decisions["history"])

    # An explicitly unapproved tournament is editable again through the
    # canonical mutation path.
    moved = move_tournament(
        season="2026-2027", tournament_id="t1", root=root, date="2026-09-19"
    )
    assert moved["plan"]["tournaments"][0]["date"] == "2026-09-19"


def test_approval_refuses_hard_invalid_tournament(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    # Deliberately corrupt canonical schedule state through a legacy path:
    # a team whose age group does not match the tournament's.
    schedule_path = root / "2026-2027" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    schedule["plan"]["tournaments"][0]["teams"][0]["age_group"] = "U12"
    schedule_path.write_text(json.dumps(schedule), encoding="utf-8")

    with pytest.raises(SeasonStateError) as excinfo:
        approve_tournament(season="2026-2027", tournament_id="t1", root=root, actor="booker")
    assert "verification" in str(excinfo.value)

    record = load_decisions("2026-2027", root=root)["decisions"]["t1"]
    assert record["status"] == "pending_review"
    assert record["approved_fingerprint"] is None


def test_calendar_booking_confirmation_binds_event_only_to_matching_tournament(tmp_path):
    root = _promote(
        tmp_path,
        [
            _tournament("t1"),
            _tournament(
                "t2",
                date_str="2026-09-12",
                host="A",
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
    problem = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": [
            *[{"club": club, "label": f"{club}1", "age_group": "U10"} for club in "ABCDEFG"],
            {"club": "A", "label": "A2", "age_group": "U10"},
        ],
        "age_groups": ["U10"],
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
        "parallel_games": {"U10": 2},
        "club_calendar_status": {"A": "known"},
        "club_busy_intervals": {
            "A": [
                {
                    "date": "2026-09-12",
                    "start": "10:00",
                    "end": "12:00",
                    "kind": "external",
                    "availability": "fixed_busy",
                    "calendar_event": "Serieturneringer U10",
                }
            ]
        },
    }
    candidates = calendar_booking_candidates(season="2026-2027", root=root, club="A", problem=problem)
    event_fp = candidates["booking_candidates"][0]["calendar_event"]["fingerprint"]
    assert {c["id"] for c in candidates["booking_candidates"][0]["candidate_tournaments"]} == {"t1", "t2"}

    dry = confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fp,
        tournament_id="t1",
        actor="booker",
        note="Matched to host calendar booking: Serieturneringer U10",
        problem=problem,
        dry_run=True,
    )
    assert dry["dry_run"] is True
    assert load_decisions("2026-2027", root=root)["decisions"]["t1"]["status"] == "pending_review"

    result = confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fp,
        tournament_id="t1",
        actor="booker",
        note="Matched to host calendar booking: Serieturneringer U10",
        problem=problem,
    )
    assert result["association"]["event_fingerprint"] == event_fp
    decisions = load_decisions("2026-2027", root=root)
    assert decisions["decisions"]["t1"]["status"] == "approved"
    assert decisions["decisions"]["t1"]["placement_locked"] is True
    assert decisions["decisions"]["t1"]["participants_locked"] is False

    from tournament_scheduler.planning_contract import verify_candidate
    from tournament_scheduler.calendar_bookings import project_associations_into_problem

    plan = load_schedule("2026-2027", root=root)["plan"]
    associated_problem = project_associations_into_problem(problem, decisions, plan)
    verification = verify_candidate(plan, associated_problem)
    assert {p["tournament_id"] for p in verification["manual_external_conflict_placements"]} == {"t2"}


def test_club_reconciliation_records_candidate_and_negative_booking_evidence_and_stales_on_move(tmp_path):
    root = _promote(tmp_path, [_tournament("t1"), _tournament("t2", date_str="2026-09-19")])
    problem = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": _teams(),
        "age_groups": ["U10"],
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
        "parallel_games": {"U10": 2},
        "club_calendar_status": {"A": "known"},
        "club_busy_intervals": {
            "A": [
                {"date": "2026-09-12", "start": "10:00", "end": "12:00", "availability": "fixed_busy", "calendar_event": "Miniputt U10"}
            ]
        },
    }

    result = reconcile_calendar_bookings(
        season="2026-2027",
        root=root,
        club="A",
        actor="booker",
        note="reviewed complete host calendar",
        problem=problem,
    )
    statuses = {row["tournament_id"]: row["status"] for row in result["classified"]}
    assert statuses == {"t1": "ambiguous", "t2": "confirmed_not_booked"}
    assert {row["tournament_id"]: row["reason"] for row in result["classified"]}["t1"] == "single_overlapping_event_requires_confirmation"
    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    assert {row["tournament_id"]: row["status"] for row in report["tournaments"]} == statuses
    assert report["counts"]["confirmed_booked"] == 0
    assert report["counts"]["confirmed_not_booked"] == 1
    assert report["counts"]["needs_attention"] == 2

    schedule_path = root / "2026-2027" / "schedule.json"
    saved = json.loads(schedule_path.read_text(encoding="utf-8"))
    saved["plan"]["tournaments"][1]["date"] = "2026-09-20"
    schedule_path.write_text(json.dumps(saved), encoding="utf-8")

    stale = booking_status_report(season="2026-2027", root=root, problem=problem)
    t2 = next(row for row in stale["tournaments"] if row["tournament_id"] == "t2")
    assert t2["status"] == "stale"
    assert "tournament_date_changed" in t2["stale_reasons"]


def _host_a_problem(events):
    return {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": _teams(),
        "age_groups": ["U10"],
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
        "parallel_games": {"U10": 2},
        "club_calendar_status": {"A": "known"},
        "club_busy_intervals": {"A": events},
    }


def _report_and_heatmap(root, problem):
    """Return the booking-status report plus the heatmap items it feeds."""

    from tournament_scheduler.html.data_computation import compute_heatmap_data
    from tournament_scheduler.serialization.season_plan import season_plan_from_dict

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    plan = season_plan_from_dict(load_schedule("2026-2027", root=root)["plan"])
    booking_by_tournament = {row["tournament_id"]: row for row in report["tournaments"]}
    heatmap, _, _ = compute_heatmap_data(plan, booking_by_tournament=booking_by_tournament)
    items = {
        item["tournament_id"]: item
        for week in heatmap.values()
        for club in week.values()
        for item in club
    }
    return report, items


def test_lone_unrelated_overlapping_event_is_not_confirmed_booked(tmp_path):
    """Occupancy alone is not proof that the interval is this tournament."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem(
        [
            {
                "date": "2026-09-12",
                "start": "10:00",
                "end": "12:00",
                "availability": "fixed_busy",
                "calendar_event": "Trening U10",
            }
        ]
    )

    result = reconcile_calendar_bookings(
        season="2026-2027", root=root, club="A", note="reviewed complete host calendar", problem=problem
    )
    row = result["classified"][0]
    assert row["status"] == "ambiguous"
    assert row["reason"] == "single_overlapping_event_requires_confirmation"
    assert row["event_fingerprint"]

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    assert report["tournaments"][0]["status"] == "ambiguous"
    assert report["counts"]["confirmed_booked"] == 0
    assert report["tournaments"][0]["needs_attention"] is True


def test_reconcile_keeps_explicit_association_confirmed_and_records_negative(tmp_path):
    """Only an explicit association makes a match confirmed_booked."""

    root = _promote(tmp_path, [_tournament("t1"), _tournament("t2", date_str="2026-09-19")])
    problem = _host_a_problem(
        [
            {
                "date": "2026-09-12",
                "start": "10:00",
                "end": "12:00",
                "availability": "fixed_busy",
                "calendar_event": "Serieturneringer U10",
            }
        ]
    )
    event_fp = calendar_booking_candidates(season="2026-2027", root=root, club="A", problem=problem)[
        "booking_candidates"
    ][0]["calendar_event"]["fingerprint"]
    confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fp,
        tournament_id="t1",
        actor="booker",
        note="Matched to host calendar booking",
        problem=problem,
    )

    result = reconcile_calendar_bookings(
        season="2026-2027", root=root, club="A", note="reviewed complete host calendar", problem=problem
    )
    statuses = {row["tournament_id"]: row["status"] for row in result["classified"]}
    assert statuses == {"t1": "confirmed_booked", "t2": "confirmed_not_booked"}
    reasons = {row["tournament_id"]: row["reason"] for row in result["classified"]}
    assert reasons["t1"] == "explicit_calendar_booking_association"
    assert reasons["t2"] == "no_overlapping_event_in_trustworthy_calendar"

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    assert {row["tournament_id"]: row["status"] for row in report["tournaments"]} == statuses
    assert report["counts"]["confirmed_booked"] == 1
    assert report["counts"]["confirmed_not_booked"] == 1


def test_projection_downgrades_historical_weak_positive_booking_evidence(tmp_path):
    """Historical ``confirmed_booked`` evidence without an association needs review.

    Legacy reconciliation could persist ``confirmed_booked`` from a lone overlap.
    The projection boundary must not keep reporting that as confirmed once the one
    definition of "confirmed booked" is a currently-valid explicit association.
    """

    from tournament_scheduler.calendar_bookings import TOURNAMENT_BOOKING_EVIDENCE_KEY

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem(
        [
            {
                "date": "2026-09-12",
                "start": "10:00",
                "end": "12:00",
                "availability": "fixed_busy",
                "calendar_event": "Trening U10",
            }
        ]
    )
    reconcile_calendar_bookings(
        season="2026-2027", root=root, club="A", note="legacy review", problem=problem
    )
    # Simulate the old single-overlap false positive directly in the persisted
    # evidence, with no explicit association to back it.
    decisions_path = root / "2026-2027" / "decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    evidence = decisions[TOURNAMENT_BOOKING_EVIDENCE_KEY]
    evidence[0]["status"] = "confirmed_booked"
    evidence[0]["reason"] = "matched_single_overlapping_event"
    decisions_path.write_text(json.dumps(decisions), encoding="utf-8")

    report, heatmap_items = _report_and_heatmap(root, problem)
    row = report["tournaments"][0]
    assert row["status"] == "ambiguous"
    assert row["needs_attention"] is True
    assert "confirmed_booking_without_valid_association" in row["stale_reasons"]
    # Historical evidence is preserved rather than deleted.
    assert row["evidence"]["status"] == "confirmed_booked"
    assert report["counts"]["confirmed_booked"] == 0
    assert report["counts"]["needs_attention"] == 1
    # The heatmap reads the same projection and must not show the stale positive.
    assert heatmap_items["t1"]["booking_status"] == "ambiguous"
    assert {tid: item["booking_status"] for tid, item in heatmap_items.items()} == {
        item["tournament_id"]: item["status"] for item in report["tournaments"]
    }


def test_projection_requires_review_after_association_release(tmp_path):
    """A released association without rebinding must not keep reporting confirmed."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem(
        [
            {
                "date": "2026-09-12",
                "start": "10:00",
                "end": "12:00",
                "availability": "fixed_busy",
                "calendar_event": "Serieturneringer U10",
            }
        ]
    )
    event_fp = calendar_booking_candidates(season="2026-2027", root=root, club="A", problem=problem)[
        "booking_candidates"
    ][0]["calendar_event"]["fingerprint"]
    confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fp,
        tournament_id="t1",
        actor="booker",
        note="host confirmed the booking",
        problem=problem,
    )
    confirmed = booking_status_report(season="2026-2027", root=root, problem=problem)
    assert confirmed["tournaments"][0]["status"] == "confirmed_booked"
    assert confirmed["counts"]["confirmed_booked"] == 1

    release_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fp,
        tournament_id="t1",
        actor="booker",
        note="host withdrew confirmation",
    )
    report, heatmap_items = _report_and_heatmap(root, problem)
    row = report["tournaments"][0]
    assert row["status"] == "ambiguous"
    assert row["needs_attention"] is True
    assert "confirmed_booking_without_valid_association" in row["stale_reasons"]
    assert row["evidence"]["status"] == "confirmed_booked"
    assert report["counts"]["confirmed_booked"] == 0
    assert report["counts"]["needs_attention"] == 1
    assert heatmap_items["t1"]["booking_status"] == "ambiguous"
    assert {tid: item["booking_status"] for tid, item in heatmap_items.items()} == {
        item["tournament_id"]: item["status"] for item in report["tournaments"]
    }


def test_club_reconciliation_does_not_record_negative_evidence_for_blocked_source(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": _teams(),
        "age_groups": ["U10"],
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
        "parallel_games": {"U10": 2},
        "club_calendar_status": {"A": "blocked"},
        "club_busy_intervals": {"A": []},
    }
    result = reconcile_calendar_bookings(season="2026-2027", root=root, club="A", problem=problem)
    assert result["classified"][0]["status"] == "not_checkable"
    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    assert report["tournaments"][0]["status"] == "not_checkable"
    assert report["counts"]["confirmed_not_booked"] == 0


def test_stale_calendar_booking_association_fails_closed_when_tournament_moves(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": _teams(),
        "age_groups": ["U10"],
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
        "parallel_games": {"U10": 2},
        "club_calendar_status": {"A": "known"},
        "club_busy_intervals": {"A": [{"date": "2026-09-12", "start": "10:00", "end": "12:00", "availability": "fixed_busy", "calendar_event": "Miniputt"}]},
    }
    event_fp = calendar_booking_candidates(season="2026-2027", root=root, club="A", problem=problem)["booking_candidates"][0]["calendar_event"]["fingerprint"]
    confirm_calendar_booking(season="2026-2027", root=root, event_fingerprint=event_fp, tournament_id="t1", problem=problem)

    # Persist the placement change through a legacy path to simulate a stale canonical association.
    schedule_path = root / "2026-2027" / "schedule.json"
    saved = json.loads(schedule_path.read_text(encoding="utf-8"))
    saved["plan"]["tournaments"][0]["arena"] = "Arena B"
    schedule_path.write_text(json.dumps(saved), encoding="utf-8")

    findings = calendar_booking_findings(season="2026-2027", root=root, problem=problem)
    assert "tournament_arena_changed" in findings["findings"][0]["reasons"]
    from tournament_scheduler.calendar_bookings import project_associations_into_problem
    from tournament_scheduler.planning_contract import verify_candidate

    plan = load_schedule("2026-2027", root=root)["plan"]
    projected = project_associations_into_problem(problem, load_decisions("2026-2027", root=root), plan)
    verification = verify_candidate(plan, projected)
    assert {p["tournament_id"] for p in verification["manual_external_conflict_placements"]} == {"t1"}


def test_stale_calendar_booking_association_fails_closed_when_start_time_changes(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": _teams(),
        "age_groups": ["U10"],
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
        "parallel_games": {"U10": 2},
        "club_calendar_status": {"A": "known"},
        "club_busy_intervals": {"A": [{"date": "2026-09-12", "start": "10:00", "end": "12:00", "availability": "fixed_busy", "calendar_event": "Miniputt"}]},
    }
    event_fp = calendar_booking_candidates(season="2026-2027", root=root, club="A", problem=problem)["booking_candidates"][0]["calendar_event"]["fingerprint"]
    confirm_calendar_booking(season="2026-2027", root=root, event_fingerprint=event_fp, tournament_id="t1", problem=problem)
    schedule_path = root / "2026-2027" / "schedule.json"
    saved = json.loads(schedule_path.read_text(encoding="utf-8"))
    saved["plan"]["tournaments"][0]["start_time"] = "10:30"
    schedule_path.write_text(json.dumps(saved), encoding="utf-8")

    findings = calendar_booking_findings(season="2026-2027", root=root, problem=problem)
    assert "tournament_start_time_changed" in findings["findings"][0]["reasons"]
    from tournament_scheduler.calendar_bookings import project_associations_into_problem
    from tournament_scheduler.planning_contract import verify_candidate

    plan = load_schedule("2026-2027", root=root)["plan"]
    projected = project_associations_into_problem(problem, load_decisions("2026-2027", root=root), plan)
    assert verify_candidate(plan, projected)["manual_external_conflict_placements"][0]["tournament_id"] == "t1"


def test_calendar_booking_association_fails_closed_when_canonical_duration_grows(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": _teams(),
        "age_groups": ["U10"],
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
        "parallel_games": {"U10": 2},
        "club_calendar_status": {"A": "known"},
        "club_busy_intervals": {"A": [{"date": "2026-09-12", "start": "10:00", "end": "12:00", "availability": "fixed_busy", "calendar_event": "Miniputt"}]},
    }
    event_fp = calendar_booking_candidates(season="2026-2027", root=root, club="A", problem=problem)["booking_candidates"][0]["calendar_event"]["fingerprint"]
    result = confirm_calendar_booking(season="2026-2027", root=root, event_fingerprint=event_fp, tournament_id="t1", problem=problem)
    assert result["association"]["tournament_interval"]["duration_minutes"] == "120"
    assert result["association"]["tournament_interval"]["end_time"] == "12:00"

    migrated_problem = dict(problem)
    migrated_problem["ice_time_minutes"] = {"U10": 180}
    findings = calendar_booking_findings(season="2026-2027", root=root, problem=migrated_problem)
    assert findings["findings"][0]["code"] == "stale_calendar_booking_association"
    assert "tournament_duration_minutes_changed" in findings["findings"][0]["reasons"]
    assert "tournament_end_time_changed" in findings["findings"][0]["reasons"]
    assert "event_does_not_cover_tournament_interval" in findings["findings"][0]["reasons"]

    from tournament_scheduler.calendar_bookings import project_associations_into_problem
    from tournament_scheduler.planning_contract import verify_candidate

    plan = load_schedule("2026-2027", root=root)["plan"]
    projected = project_associations_into_problem(migrated_problem, load_decisions("2026-2027", root=root), plan)
    verification = verify_candidate(plan, projected)
    assert verification["manual_external_conflict_placements"][0]["tournament_id"] == "t1"


def test_calendar_booking_confirmation_rejects_partial_overlap(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": _teams(),
        "age_groups": ["U10"],
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
        "parallel_games": {"U10": 2},
        "club_calendar_status": {"A": "known"},
        "club_busy_intervals": {"A": [{"date": "2026-09-12", "start": "11:00", "end": "13:00", "availability": "fixed_busy", "calendar_event": "Miniputt"}]},
    }
    event_fp = calendar_booking_candidates(season="2026-2027", root=root, club="A", problem=problem)["booking_candidates"][0]["calendar_event"]["fingerprint"]
    with pytest.raises(SeasonStateError, match="interval_mismatch"):
        confirm_calendar_booking(season="2026-2027", root=root, event_fingerprint=event_fp, tournament_id="t1", problem=problem)


def test_calendar_booking_release_is_required_before_rebind(tmp_path):
    root = _promote(
        tmp_path,
        [
            _tournament("t1"),
            _tournament(
                "t2",
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
    problem = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": [*_teams(("A", "B", "C", "D")), {"club": "A", "label": "A2", "age_group": "U10"}, *_teams(("E", "F", "G"))],
        "age_groups": ["U10"],
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
        "parallel_games": {"U10": 2},
        "club_calendar_status": {"A": "known"},
        "club_busy_intervals": {"A": [{"date": "2026-09-12", "start": "10:00", "end": "12:00", "availability": "fixed_busy", "calendar_event": "Miniputt"}]},
    }
    event_fp = calendar_booking_candidates(season="2026-2027", root=root, club="A", problem=problem)["booking_candidates"][0]["calendar_event"]["fingerprint"]
    confirm_calendar_booking(season="2026-2027", root=root, event_fingerprint=event_fp, tournament_id="t1", problem=problem)
    with pytest.raises(SeasonStateError, match="release it before rebinding"):
        confirm_calendar_booking(season="2026-2027", root=root, event_fingerprint=event_fp, tournament_id="t2", problem=problem)

    release = release_calendar_booking(season="2026-2027", root=root, event_fingerprint=event_fp, tournament_id="t1", note="wrong match")
    assert release["released"][0]["status"] == "released"
    result = confirm_calendar_booking(season="2026-2027", root=root, event_fingerprint=event_fp, tournament_id="t2", problem=problem)
    assert result["association"]["tournament_id"] == "t2"


def test_calendar_booking_confirmation_rejects_wrong_tournament_and_reports_stale(tmp_path):
    root = _promote(tmp_path, [_tournament("t1"), _tournament("t2", date_str="2026-09-19", host="A")])
    problem = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": [{"club": club, "label": f"{club}1", "age_group": "U10"} for club in "ABCD"],
        "age_groups": ["U10"],
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
        "parallel_games": {"U10": 2},
        "club_calendar_status": {"A": "known"},
        "club_busy_intervals": {
            "A": [
                {
                    "date": "2026-09-12",
                    "start": "10:00",
                    "end": "12:00",
                    "kind": "external",
                    "availability": "fixed_busy",
                    "calendar_event": "Miniputt",
                }
            ]
        },
    }
    event_fp = calendar_booking_candidates(season="2026-2027", root=root, club="A", problem=problem)["booking_candidates"][0]["calendar_event"]["fingerprint"]
    with pytest.raises(SeasonStateError, match="date_mismatch"):
        confirm_calendar_booking(
            season="2026-2027",
            root=root,
            event_fingerprint=event_fp,
            tournament_id="t2",
            problem=problem,
        )

    confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fp,
        tournament_id="t1",
        problem=problem,
    )
    changed_problem = dict(problem)
    changed_problem["club_busy_intervals"] = {"A": []}
    findings = calendar_booking_findings(season="2026-2027", root=root, problem=changed_problem)
    assert findings["findings"][0]["code"] == "stale_calendar_booking_association"
    assert "event_missing" in findings["findings"][0]["reasons"]

    renamed_problem = dict(problem)
    renamed_problem["club_busy_intervals"] = {
        "A": [
            {
                "date": "2026-09-12",
                "start": "10:00",
                "end": "12:00",
                "kind": "external",
                "availability": "fixed_busy",
                "calendar_event": "Renamed Miniputt",
            }
        ]
    }
    findings = calendar_booking_findings(season="2026-2027", root=root, problem=renamed_problem)
    assert findings["findings"][0]["code"] == "stale_calendar_booking_association"
    assert "event_missing" in findings["findings"][0]["reasons"]


def test_approval_refuses_known_external_conflict(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": [
            {"club": club, "label": f"{club}1", "age_group": "U10"} for club in "ABCD"
        ],
        "age_groups": ["U10"],
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
        "parallel_games": {"U10": 2},
        "club_calendar_status": {"A": "known"},
        "club_busy_intervals": {
            "A": [
                {"date": "2026-09-12", "start": "10:00", "end": "12:00", "kind": "external"}
            ]
        },
    }
    with pytest.raises(SeasonStateError):
        approve_tournament(
            season="2026-2027", tournament_id="t1", root=root, actor="booker", problem=problem
        )

    # Approval does not suppress the conflict either: the same problem makes
    # final verification surface it for the operator.
    from tournament_scheduler.planning_contract import verify_candidate

    verification = verify_candidate(load_schedule("2026-2027", root=root)["plan"], problem)
    assert verification["manual_external_conflict_placements"][0]["tournament_id"] == "t1"


def test_cli_approve_honours_calendar_booking_confirmed_after_promotion(tmp_path):
    """``season approve`` (CLI) must see a calendar-booking association
    recorded after promotion, not just the frozen promotion-time problem.

    ``_canonical_verification_problem`` in ``cli/rvv_cli.py`` reads
    ``schedule.json``'s ``verification_context.problem`` -- a snapshot frozen
    at promotion -- to gate ``season approve``/``season move``. A calendar
    booking confirmed afterwards only lives in ``decisions.json`` and must be
    projected into that frozen problem the same way ``season export``
    already does (see the analogous holiday-date-exception regression),
    otherwise a real Jar-confirmed booking is refused forever as an
    "external calendar conflict" the operator already resolved.
    """
    problem = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "teams": [{"club": club, "label": f"{club}1", "age_group": "U10"} for club in "ABCD"],
        "age_groups": ["U10"],
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
        "parallel_games": {"U10": 2},
        "club_calendar_status": {"A": "known"},
        "club_busy_intervals": {
            "A": [
                {
                    "date": "2026-09-12",
                    "start": "10:00",
                    "end": "12:00",
                    "kind": "external",
                    "availability": "fixed_busy",
                    "calendar_event": "Booking - Serieturnering U10",
                }
            ]
        },
    }
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    state.write_stage(StageName.PLANNING, {"plan": _plan([_tournament("t1")])}, status=StageStatus.DONE)
    write_reviewed_stage4_export(state, problem=problem)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")

    event_fp = calendar_booking_candidates(season="2026-2027", root=root, club="A", problem=problem)[
        "booking_candidates"
    ][0]["calendar_event"]["fingerprint"]
    confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fp,
        tournament_id="t1",
        actor="tester",
        note="Jar confirmed this booking",
        problem=problem,
    )

    from tournament_scheduler.cli.rvv_cli import main as cli_main

    exit_code = cli_main(
        [
            "season",
            "approve",
            "--season",
            "2026-2027",
            "--tournament-id",
            "t1",
            "--root",
            str(root),
            "--actor",
            "tester",
        ]
    )

    assert exit_code == 0
    assert load_decisions("2026-2027", root=root)["decisions"]["t1"]["status"] == "approved"


def test_changed_approval_is_deterministic_stale_approval(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    approve_tournament(season="2026-2027", tournament_id="t1", root=root, actor="booker")
    old_fingerprint = load_decisions("2026-2027", root=root)["decisions"]["t1"]["approved_fingerprint"]

    # A legacy/out-of-band mutation that bypasses the unapprove flow.
    schedule_path = root / "2026-2027" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    schedule["plan"]["tournaments"][0]["date"] = "2026-09-19"
    schedule_path.write_text(json.dumps(schedule), encoding="utf-8")

    report = approval_report("2026-2027", root=root)
    assert report["counts"]["stale"] == 1
    assert report["counts"]["approved"] == 0
    stale = report["stale_approvals"][0]
    assert stale["code"] == "stale_approval"
    assert stale["tournament_id"] == "t1"
    assert stale["approved_fingerprint"] == old_fingerprint

    # The stale lock is dropped: a legacy-changed tournament is not silently
    # kept frozen, but the state is still surfaced rather than reported
    # approved.
    baseline = resolve_canonical_baseline({}, date(2026, 9, 1), date(2027, 4, 30), root=root)
    assert baseline["locks"] == {}
    assert baseline["stale_approvals"][0]["tournament_id"] == "t1"
    assert len(baseline["approvals"]) == 1
    assert baseline["approvals"]["t1"]["status"] == "stale_approval"


def test_reapprove_after_change_records_new_fingerprint(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    approve_tournament(season="2026-2027", tournament_id="t1", root=root, actor="booker")
    first_fingerprint = load_decisions("2026-2027", root=root)["decisions"]["t1"]["approved_fingerprint"]

    unapprove_tournament(season="2026-2027", tournament_id="t1", root=root, actor="booker")
    move_tournament(season="2026-2027", tournament_id="t1", root=root, date="2026-09-19")
    approve_tournament(season="2026-2027", tournament_id="t1", root=root, actor="booker")

    decisions = load_decisions("2026-2027", root=root)
    record = decisions["decisions"]["t1"]
    assert record["status"] == "approved"
    assert record["approved_fingerprint"] != first_fingerprint
    events = [entry["event"] for entry in decisions["history"]]
    assert events.count("approve") == 2
    assert "unapprove" in events


def test_apply_candidate_marks_changed_unlocked_approval_stale(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    # Approval without any lock: the operator accepted the placement but an
    # automatic replan may still change it.
    approve_tournament(
        season="2026-2027",
        tournament_id="t1",
        root=root,
        actor="booker",
        placement_locked=False,
        participants_locked=False,
    )

    candidate = _plan([_tournament("t1", date_str="2026-09-19")])
    _, decisions, _ = apply_candidate(season="2026-2027", candidate=candidate, root=root)

    record = decisions["decisions"]["t1"]
    assert record["status"] == "stale_approval"
    assert record["approved_fingerprint"] is not None
    assert record["stale_reason"]


def test_approved_tournament_still_participates_in_collision_verification(tmp_path):
    root = _promote(tmp_path, [_tournament("t1"), _tournament("t2", date_str="2026-10-10", host="E")])
    approve_tournament(season="2026-2027", tournament_id="t1", root=root, actor="booker")
    baseline = resolve_canonical_baseline({}, date(2026, 9, 1), date(2027, 4, 30), root=root)

    # A candidate that keeps t1 in place but creates a duplicate same-day
    # participation for a t1 team is still a hard violation: approval is not
    # a correctness waiver.
    candidate = _plan(
        [
            _tournament("t1"),
            _tournament("t3", date_str="2026-09-12", host="B", teams=_teams(("A", "B", "C", "E"))),
        ]
    )
    violations = verify_canonical_locks(baseline, candidate)
    assert violations == []


def test_full_lifecycle_approve_replan_unapprove_move_reapprove(tmp_path):
    from tournament_scheduler.canonical_baseline import approval_fingerprint
    from tournament_scheduler.canonical_replan import replan_around_baseline

    first = _teams(("A", "B", "C", "D"))
    second = [
        {"club": club, "label": f"{club}1", "age_group": "U10"} for club in ("E", "F", "G", "H")
    ]
    t1 = _tournament("t1", host="A", teams=first)
    t2 = _tournament("t2", date_str="2026-10-10", arena="Arena B", host="E", teams=second)
    root = _promote(tmp_path, [t1, t2])

    approve_tournament(
        season="2026-2027",
        tournament_id="t1",
        root=root,
        actor="booker",
        placement_locked=True,
        participants_locked=True,
    )
    approved_fingerprint = load_decisions("2026-2027", root=root)["decisions"]["t1"]["approved_fingerprint"]
    protected_before = load_schedule("2026-2027", root=root)["plan"]["tournaments"][0]

    config = {"teams": first + second, "parallel_games": {"U10": 2}}
    result = replan_around_baseline(
        season="2026-2027",
        config=config,
        scraping_result=None,
        start_date=date(2026, 9, 1),
        end_date=date(2027, 4, 30),
        root=root,
        engine="local_search",
        request={"iterations": 200, "seed": 7},
    )
    assert result["lock_violations"] == []
    assert result["verification"]["ok"], result["verification"]["violations"]
    schedule, decisions, _ = apply_candidate(
        season="2026-2027", candidate=result["candidate"], root=root, problem=result["problem"]
    )

    protected_after = next(t for t in schedule["plan"]["tournaments"] if t["id"] == "t1")
    # Protected fields (the normalized approval fingerprint) are unchanged by
    # replanning around the approval, even though semantically irrelevant
    # team/game ordering may differ.
    assert approval_fingerprint(protected_after) == approved_fingerprint
    for field in ("date", "arena", "host_club", "start_time", "age_group"):
        assert protected_after[field] == protected_before[field], field
    assert {team["label"] for team in protected_after["teams"]} == {
        team["label"] for team in protected_before["teams"]
    }
    assert decisions["decisions"]["t1"]["status"] == "approved"
    assert decisions["decisions"]["t1"]["approved_fingerprint"] == approved_fingerprint

    # Unapproved tournaments remain optimizable / editable.
    unapprove_tournament(season="2026-2027", tournament_id="t1", root=root, actor="booker")
    moved = move_tournament(
        season="2026-2027", tournament_id="t1", root=root, date="2026-09-26"
    )
    assert next(t for t in moved["plan"]["tournaments"] if t["id"] == "t1")["date"] == "2026-09-26"

    approve_tournament(season="2026-2027", tournament_id="t1", root=root, actor="booker")
    new_fingerprint = load_decisions("2026-2027", root=root)["decisions"]["t1"]["approved_fingerprint"]
    assert new_fingerprint != approved_fingerprint


def test_season_approvals_and_unapprove_cli(tmp_path, capsys):
    from tournament_scheduler.cli.rvv_cli import main

    root = _promote(tmp_path, [_tournament("t1")])
    work_dir = str(tmp_path / ".pipeline")
    assert main(
        [
            "season", "approve",
            "--season", "2026-2027",
            "--tournament-id", "t1",
            "--root", str(root),
            "--work-dir", work_dir,
            "--actor", "booker",
        ]
    ) == 0
    capsys.readouterr()

    rc = main(["season", "approvals", "--season", "2026-2027", "--root", str(root), "--json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["counts"]["approved"] == 1
    assert report["tournaments"][0]["status"] == "approved"

    rc = main(
        [
            "season", "status",
            "--season", "2026-2027",
            "--root", str(root),
            "--json",
        ]
    )
    assert rc == 0
    status = json.loads(capsys.readouterr().out)
    assert status["approved_count"] == 1
    assert status["stale_approval_count"] == 0

    rc = main(
        [
            "season", "unapprove",
            "--season", "2026-2027",
            "--tournament-id", "t1",
            "--root", str(root),
            "--note", "rebooked",
        ]
    )
    assert rc == 0
    capsys.readouterr()
    assert load_decisions("2026-2027", root=root)["decisions"]["t1"]["status"] == "pending_review"


def test_baseline_and_approval_report_expose_counts(tmp_path):
    root = _promote(tmp_path, [_tournament("t1"), _tournament("t2", date_str="2026-10-10", host="E")])
    approve_tournament(season="2026-2027", tournament_id="t1", root=root, actor="booker")

    report = approval_report("2026-2027", root=root)
    assert report["counts"] == {
        "total": 2,
        "approved": 1,
        "stale": 0,
        "orphaned": 0,
        "locked": 1,
        "pending_review": 1,
    }

    baseline = build_canonical_baseline(
        load_schedule("2026-2027", root=root), load_decisions("2026-2027", root=root)
    )
    assert baseline["approval_summary"]["approved_count"] == 1
    assert baseline["approvals"]["t1"]["status"] == "approved"
    assert set(baseline["locks"]) == {"t1"}


def test_export_surfaces_approval_status_in_operator_html(tmp_path):
    from tournament_scheduler.html.html_exporter import HtmlExporter
    from tournament_scheduler.serialization.season_plan import season_plan_from_dict

    root = _promote(tmp_path, [_tournament("t1")])
    approve_tournament(season="2026-2027", tournament_id="t1", root=root, actor="booker")
    schedule = load_schedule("2026-2027", root=root)
    report = approval_report("2026-2027", root=root)

    path = tmp_path / "season_plan.html"
    HtmlExporter().export(
        season_plan_from_dict(schedule["plan"]),
        str(path),
        pipeline_meta={"approval_status": report, "canonical_revision": schedule["revision"]},
    )
    html = path.read_text(encoding="utf-8")
    assert "1 godkjent" in html
    assert '"ap": "approved"' in html

    # The run evidence bundle carries the same approval status for the audit
    # trail (which tournaments are approved, and whether fingerprints match).
    from tournament_scheduler.pipeline.evidence_bundle import build_final_operator_evidence

    evidence = build_final_operator_evidence(
        run_id="run-1",
        plan_dict=schedule["plan"],
        final_candidate_fingerprint="fp",
        export_fingerprint="fp",
        final_verify_result={"ok": True},
        approval_status=report,
    )
    assert evidence["approval_status"]["counts"]["approved"] == 1


def test_season_move_cli_rejects_locked_and_allows_after_unapprove(tmp_path, capsys):
    from tournament_scheduler.cli.rvv_cli import main

    root = _promote(tmp_path, [_tournament("t1")])
    work_dir = str(tmp_path / ".pipeline")
    assert main(
        [
            "season", "approve",
            "--season", "2026-2027",
            "--tournament-id", "t1",
            "--root", str(root),
            "--work-dir", work_dir,
        ]
    ) == 0
    capsys.readouterr()

    rc = main(
        [
            "season", "move",
            "--season", "2026-2027",
            "--tournament-id", "t1",
            "--root", str(root),
            "--work-dir", work_dir,
            "--date", "2026-09-19",
        ]
    )
    assert rc == 1
    assert "unapprove" in capsys.readouterr().out

    assert main(
        [
            "season", "unapprove",
            "--season", "2026-2027",
            "--tournament-id", "t1",
            "--root", str(root),
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "season", "move",
            "--season", "2026-2027",
            "--tournament-id", "t1",
            "--root", str(root),
            "--work-dir", work_dir,
            "--date", "2026-09-19",
        ]
    ) == 0
    assert load_schedule("2026-2027", root=root)["plan"]["tournaments"][0]["date"] == "2026-09-19"
