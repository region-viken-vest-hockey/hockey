from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from tournament_scheduler.infrastructure.canonical_calendar_snapshot_archive import (
    load_calendar_snapshot,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.season_maintenance import list_findings, repair_options, search
from tournament_scheduler.season_state import (
    SeasonStateError,
    booking_status_report,
    canonical_state_revision,
    load_decisions,
    load_schedule,
    move_tournament,
    promote_from_stage3,
    refresh_calendars,
    schedule_fingerprint,
    season_baseline_create,
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


def _patch_refresh_inputs(
    monkeypatch,
    *,
    busy: bool,
    source_url: str = "https://example.test/a.ics",
    source_type: str = "ical",
    sources: list[dict] | None = None,
    source_name: str = "Arena A",
) -> None:
    from tournament_scheduler.pipeline import stage1_config, stage2_scraping

    def fake_stage1_run(input_path, state, *, strict=True):
        state.write_stage(StageName.CONFIG, {"teams": [], "input_path": str(input_path)}, status=StageStatus.DONE)
        return {}

    def fake_effective_config(state, *, input_path=None):
        if sources is not None:
            return {"sources": sources}
        return {"sources": [{"name": source_name, "type": source_type, "url": source_url}]}

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
                    "name": source_name,
                    "type": source_type,
                    "url": source_url,
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


def _calendar_payload(problem: dict) -> dict:
    return {
        key: problem.get(key)
        for key in (
            "club_busy_dates",
            "club_busy_intervals",
            "club_calendar_status",
            "unclassified_calendar_events",
        )
    }


def test_refresh_records_source_policy_and_is_idempotent_for_unchanged_config(
    tmp_path: Path, monkeypatch
) -> None:
    """A refresh persists the promoted source policy; an unchanged config is not drift."""

    root = _promote(tmp_path)
    _patch_refresh_inputs(monkeypatch, busy=False)
    first = refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", actor="tester")

    assert first["source_policy_changes"] == []
    evidence = load_schedule("2026-2027", root=root)["verification_context"]["calendar_evidence"]
    assert evidence["source_policy"]["sources"][0]["name"] == "Arena A"
    assert evidence["source_policy_fingerprint"] == first["source_policy_fingerprint"]
    assert evidence["previous_source_policy_fingerprint"] is None

    second = refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", actor="tester")

    assert second["source_policy_changes"] == []
    assert second["previous_source_policy_fingerprint"] == first["source_policy_fingerprint"]


def test_refresh_refuses_source_policy_drift_without_explicit_opt_in(
    tmp_path: Path, monkeypatch
) -> None:
    """Today's workbook cannot silently change the meaning of promoted evidence."""

    root = _promote(tmp_path)
    _patch_refresh_inputs(monkeypatch, busy=False)
    refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", actor="tester")

    _patch_refresh_inputs(monkeypatch, busy=False, source_url="https://example.test/CHANGED.ics")
    before_revision = canonical_state_revision(
        load_schedule("2026-2027", root=root), load_decisions("2026-2027", root=root)
    )

    with pytest.raises(SeasonStateError):
        refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", actor="tester")
    assert (
        canonical_state_revision(
            load_schedule("2026-2027", root=root), load_decisions("2026-2027", root=root)
        )
        == before_revision
    )

    preview = refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", dry_run=True)
    assert preview["refused"] is True
    assert preview["source_policy_changes"] == [
        {
            "field": "sources",
            "source": "Arena A",
            "change": "modified",
            "fields": {
                "url": {
                    "before": "https://example.test/a.ics",
                    "after": "https://example.test/CHANGED.ics",
                }
            },
        }
    ]
    assert (
        canonical_state_revision(
            load_schedule("2026-2027", root=root), load_decisions("2026-2027", root=root)
        )
        == before_revision
    )

    accepted = refresh_calendars(
        season="2026-2027",
        root=root,
        input_path="input.xlsx",
        actor="tester",
        allow_source_policy_change=True,
    )

    assert accepted["source_policy_changes"] == preview["source_policy_changes"]
    evidence = load_schedule("2026-2027", root=root)["verification_context"]["calendar_evidence"]
    assert evidence["source_policy"]["sources"][0]["url"] == "https://example.test/CHANGED.ics"
    assert evidence["previous_source_policy_fingerprint"] == preview["previous_source_policy_fingerprint"]


def test_refresh_archives_pre_refresh_snapshot_on_first_refresh(tmp_path: Path, monkeypatch) -> None:
    """A pre-`calendar_evidence` season keeps the actual replaced payload, not just a hash."""

    root = _promote(tmp_path)
    before_context = load_schedule("2026-2027", root=root)["verification_context"]
    assert before_context.get("calendar_evidence") is None
    before_payload = _calendar_payload(before_context["problem"])

    _patch_refresh_inputs(monkeypatch, busy=True)
    result = refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", actor="tester")

    ref = result["previous_snapshot"]
    assert ref["had_prior_calendar_evidence"] is False
    assert ref["had_prior_source_policy"] is False
    snapshot = load_calendar_snapshot("2026-2027", ref, root=root)
    assert snapshot["calendar_payload"] == before_payload
    assert snapshot["calendar_fingerprint"] == result["previous_calendar_fingerprint"]
    assert snapshot["source_policy"] is None
    assert (root / "2026-2027" / ref["path"]).exists()


def test_refresh_drives_findings_repair_search_and_baseline(tmp_path: Path, monkeypatch) -> None:
    """Refreshed evidence feeds findings/repair/search/move and surfaces NEW findings."""

    root = _promote(tmp_path)
    _patch_refresh_inputs(monkeypatch, busy=False)
    refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", actor="tester")
    season_baseline_create(season="2026-2027", root=root, actor="tester", note="clean baseline")

    clean = list_findings("2026-2027", root=root)
    assert clean["baseline_comparison"]["active"] is True
    assert clean["baseline_comparison"]["ok_to_advance"] is True
    assert "manual_placement:u10-a-20260912" not in {f["finding_id"] for f in clean["findings"]}

    _patch_refresh_inputs(monkeypatch, busy=True)
    refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", actor="tester")

    after = list_findings("2026-2027", root=root)
    conflict_id = "manual_placement:u10-a-20260912"
    assert conflict_id in {finding["finding_id"] for finding in after["findings"]}
    comparison = after["baseline_comparison"]
    assert comparison["new_count"] >= 1
    assert comparison["ok_to_advance"] is False
    assert any(
        entry["status"] == "NEW" and entry["finding_id"] == conflict_id
        for entry in comparison["entries"]
    )

    options = repair_options("2026-2027", conflict_id, root=root)
    assert options["finding"]["finding_id"] == conflict_id
    search_result = search("2026-2027", conflict_id, root=root, dimensions=("host",))
    assert search_result["finding"]["finding_id"] == conflict_id

    preview = move_tournament(
        season="2026-2027",
        root=root,
        tournament_id="u10-a-20260912",
        start_time="11:00",
        dry_run=True,
        actor="tester",
    )
    moved_conflicts = preview["move_preview"]["verification_result"]["manual_external_conflict_placements"]
    assert any(placement["tournament_id"] == "u10-a-20260912" for placement in moved_conflicts)


def test_calendar_snapshot_archive_fails_closed_on_corruption(tmp_path: Path, monkeypatch) -> None:
    from tournament_scheduler.infrastructure.canonical_calendar_snapshot_archive import (
        CalendarSnapshotArchiveError,
    )

    root = _promote(tmp_path)
    _patch_refresh_inputs(monkeypatch, busy=True)
    result = refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", actor="tester")

    ref = result["previous_snapshot"]
    archive_path = root / "2026-2027" / ref["path"]
    archive_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(CalendarSnapshotArchiveError):
        load_calendar_snapshot("2026-2027", ref, root=root)


def test_refresh_detects_drift_after_zero_source_policy(tmp_path: Path, monkeypatch) -> None:
    """An empty recorded policy is authoritative, not treated as absent."""

    root = _promote(tmp_path)
    _patch_refresh_inputs(monkeypatch, busy=False, sources=[])
    refresh_calendars(
        season="2026-2027",
        root=root,
        input_path="input.xlsx",
        actor="tester",
        allow_missing_sources=True,
    )
    evidence = load_schedule("2026-2027", root=root)["verification_context"]["calendar_evidence"]
    assert evidence["source_policy"]["sources"] == []

    _patch_refresh_inputs(monkeypatch, busy=False)
    preview = refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", dry_run=True)

    assert preview["refused"] is True
    change = preview["source_policy_changes"][0]
    assert change["change"] == "added"
    assert change["source"] == "Arena A"


def test_refresh_detects_duplicate_source_name_drift(tmp_path: Path, monkeypatch) -> None:
    """A surviving same-name row must not mask an added/removed sibling."""

    root = _promote(tmp_path)
    base = [{"name": "Arena A", "type": "ical", "url": "https://example.test/a.ics"}]
    _patch_refresh_inputs(monkeypatch, busy=False, sources=base)
    refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", actor="tester")

    duplicate = base + [{"name": "Arena A", "type": "ical", "url": "https://example.test/b.ics"}]
    _patch_refresh_inputs(monkeypatch, busy=False, sources=duplicate)
    preview = refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", dry_run=True)

    assert preview["refused"] is True
    change = preview["source_policy_changes"][0]
    assert change["change"] == "multiset_changed"
    assert change["before_count"] == 1
    assert change["after_count"] == 2
    assert preview["refusal_reasons"]


def test_refresh_detects_stage2_dispatch_strategy_change(tmp_path: Path, monkeypatch) -> None:
    """A code-owned strategy/engine change is source-policy drift, not silence."""

    from tournament_scheduler.pipeline import scraper_strategies

    root = _promote(tmp_path)
    sources = [{"name": "Jutul", "type": "outlook", "url": "https://example.test/jutul/"}]
    _patch_refresh_inputs(monkeypatch, busy=False, sources=sources, source_name="Jutul")
    first = refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", actor="tester")

    assert first["source_policy_changes"] == []
    evidence = load_schedule("2026-2027", root=root)["verification_context"]["calendar_evidence"]
    entry = evidence["source_policy"]["sources"][0]
    assert entry["engine"] == "styled_calendar"
    assert entry["deterministic_scraper"] == "styledcalendar"
    assert entry["needs_llm_agent"] is True

    original = scraper_strategies.STRATEGIES["Jutul"]
    monkeypatch.setitem(
        scraper_strategies.STRATEGIES,
        "Jutul",
        dataclasses.replace(original, engine=scraper_strategies.CalendarEngine.OUTLOOK_IFRAME),
    )
    preview = refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", dry_run=True)

    assert preview["refused"] is True
    change = preview["source_policy_changes"][0]
    assert change["change"] == "modified"
    assert change["fields"]["engine"] == {"before": "styled_calendar", "after": "outlook_iframe"}


def test_load_calendar_snapshot_rejects_mismatched_reference(tmp_path: Path, monkeypatch) -> None:
    """A substituted path/digest must not resolve to valid-looking content."""

    from tournament_scheduler.infrastructure.canonical_calendar_snapshot_archive import (
        CalendarSnapshotArchiveError,
    )

    root = _promote(tmp_path)
    _patch_refresh_inputs(monkeypatch, busy=True)
    result = refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", actor="tester")
    ref = result["previous_snapshot"]

    with pytest.raises(CalendarSnapshotArchiveError):
        load_calendar_snapshot("2026-2027", {**ref, "sha256": "not-a-digest"}, root=root)
    with pytest.raises(CalendarSnapshotArchiveError):
        load_calendar_snapshot(
            "2026-2027",
            {**ref, "path": f"evidence/calendar/{'0' * 64}.json"},
            root=root,
        )


def test_refresh_legacy_duplicate_source_name_policy_has_no_false_drift(
    tmp_path: Path, monkeypatch
) -> None:
    """A legacy name/type/url-only duplicate-name policy compares on those fields."""

    root = _promote(tmp_path)
    legacy_sources = [
        {"name": "Arena A", "type": "ical", "url": "https://example.test/a.ics"},
        {"name": "Arena A", "type": "ical", "url": "https://example.test/b.ics"},
    ]
    schedule_path = root / "2026-2027" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    schedule["verification_context"]["calendar_evidence"] = {
        "schema_version": 1,
        "sources": legacy_sources,
    }
    schedule_path.write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    _patch_refresh_inputs(monkeypatch, busy=False, sources=legacy_sources)
    result = refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", actor="tester")

    assert result["source_policy_changes"] == []
    evidence = load_schedule("2026-2027", root=root)["verification_context"]["calendar_evidence"]
    # The current entries carry extra registry fields; the persisted policy now
    # pins them for the next refresh.
    assert evidence["source_policy"]["sources"][0]["club"] is None


def test_refresh_archive_survives_interleaved_writer(tmp_path: Path, monkeypatch) -> None:
    """The pre-refresh archive is installed in the same atomic swap as the reference."""

    from tournament_scheduler.application.canonical_season import lifecycle

    root = _promote(tmp_path)
    _patch_refresh_inputs(monkeypatch, busy=True)

    original_commit = lifecycle._commit

    def interfering_commit(service, snapshot, **kwargs):
        # Simulate another canonical writer swapping the season directory after a
        # standalone archive write but before this commit, dropping the file.
        archive_dir = root / "2026-2027" / "evidence" / "calendar"
        if archive_dir.is_dir():
            for archive_file in archive_dir.glob("*.json"):
                archive_file.unlink()
        return original_commit(service, snapshot, **kwargs)

    monkeypatch.setattr(lifecycle, "_commit", interfering_commit)
    result = refresh_calendars(season="2026-2027", root=root, input_path="input.xlsx", actor="tester")

    snapshot = load_calendar_snapshot("2026-2027", result["previous_snapshot"], root=root)
    assert snapshot["calendar_fingerprint"] == result["previous_calendar_fingerprint"]
    assert (root / "2026-2027" / result["previous_snapshot"]["path"]).exists()
