"""Unit tests for baseline-aware canonical planning (issue #355)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from tournament_scheduler.canonical_baseline import (
    build_canonical_baseline,
    change_cost,
    locked_dates,
    pinned_tournament_ids,
    verify_canonical_locks,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.testing.reviewed_export import write_reviewed_stage4_export
from tournament_scheduler.planning_contract import build_planning_problem, verify_candidate
from tournament_scheduler.season_state import (
    SeasonStateError,
    apply_candidate,
    approve_tournament,
    load_decisions,
    load_schedule,
    promote_from_stage3,
)


def _teams(club_letters=("A", "B", "C", "D")):
    return [
        {"club": club, "label": f"{club}1", "age_group": "U10"}
        for club in club_letters
    ]


def _tournament(t_id, *, date_str="2026-09-12", arena="Arena A", host="A", start_time="10:00", teams=None):
    teams = teams if teams is not None else _teams()
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
        "start_time": start_time,
    }


def _plan(tournaments):
    return {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": tournaments}


def _schedule(tournaments, *, season="2026-2027", revision="rev-1"):
    return {"season": season, "revision": revision, "fingerprint": revision, "plan": _plan(tournaments)}


def test_build_canonical_baseline_marks_locks_and_dates():
    schedule = _schedule([_tournament("t1", date_str="2026-09-12"), _tournament("t2", date_str="2026-10-10")])
    decisions = {
        "decisions": {
            "t1": {"placement_locked": True, "participants_locked": False},
            "t2": {"placement_locked": False, "participants_locked": True},
        }
    }

    baseline = build_canonical_baseline(schedule, decisions)

    assert baseline["season"] == "2026-2027"
    assert baseline["revision"] == "rev-1"
    assert baseline["locks"] == {
        "t1": {"placement": True, "participants": False},
        "t2": {"placement": False, "participants": True},
    }
    assert pinned_tournament_ids(baseline) == ["t1", "t2"]
    assert locked_dates(baseline) == ["2026-09-12"]
    assert {snapshot["id"] for snapshot in baseline["tournaments"]} == {"t1", "t2"}


def test_verify_canonical_locks_reports_placement_participants_and_missing():
    schedule = _schedule([_tournament("t1"), _tournament("t2")])
    baseline = build_canonical_baseline(
        schedule,
        {"decisions": {"t1": {"placement_locked": True}, "t2": {"participants_locked": True}}},
    )

    moved = _plan([_tournament("t1", date_str="2026-09-19"), _tournament("t2")])
    codes = {v["code"] for v in verify_canonical_locks(baseline, moved)}
    assert "canonical_placement_locked" in codes

    swapped = _plan(
        [
            _tournament("t1"),
            _tournament("t2", teams=_teams(("A", "B", "C", "E"))),
        ]
    )
    codes = {v["code"] for v in verify_canonical_locks(baseline, swapped)}
    assert codes == {"canonical_participants_locked"}

    dropped = _plan([_tournament("t2")])
    codes = {v["code"] for v in verify_canonical_locks(baseline, dropped)}
    assert "canonical_locked_tournament_missing" in codes


def test_change_cost_classifies_each_tournament_once():
    baseline = build_canonical_baseline(
        _schedule(
            [
                _tournament("t1"),
                _tournament("t2", date_str="2026-10-10"),
                _tournament("t3", date_str="2026-11-07"),
                _tournament("t4", date_str="2026-12-05"),
            ]
        ),
        {"decisions": {}},
    )
    candidate = _plan(
        [
            _tournament("t1"),  # unchanged
            _tournament("t2", date_str="2026-10-10", teams=_teams(("A", "B", "C", "E"))),  # participants
            _tournament("t3", date_str="2026-11-14"),  # placement
            _tournament("t5", date_str="2027-01-09"),  # replacement (t4 removed, t5 new)
        ]
    )

    cost = change_cost(baseline, candidate)

    assert cost["counts"] == {"none": 1, "participants": 1, "placement": 1, "replacement": 2}
    assert cost["total"] == pytest.approx(1.0 + 3.0 + 2 * 10.0)


def test_change_cost_weights_are_configurable():
    baseline = build_canonical_baseline(_schedule([_tournament("t1")]), {"decisions": {}})
    candidate = _plan([_tournament("t1", date_str="2026-09-19")])

    cost = change_cost(baseline, candidate, weights={"placement": 0.5})

    assert cost["counts"]["placement"] == 1
    assert cost["total"] == pytest.approx(0.5)


def test_build_planning_problem_applies_canonical_baseline_and_verifies_locks():
    schedule = _schedule([_tournament("t1")])
    baseline = build_canonical_baseline(schedule, {"decisions": {"t1": {"placement_locked": True}}})
    config = {"teams": _teams()}

    problem = build_planning_problem(
        config, None, date(2026, 9, 1), date(2027, 4, 30), canonical_baseline=baseline
    )

    assert problem["canonical_baseline"] is baseline
    assert "t1" in problem["manual_adjustments"]["pinned_tournament_ids"]
    assert "2026-09-12" in problem["manual_adjustments"]["locked_dates"]

    moved = _plan([_tournament("t1", date_str="2026-09-19")])
    result = verify_candidate(moved, problem)
    assert not result["ok"]
    assert "canonical_placement_locked" in {v["code"] for v in result["violations"]}

    unchanged = _plan([_tournament("t1")])
    assert verify_candidate(unchanged, problem)["ok"]


def test_build_planning_problem_reads_baseline_from_config():
    schedule = _schedule([_tournament("t1")])
    baseline = build_canonical_baseline(schedule, {"decisions": {"t1": {"placement_locked": True}}})
    config = {"teams": _teams(), "canonical_baseline": baseline}

    problem = build_planning_problem(config, None, date(2026, 9, 1), date(2027, 4, 30))

    assert problem["canonical_baseline"] == baseline
    assert "t1" in problem["manual_adjustments"]["pinned_tournament_ids"]


def _promote(tmp_path, tournaments):
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    state.write_stage(StageName.PLANNING, {"plan": _plan(tournaments)}, status=StageStatus.DONE)
    write_reviewed_stage4_export(state)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    return root


def test_apply_candidate_preserves_approvals_and_reconciles_records(tmp_path):
    root = _promote(tmp_path, [_tournament("t1"), _tournament("t2", date_str="2026-10-10")])
    approve_tournament(season="2026-2027", tournament_id="t1", root=root, actor="booker")

    candidate = _plan(
        [
            _tournament("t1"),
            _tournament("t2", date_str="2026-10-17"),
            _tournament("t3", date_str="2027-01-09"),
        ]
    )
    schedule, decisions, cost = apply_candidate(season="2026-2027", candidate=candidate, root=root)

    assert {t["id"] for t in schedule["plan"]["tournaments"]} == {"t1", "t2", "t3"}
    assert decisions["decisions"]["t1"]["status"] == "approved"
    assert decisions["decisions"]["t2"]["status"] == "pending_review"
    assert decisions["decisions"]["t3"]["status"] == "pending_review"
    assert cost["counts"]["none"] == 1
    assert cost["counts"]["placement"] == 1
    assert cost["counts"]["replacement"] == 1
    assert load_schedule("2026-2027", root=root)["revision"] == schedule["revision"]


def test_apply_candidate_rejects_locked_change_without_writing(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    approve_tournament(season="2026-2027", tournament_id="t1", root=root, actor="booker")
    schedule_file = root / "2026-2027" / "schedule.json"
    decisions_file = root / "2026-2027" / "decisions.json"
    before_schedule = schedule_file.read_bytes()
    before_decisions = decisions_file.read_bytes()

    moved = _plan([_tournament("t1", date_str="2026-09-19")])
    with pytest.raises(SeasonStateError):
        apply_candidate(season="2026-2027", candidate=moved, root=root)

    assert schedule_file.read_bytes() == before_schedule
    assert decisions_file.read_bytes() == before_decisions


def test_apply_candidate_rejects_hard_verification_failure(tmp_path):
    root = _promote(tmp_path, [_tournament("t1")])
    before = (root / "2026-2027" / "schedule.json").read_bytes()

    # Odd-sized tournament: a self-consistency hard violation.
    bad = _plan([_tournament("t1", teams=_teams(("A", "B", "C")))])
    with pytest.raises(SeasonStateError):
        apply_candidate(season="2026-2027", candidate=bad, root=root)

    assert (root / "2026-2027" / "schedule.json").read_bytes() == before
    assert load_decisions("2026-2027", root=root)["decisions"]["t1"]["status"] == "pending_review"


def test_apply_candidate_rejects_unexplained_hosting_responsibility_transfer(tmp_path):
    root = _promote(
        tmp_path,
        [
            _tournament("t1", host="A"),
            _tournament("t2", date_str="2026-10-10", arena="Arena B", host="B"),
        ],
    )
    schedule_file = root / "2026-2027" / "schedule.json"
    before = schedule_file.read_bytes()
    problem = {"teams": _teams()}

    # Convenience rehost: move B's tournament onto A's arena even though A does
    # not owe that hosting responsibility.
    candidate = _plan(
        [
            _tournament("t1", host="A"),
            _tournament("t2", date_str="2026-10-10", arena="Arena A", host="A"),
        ]
    )
    with pytest.raises(SeasonStateError, match="transfers hosting responsibility"):
        apply_candidate(season="2026-2027", candidate=candidate, root=root, problem=problem)

    assert schedule_file.read_bytes() == before


def test_apply_candidate_allows_responsibility_preserving_change(tmp_path):
    root = _promote(
        tmp_path,
        [
            _tournament("t1", host="A"),
            _tournament("t2", date_str="2026-10-10", arena="Arena B", host="B"),
        ],
    )
    problem = {"teams": _teams()}

    # A pure date move keeps each host responsible for its own tournament.
    candidate = _plan(
        [
            _tournament("t1", host="A"),
            _tournament("t2", date_str="2026-10-17", arena="Arena B", host="B"),
        ]
    )
    schedule, _decisions, _cost = apply_candidate(
        season="2026-2027", candidate=candidate, root=root, problem=problem
    )

    hosts = {t["id"]: t["host_club"] for t in schedule["plan"]["tournaments"]}
    assert hosts == {"t1": "A", "t2": "B"}


def test_replan_around_baseline_preserves_ids_and_reports_change_cost(tmp_path):
    from tournament_scheduler.canonical_replan import replan_around_baseline

    first = _teams(("A", "B", "C", "D"))
    second = [
        {"club": club, "label": f"{club}1", "age_group": "U10"} for club in ("E", "F", "G", "H")
    ]
    t1 = _tournament("t1", host="A", teams=first)
    t2 = _tournament("t2", date_str="2026-10-10", arena="Arena B", host="E", teams=second)
    root = _promote(tmp_path, [t1, t2])
    config = {"teams": first + second, "parallel_games": {"U10": 2}}

    result = replan_around_baseline(
        season="2026-2027",
        config=config,
        scraping_result=None,
        start_date=date(2026, 9, 1),
        end_date=date(2027, 4, 30),
        root=root,
        engine="local_search",
        request={"iterations": 200, "seed": 3},
    )

    assert result["lock_violations"] == []
    assert {t["id"] for t in result["candidate"]["tournaments"]} == {"t1", "t2"}
    assert result["change_cost"]["baseline_tournament_count"] == 2
    assert result["verification"]["ok"], result["verification"]["violations"]


def test_season_diff_cli_reports_change_cost(tmp_path, capsys):
    from tournament_scheduler.cli.rvv_cli import main

    root = _promote(tmp_path, [_tournament("t1")])
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(
        json.dumps(_plan([_tournament("t1", date_str="2026-09-19")])), encoding="utf-8"
    )

    rc = main(
        [
            "season",
            "diff",
            "--season",
            "2026-2027",
            "--candidate",
            str(candidate_path),
            "--root",
            str(root),
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "placement: 1" in output
    assert "total: 3.0" in output


# ---------------------------------------------------------------------------
# Default-path baseline awareness (issue #355)
# ---------------------------------------------------------------------------


def test_resolve_canonical_state_from_planning_window(tmp_path):
    from tournament_scheduler.canonical_baseline import (
        resolve_canonical_baseline,
        resolve_canonical_season,
        resolve_canonical_state,
    )

    root = _promote(tmp_path, [_tournament("t1")])

    # A Sep-Apr planning window anchors the season id on either calendar year.
    assert resolve_canonical_season({}, date(2026, 9, 1), date(2027, 4, 30), root=root) == "2026-2027"
    assert resolve_canonical_season({}, date(2027, 1, 5), date(2027, 4, 30), root=root) == "2026-2027"
    state = resolve_canonical_state({}, date(2026, 9, 1), date(2027, 4, 30), root=root)
    assert state is not None
    assert state["season"] == "2026-2027"
    assert state["baseline"]["tournaments"][0]["id"] == "t1"
    assert resolve_canonical_baseline({}, date(2026, 9, 1), date(2027, 4, 30), root=root) == state["baseline"]

    # No canonical season for an unrelated window.
    assert resolve_canonical_state({}, date(2029, 9, 1), date(2030, 4, 30), root=root) is None


def test_default_stage3_adopts_promoted_baseline(tmp_path):
    """A normal Stage 3 run must start from canonical state, not regenerate."""
    from datetime import datetime

    from tournament_scheduler.pipeline.stage3_planning import run

    root = _promote(
        tmp_path,
        [
            _tournament("t1", date_str="2026-09-12"),
            _tournament("t2", date_str="2026-10-10"),
        ],
    )
    config = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "age_groups": ["U10"],
        "parallel_games": {"U10": 2},
        "teams": _teams(),
        "canonical_season_root": str(root),
    }
    state = PipelineState(tmp_path / ".pipeline")

    result = run(
        config,
        {},
        state,
        datetime(2026, 9, 1),
        datetime(2027, 4, 30),
        today=date(2026, 8, 1),
    )

    assert result["plan_source"] == "canonical_baseline"
    assert result["canonical_state"]["season"] == "2026-2027"
    # Durable ids survive; no wholly regenerated season replaced the baseline.
    assert {t["id"] for t in result["plan"]["tournaments"]} == {"t1", "t2"}
    assert {t["date"] for t in result["plan"]["tournaments"]} == {"2026-09-12", "2026-10-10"}
    assert result["canonical_baseline_verification"]["ok"]
    assert state.is_done(StageName.PLANNING)


def test_export_verification_problem_carries_canonical_locks(tmp_path):
    from tournament_scheduler.pipeline.stage4_export_verification import (
        _build_export_verification_problem,
    )

    root = _promote(tmp_path, [_tournament("t1")])
    approve_tournament(season="2026-2027", tournament_id="t1", root=root, actor="booker")

    state = PipelineState(tmp_path / ".pipeline")
    state.write_stage(
        StageName.CONFIG,
        {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "teams": _teams(),
            "canonical_season_root": str(root),
        },
        status=StageStatus.DONE,
    )

    problem = _build_export_verification_problem(
        {"start_date": "2026-09-01", "end_date": "2027-04-30", "teams": _teams(), "canonical_season_root": str(root)},
        state,
    )

    assert problem is not None
    assert problem["canonical_baseline"] is not None
    moved = _plan([_tournament("t1", date_str="2026-09-19")])
    assert not verify_candidate(moved, problem)["ok"]


def test_export_verification_problem_prefers_canonical_calendar_evidence(tmp_path):
    """A promoted season's export gate must use canonical, refreshed calendar
    evidence, not a stale ``.pipeline`` Stage 2 checkpoint.

    ``season refresh-calendars`` writes live evidence into canonical
    ``schedule.json``'s ``verification_context`` -- the same source every
    ordinary ``season`` command (``load_context``/``_problem_from_schedule``)
    verifies against. The Stage 4 export gate historically rebuilt its
    calendar evidence from the mutable ``.pipeline/stage2_scraping.json``
    checkpoint instead, which ``refresh-calendars`` never touches, so an
    already-confirmed booking could look stale/unconfirmed at export/publish
    time while ``season findings`` correctly reported it as fine.
    """

    from tournament_scheduler.pipeline.stage4_export_verification import (
        _build_export_verification_problem,
    )

    root = _promote(tmp_path, [_tournament("t1")])

    fresh_event = {
        "availability": "fixed_busy",
        "calendar_event": "U10 Serierunde",
        "date": "2026-09-12",
        "start": "10:00",
        "end": "11:50",
        "club": "A",
    }
    schedule_path = root / "2026-2027" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    schedule["verification_context"]["problem"]["club_busy_intervals"] = {"A": [fresh_event]}
    schedule["verification_context"]["problem"]["club_calendar_status"] = {"A": "known"}
    schedule_path.write_text(json.dumps(schedule), encoding="utf-8")

    state = PipelineState(tmp_path / ".pipeline")
    state.write_stage(
        StageName.CONFIG,
        {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "teams": _teams(),
            "canonical_season_root": str(root),
        },
        status=StageStatus.DONE,
    )
    # A stale Stage 2 pipeline checkpoint that predates the refresh above --
    # this must not shadow the canonical evidence for a promoted season.
    state.write_stage(
        StageName.SCRAPING,
        {"events_by_club": {}, "club_calendar_status": {"A": "unknown"}},
        status=StageStatus.DONE,
    )

    problem = _build_export_verification_problem(
        {"start_date": "2026-09-01", "end_date": "2027-04-30", "teams": _teams(), "canonical_season_root": str(root)},
        state,
    )

    assert problem is not None
    assert problem["club_busy_intervals"]["A"] == [fresh_event]
    assert problem["club_calendar_status"]["A"] == "known"


def test_export_verification_problem_respects_genuinely_empty_canonical_calendar(tmp_path):
    """A canonical calendar that legitimately proves a club is fully free must
    not be backfilled with a stale Stage 2 scrape's busy events.

    ``verification_context.problem`` existing at all (even with an empty
    ``club_busy_intervals``) is itself the canonical refresh's authoritative
    answer for this club -- proven by its own ``club_calendar_status``/
    ``club_coverage_proven`` markers, not inferred from list emptiness.
    """

    from tournament_scheduler.pipeline.stage4_export_verification import (
        _build_export_verification_problem,
    )

    root = _promote(tmp_path, [_tournament("t1")])

    schedule_path = root / "2026-2027" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    canonical_problem = schedule["verification_context"]["problem"]
    # The refresh genuinely found nothing for club A -- a fully cleared
    # calendar, proven current, not "we never checked".
    canonical_problem["club_busy_intervals"] = {}
    canonical_problem["club_calendar_status"] = {"A": "known"}
    canonical_problem["club_coverage_proven"] = {"A": True}
    schedule_path.write_text(json.dumps(schedule), encoding="utf-8")

    state = PipelineState(tmp_path / ".pipeline")
    state.write_stage(
        StageName.CONFIG,
        {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "teams": _teams(),
            "canonical_season_root": str(root),
        },
        status=StageStatus.DONE,
    )
    # A stale Stage 2 checkpoint that (incorrectly, from an older scrape)
    # still shows club A as busy -- this must never leak through once
    # canonical evidence exists for the season, even though canonical's own
    # club_busy_intervals is empty.
    stale_event = {
        "date": "2026-09-12",
        "name": "Stale stage2 event",
        "datetime": "2026-09-12T10:00:00",
        "duration_hours": 1.5,
    }
    state.write_stage(
        StageName.SCRAPING,
        {"events_by_club": {"A": [stale_event]}, "club_calendar_status": {"A": "known"}},
        status=StageStatus.DONE,
    )

    problem = _build_export_verification_problem(
        {"start_date": "2026-09-01", "end_date": "2027-04-30", "teams": _teams(), "canonical_season_root": str(root)},
        state,
    )

    assert problem is not None
    assert problem["club_busy_intervals"].get("A", []) == []
    assert problem["club_coverage_proven"]["A"] is True


def test_export_verification_problem_takes_all_calendar_fields_atomically(tmp_path):
    """Canonical vs. pipeline calendar evidence is an all-or-nothing choice.

    Construct canonical and Stage 2 evidence that disagree on two different
    fields (``club_busy_intervals`` and ``club_source_integrity``) with
    distinct markers, and assert the result is 100% one source or the other
    -- never a hybrid of canonical for one field and pipeline for another.
    """

    from tournament_scheduler.pipeline.stage4_export_verification import (
        _build_export_verification_problem,
    )

    root = _promote(tmp_path, [_tournament("t1")])

    canonical_event = {
        "availability": "fixed_busy",
        "calendar_event": "canonical event",
        "date": "2026-09-12",
        "start": "10:00",
        "end": "11:50",
        "club": "A",
    }
    schedule_path = root / "2026-2027" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    canonical_problem = schedule["verification_context"]["problem"]
    canonical_problem["club_busy_intervals"] = {"A": [canonical_event]}
    canonical_problem["club_source_integrity"] = {"A": "canonical_marker"}
    schedule_path.write_text(json.dumps(schedule), encoding="utf-8")

    state = PipelineState(tmp_path / ".pipeline")
    state.write_stage(
        StageName.CONFIG,
        {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "teams": _teams(),
            "canonical_season_root": str(root),
        },
        status=StageStatus.DONE,
    )
    stage2_event = {
        "date": "2026-09-19",
        "name": "pipeline event",
        "datetime": "2026-09-19T14:00:00",
        "duration_hours": 1.0,
    }
    state.write_stage(
        StageName.SCRAPING,
        {
            "events_by_club": {"A": [stage2_event]},
            "club_source_integrity": {"A": "pipeline_marker"},
        },
        status=StageStatus.DONE,
    )

    problem = _build_export_verification_problem(
        {"start_date": "2026-09-01", "end_date": "2027-04-30", "teams": _teams(), "canonical_season_root": str(root)},
        state,
    )

    assert problem is not None
    # Both fields must come from the same source. Either fully canonical...
    is_canonical = (
        problem["club_busy_intervals"].get("A") == [canonical_event]
        and problem["club_source_integrity"].get("A") == "canonical_marker"
    )
    # ...or fully pipeline -- but never one field from each.
    is_pipeline = (
        problem["club_source_integrity"].get("A") == "pipeline_marker"
        and problem["club_busy_intervals"].get("A") != [canonical_event]
    )
    assert is_canonical and not is_pipeline
    # A promoted season with resolvable canonical evidence always wins here.
    assert problem["club_busy_intervals"]["A"] == [canonical_event]
    assert problem["club_source_integrity"]["A"] == "canonical_marker"


def test_export_verification_problem_uses_stage2_checkpoint_when_not_canonical(tmp_path):
    """The from-scratch Stage 1-4 pipeline path (no promoted season) is unchanged:
    calendar evidence still comes from the Stage 2 checkpoint on disk."""

    from tournament_scheduler.pipeline.stage4_export_verification import (
        _build_export_verification_problem,
    )

    state = PipelineState(tmp_path / ".pipeline")
    stage2_event = {
        "date": "2026-09-12",
        "name": "Stage 2 event",
        "datetime": "2026-09-12T10:00:00",
        "duration_hours": 1.5,
    }
    state.write_stage(
        StageName.SCRAPING,
        {"events_by_club": {"A": [stage2_event]}, "club_calendar_status": {"A": "known"}},
        status=StageStatus.DONE,
    )

    problem = _build_export_verification_problem(
        {"start_date": "2026-09-01", "end_date": "2027-04-30", "teams": _teams()},
        state,
    )

    assert problem is not None
    assert problem["club_busy_intervals"].get("A")
    assert problem["club_calendar_status"]["A"] == "known"


def test_export_verification_problem_prefers_canonical_busy_dates(tmp_path):
    """``club_busy_dates`` must move with the rest of the canonical calendar
    snapshot, including when canonical proves a club has zero busy dates.

    Regression for a review finding on the calendar-evidence source fix: the
    overlay reused ``application.canonical_season.calendars._CALENDAR_PROBLEM_KEYS``
    (the exact key set ``season refresh-calendars`` treats as one coherent
    snapshot) rather than a hand-maintained subset that once omitted
    ``club_busy_dates`` entirely, silently leaving it sourced from the stale
    Stage 2 checkpoint even after every other calendar field had switched to
    canonical evidence.
    """

    from tournament_scheduler.pipeline.stage4_export_verification import (
        _build_export_verification_problem,
    )

    root = _promote(tmp_path, [_tournament("t1")])

    schedule_path = root / "2026-2027" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    canonical_problem = schedule["verification_context"]["problem"]
    # Canonical refresh proves club A has zero busy dates -- an authoritative
    # empty result, not "we never checked".
    canonical_problem["club_busy_dates"] = {}
    canonical_problem["club_calendar_status"] = {"A": "known"}
    canonical_problem["club_coverage_proven"] = {"A": True}
    schedule_path.write_text(json.dumps(schedule), encoding="utf-8")

    state = PipelineState(tmp_path / ".pipeline")
    state.write_stage(
        StageName.CONFIG,
        {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "teams": _teams(),
            "canonical_season_root": str(root),
        },
        status=StageStatus.DONE,
    )
    # Stale Stage 2 checkpoint still claims club A is busy on a date the
    # canonical refresh has since proven clear -- must not leak through.
    stale_event = {
        "date": "2026-09-19",
        "name": "Stale stage2 busy date",
        "datetime": "2026-09-19T09:00:00",
        "duration_hours": 2.0,
    }
    state.write_stage(
        StageName.SCRAPING,
        {"events_by_club": {"A": [stale_event]}, "club_calendar_status": {"A": "known"}},
        status=StageStatus.DONE,
    )

    problem = _build_export_verification_problem(
        {"start_date": "2026-09-01", "end_date": "2027-04-30", "teams": _teams(), "canonical_season_root": str(root)},
        state,
    )

    assert problem is not None
    assert problem["club_busy_dates"].get("A", []) == []
    assert "2026-09-19" not in problem["club_busy_dates"].get("A", [])


def test_export_verification_problem_prefers_canonical_busy_dates_when_different(tmp_path):
    """A different (not merely absent) canonical busy-dates value also wins."""

    from tournament_scheduler.pipeline.stage4_export_verification import (
        _build_export_verification_problem,
    )

    root = _promote(tmp_path, [_tournament("t1")])

    schedule_path = root / "2026-2027" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    canonical_problem = schedule["verification_context"]["problem"]
    canonical_problem["club_busy_dates"] = {"A": ["2026-09-26"]}
    schedule_path.write_text(json.dumps(schedule), encoding="utf-8")

    state = PipelineState(tmp_path / ".pipeline")
    state.write_stage(
        StageName.CONFIG,
        {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "teams": _teams(),
            "canonical_season_root": str(root),
        },
        status=StageStatus.DONE,
    )
    stale_event = {
        "date": "2026-09-19",
        "name": "Stale stage2 busy date",
        "datetime": "2026-09-19T09:00:00",
        "duration_hours": 2.0,
    }
    state.write_stage(
        StageName.SCRAPING,
        {"events_by_club": {"A": [stale_event]}, "club_calendar_status": {"A": "known"}},
        status=StageStatus.DONE,
    )

    problem = _build_export_verification_problem(
        {"start_date": "2026-09-01", "end_date": "2027-04-30", "teams": _teams(), "canonical_season_root": str(root)},
        state,
    )

    assert problem is not None
    assert problem["club_busy_dates"]["A"] == ["2026-09-26"]


def test_export_verification_problem_calendar_keys_match_refresh_owned_contract():
    """Guard against the export gate's calendar-evidence key set silently
    drifting away from the set ``season refresh-calendars`` itself owns."""

    import inspect

    from tournament_scheduler.application.canonical_season.calendars import (
        _CALENDAR_PROBLEM_KEYS,
    )
    from tournament_scheduler.pipeline import stage4_export_verification

    assert "club_busy_dates" in _CALENDAR_PROBLEM_KEYS
    source = inspect.getsource(stage4_export_verification._prefer_canonical_calendar_evidence)
    assert "_CALENDAR_PROBLEM_KEYS" in source


def test_export_verification_problem_degrades_when_canonical_calendar_evidence_missing(tmp_path):
    """A promoted season whose ``verification_context.problem`` cannot be
    resolved falls back to the Stage 2 checkpoint rather than crashing --
    documented, intentional best-effort degradation for this specific export
    chokepoint. It is not the fail-closed gate for this condition: ordinary
    ``season`` commands (``_problem_from_schedule``) raise
    ``SeasonMaintenanceError`` and the publish preflight
    (``resolve_publish_verification_context``) raises
    ``VerificationContextError`` when canonical calendar evidence is
    genuinely missing for a promoted season, and both run before export
    output reaches the public artifact.
    """

    from tournament_scheduler.pipeline.stage4_export_verification import (
        _build_export_verification_problem,
    )

    root = _promote(tmp_path, [_tournament("t1")])

    # Simulate a legacy/corrupted promoted season whose schedule predates the
    # verification_context.problem contract.
    schedule_path = root / "2026-2027" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    del schedule["verification_context"]
    schedule_path.write_text(json.dumps(schedule), encoding="utf-8")

    from tournament_scheduler.season_maintenance import (
        SeasonMaintenanceError,
        load_context,
    )

    # The ordinary season command path fails closed for this exact condition.
    with pytest.raises(SeasonMaintenanceError):
        load_context("2026-2027", root=root)

    state = PipelineState(tmp_path / ".pipeline")
    state.write_stage(
        StageName.CONFIG,
        {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "teams": _teams(),
            "canonical_season_root": str(root),
        },
        status=StageStatus.DONE,
    )
    stage2_event = {
        "date": "2026-09-12",
        "name": "Stage 2 event",
        "datetime": "2026-09-12T10:00:00",
        "duration_hours": 1.5,
    }
    state.write_stage(
        StageName.SCRAPING,
        {"events_by_club": {"A": [stage2_event]}, "club_calendar_status": {"A": "known"}},
        status=StageStatus.DONE,
    )

    # The export chokepoint instead degrades gracefully, per its documented
    # best-effort contract -- it does not crash, and it is not itself the
    # fail-closed gate for a corrupted canonical calendar snapshot.
    problem = _build_export_verification_problem(
        {"start_date": "2026-09-01", "end_date": "2027-04-30", "teams": _teams(), "canonical_season_root": str(root)},
        state,
    )

    assert problem is not None
    assert problem["club_busy_intervals"].get("A")


def _team(club, label, age_group="U10"):
    return {"club": club, "label": label, "age_group": age_group}


def _slot_candidate():
    teams = [_team("A", "A1"), _team("B", "B1"), _team("C", "C1"), _team("D", "D1")]
    other = [_team("A", "A1"), _team("B", "B1"), _team("E", "E1"), _team("F", "F1")]
    return _plan(
        [
            _tournament("t1", teams=teams),
            _tournament("t2", date_str="2026-10-10", arena="Arena B", host="A", teams=other),
        ]
    )


def test_search_state_folds_in_canonical_change_cost():
    from tournament_scheduler.canonical_baseline import DEFAULT_CHANGE_WEIGHTS
    from tournament_scheduler.stage3_optimizer import (
        DEFAULT_WEIGHTS,
        _SearchState,
        _build_slots,
        _resolve_weights,
    )

    candidate = _slot_candidate()
    baseline = build_canonical_baseline(_schedule(candidate["tournaments"]), {"decisions": {}})
    slots, _ = _build_slots(candidate, None)
    weights_by_age_group = {
        slot.age_group: _resolve_weights(DEFAULT_WEIGHTS, None, slot.age_group) for slot in slots
    }
    state = _SearchState(slots, weights_by_age_group, baseline=baseline)

    # The candidate equals the canonical baseline, so there is no change cost.
    assert state.change_total == 0.0
    assert state.total == state.full_objective(DEFAULT_WEIGHTS)

    # Swapping two teams across the two tournaments changes both participant
    # lists -> two participant changes, folded into the objective.
    state.apply_team_swap(0, 2, 1, 2)
    assert state.change_total == 2 * DEFAULT_CHANGE_WEIGHTS["participants"]
    assert state.total == pytest.approx(state.full_objective(DEFAULT_WEIGHTS), abs=1e-6)

    # Its own inverse restores both the fairness score and the change cost.
    state.apply_team_swap(0, 2, 1, 2)
    assert state.change_total == 0.0
    assert state.total == pytest.approx(state.full_objective(DEFAULT_WEIGHTS), abs=1e-6)


def test_optimizer_never_moves_placement_locked_tournament():
    from tournament_scheduler.stage3_optimizer import optimize_candidate

    candidate = _slot_candidate()
    baseline = build_canonical_baseline(
        _schedule(candidate["tournaments"]),
        {"decisions": {"t1": {"placement_locked": True}}},
    )
    problem = {
        "parallel_games": {"U10": 2},
        "clubs": {"A": "Arena A", "B": "Arena B", "C": "Arena C", "D": "Arena D", "E": "Arena E", "F": "Arena F"},
        "club_calendar_status": {},
        "canonical_baseline": baseline,
        "christmas_split_date": None,
    }

    result = optimize_candidate(
        candidate,
        problem,
        iterations=400,
        seed=1,
        move_dates=True,
        move_hosts=True,
        move_slots=True,
    )

    locked = next(t for t in result["tournaments"] if t["id"] == "t1")
    assert locked["date"] == "2026-09-12"
    assert locked["host_club"] == "A"
    assert locked["start_time"] == "10:00"
    assert result["source"]["canonical_baseline"]["placed_locked_slots"] == 1


def test_default_season_root_respects_env_override(monkeypatch):
    from tournament_scheduler.canonical_baseline import (
        SEASON_ROOT_ENV_VAR,
        default_season_root,
    )

    monkeypatch.delenv(SEASON_ROOT_ENV_VAR, raising=False)
    assert default_season_root() == "season"
    monkeypatch.setenv(SEASON_ROOT_ENV_VAR, "/tmp/canonical-root")
    assert default_season_root() == "/tmp/canonical-root"


def test_high_change_cost_suppresses_published_churn():
    from tournament_scheduler.stage3_optimizer import optimize_candidate

    candidate = _slot_candidate()
    baseline = build_canonical_baseline(_schedule(candidate["tournaments"]), {"decisions": {}})
    problem = {
        "parallel_games": {"U10": 2},
        "canonical_baseline": baseline,
        # An implausibly steep change-cost scale must dominate every soft
        # fairness improvement, so the search keeps the published baseline.
        "canonical_change_cost_scale": 1_000_000.0,
        "christmas_split_date": None,
    }

    result = optimize_candidate(candidate, problem, iterations=500, seed=2)

    assert change_cost(baseline, result)["total"] == 0.0


def test_default_stage3_refuses_unreadable_canonical_state(tmp_path):
    """Corrupt canonical state must fail loudly, never fall back to a fresh season."""
    from datetime import datetime

    import pytest as _pytest

    from tournament_scheduler.pipeline.stage3_planning import Stage3Error, run

    season_dir = tmp_path / "season" / "2026-2027"
    season_dir.mkdir(parents=True)
    (season_dir / "schedule.json").write_text(
        json.dumps({"schema_version": 1, "season": "2026-2027", "plan": _plan([_tournament("t1")])}),
        encoding="utf-8",
    )
    # decisions.json is intentionally missing -> unreadable canonical state.
    config = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "age_groups": ["U10"],
        "parallel_games": {"U10": 2},
        "teams": _teams(),
        "canonical_season_root": str(tmp_path / "season"),
    }
    state = PipelineState(tmp_path / ".pipeline")

    with _pytest.raises(Stage3Error):
        run(config, {}, state, datetime(2026, 9, 1), datetime(2027, 4, 30), today=date(2026, 8, 1))
