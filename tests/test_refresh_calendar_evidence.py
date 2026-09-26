from __future__ import annotations

import json
from pathlib import Path

from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.season_state import (
    booking_status_report,
    canonical_state_revision,
    load_decisions,
    load_schedule,
    promote_from_stage3,
    refresh_calendars,
    schedule_fingerprint,
    set_manual_booking_assertion,
)
from tournament_scheduler.testing.reviewed_export import build_problem_from_candidate, write_reviewed_stage4_export


def _candidate() -> dict:
    teams = [
        {"club": "A", "label": "A1", "age_group": "U10"},
        {"club": "B", "label": "B1", "age_group": "U10"},
        {"club": "C", "label": "C1", "age_group": "U10"},
        {"club": "D", "label": "D1", "age_group": "U10"},
    ]
    return {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [
            {
                "id": "u10-a-20260912",
                "date": "2026-09-12",
                "arena": "Arena A",
                "age_group": "U10",
                "host_club": "A",
                "teams": teams,
                "games": [
                    {"home": "A1", "away": "B1", "parallel_slot": 0, "round_number": 1},
                    {"home": "C1", "away": "D1", "parallel_slot": 1, "round_number": 1},
                    {"home": "A1", "away": "C1", "parallel_slot": 0, "round_number": 2},
                    {"home": "B1", "away": "D1", "parallel_slot": 1, "round_number": 2},
                    {"home": "A1", "away": "D1", "parallel_slot": 0, "round_number": 3},
                    {"home": "B1", "away": "C1", "parallel_slot": 1, "round_number": 3},
                ],
                "start_time": "10:00",
            }
        ],
    }


def _promote(tmp_path: Path) -> Path:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    candidate = _candidate()
    state.write_stage(StageName.PLANNING, {"plan": candidate}, status=StageStatus.DONE)
    problem = build_problem_from_candidate(candidate)
    problem["ice_time_minutes"] = {"U10": 120}
    problem["round_length_minutes"] = {"U10": 30}
    problem["parallel_games"] = {"U10": 2}
    problem["rounds_per_tournament"] = {"U10": 3}
    write_reviewed_stage4_export(state, problem=problem)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    return root


def _patch_refresh_inputs(monkeypatch, *, busy: bool) -> None:
    from tournament_scheduler.pipeline import stage1_config, stage2_scraping

    def fake_stage1_run(input_path, state, *, strict=True):
        state.write_stage(StageName.CONFIG, {"teams": [], "input_path": str(input_path)}, status=StageStatus.DONE)
        return {}

    def fake_effective_config(state, *, input_path=None):
        return {"sources": [{"name": "Arena A", "type": "ical", "url": "https://example.test/a.ics"}]}

    def fake_stage2_run(config, state, start_date, end_date, **kwargs):
        events = []
        if busy:
            events = [
                {
                    "date": "12.09.2026",
                    "name": "External booking",
                    "datetime": "2026-09-12T10:00:00",
                    "duration_hours": 2.0,
                }
            ]
        return {
            "sources": [
                {
                    "name": "Arena A",
                    "type": "ical",
                    "url": "https://example.test/a.ics",
                    "events": events,
                    "event_count": len(events),
                    "blocked": False,
                    "block_reason": "",
                    "llm_fallback": False,
                    "scrape_timestamp": "2026-08-01T12:00:00+00:00",
                }
            ],
            "events_by_club": {"A": events},
            "club_calendar_status": {"A": "known"},
            "blocked": [],
            "empty_sources": [],
            "cached": [],
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
        }

    monkeypatch.setattr(stage1_config, "run", fake_stage1_run)
    monkeypatch.setattr(stage1_config, "load_effective_config", fake_effective_config)
    monkeypatch.setattr(stage2_scraping, "run", fake_stage2_run)


def test_refresh_calendars_dry_run_does_not_mutate_schedule_or_decisions(tmp_path: Path, monkeypatch) -> None:
    root = _promote(tmp_path)
    _patch_refresh_inputs(monkeypatch, busy=True)
    before_schedule_bytes = (root / "2026-2027" / "schedule.json").read_bytes()
    before_decisions_bytes = (root / "2026-2027" / "decisions.json").read_bytes()

    result = refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", dry_run=True)

    assert result["dry_run"] is True
    assert result["changed"] is True
    assert result["manual_external_conflict_placements"]
    assert (root / "2026-2027" / "schedule.json").read_bytes() == before_schedule_bytes
    assert (root / "2026-2027" / "decisions.json").read_bytes() == before_decisions_bytes


def test_refresh_calendars_updates_only_evidence_and_marks_export_stale(tmp_path: Path, monkeypatch) -> None:
    root = _promote(tmp_path)
    _patch_refresh_inputs(monkeypatch, busy=False)
    before_schedule = load_schedule("2026-2027", root=root)
    before_decisions = load_decisions("2026-2027", root=root)
    before_revision = canonical_state_revision(before_schedule, before_decisions)
    before_plan = json.dumps(before_schedule["plan"], sort_keys=True)

    result = refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", actor="tester")

    after_schedule = load_schedule("2026-2027", root=root)
    after_decisions = load_decisions("2026-2027", root=root)
    assert json.dumps(after_schedule["plan"], sort_keys=True) == before_plan
    assert schedule_fingerprint(after_schedule["plan"]) == schedule_fingerprint(before_schedule["plan"])
    assert canonical_state_revision(after_schedule, after_decisions) != before_revision
    assert result["calendar_fingerprint"] == after_schedule["verification_context"]["calendar_evidence"]["calendar_fingerprint"]
    assert after_schedule["verification_context"]["calendar_evidence"]["sources"][0]["fingerprint"]
    assert after_decisions["export_state"]["status"] == "stale"
    assert after_decisions["export_state"]["requires_fresh_audit"] is True
    assert after_decisions["history"][-1]["event"] == "refresh_calendar_evidence"


def test_refresh_calendars_new_conflict_is_not_hidden_by_approval(tmp_path: Path, monkeypatch) -> None:
    root = _promote(tmp_path)
    # Simulate a pre-existing approval; a refreshed fixed-busy calendar conflict
    # must still be reported by verification/findings instead of waived.
    decisions_path = root / "2026-2027" / "decisions.json"
    decisions = json.loads(decisions_path.read_text())
    record = decisions["decisions"]["u10-a-20260912"]
    record["status"] = "approved"
    record["placement_locked"] = True
    record["approved_fingerprint"] = "legacy"
    decisions_path.write_text(json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _patch_refresh_inputs(monkeypatch, busy=True)

    result = refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", actor="tester")

    assert result["manual_external_conflict_placements"] == [
        {
            "tournament_id": "u10-a-20260912",
            "host_club": "A",
            "age_group": "U10",
            "date": "2026-09-12",
        }
    ]
    after_decisions = load_decisions("2026-2027", root=root)
    assert after_decisions["decisions"]["u10-a-20260912"]["status"] == "approved"


def test_refresh_calendars_preserves_manual_booking_assertion(tmp_path: Path, monkeypatch) -> None:
    """A calendar refresh must not erase or demote an explicit manual assertion."""

    from tournament_scheduler.calendar_bookings import MANUAL_BOOKING_ASSERTIONS_KEY

    root = _promote(tmp_path)
    set_manual_booking_assertion(
        season="2026-2027",
        root=root,
        tournament_id="u10-a-20260912",
        booking_status="booked",
        actor="booker",
        note="club confirmed by email; public calendar is not maintained",
        reference="email:1",
    )
    before = booking_status_report(season="2026-2027", root=root)
    assert before["tournaments"][0]["status"] == "manually_booked"

    _patch_refresh_inputs(monkeypatch, busy=True)
    refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", actor="tester")

    decisions = load_decisions("2026-2027", root=root)
    records = decisions[MANUAL_BOOKING_ASSERTIONS_KEY]
    assert [record["status"] for record in records] == ["active"]
    assert records[0]["authority"] == "manual_club_confirmation"
    after = booking_status_report(season="2026-2027", root=root)
    row = after["tournaments"][0]
    assert row["status"] == "manually_booked"
    assert row["authority"] == "manual_club_confirmation"
