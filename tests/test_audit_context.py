"""Tests for tournament_scheduler.pipeline.audit_context (issue #325)."""

from __future__ import annotations

import json

from tournament_scheduler.pipeline.audit_context import AUDIT_CHECKLIST, build_audit_context
from tournament_scheduler.pipeline.run_manifest import RunManifest
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus


def _write_export(work_dir, *, fingerprint: str, verify_ok: bool = True) -> None:
    export_dir = work_dir / "export"
    export_dir.mkdir(exist_ok=True)
    (export_dir / "evidence_bundle.json").write_text(
        json.dumps({"source_summary": {"sources_scanned": 3, "blocked_sources": []}}), encoding="utf-8"
    )
    PipelineState(work_dir).write_stage(
        StageName.EXPORT,
        {
            "export_dir": str(export_dir),
            "output_files": {"html": str(export_dir / "season_plan.html")},
            "verify_result": {"ok": verify_ok, "violations": []},
            "export_fingerprint": fingerprint,
        },
        status=StageStatus.DONE,
    )


def test_checklist_has_nine_items_in_order():
    assert [item["item_id"] for item in AUDIT_CHECKLIST] == list(range(1, 10))
    assert AUDIT_CHECKLIST[8]["question"].startswith("Ser harnesset")


def test_context_reflects_the_latest_export_not_a_stale_one(tmp_path):
    _write_export(tmp_path, fingerprint="fp-1")
    first = build_audit_context(work_dir=tmp_path)
    assert first["export_fingerprint"] == "fp-1"

    _write_export(tmp_path, fingerprint="fp-2")
    second = build_audit_context(work_dir=tmp_path)
    assert second["export_fingerprint"] == "fp-2"


def test_context_includes_run_and_source_fingerprints(tmp_path):
    RunManifest(tmp_path).start_run("test objective")
    _write_export(tmp_path, fingerprint="fp-1")
    context = build_audit_context(work_dir=tmp_path)
    assert "run_id" in context
    assert "source_fingerprints" in context
    assert set(context["source_fingerprints"]) == {"input_fingerprint", "effective_config_fingerprint"}


def test_context_includes_evidence_inventory(tmp_path):
    _write_export(tmp_path, fingerprint="fp-1")
    context = build_audit_context(work_dir=tmp_path)
    assert context["output_files"]
    assert context["deterministic_verify_result"] == {"ok": True, "violations": []}
    assert context["publication_readiness"] is not None
    assert context["evidence_bundle"] == {"source_summary": {"sources_scanned": 3, "blocked_sources": []}}
    assert context["calendar_evidence_summary"] == {"sources_scanned": 3, "blocked_sources": []}
    assert "plan_audit_summary" in context
    assert "export_consistency_summary" in context
    assert "missing rules" in context["audit_mission"]["purpose"]
    assert [item["item_id"] for item in context["checklist_evidence_guide"]] == list(range(1, 10))


def test_context_falls_back_to_stage2_calendar_summary_when_bundle_missing(tmp_path):
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    PipelineState(tmp_path).write_stage(
        StageName.SCRAPING,
        {
            "sources": [
                {"name": "Jar", "type": "ical", "event_count": 12, "from_cache": True},
                {"name": "Holmen", "type": "html", "events": [{"title": "A"}], "blocked": False},
            ],
            "events_by_club": {"Jar": [{"title": "A"}], "Holmen": []},
            "club_calendar_status": {"Jar": "ok", "Holmen": "sparse"},
            "blocked": [],
            "empty_sources": ["Holmen"],
            "cached": ["Jar"],
            "start_date": "2026-10-01",
            "end_date": "2027-03-31",
        },
        status=StageStatus.DONE,
    )
    PipelineState(tmp_path).write_stage(
        StageName.EXPORT,
        {
            "export_dir": str(export_dir),
            "output_files": {},
            "verify_result": {"ok": True, "violations": []},
            "export_fingerprint": "fp-1",
        },
        status=StageStatus.DONE,
    )

    context = build_audit_context(work_dir=tmp_path)

    assert context["evidence_bundle"] is None
    assert context["calendar_evidence_summary"]["sources_scanned"] == 2
    assert context["calendar_evidence_summary"]["event_counts_by_club"] == {"Jar": 1, "Holmen": 0}
    assert context["calendar_evidence_summary"]["per_source"][0]["name"] == "Jar"


def test_context_includes_selected_plan_cross_checks(tmp_path):
    PipelineState(tmp_path).write_stage(
        StageName.CONFIG,
        {"round_length_minutes": {"U10": 15}, "ice_time_minutes": {"U10": 30}, "parallel_games": {"U10": 3}},
        status=StageStatus.DONE,
    )
    PipelineState(tmp_path).write_stage(
        StageName.PLANNING,
        {
            "plan": {
                "team_game_counts": {"A": 1, "B": 1, "C": 1},
                "tournaments": [
                    {
                        "id": "t1",
                        "date": "2026-10-10",
                        "age_group": "U10",
                        "arena": "Arena",
                        "host_club": "Jar",
                        "start_time": "10:00",
                        "teams": [
                            {"label": "A", "club": "Jar", "age_group": "U10"},
                            {"label": "B", "club": "Jar", "age_group": "U10"},
                            {"label": "C", "club": "Jar", "age_group": "U10"},
                        ],
                        "games": [
                            {"home": "A", "away": "B", "round_number": 1},
                            {"home": "A", "away": "C", "round_number": 2},
                        ],
                    },
                    {
                        "id": "t2",
                        "date": "2026-10-10",
                        "age_group": "U10",
                        "arena": "Arena 2",
                        "host_club": "Holmen",
                        "teams": [{"label": "A", "club": "Jar", "age_group": "U10"}],
                        "games": [],
                    },
                ],
                "club_participation_fairness": [
                    {"age_group": "U10", "period": None, "club": "Jar", "target_share": 0.4, "actual_share": 0.4},
                ],
                "unresolved_participation_shortfalls": [
                    {
                        "club": "Jar",
                        "label": "A",
                        "age_group": "U10",
                        "actual": "4",
                        "target": "5",
                        "category": "participation_under_target_club_share_ok",
                        "reason": "klubben har likevel fått sin forholdsmessige andel",
                    },
                ],
            }
        },
        status=StageStatus.DONE,
    )
    _write_export(tmp_path, fingerprint="fp-1")

    context = build_audit_context(work_dir=tmp_path)
    summary = context["plan_audit_summary"]

    assert summary["tournament_count"] == 2
    assert summary["game_count"] == 2
    assert summary["csv_pause_row_count"] == 2
    assert summary["expected_csv_game_rows"] == 4
    assert summary["duration_summary"]["max_minutes"] == 40
    assert summary["duration_summary"]["missing_duration_count"] == 1
    utilisation = summary["tournament_utilisation_summary"]
    assert utilisation["tournaments_with_byes_or_invalid_no_bye_roster"] == 2
    assert utilisation["bye_examples"][0]["configured_parallel_game_capacity"] == 6
    assert utilisation["bye_examples"][0]["full_capacity_game_count"] == 15
    assert utilisation["underfilled_by_age_group"] == {"U10": 2}
    assert summary["host_participation_summary"]["tournaments_where_host_club_not_in_participants"] == 1
    assert summary["team_daily_participation_summary"]["duplicate_team_day_count"] == 1
    assert summary["same_club_per_tournament_summary"]["tournaments_with_more_than_two_from_same_club"] == 1

    # issue #327: per-team shortfall category/reason must reach the judge
    # alongside the club-level fairness rows, both directly on the plan
    # summary and referenced from checklist item 1's evidence guide.
    assert summary["club_participation_fairness"][0]["club"] == "Jar"
    assert summary["unresolved_participation_shortfalls"][0]["category"] == "participation_under_target_club_share_ok"
    item_1 = next(item for item in context["checklist_evidence_guide"] if item["item_id"] == 1)
    assert "plan_audit_summary.unresolved_participation_shortfalls" in item_1["primary_evidence"]
    assert item_1["summary"]["unresolved_participation_shortfalls"][0]["category"] == (
        "participation_under_target_club_share_ok"
    )


def test_unresolved_tournament_placements_reach_the_audit_context(tmp_path):
    """issue #330: a concrete tournament-placement search failure (a real
    roster/date that could not get a participant-host arena/time) must
    reach the audit evidence -- distinct from a hosting-obligation
    deficit -- with the exact roster preserved, not just a deduplicated
    club set."""
    PipelineState(tmp_path).write_stage(
        StageName.CONFIG,
        {"round_length_minutes": {"U10": 15}, "ice_time_minutes": {"U10": 30}},
        status=StageStatus.DONE,
    )
    PipelineState(tmp_path).write_stage(
        StageName.PLANNING,
        {
            "plan": {
                "tournaments": [],
                "unresolved_tournament_placements": [
                    {
                        "age_group": "U10",
                        "date": "2027-03-14",
                        "period": "before_christmas",
                        "candidate_hosts": ["Frisk Asker", "Jar"],
                        "participant_clubs": ["Frisk Asker", "Jar"],
                        "participant_teams": [
                            {"club": "Frisk Asker", "label": "Frisk Asker 1", "age_group": "U10"},
                            {"club": "Jar", "label": "Jar 1", "age_group": "U10"},
                        ],
                        "participant_team_count": 2,
                        "category": "manual_tournament_placement",
                        "search_attempted": True,
                        "reason": "no_participant_host_slot",
                    }
                ],
            }
        },
        status=StageStatus.DONE,
    )
    _write_export(tmp_path, fingerprint="fp-1")

    context = build_audit_context(work_dir=tmp_path)
    summary = context["plan_audit_summary"]

    assert summary["unresolved_tournament_placements"][0]["participant_team_count"] == 2
    assert summary["unresolved_tournament_placements"][0]["participant_teams"][0]["label"] == "Frisk Asker 1"

    item_2 = next(item for item in context["checklist_evidence_guide"] if item["item_id"] == 2)
    assert "plan_audit_summary.unresolved_tournament_placements" in item_2["primary_evidence"]
    assert item_2["summary"]["unresolved_tournament_placement_count"] == 1


def test_host_participation_cross_check_uses_shared_registration_constituents(tmp_path):
    PipelineState(tmp_path).write_stage(
        StageName.PLANNING,
        {
            "plan": {
                "tournaments": [
                    {
                        "id": "shared-host",
                        "date": "2027-01-16",
                        "age_group": "JU12",
                        "arena": "Kongsberghallen",
                        "host_club": "Kongsberg",
                        "teams": [
                            {"label": "Kongsberg/Tønsberg JU12", "club": "Kongsberg/Tønsberg", "age_group": "JU12"},
                            {"label": "Jar JU12", "club": "Jar", "age_group": "JU12"},
                        ],
                        "games": [],
                    }
                ]
            }
        },
        status=StageStatus.DONE,
    )
    _write_export(tmp_path, fingerprint="fp-1")

    context = build_audit_context(work_dir=tmp_path)
    host_summary = context["plan_audit_summary"]["host_participation_summary"]

    assert host_summary["tournaments_where_host_club_not_in_participants"] == 0
    assert host_summary["examples"] == []


def test_context_records_prompt_and_runbook_version(tmp_path):
    _write_export(tmp_path, fingerprint="fp-1")
    context = build_audit_context(work_dir=tmp_path)
    assert context["audit_prompt_version"] == 1
    assert context["runbook_version"]

    # Stable across repeated calls when SKILL.md hasn't changed.
    again = build_audit_context(work_dir=tmp_path)
    assert again["runbook_version"] == context["runbook_version"]


def test_context_without_any_export_has_no_fingerprint(tmp_path):
    context = build_audit_context(work_dir=tmp_path)
    assert context["export_fingerprint"] is None
