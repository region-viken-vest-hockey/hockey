"""End-to-end approval/lock lifecycle tests."""

from __future__ import annotations

import copy
import json
from datetime import date

import pytest

from tournament_scheduler.calendar_bookings import event_fingerprint
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
    clear_manual_booking_assertion,
    confirm_calendar_booking,
    reconcile_calendar_bookings,
    load_decisions,
    release_calendar_booking,
    load_schedule,
    move_tournament,
    promote_from_stage3,
    set_manual_booking_assertion,
    swap_participants,
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


def _rename_team(tournament, old_label, new_label):
    """Return a placement-identical copy with one roster identity renamed.

    Renames the team and every game reference so the change is a genuine
    participant-only mutation (no placement field or game structure change).
    """
    changed = copy.deepcopy(tournament)
    for team in changed.get("teams", []) or []:
        if team.get("label") == old_label:
            team["label"] = new_label
    for game in changed.get("games", []) or []:
        if game.get("home") == old_label:
            game["home"] = new_label
        if game.get("away") == old_label:
            game["away"] = new_label
    return changed


def _scoped_approval_record(tournament, *, participants_locked=False):
    from tournament_scheduler.canonical_baseline import (
        approval_fingerprint,
        approval_placement_fingerprint,
    )

    return {
        "status": "approved",
        "placement_locked": True,
        "participants_locked": participants_locked,
        "approved_fingerprint": approval_fingerprint(tournament),
        "approved_placement_fingerprint": approval_placement_fingerprint(tournament),
        "approved_at": "2026-09-15T00:00:00+00:00",
        "approved_by": "booker",
        "note": "",
    }


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


def test_club_reconciliation_records_candidate_evidence_and_stales_on_move(tmp_path):
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
    assert statuses == {"t1": "ambiguous", "t2": "ambiguous"}
    reasons = {row["tournament_id"]: row["reason"] for row in result["classified"]}
    assert reasons["t1"] == "single_overlapping_event_requires_confirmation"
    assert reasons["t2"] == "no_covering_event_for_current_slot"
    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    assert {row["tournament_id"]: row["status"] for row in report["tournaments"]} == statuses
    assert report["counts"]["confirmed_booked"] == 0
    assert report["counts"]["confirmed_not_booked"] == 0
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


def _export_html_with_booking_report(root, problem, tmp_path):
    """Export the season HTML fed by the canonical booking-status projection."""

    from tournament_scheduler.html.html_exporter import HtmlExporter
    from tournament_scheduler.serialization.season_plan import season_plan_from_dict

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    plan = season_plan_from_dict(load_schedule("2026-2027", root=root)["plan"])
    out_path = tmp_path / "season_plan.html"
    HtmlExporter().export(plan, out_path, pipeline_meta={"booking_status": report})
    return out_path.read_text(encoding="utf-8")


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


@pytest.mark.parametrize(
    "approval_note",
    [
        "A confirmed this booking via their change sheet",
        "Placement approved from organizer sheet",
        "Placement approved; not confirmed booking",
    ],
)
def test_reconcile_preserves_approved_placement_without_calendar_event(tmp_path, approval_note):
    """Calendar absence is follow-up evidence, not cancellation of an approved placement."""

    root = _promote(tmp_path, [_tournament("t1")])
    approve_tournament(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        actor="booker",
        note=approval_note,
    )
    problem = _host_a_problem([])

    result = reconcile_calendar_bookings(
        season="2026-2027", root=root, club="A", note="reviewed complete host calendar", problem=problem
    )
    row = result["classified"][0]
    assert row["status"] == "ambiguous"
    assert row["reason"] == "approved_placement_without_calendar_evidence"

    report, heatmap_items = _report_and_heatmap(root, problem)
    assert report["tournaments"][0]["status"] == "ambiguous"
    assert report["counts"]["confirmed_not_booked"] == 0
    assert heatmap_items["t1"]["booking_status"] == "ambiguous"

    decision = load_decisions("2026-2027", root=root)["decisions"]["t1"]
    assert decision["status"] == "approved"
    assert decision["placement_locked"] is True


def test_reconcile_keeps_explicit_association_confirmed_and_absence_ambiguous(tmp_path):
    """Only an explicit association makes a match confirmed_booked.

    A trustworthy calendar with no event at ``t2``'s canonical slot is an
    observation about that slot, not evidence that ``t2`` is unbooked.
    """

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
    assert statuses == {"t1": "confirmed_booked", "t2": "ambiguous"}
    reasons = {row["tournament_id"]: row["reason"] for row in result["classified"]}
    assert reasons["t1"] == "explicit_calendar_booking_association"
    assert reasons["t2"] == "no_covering_event_for_current_slot"

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    assert {row["tournament_id"]: row["status"] for row in report["tournaments"]} == statuses
    assert report["counts"]["confirmed_booked"] == 1
    assert report["counts"]["confirmed_not_booked"] == 0


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


def test_zero_overlap_never_projects_ikke_booket(tmp_path):
    """The production shape: a pending tournament absent from a trusted calendar.

    Zero overlap at the current canonical slot is an observation about that
    slot. A changed slot, an explicit club confirmation or incomplete source
    attribution may still mean the tournament is booked, so the classifier and
    the booking-status/heatmap/HTML projection must never turn it into
    ``confirmed_not_booked``.
    """

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])

    result = reconcile_calendar_bookings(
        season="2026-2027", root=root, club="A", note="reviewed complete host calendar", problem=problem
    )
    row = result["classified"][0]
    assert row["status"] == "ambiguous"
    assert row["reason"] == "no_covering_event_for_current_slot"

    report, heatmap_items = _report_and_heatmap(root, problem)
    assert report["tournaments"][0]["status"] == "ambiguous"
    assert report["counts"]["confirmed_not_booked"] == 0
    assert heatmap_items["t1"]["booking_status"] == "ambiguous"

    html = _export_html_with_booking_report(root, problem, tmp_path)
    assert '"bs": "ambiguous"' in html
    assert '"bs": "confirmed_not_booked"' not in html


def test_pending_non_overlapping_replacement_slot_stays_unresolved(tmp_path):
    """A booking moved to a different time must not become IKKE BOOKET.

    The canonical slot (10:00-12:00) is absent from the calendar while the
    club change sheet moved it to 13:00-14:50. The replacement window is not an
    automatic association, so the tournament stays ambiguous rather than being
    declared unbooked.
    """

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem(
        [
            {
                "date": "2026-09-12",
                "start": "13:00",
                "end": "14:50",
                "availability": "fixed_busy",
                "calendar_event": "Miniputt U10 (flyttet)",
            }
        ]
    )

    result = reconcile_calendar_bookings(
        season="2026-2027", root=root, club="A", note="club change sheet", problem=problem
    )
    row = result["classified"][0]
    assert row["status"] == "ambiguous"
    assert row["reason"] == "no_covering_event_for_current_slot"
    # The non-overlapping replacement window is not silently bound to t1.
    assert not row["event_fingerprint"]

    report, heatmap_items = _report_and_heatmap(root, problem)
    assert report["counts"]["confirmed_not_booked"] == 0
    assert heatmap_items["t1"]["booking_status"] == "ambiguous"


def test_projection_distinguishes_explicit_negative_from_absence(tmp_path):
    """Only explicit negative evidence may project as ``confirmed_not_booked``.

    A persisted record whose reason is current-slot absence is an observation,
    not a booking conclusion, so the projection downgrades it to ambiguity. A
    record carrying explicit operator/source negative evidence stays
    authoritative. Historical evidence is preserved either way.
    """

    from tournament_scheduler.calendar_bookings import TOURNAMENT_BOOKING_EVIDENCE_KEY

    root = _promote(tmp_path, [_tournament("t1"), _tournament("t2", date_str="2026-09-19")])
    problem = _host_a_problem([])
    reconcile_calendar_bookings(
        season="2026-2027", root=root, club="A", note="absence review", problem=problem
    )

    # Simulate one legacy absence-derived negative alongside one genuinely
    # explicit negative through the persisted evidence boundary.
    decisions_path = root / "2026-2027" / "decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    evidence = {row["tournament_id"]: row for row in decisions[TOURNAMENT_BOOKING_EVIDENCE_KEY]}
    evidence["t1"]["status"] = "confirmed_not_booked"
    evidence["t1"]["reason"] = "no_overlapping_event_in_trustworthy_calendar"
    evidence["t2"]["status"] = "confirmed_not_booked"
    evidence["t2"]["reason"] = "explicit_club_rejected_booking"
    decisions_path.write_text(json.dumps(decisions), encoding="utf-8")

    report, heatmap_items = _report_and_heatmap(root, problem)
    statuses = {row["tournament_id"]: row["status"] for row in report["tournaments"]}
    assert statuses == {"t1": "ambiguous", "t2": "confirmed_not_booked"}
    t1 = next(row for row in report["tournaments"] if row["tournament_id"] == "t1")
    assert "absence_only_negative_booking_requires_review" in t1["stale_reasons"]
    assert t1["evidence"]["reason"] == "no_overlapping_event_in_trustworthy_calendar"
    assert t1["evidence"]["status"] == "confirmed_not_booked"
    assert report["counts"]["confirmed_not_booked"] == 1
    assert heatmap_items["t1"]["booking_status"] == "ambiguous"
    assert heatmap_items["t2"]["booking_status"] == "confirmed_not_booked"


def test_partial_source_overlap_remains_candidate_and_reconciliation_ambiguous(tmp_path):
    """Coverage-incomplete calendars can supply positive candidates without proving absence."""
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem(
        [
            {
                "date": "2026-09-12",
                "start": "10:00",
                "end": "12:00",
                "availability": "fixed_busy",
                "calendar_event": "Miniputt U10",
            }
        ]
    )
    problem["club_calendar_status"] = {"A": "source_review_required"}
    problem["club_source_integrity"] = {"A": "partial"}
    problem["club_coverage_proven"] = {"A": False}

    candidates = calendar_booking_candidates(season="2026-2027", root=root, club="A", problem=problem)
    assert len(candidates["booking_candidates"]) == 1
    assert candidates["booking_candidates"][0]["candidate_tournaments"][0]["id"] == "t1"
    assert candidates["booking_candidates"][0]["calendar_event"]["source_positive_evidence_usable"] is True

    result = reconcile_calendar_bookings(season="2026-2027", root=root, club="A", problem=problem)
    row = result["classified"][0]
    assert row["status"] == "ambiguous"
    assert row["reason"] == "single_overlapping_event_requires_confirmation"
    assert row["source_integrity_concern"] is True
    assert row["source_positive_evidence_usable"] is True
    assert "event_evidence_usable" in row["evidence_reasons"]


def test_unrelated_calendar_change_does_not_stale_event_level_booking_evidence(tmp_path):
    """A club feed hash change is not actionable when the matched event survives."""

    root = _promote(tmp_path, [_tournament("t1")])
    booking_event = {
        "date": "2026-09-12",
        "start": "10:00",
        "end": "12:00",
        "availability": "fixed_busy",
        "calendar_event": "Miniputt U10",
    }
    original_problem = _host_a_problem([booking_event])
    reconcile_calendar_bookings(
        season="2026-2027",
        root=root,
        club="A",
        note="record matched calendar evidence",
        problem=original_problem,
    )
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        booking_status="booked",
        actor="clubrep",
        note="club also confirmed the same booking",
        reference="email:booking",
        problem=original_problem,
    )

    refreshed_problem = _host_a_problem(
        [
            booking_event,
            {
                "date": "2026-09-13",
                "start": "08:00",
                "end": "09:00",
                "availability": "fixed_busy",
                "calendar_event": "Unrelated practice",
            },
        ]
    )
    report = booking_status_report(season="2026-2027", root=root, problem=refreshed_problem)
    row = report["tournaments"][0]

    assert row["status"] == "manually_booked"
    assert row["calendar_status"] == "ambiguous"
    assert row["calendar_stale_reasons"] == []
    assert "calendar_evidence_changed" not in row["follow_up_reasons"]
    assert row["needs_attention"] is False


def test_partial_source_without_overlap_does_not_record_negative_evidence(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    problem["club_calendar_status"] = {"A": "source_review_required"}
    problem["club_source_integrity"] = {"A": "partial"}
    problem["club_coverage_proven"] = {"A": False}

    result = reconcile_calendar_bookings(season="2026-2027", root=root, club="A", problem=problem)
    row = result["classified"][0]
    assert row["status"] == "ambiguous"
    assert row["reason"] == "source_coverage_unproven"
    assert row["source_positive_evidence_usable"] is True
    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    assert report["tournaments"][0]["status"] == "ambiguous"
    assert report["counts"]["confirmed_not_booked"] == 0


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


def test_confirm_calendar_booking_aligns_canonical_interval_to_authoritative_event(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    event = {
        "date": "2026-09-12",
        "start": "11:00",
        "end": "12:30",
        "availability": "fixed_busy",
        "calendar_event": "Miniputt U10 bekreftet",
        "club": "A",
    }
    problem = _host_a_problem([event])
    event_fp = event_fingerprint(event)

    result = confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fp,
        tournament_id="t1",
        problem=problem,
        note="Accepted authoritative calendar interval",
    )

    alignment = result["interval_alignment"]
    assert alignment["previous_interval"] == {
        "date": "2026-09-12",
        "start_time": "10:00",
        "duration_minutes": "120",
        "end_time": "12:00",
        "age_group": "U10",
        "round_count": "1",
    }
    assert alignment["accepted_calendar_interval"] == {
        "date": "2026-09-12",
        "start_time": "11:00",
        "duration_minutes": 90,
        "end_time": "12:30",
    }
    assert result["ice_time_override"]["minutes"] == 90
    assert [warning["code"] for warning in result["booking_feasibility_warnings"]] == [
        "ice_time_governing_minimum"
    ]
    # The warning must originate from ``verify_candidate``'s accepted-interval
    # path (the effective occupancy comes from the accepted interval override),
    # not an apply-time fallback that reclassifies an unmatched hard blocker.
    assert all(
        warning.get("accepted_booking_interval")
        for warning in result["booking_feasibility_warnings"]
    )

    schedule = load_schedule("2026-2027", root=root)["plan"]
    tournament = next(t for t in schedule["tournaments"] if t["id"] == "t1")
    assert tournament["start_time"] == "11:00"
    decisions = load_decisions("2026-2027", root=root)
    assert decisions["ice_time_minutes_overrides"][-1]["minutes"] == 90
    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = report["tournaments"][0]
    assert row["status"] == "confirmed_booked"
    assert row["operational_state"] == "booked"

    # The original complete interval stays in the canonical history and replays
    # even when the accepted event shortened the booking.
    history = load_decisions("2026-2027", root=root)["history"]
    confirm_entry = next(
        entry for entry in history if entry.get("event") == "confirm_calendar_booking"
    )
    assert confirm_entry["details"]["interval_alignment"]["previous_interval"] == {
        "date": "2026-09-12",
        "start_time": "10:00",
        "duration_minutes": "120",
        "end_time": "12:00",
        "age_group": "U10",
        "round_count": "1",
    }
    from tournament_scheduler.published_mutation_history import replay_recorded_mutations

    seed = {
        "t1": {
            "id": "t1",
            "date": "2026-09-12",
            "start_time": "10:00",
            "duration_minutes": 120,
            "end_time": "12:00",
            "arena": "Arena A",
            "host_club": "A",
            "age_group": "U10",
            "participants": [],
        }
    }
    projection, applied = replay_recorded_mutations(seed, history)
    assert projection["t1"]["start_time"] == "11:00"
    assert projection["t1"]["duration_minutes"] == 90
    assert projection["t1"]["end_time"] == "12:30"
    assert any(item["event"] == "confirm_calendar_booking" for item in applied)


def test_confirm_calendar_booking_uses_previous_override_in_interval_evidence(tmp_path):
    """A prior override is the previous effective interval, not the age default."""
    root = _promote(tmp_path, [_tournament("t1")])
    base_problem = _host_a_problem([])
    from tournament_scheduler.season_state import set_ice_time_minutes

    set_ice_time_minutes(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        minutes=150,
        request_id="host:prior-window",
        note="host confirmed a 150-minute window",
        problem=base_problem,
    )
    event = {
        "date": "2026-09-12",
        "start": "11:00",
        "end": "12:00",
        "availability": "fixed_busy",
        "calendar_event": "Miniputt U10",
        "club": "A",
    }
    problem = _host_a_problem([event])
    event_fp = event_fingerprint(event)

    result = confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fp,
        tournament_id="t1",
        problem=problem,
        note="Accepted authoritative calendar interval",
    )

    assert result["interval_alignment"]["previous_interval"]["duration_minutes"] == "150"
    assert result["interval_alignment"]["previous_interval"]["end_time"] == "12:30"


def test_confirm_calendar_booking_refuses_unusable_source_evidence(tmp_path):
    """Direct confirmation must re-apply the positive-evidence gate at the boundary."""
    root = _promote(tmp_path, [_tournament("t1")])
    event = {
        "date": "2026-09-12",
        "start": "10:00",
        "end": "12:00",
        "availability": "fixed_busy",
        "calendar_event": "Miniputt U10",
        "club": "A",
    }
    for status in ("unknown", "untrusted"):
        problem = _host_a_problem([event])
        problem["club_calendar_status"] = {"A": status}
        event_fp = event_fingerprint(event)
        with pytest.raises(SeasonStateError, match="cannot support positive evidence"):
            confirm_calendar_booking(
                season="2026-2027",
                root=root,
                event_fingerprint=event_fp,
                tournament_id="t1",
                problem=problem,
            )
    # No canonical approval happened for a refused source.
    schedule = load_schedule("2026-2027", root=root)["plan"]
    assert next(t for t in schedule["tournaments"] if t["id"] == "t1")["start_time"] == "10:00"


def test_confirm_calendar_booking_refuses_fabricated_source_evidence(tmp_path):
    """A fabricated placeholder calendar cannot be promoted by direct confirmation."""
    root = _promote(tmp_path, [_tournament("t1")])
    fabricated = [
        {
            "date": f"2026-09-{day:02d}",
            "start": "00:00",
            "end": "01:00",
            "availability": "fixed_busy",
            "calendar_event": f"Jutul U{day % 8}",
            "club": "A",
        }
        for day in range(1, 26)
    ]
    problem = _host_a_problem(fabricated)
    target = next(event for event in fabricated if event["date"] == "2026-09-12")
    with pytest.raises(SeasonStateError, match="cannot support positive evidence"):
        confirm_calendar_booking(
            season="2026-2027",
            root=root,
            event_fingerprint=event_fingerprint(target),
            tournament_id="t1",
            problem=problem,
        )


def test_confirm_calendar_booking_refuses_cancelled_tournament_atomically(tmp_path):
    """A cancelled tournament is not an active placement.

    The candidate verifier excludes cancelled tournaments, so without a
    lifecycle guard ``confirm_calendar_booking`` writes an ``approved``/
    ``placement_locked`` record the verifier never sees, which
    ``canonical_locked_tournament_missing`` then reports as a hard violation.
    The refused call must leave the approval/association/evidence overlay
    byte-identical rather than retaining rejected side effects.
    """
    root = _promote(tmp_path, [_tournament("t1")])
    schedule_path = root / "2026-2027" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    schedule["plan"]["tournaments"][0]["cancelled"] = True
    schedule["plan"]["tournaments"][0]["cancellation_reason"] = "arena unavailable"
    schedule_path.write_text(json.dumps(schedule), encoding="utf-8")

    event = {
        "date": "2026-09-12",
        "start": "10:00",
        "end": "12:00",
        "availability": "fixed_busy",
        "calendar_event": "Miniputt U10 bekreftet",
        "club": "A",
    }
    problem = _host_a_problem([event])
    event_fp = event_fingerprint(event)
    before = load_decisions("2026-2027", root=root)

    with pytest.raises(SeasonStateError, match="cancelled and cannot be confirmed"):
        confirm_calendar_booking(
            season="2026-2027",
            root=root,
            event_fingerprint=event_fp,
            tournament_id="t1",
            problem=problem,
            note="must not be written",
        )

    after = load_decisions("2026-2027", root=root)
    assert after["decisions"] == before["decisions"]
    for key in (
        "calendar_booking_associations",
        "manual_booking_assertions",
        "tournament_booking_evidence",
        "rejected_booking_evidence",
    ):
        assert after.get(key) == before.get(key)
    schedule_after = load_schedule("2026-2027", root=root)["plan"]["tournaments"][0]
    assert schedule_after["start_time"] == "10:00"
    assert schedule_after["cancelled"] is True


def test_confirm_calendar_booking_rejection_leaves_approval_untouched(tmp_path):
    """A hard-blocked confirmation must not write association/evidence/approval.

    The blockers branch retains separately specified rejected-source evidence,
    but the active placement, approval/lock and association must be committed
    only after validation succeeds.
    """
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
                    "calendar_event": "Serieturneringer U10",
                }
            ]
        },
    }
    event_fp = calendar_booking_candidates(season="2026-2027", root=root, club="A", problem=problem)[
        "booking_candidates"
    ][0]["calendar_event"]["fingerprint"]
    before = load_decisions("2026-2027", root=root)

    with pytest.raises(SeasonStateError, match="scheduled in 2 tournaments"):
        confirm_calendar_booking(
            season="2026-2027",
            root=root,
            event_fingerprint=event_fp,
            tournament_id="t2",
            problem=problem,
            note="rejected attempt must not approve",
        )

    after = load_decisions("2026-2027", root=root)
    assert after["decisions"] == before["decisions"]
    assert after.get("calendar_booking_associations") == before.get("calendar_booking_associations")
    assert after.get("tournament_booking_evidence") == before.get("tournament_booking_evidence")
    # The refused source is still retained as unresolved evidence by design.
    assert after["rejected_booking_evidence"][-1]["tournament_id"] == "t2"


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


def test_calendar_booking_association_uses_accepted_override_when_default_duration_grows(tmp_path):
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
    assert findings["findings"] == []

    from tournament_scheduler.calendar_bookings import project_associations_into_problem
    from tournament_scheduler.canonical_ice_time_overrides import project_overrides_into_problem
    from tournament_scheduler.planning_contract import verify_candidate

    plan = load_schedule("2026-2027", root=root)["plan"]
    decisions = load_decisions("2026-2027", root=root)
    projected = project_overrides_into_problem(migrated_problem, decisions)
    projected = project_associations_into_problem(projected, decisions, plan)
    verification = verify_candidate(plan, projected)
    assert verification["manual_external_conflict_placements"] == []


def test_calendar_booking_confirmation_aligns_partial_overlap_to_authoritative_interval(tmp_path):
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
    result = confirm_calendar_booking(season="2026-2027", root=root, event_fingerprint=event_fp, tournament_id="t1", problem=problem)
    assert result["interval_alignment"]["accepted_calendar_interval"] == {
        "date": "2026-09-12",
        "start_time": "11:00",
        "duration_minutes": 120,
        "end_time": "13:00",
    }
    tournament = load_schedule("2026-2027", root=root)["plan"]["tournaments"][0]
    assert tournament["start_time"] == "11:00"


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
    with pytest.raises(SeasonStateError, match="scheduled in 2 tournaments"):
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


def test_placement_only_approval_survives_roster_change_at_owner_boundary():
    from tournament_scheduler.canonical_baseline import resolve_approval

    tournament = _tournament("t1")
    record = _scoped_approval_record(tournament, participants_locked=False)

    resolved = resolve_approval(record, _rename_team(tournament, "D1", "D9"))

    assert resolved["stale"] is False
    assert resolved["status"] == "approved"
    assert resolved["placement_locked"] is True
    assert resolved["participants_locked"] is False


def test_placement_only_approval_stales_on_placement_change_at_owner_boundary():
    from tournament_scheduler.canonical_baseline import resolve_approval

    tournament = _tournament("t1")
    record = _scoped_approval_record(tournament, participants_locked=False)
    moved = copy.deepcopy(tournament)
    moved["date"] = "2026-09-19"

    resolved = resolve_approval(record, moved)

    assert resolved["stale"] is True
    assert resolved["status"] == "stale_approval"
    assert resolved["placement_locked"] is False


def test_participant_locked_approval_stales_on_roster_change_at_owner_boundary():
    from tournament_scheduler.canonical_baseline import resolve_approval

    tournament = _tournament("t1")
    record = _scoped_approval_record(tournament, participants_locked=True)

    resolved = resolve_approval(record, _rename_team(tournament, "D1", "D9"))

    assert resolved["stale"] is True
    assert resolved["status"] == "stale_approval"
    assert resolved["participants_locked"] is False


def test_legacy_approval_without_placement_scope_keeps_full_payload_staleness():
    from tournament_scheduler.canonical_baseline import (
        approval_fingerprint,
        resolve_approval,
    )

    tournament = _tournament("t1")
    # A pre-scope approval record stored only the full protected fingerprint.
    legacy = {
        "status": "approved",
        "placement_locked": True,
        "participants_locked": False,
        "approved_fingerprint": approval_fingerprint(tournament),
        "approved_at": "2026-09-15T00:00:00+00:00",
        "approved_by": "booker",
        "note": "",
    }

    resolved = resolve_approval(legacy, _rename_team(tournament, "D1", "D9"))

    assert resolved["stale"] is True
    assert resolved["status"] == "stale_approval"


def test_placement_only_approval_survives_out_of_band_roster_change(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    approve_tournament(
        season="2026-2027",
        tournament_id="t1",
        root=root,
        actor="booker",
        placement_locked=True,
        participants_locked=False,
    )

    schedule_path = root / "2026-2027" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    schedule["plan"]["tournaments"][0] = _rename_team(
        schedule["plan"]["tournaments"][0], "D1", "D9"
    )
    schedule_path.write_text(json.dumps(schedule), encoding="utf-8")

    report = approval_report("2026-2027", root=root)
    assert report["counts"]["stale"] == 0
    assert report["counts"]["approved"] == 1
    entry = report["tournaments"][0]
    assert entry["status"] == "approved"
    assert entry["placement_locked"] is True


def test_participant_locked_approval_stales_on_out_of_band_roster_change(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    approve_tournament(
        season="2026-2027",
        tournament_id="t1",
        root=root,
        actor="booker",
        placement_locked=True,
        participants_locked=True,
    )

    schedule_path = root / "2026-2027" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    schedule["plan"]["tournaments"][0] = _rename_team(
        schedule["plan"]["tournaments"][0], "D1", "D9"
    )
    schedule_path.write_text(json.dumps(schedule), encoding="utf-8")

    report = approval_report("2026-2027", root=root)
    assert report["counts"]["stale"] == 1
    assert report["counts"]["approved"] == 0


def test_placement_only_approval_survives_verified_participant_swap(tmp_path):
    first = _tournament("t1", host="A", teams=_teams(("A", "B", "C", "D")))
    second = _tournament(
        "t2",
        date_str="2026-09-20",
        arena="Arena B",
        host="E",
        teams=[
            {"club": club, "label": f"{club}1", "age_group": "U10"}
            for club in ("E", "F", "G", "H")
        ],
    )
    root = _promote(tmp_path, [first, second])
    approve_tournament(
        season="2026-2027",
        tournament_id="t1",
        root=root,
        actor="booker",
        placement_locked=True,
        participants_locked=False,
    )

    result = swap_participants(
        season="2026-2027",
        tournament_a_id="t1",
        team_a_label="D1",
        tournament_b_id="t2",
        team_b_label="H1",
        root=root,
        actor="tester",
        note="fairness repair",
    )
    assert result["dry_run"] is False
    assert result["verification_result"]["ok"] is True

    schedule = load_schedule("2026-2027", root=root)
    t1 = next(t for t in schedule["plan"]["tournaments"] if t["id"] == "t1")
    assert {team["label"] for team in t1["teams"]} == {"A1", "B1", "C1", "H1"}
    # Placement itself is untouched by the verified roster repair.
    assert (t1["date"], t1["arena"], t1["host_club"], t1["start_time"]) == (
        first["date"],
        first["arena"],
        first["host_club"],
        first["start_time"],
    )

    decisions = load_decisions("2026-2027", root=root)
    record = decisions["decisions"]["t1"]
    assert record["status"] == "approved"
    assert record["placement_locked"] is True
    assert record.get("stale_at") is None
    report = approval_report("2026-2027", root=root)
    assert report["counts"]["stale"] == 0
    assert report["counts"]["approved"] == 1


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


# ---------------------------------------------------------------------------
# Manual club booking/rejection assertions
# ---------------------------------------------------------------------------


def _booking_row(report, tournament_id):
    return next(row for row in report["tournaments"] if row["tournament_id"] == tournament_id)


def _manual_set(root, *, tournament_id="t1", status="booked", problem, **overrides):
    kwargs = {
        "season": "2026-2027",
        "root": root,
        "tournament_id": tournament_id,
        "booking_status": status,
        "problem": problem,
    }
    kwargs.update(overrides)
    return set_manual_booking_assertion(**kwargs)


def test_manual_booking_assertion_is_durable_across_reconciliation(tmp_path):
    """A manual booked assertion is source authority, not replaceable calendar evidence."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    result = _manual_set(
        root,
        problem=problem,
        actor="booker",
        note="Tønsberg email: all miniputt bookings are made",
        reference="email:tønsberg-2026-09-24",
        source_scope="club_wide_interpretation",
    )
    assert result["assertion"]["authority"] == "manual_club_confirmation"
    assert result["assertion"]["source_scope"] == "club_wide_interpretation"

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = _booking_row(report, "t1")
    assert row["status"] == "manually_booked"
    assert row["authority"] == "manual_club_confirmation_interpretation"
    assert row["source_scope"] == "club_wide_interpretation"
    assert row["needs_attention"] is False
    assert report["counts"]["manually_booked"] == 1

    # Routine reconciliation rewrites calendar evidence; it must never erase or
    # demote the independent manual assertion.
    for _ in range(2):
        reconcile_calendar_bookings(season="2026-2027", root=root, club="A", problem=problem)

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = _booking_row(report, "t1")
    assert row["status"] == "manually_booked"
    assert row["authority"] == "manual_club_confirmation_interpretation"
    assert row["calendar_status"] == "ambiguous"
    decisions = load_decisions("2026-2027", root=root)
    assert [record["status"] for record in decisions["manual_booking_assertions"]] == ["active"]

    html = _export_html_with_booking_report(root, problem, tmp_path)
    assert '"bs": "manually_booked"' in html


def test_manual_rejection_keeps_tournament_visible(tmp_path):
    """Rejecting a proposed slot is not cancellation and not optimistic absence."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    _manual_set(
        root,
        status="not-booked",
        problem=problem,
        actor="booker",
        note="club cannot host the assigned weekend",
        reference="email:reject",
    )
    schedule = load_schedule("2026-2027", root=root)
    assert [t["id"] for t in schedule["plan"]["tournaments"]] == ["t1"]
    assert not schedule["plan"]["tournaments"][0].get("cancelled")

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = _booking_row(report, "t1")
    assert row["status"] == "manually_not_booked"
    assert row["needs_attention"] is True
    assert report["counts"]["manually_not_booked"] == 1


def test_rejection_export_carries_traceable_manual_queue_item(tmp_path):
    """An explicit rejection becomes structured manual work, not a bare badge.

    The operational badge stays the single top-level state; the reason, source
    reference/author, owner and resolution live in the queue projection and the
    expandable booking details so the work is actionable and traceable.
    """

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    _manual_set(
        root,
        status="not-booked",
        problem=problem,
        actor="booker",
        note="club cannot host the assigned weekend",
        reference="email:reject-queue",
    )

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    queue = report["manual_booking_queue"]
    assert len(queue) == 1
    item = queue[0]
    assert item["tournament_id"] == "t1"
    assert item["reason_code"] == "explicit_rejection"
    assert item["owner"] == "A"
    assert item["source"]["reference"] == "email:reject-queue"
    assert item["source"]["asserted_by"] == "booker"
    assert item["resolution"]["status"] == "open"

    html = _export_html_with_booking_report(root, problem, tmp_path)
    assert '"bq"' in html
    assert '"r": "explicit_rejection"' in html
    # The private source evidence stays in the operator report; the public plan
    # only carries the public-safe reason/owner/action projection.
    assert '"src"' not in html
    assert "email:reject-queue" not in html
    assert "club cannot host the assigned weekend" not in html


def test_manual_confirmation_precedes_negative_calendar_evidence_in_export(tmp_path):
    """Manual booker authority stays booked even next to contradictory evidence."""

    from tournament_scheduler.calendar_bookings import TOURNAMENT_BOOKING_EVIDENCE_KEY

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    reconcile_calendar_bookings(season="2026-2027", root=root, club="A", problem=problem)
    _manual_set(root, problem=problem, note="booker confirmed the slot", reference="email:booker")

    decisions_path = root / "2026-2027" / "decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    evidence = decisions[TOURNAMENT_BOOKING_EVIDENCE_KEY][0]
    evidence["status"] = "confirmed_not_booked"
    evidence["reason"] = "explicit_host_rejection_superseded_by_booker_confirmation"
    decisions_path.write_text(json.dumps(decisions), encoding="utf-8")

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = _booking_row(report, "t1")
    assert row["status"] == "manually_booked"
    assert row["calendar_status"] == "confirmed_not_booked"
    assert row["operational_state"] == "booked"
    assert row["operational_lock"] is True
    assert "calendar_negative_conflicts_with_manual_booking" in row["follow_up_reasons"]

    html = _export_html_with_booking_report(root, problem, tmp_path)
    assert '"obs": "booked"' in html
    assert '"bs": "manually_booked"' in html
    assert "calendar_negative_conflicts_with_manual_booking" in html


def test_manual_assertion_conflict_with_calendar_association_flags_review(tmp_path):
    """A calendar match conflicting with a manual rejection needs review, not overwrite."""

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
    _manual_set(root, status="not-booked", problem=problem, note="club rejected the proposal")
    confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fp,
        tournament_id="t1",
        problem=problem,
        note="host calendar later matched",
    )
    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = _booking_row(report, "t1")
    assert row["status"] == "manually_not_booked"
    assert row["calendar_status"] == "confirmed_booked"
    assert "calendar_association_conflicts_with_manual_rejection" in row["follow_up_reasons"]
    assert row["needs_attention"] is True
    assert report["counts"]["conflicts"] == 1


def test_manual_assertion_is_idempotent_and_supersession_is_explicit(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    first = _manual_set(root, problem=problem, note="confirmed", reference="email-1")
    assert first["changed"] is True
    assert first["idempotent"] is False

    repeat = _manual_set(root, problem=problem, note="confirmed again", reference="email-1")
    assert repeat["idempotent"] is True
    assert repeat["changed"] is False

    with pytest.raises(SeasonStateError):
        _manual_set(root, status="not-booked", problem=problem, reference="email-2")

    superseded = _manual_set(
        root,
        status="not-booked",
        problem=problem,
        note="club corrected the earlier confirmation",
        reference="email-2",
        supersede=True,
    )
    assert superseded["assertion"]["supersedes"]
    records = load_decisions("2026-2027", root=root)["manual_booking_assertions"]
    assert [record["status"] for record in records].count("active") == 1
    assert [record["status"] for record in records].count("superseded") == 1
    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    assert _booking_row(report, "t1")["status"] == "manually_not_booked"


def test_manual_assertion_fails_closed_on_bad_inputs(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    with pytest.raises(SeasonStateError):
        _manual_set(root, status="maybe", problem=problem)
    with pytest.raises(SeasonStateError):
        _manual_set(root, tournament_id="rvv-9999", problem=problem)
    with pytest.raises(SeasonStateError):
        _manual_set(root, problem=problem, expected_revision="deadbeef")
    with pytest.raises(SeasonStateError):
        _manual_set(root, problem=problem, stated_start="10:00")


def test_manual_assertion_stales_when_slot_changes(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    _manual_set(root, problem=problem, note="confirmed at current slot")
    move_tournament(season="2026-2027", tournament_id="t1", root=root, date="2026-09-19")
    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = _booking_row(report, "t1")
    assert row["status"] == "stale"
    assert "tournament_date_changed" in row["stale_reasons"]
    assert row["evidence"]["booking_status"] == "booked"


def test_manual_assertion_stated_duration_updates_canonical_occupancy(tmp_path):
    """A source-stated booked window becomes the canonical occupied interval."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    result = _manual_set(
        root,
        problem=problem,
        note="email states a shorter window than the canonical block",
        stated_start="10:00",
        stated_end="10:45",
    )
    assert result["assertion"]["asserted_interval"]["duration_minutes"] == "45"
    assert result["interval_alignment"]["accepted_source_interval"]["end_time"] == "10:45"
    assert result["booking_feasibility_warnings"]
    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = _booking_row(report, "t1")
    assert row["status"] == "manually_booked"
    assert "manual_booking_stated_end_differs_from_canonical" not in row["follow_up_reasons"]
    assert "ice_time_governing_minimum" in row["follow_up_reasons"]
    tournament = load_schedule("2026-2027", root=root)["plan"]["tournaments"][0]
    assert tournament["start_time"] == "10:00"


def test_manual_assertion_below_floor_warning_carries_exact_evidence(tmp_path):
    """A governing-floor shortfall is a finding for an accepted interval.

    The warning must originate from ``verify_candidate``'s accepted-interval
    path (the effective occupancy comes from the accepted interval override)
    rather than an apply-time fallback that reclassifies an unmatched hard
    blocker.
    """

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    result = _manual_set(
        root,
        problem=problem,
        note="email states a shorter window than the canonical block",
        stated_start="10:00",
        stated_end="10:45",
    )
    warnings = result["booking_feasibility_warnings"]
    assert [warning["code"] for warning in warnings] == ["ice_time_governing_minimum"]
    assert all(warning.get("accepted_booking_interval") for warning in warnings)


def test_manual_assertion_reconfirms_stale_below_floor_slot_with_fresh_evidence(tmp_path):
    """A stale assertion is not evidence for a newly stated below-floor interval.

    Re-confirming a moved slot with a fresh below-floor stated interval records
    a new accepted interval override, so the governing-floor shortfall stays a
    non-blocking finding instead of an unmatched hard violation.
    """

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    _manual_set(root, problem=problem, note="confirmed at the original slot", reference="email-1")
    move_tournament(season="2026-2027", tournament_id="t1", root=root, date="2026-09-19")

    result = _manual_set(
        root,
        problem=problem,
        note="club re-confirmed the moved slot with a shorter window",
        reference="email-2",
        stated_start="10:00",
        stated_end="10:45",
    )
    assert result["changed"] is True
    warnings = result["booking_feasibility_warnings"]
    assert [warning["code"] for warning in warnings] == ["ice_time_governing_minimum"]
    assert all(warning.get("accepted_booking_interval") for warning in warnings)

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    assert _booking_row(report, "t1")["status"] == "manually_booked"


def test_below_floor_booking_findings_and_export_preflight_parity(tmp_path):
    """Apply-time confirmation, findings/audit and export/preflight agree on a
    source-confirmed exact short interval for the same revision (ADR 0005)."""

    from tournament_scheduler.final_verification import verify_final_candidate
    from tournament_scheduler.season_maintenance import list_findings, load_context

    event = {
        "date": "2026-09-12",
        "start": "11:00",
        "end": "12:30",
        "availability": "fixed_busy",
        "calendar_event": "Miniputt U10 bekreftet",
        "club": "A",
    }
    # A valid 4-team round-robin (3 rounds, 2 parallel games each) so the final
    # verifier's game-integrity checks pass and only the below-floor booking is
    # under test.
    teams = [
        {"club": club, "label": f"{club}1", "age_group": "U10"}
        for club in ("A", "B", "C", "D")
    ]
    labels = [team["label"] for team in teams]
    games = [
        {"home": labels[0], "away": labels[1], "parallel_slot": 0, "round_number": 1},
        {"home": labels[2], "away": labels[3], "parallel_slot": 1, "round_number": 1},
        {"home": labels[0], "away": labels[2], "parallel_slot": 0, "round_number": 2},
        {"home": labels[1], "away": labels[3], "parallel_slot": 1, "round_number": 2},
        {"home": labels[0], "away": labels[3], "parallel_slot": 0, "round_number": 3},
        {"home": labels[1], "away": labels[2], "parallel_slot": 1, "round_number": 3},
    ]
    tournament = _tournament("t1", teams=teams)
    tournament["games"] = games
    problem = _host_a_problem([event])
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    state.write_stage(StageName.PLANNING, {"plan": _plan([tournament])}, status=StageStatus.DONE)
    write_reviewed_stage4_export(state, problem=problem)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")

    result = confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fingerprint(event),
        tournament_id="t1",
        actor="tester",
        note="host confirmed a 90-minute window",
        problem=problem,
    )
    assert [warning["code"] for warning in result["booking_feasibility_warnings"]] == [
        "ice_time_governing_minimum"
    ]

    # Reload canonical state fresh (same revision, projected overlays).
    schedule, decisions, plan, reloaded_problem = load_context("2026-2027", root=root)
    assert reloaded_problem.get("ice_time_minutes_overrides") == {"t1": 90}

    # Findings/audit classify the accepted short interval as follow-up, not hard.
    findings = list_findings("2026-2027", root=root)
    assert findings["verification_ok"] is True
    hard_codes = {
        finding["code"]
        for finding in findings["findings"]
        if finding.get("category") == "hard_violation"
    }
    assert "ice_time_governing_minimum" not in hard_codes
    booking_findings = [
        finding
        for finding in findings["findings"]
        if finding["code"] == "ice_time_governing_minimum"
    ]
    assert booking_findings and all(
        finding.get("accepted_booking_interval") for finding in booking_findings
    )

    # Export/preflight: the same revision-bound projection is hard-valid and
    # preserves the exact short interval as a durable feasibility warning.
    verification = verify_final_candidate(plan, reloaded_problem)
    assert verification["ok"] is True, verification["violations"]
    warnings = verification["booking_feasibility_warnings"]
    assert [warning["code"] for warning in warnings] == ["ice_time_governing_minimum"]
    assert warnings[0]["configured_ice_time_minutes"] == 90
    assert warnings[0]["minimum_required_minutes"] == 120
    assert verification["publication_readiness"]["status"] == "REVIEW_REQUIRED"
    informational = {
        item["code"] for item in verification["publication_readiness"]["informational_reasons"]
    }
    assert "booking_feasibility_warnings" in informational


def test_below_floor_booking_stage4_export_builder_stays_hard_valid(tmp_path):
    """The real Stage 4 export builder agrees with apply-time projection.

    The PR's central failure mode is apply-time and export-time disagreement
    for a source-confirmed below-floor interval. This exercises the actual
    ``stage4_export_verification._build_export_verification_problem`` entry
    point (a promoted canonical root plus a Stage 2 scraping checkpoint), and
    asserts the rebuilt export problem carries the exact accepted override and
    association so final verification stays hard-valid with the same warning.
    """

    from tournament_scheduler.final_verification import verify_final_candidate
    from tournament_scheduler.pipeline.stage4_export_verification import (
        _build_export_verification_problem,
    )
    from tournament_scheduler.planning_contract import extract_candidate

    event = {
        "date": "2026-09-12",
        "start": "11:00",
        "end": "12:30",
        "availability": "fixed_busy",
        "calendar_event": "Miniputt U10 bekreftet",
        "club": "A",
    }
    teams = [
        {"club": club, "label": f"{club}1", "age_group": "U10"}
        for club in ("A", "B", "C", "D")
    ]
    labels = [team["label"] for team in teams]
    games = [
        {"home": labels[0], "away": labels[1], "parallel_slot": 0, "round_number": 1},
        {"home": labels[2], "away": labels[3], "parallel_slot": 1, "round_number": 1},
        {"home": labels[0], "away": labels[2], "parallel_slot": 0, "round_number": 2},
        {"home": labels[1], "away": labels[3], "parallel_slot": 1, "round_number": 2},
        {"home": labels[0], "away": labels[3], "parallel_slot": 0, "round_number": 3},
        {"home": labels[1], "away": labels[2], "parallel_slot": 1, "round_number": 3},
    ]
    tournament = _tournament("t1", teams=teams)
    tournament["games"] = games
    problem = _host_a_problem([event])
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    state.write_stage(StageName.PLANNING, {"plan": _plan([tournament])}, status=StageStatus.DONE)
    write_reviewed_stage4_export(state, problem=problem)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")

    result = confirm_calendar_booking(
        season="2026-2027",
        root=root,
        event_fingerprint=event_fingerprint(event),
        tournament_id="t1",
        actor="tester",
        note="host confirmed a 90-minute window",
        problem=problem,
    )
    assert [warning["code"] for warning in result["booking_feasibility_warnings"]] == [
        "ice_time_governing_minimum"
    ]

    # The Stage 2 checkpoint is what Stage 4 uses to rebuild the export
    # problem: the same event as a ``CalendarEvent`` serialization.
    state.write_stage(
        StageName.SCRAPING,
        {
            "events_by_club": {
                "A": [
                    {
                        "date": "12.09.2026",
                        "name": "Miniputt U10 bekreftet",
                        "datetime": "2026-09-12T11:00:00",
                        "duration_hours": 1.5,
                    }
                ]
            },
            "club_calendar_status": {"A": "known"},
        },
        status=StageStatus.DONE,
    )
    config = dict(problem)
    config["canonical_season_root"] = str(root)
    config["start_date"] = "2026-09-01"
    config["end_date"] = "2027-04-30"
    state.write_stage(StageName.CONFIG, config, status=StageStatus.DONE)

    built = _build_export_verification_problem(config, state)
    assert built is not None
    assert built["ice_time_minutes_overrides"] == {"t1": 90}
    assert built["club_busy_intervals"]["A"][0]["start"] == "11:00"
    assert built["club_busy_intervals"]["A"][0]["end"] == "12:30"

    canonical_plan = dict(load_schedule("2026-2027", root=root)["plan"])
    candidate = extract_candidate({"plan": canonical_plan})
    verification = verify_final_candidate(candidate, built)
    assert verification["ok"] is True, verification["violations"]
    assert [warning["code"] for warning in verification["booking_feasibility_warnings"]] == [
        "ice_time_governing_minimum"
    ]
    assert verification["publication_readiness"]["status"] == "REVIEW_REQUIRED"


def test_manual_assertion_stated_duration_is_exported_as_canonical_html(tmp_path):
    """The HTML export must render the accepted source interval, not the default."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    _manual_set(
        root,
        problem=problem,
        note="email states a shorter window than the canonical block",
        stated_start="10:00",
        stated_end="10:45",
    )
    html = _export_html_with_booking_report(root, problem, tmp_path)
    assert '"s": "10:00"' in html
    assert '"e": "10:45"' in html
    assert "ice_time_governing_minimum" in html


def test_manual_assertion_clear_revokes_and_is_idempotent(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    _manual_set(root, problem=problem, note="wrong tournament")
    cleared = clear_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="t1",
        actor="booker",
        note="recorded against the wrong id",
    )
    assert cleared["changed"] is True
    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    assert _booking_row(report, "t1")["status"] != "manually_booked"
    again = clear_manual_booking_assertion(
        season="2026-2027", root=root, tournament_id="t1", actor="booker", note="again"
    )
    assert again["changed"] is False
    records = load_decisions("2026-2027", root=root)["manual_booking_assertions"]
    assert [record["status"] for record in records] == ["revoked"]
    with pytest.raises(SeasonStateError):
        clear_manual_booking_assertion(
            season="2026-2027", root=root, tournament_id="rvv-9999", actor="booker"
        )


def test_manual_booking_cli_set_status_and_clear(tmp_path, capsys):
    from tournament_scheduler.cli.rvv_cli import main

    root = _promote(tmp_path, [_tournament("t1")])
    assert main(
        [
            "season", "booking-set",
            "--season", "2026-2027",
            "--root", str(root),
            "--tournament-id", "t1",
            "--status", "booked",
            "--reference", "email:1",
            "--note", "club confirmed",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "season", "booking-status",
            "--season", "2026-2027",
            "--root", str(root),
            "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["manually_booked"] == 1
    assert main(
        [
            "season", "booking-clear",
            "--season", "2026-2027",
            "--root", str(root),
            "--tournament-id", "t1",
            "--note", "wrong",
        ]
    ) == 0
    capsys.readouterr()
    assert load_decisions("2026-2027", root=root)["manual_booking_assertions"][0]["status"] == "revoked"


def test_manual_assertion_advances_canonical_revision(tmp_path):
    """The durable manual overlay participates in canonical-state identity."""

    from tournament_scheduler.canonical_state import canonical_state_revision

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    before = canonical_state_revision(
        load_schedule("2026-2027", root=root), load_decisions("2026-2027", root=root)
    )
    result = _manual_set(root, problem=problem, note="club confirmed by email")
    after = canonical_state_revision(
        load_schedule("2026-2027", root=root), load_decisions("2026-2027", root=root)
    )
    assert after != before
    assert result["canonical_state_revision"] == after

    # The revision-bound guard fails closed after the advance.
    with pytest.raises(SeasonStateError):
        _manual_set(root, status="not-booked", problem=problem, expected_revision=before)


def test_manual_assertion_requires_traceable_source(tmp_path):
    """A positive confirmation must carry a source reference or rationale."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    with pytest.raises(SeasonStateError):
        _manual_set(root, problem=problem)
    with pytest.raises(SeasonStateError):
        _manual_set(root, status="not-booked", problem=problem)
    with pytest.raises(SeasonStateError):
        _manual_set(root, problem=problem, note="   ", reference="   ")
    # A rationale alone is enough; the provenance is recorded.
    result = _manual_set(root, problem=problem, note="club called the booking team")
    assert result["assertion"]["note"] == "club called the booking team"
    assert result["assertion"]["reference"] == ""


@pytest.mark.parametrize(
    "start,end",
    [
        ("10:00", "10:00"),  # zero length
        ("10:00", "09:00"),  # reversed / overnight
        ("25:00", "26:00"),  # malformed hours
        ("10:00", "bogus"),  # malformed minutes
    ],
)
def test_manual_assertion_rejects_invalid_stated_interval(tmp_path, start, end):
    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    with pytest.raises(SeasonStateError):
        _manual_set(root, problem=problem, note="source window", stated_start=start, stated_end=end)
    with pytest.raises(SeasonStateError):
        _manual_set(root, problem=problem, note="source window", stated_start="10:00")
    # Nothing was persisted by the rejected attempts.
    assert "manual_booking_assertions" not in load_decisions("2026-2027", root=root)


def test_manual_assertion_reconfirms_after_move_without_supersede(tmp_path):
    """A moved slot stales the assertion; a fresh source re-confirms directly."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    _manual_set(root, problem=problem, note="confirmed at the original slot", reference="email-1")
    move_tournament(season="2026-2027", tournament_id="t1", root=root, date="2026-09-19")
    stale = booking_status_report(season="2026-2027", root=root, problem=problem)
    assert _booking_row(stale, "t1")["status"] == "stale"

    reconfirmed = _manual_set(
        root,
        problem=problem,
        note="club re-confirmed the moved slot",
        reference="email-2",
    )
    assert reconfirmed["changed"] is True
    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = _booking_row(report, "t1")
    assert row["status"] == "manually_booked"
    assert row["authority"] == "manual_club_confirmation"

    records = load_decisions("2026-2027", root=root)["manual_booking_assertions"]
    assert [record["status"] for record in records].count("active") == 1
    superseded = next(record for record in records if record["status"] == "superseded")
    assert superseded["supersede_reason"] == "club re-confirmed the moved slot"
    assert superseded["superseded_stale_reasons"] == ["tournament_date_changed"]


def test_manual_assertion_scope_change_requires_supersede(tmp_path):
    """Changing direct confirmation vs. interpretation is a new decision, not a no-op."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    _manual_set(root, problem=problem, note="direct confirmation", reference="email-1")
    with pytest.raises(SeasonStateError):
        _manual_set(
            root,
            problem=problem,
            note="direct confirmation",
            reference="email-1",
            source_scope="club_wide_interpretation",
        )
    superseded = _manual_set(
        root,
        problem=problem,
        note="accepted as a club-wide interpretation",
        reference="email-1",
        source_scope="club_wide_interpretation",
        supersede=True,
    )
    assert superseded["assertion"]["source_scope"] == "club_wide_interpretation"


def test_manual_authority_preserves_actionable_calendar_warning(tmp_path):
    """An independent stale calendar association stays visible next to manual authority."""

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
        problem=problem,
        note="host calendar match",
    )
    # Simulate a canonical move through the legacy path so the association is
    # now stale without being explicitly released.
    schedule_path = root / "2026-2027" / "schedule.json"
    saved = json.loads(schedule_path.read_text(encoding="utf-8"))
    saved["plan"]["tournaments"][0]["date"] = "2026-09-19"
    schedule_path.write_text(json.dumps(saved), encoding="utf-8")

    _manual_set(root, problem=problem, note="club confirmed after the move", reference="email-1")
    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = _booking_row(report, "t1")
    assert row["status"] == "manually_booked"
    assert row["authority"] == "manual_club_confirmation"
    assert row["calendar_status"] == "stale"
    assert "calendar_booking_association_stale" in row["follow_up_reasons"]
    assert row["needs_attention"] is True


def test_club_reconciliation_fails_closed_on_fabricated_placeholder_intervals(tmp_path):
    """A stored ``known`` status must not launder a scraper's fabricated
    00:00/1h fallback into a negative booking claim during reconcile."""
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
        "club_busy_intervals": {
            "A": [
                {
                    "date": f"2026-09-{day:02d}",
                    "start": "00:00",
                    "end": "01:00",
                    "availability": "fixed_busy",
                    "calendar_event": f"Jutul U{day % 8}",
                }
                for day in range(1, 26)
            ]
        },
    }
    result = reconcile_calendar_bookings(season="2026-2027", root=root, club="A", problem=problem)

    row = result["classified"][0]
    assert row["status"] == "not_checkable"
    assert row["reason"] == "fabricated_calendar_placeholder_evidence"
    assert row["source_fabricated_placeholder"] is True


# ---------------------------------------------------------------------------
# Unified operational booking state
#
# One operator-facing state per tournament: booked/locked, not booked (muted),
# or action required (manual booking queue). Detailed status, authority and
# follow-up evidence stay available, but must not become competing top-level
# badges.
# ---------------------------------------------------------------------------


def test_email_confirmation_is_booked_even_without_calendar_evidence(tmp_path):
    """A Tønsberg-style email/manual confirmation locks the slot on its own.

    A trustworthy calendar with no matching event is a reconciliation
    observation, not a reason to show the tournament as unbooked or to move
    the accepted confirmation into the manual queue.
    """

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    _manual_set(
        root,
        problem=problem,
        note="Tønsberg email: miniputt ice is booked",
        reference="email:tonsberg-2026-09-24",
    )

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = _booking_row(report, "t1")
    assert row["status"] == "manually_booked"
    assert row["calendar_status"] in ("ambiguous", "unknown")
    assert row["operational_state"] == "booked"
    assert row["operational_lock"] is True
    assert report["counts"]["booked"] == 1


def test_missing_calendar_evidence_is_not_booked_and_not_manual_queue(tmp_path):
    """Absence of calendar evidence must not masquerade as host rejection."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = _booking_row(report, "t1")
    assert row["status"] in ("ambiguous", "unknown")
    assert row["operational_state"] == "unknown"
    assert row["operational_lock"] is False


def test_explicit_rejection_enters_manual_queue_without_removing_tournament(tmp_path):
    """A rejected proposal becomes manual work, not a silent deletion."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    _manual_set(
        root,
        status="not-booked",
        problem=problem,
        note="host rejected the assigned weekend",
        reference="email:reject",
    )

    schedule = load_schedule("2026-2027", root=root)
    assert [t["id"] for t in schedule["plan"]["tournaments"]] == ["t1"]
    assert not schedule["plan"]["tournaments"][0].get("cancelled")

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = _booking_row(report, "t1")
    assert row["status"] == "manually_not_booked"
    assert row["operational_state"] == "action_required"
    assert row["operational_lock"] is False


def test_host_controlled_slot_enters_manual_queue(tmp_path):
    """A movable/unconfirmed host interval is manual work, not booked ice."""

    tournaments = [_tournament("t1")]
    tournaments[0]["requires_host_confirmation"] = True
    tournaments[0]["host_confirmation_reason"] = "åpen ishall må flyttes"
    root = _promote(tmp_path, tournaments)
    problem = _host_a_problem([])

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = _booking_row(report, "t1")
    assert row["operational_state"] == "action_required"
    assert row["operational_lock"] is False


def test_confirmation_overrides_retained_manual_booking_reason(tmp_path):
    """An accepted confirmation wins over a plan-time provisional reason.

    ``manual_booking_reason`` is set when the plan is built, before the host
    calendar is known. A later email/manual confirmation makes the slot real;
    the retained reason is historical metadata and must not drop the lock or
    push the tournament back into the manual queue.
    """

    tournaments = [_tournament("t1")]
    tournaments[0]["manual_booking_reason"] = "Kalender utilgjengelig — istid må bookes manuelt."
    root = _promote(tmp_path, tournaments)
    problem = _host_a_problem([])
    _manual_set(
        root,
        problem=problem,
        note="club emailed confirmation for the provisional slot",
        reference="email:confirm-provisional",
    )

    row = _booking_row(booking_status_report(season="2026-2027", root=root, problem=problem), "t1")
    assert row["status"] == "manually_booked"
    assert row["operational_state"] == "booked"
    assert row["operational_lock"] is True


def test_confirmation_overrides_retained_host_confirmation_flag(tmp_path):
    """An accepted confirmation wins over a retained movable-interval flag."""

    tournaments = [_tournament("t1")]
    tournaments[0]["requires_host_confirmation"] = True
    tournaments[0]["host_confirmation_reason"] = "åpen ishall må flyttes"
    root = _promote(tmp_path, tournaments)
    problem = _host_a_problem([])
    _manual_set(
        root,
        problem=problem,
        note="club confirmed the movable slot by email",
        reference="email:confirm-movable",
    )

    row = _booking_row(booking_status_report(season="2026-2027", root=root, problem=problem), "t1")
    assert row["operational_state"] == "booked"
    assert row["operational_lock"] is True


def test_invalidated_confirmation_returns_to_action_required(tmp_path):
    """Explicit invalidation (slot moved) drops the lock and needs rework."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    _manual_set(root, problem=problem, note="confirmed at current slot", reference="email-1")
    assert _booking_row(
        booking_status_report(season="2026-2027", root=root, problem=problem), "t1"
    )["operational_state"] == "booked"

    move_tournament(season="2026-2027", tournament_id="t1", root=root, date="2026-09-19")

    row = _booking_row(booking_status_report(season="2026-2027", root=root, problem=problem), "t1")
    assert row["status"] == "stale"
    assert row["operational_state"] == "action_required"
    assert row["operational_lock"] is False


def test_refresh_does_not_downgrade_manual_confirmation(tmp_path):
    """A routine reconcile/refresh cannot demote an accepted confirmation."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    _manual_set(root, problem=problem, note="confirmed", reference="email-1")

    before = _booking_row(booking_status_report(season="2026-2027", root=root, problem=problem), "t1")
    assert before["operational_state"] == "booked"

    for _ in range(2):
        reconcile_calendar_bookings(season="2026-2027", root=root, club="A", problem=problem)

    after = _booking_row(booking_status_report(season="2026-2027", root=root, problem=problem), "t1")
    assert after["status"] == "manually_booked"
    assert after["operational_state"] == "booked"
    assert after["operational_lock"] is True


def test_refresh_never_enqueues_confirmed_booking_as_manual_work(tmp_path):
    """An accepted confirmation must not migrate into the manual queue.

    Reconciliation rewrites calendar evidence; the durable manual assertion and
    its locked operational state must survive unchanged, and the exported plan
    must carry no manual-queue work item for the tournament.
    """

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    _manual_set(root, problem=problem, note="confirmed by email", reference="email:confirm")

    for _ in range(2):
        reconcile_calendar_bookings(season="2026-2027", root=root, club="A", problem=problem)

    report = booking_status_report(season="2026-2027", root=root, problem=problem)
    row = _booking_row(report, "t1")
    assert row["operational_state"] == "booked"
    assert "manual_work" not in row
    assert report["manual_booking_queue"] == []

    html = _export_html_with_booking_report(root, problem, tmp_path)
    assert '"obs": "booked"' in html
    assert '"bq"' not in html


def test_export_renders_one_operational_badge_with_details(tmp_path):
    """The season plan shows one booking badge; evidence moves to details."""

    root = _promote(tmp_path, [_tournament("t1")])
    problem = _host_a_problem([])
    _manual_set(
        root,
        problem=problem,
        note="email states a shorter window than the canonical block",
        reference="email-1",
        stated_start="10:00",
        stated_end="10:45",
    )

    html = _export_html_with_booking_report(root, problem, tmp_path)
    # The canonical operational state drives the single top-level badge.
    assert '"obs": "booked"' in html
    assert '"obl": true' in html
    assert html.count("booking-badge booking-badge--") == 1
    # The follow-up discrepancy is present, but as expandable booking details
    # rather than a second competing top-level badge.
    assert "buildBookingDetails" in html
    assert "booking-details-head" in html
    assert "manual_booking_stated_end_differs_from_canonical" in html
    assert "MÅ FØLGES OPP" not in html
    assert '<div class="manual-badge"' not in html
