"""Tests for tournament_scheduler.pipeline.audit_context (issue #325)."""

from __future__ import annotations

import json

from tournament_scheduler.pipeline.audit_context import (
    AUDIT_CHECKLIST,
    build_audit_context,
    build_audit_evidence,
)
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
    # issue #356: the default context never embeds the wholesale evidence
    # bundle; it exposes a bounded, queryable evidence index instead.
    assert "evidence_bundle" not in context
    assert context["calendar_evidence_summary"] == {"sources_scanned": 3, "blocked_sources": []}
    assert "plan_audit_summary" in context
    assert "export_consistency_summary" in context
    assert "missing rules" in context["audit_mission"]["purpose"]
    assert [item["item_id"] for item in context["checklist_evidence_guide"]] == list(range(1, 10))
    assert context["evidence_index"]["export_fingerprint"] == "fp-1"
    assert context["evidence_index"]["available_selectors"]["checklist_items"] == list(range(1, 10))
    assert context["evidence_index"]["evidence_commands"]["item"] == "operator audit-evidence --item <n>"


def test_context_keeps_normalized_booking_reconciliation_reference(tmp_path):
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    booking_path = export_dir / "booking_reconciliation.json"
    booking_path.write_text(
        json.dumps({"schema_version": 2, "events": {}, "tournaments": []}),
        encoding="utf-8",
    )
    (export_dir / "evidence_bundle.json").write_text(
        json.dumps({"source_summary": {"sources_scanned": 1, "blocked_sources": []}}),
        encoding="utf-8",
    )
    PipelineState(tmp_path).write_stage(
        StageName.EXPORT,
        {
            "export_dir": str(export_dir),
            "output_files": {
                "html": str(export_dir / "season_plan.html"),
                "booking_reconciliation": str(booking_path),
            },
            "verify_result": {"ok": True, "violations": []},
            "export_fingerprint": "fp-normalized-booking",
        },
        status=StageStatus.DONE,
    )

    context = build_audit_context(work_dir=tmp_path)

    assert context["output_files"]["booking_reconciliation"] == str(booking_path)


def test_bound_final_operator_evidence_overrides_stale_stage3_manual_state(tmp_path):
    RunManifest(tmp_path).start_run("test objective", run_id="run-1")
    PipelineState(tmp_path).write_stage(
        StageName.PLANNING,
        {
            "plan": {
                "tournaments": [],
                "unresolved_participation_shortfalls": [
                    {"club": "Jar", "label": "A", "age_group": "U10", "actual": "3", "target": "5"},
                    {"club": "Jar", "label": "B", "age_group": "U10", "actual": "3", "target": "5"},
                ],
                "publication_readiness": {
                    "status": "REVIEW_REQUIRED",
                    "publishable": False,
                    "reasons": [{"code": "participation_shortfalls", "count": 2}],
                },
            }
        },
        status=StageStatus.DONE,
    )
    _write_export(tmp_path, fingerprint="fp-1")
    export_dir = tmp_path / "export"
    (export_dir / "evidence_bundle.json").write_text(
        json.dumps(
            {
                "source_summary": {"sources_scanned": 3, "blocked_sources": []},
                "final_operator_evidence": {
                    "run_id": "run-1",
                    "export_fingerprint": "fp-1",
                    "final_candidate_fingerprint": "fp-1",
                    "publication_readiness": {
                        "status": "REVIEW_REQUIRED",
                        "publishable": False,
                        "reasons": [{"code": "participation_shortfalls", "count": 1}],
                    },
                    "unresolved_participation_shortfalls": [
                        {"club": "Jar", "label": "A", "age_group": "U10", "actual": "4", "target": "5"}
                    ],
                    "unresolved_tournament_placements": [
                        {"age_group": "U10", "date": "2027-03-14", "reason": "no_participant_host_slot"}
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    context = build_audit_context(work_dir=tmp_path)

    assert context["publication_readiness"]["reasons"] == [{"code": "participation_shortfalls", "count": 1}]
    assert context["plan_audit_summary"]["unresolved_participation_shortfall_count"] == 1
    evidence = build_audit_evidence(work_dir=tmp_path, category="participation_shortfalls")
    assert evidence["matched_record_count"] == 1
    assert evidence["records"][0]["detail"]["actual"] == "4"
    placements = build_audit_evidence(work_dir=tmp_path, category="unresolved_tournament_placements")
    assert placements["records"][0]["detail"]["reason"] == "no_participant_host_slot"


def test_mismatched_final_operator_evidence_does_not_override_stage3_state(tmp_path):
    RunManifest(tmp_path).start_run("test objective", run_id="run-1")
    PipelineState(tmp_path).write_stage(
        StageName.PLANNING,
        {"plan": {"tournaments": [], "unresolved_participation_shortfalls": [{"label": "stale-but-current-run"}]}},
        status=StageStatus.DONE,
    )
    _write_export(tmp_path, fingerprint="fp-1")
    export_dir = tmp_path / "export"
    (export_dir / "evidence_bundle.json").write_text(
        json.dumps(
            {
                "final_operator_evidence": {
                    "run_id": "run-1",
                    "export_fingerprint": "different-fingerprint",
                    "unresolved_participation_shortfalls": [{"label": "wrong-export"}],
                }
            }
        ),
        encoding="utf-8",
    )

    context = build_audit_context(work_dir=tmp_path)

    assert context["plan_audit_summary"]["unresolved_participation_shortfall_count"] == 1
    evidence = build_audit_evidence(work_dir=tmp_path, category="participation_shortfalls")
    assert evidence["records"][0]["detail"] == {"label": "stale-but-current-run"}


def test_context_surfaces_operator_waivers_and_waived_violations(tmp_path):
    from tournament_scheduler.season.waivers import create_waiver

    create_waiver(
        tmp_path,
        rule="participation_target_exceeded",
        team={"club": "Frisk Asker", "label": "Frisk Asker 4", "age_group": "U11"},
        tournament_id="e7276974",
        half="before_christmas",
        configured_value=5,
        allowed_value=6,
        reason="operator chose Frisk Asker 4 for host placement",
        actor="operator",
    )
    PipelineState(tmp_path).write_stage(
        StageName.EXPORT,
        {
            "export_dir": str(tmp_path),
            "output_files": {},
            "verify_result": {
                "ok": True,
                "violations": [],
                "waived_violations": [{"code": "participation_target_exceeded"}],
            },
            "export_fingerprint": "fp-1",
        },
        status=StageStatus.DONE,
    )
    context = build_audit_context(work_dir=tmp_path)
    assert context["operator_waivers"][0]["id"].startswith("waiver-")
    assert context["operator_waivers"][0]["reason"].startswith("operator chose")
    assert context["operator_waived_violations"] == [{"code": "participation_target_exceeded"}]


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

    assert "evidence_bundle" not in context
    assert context["calendar_evidence_summary"]["sources_scanned"] == 2
    assert context["calendar_evidence_summary"]["event_counts_by_club"] == {"Jar": 1, "Holmen": 0}
    assert context["calendar_evidence_summary"]["per_source"][0]["name"] == "Jar"


def test_context_includes_selected_plan_cross_checks(tmp_path):
    PipelineState(tmp_path).write_stage(
        StageName.CONFIG,
        {"round_length_minutes": {"U10": 15}, "ice_time_minutes": {"U10": 120}, "parallel_games": {"U10": 3}},
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
    assert summary["duration_summary"]["max_minutes"] == 120
    assert summary["duration_summary"]["missing_duration_count"] == 1
    utilisation = summary["tournament_utilisation_summary"]
    assert utilisation["tournaments_with_byes_or_invalid_no_bye_roster"] == 2
    assert utilisation["bye_examples"][0]["configured_parallel_game_capacity"] == 6
    assert utilisation["bye_examples"][0]["full_capacity_game_count"] == 15
    assert utilisation["underfilled_by_age_group"] == {"U10": 2}
    assert summary["host_participation_summary"]["tournaments_where_host_club_not_in_participants"] == 1
    assert summary["team_daily_participation_summary"]["duplicate_team_day_count"] == 1
    assert summary["same_club_per_tournament_summary"]["tournaments_with_more_than_two_from_same_club"] == 1

    # issue #327: per-team shortfall category/reason must be retrievable on
    # demand alongside the club-level fairness rows; the bounded overview
    # carries only counts, and checklist item 1 references the queryable
    # categories rather than re-embedding the lists.
    assert summary["club_participation_fairness_count"] == 1
    assert summary["unresolved_participation_shortfall_count"] == 1
    shortfalls = build_audit_evidence(work_dir=tmp_path, category="participation_shortfalls")
    assert shortfalls["records"][0]["finding_type"] == "participation_under_target_club_share_ok"
    fairness = build_audit_evidence(work_dir=tmp_path, category="club_participation_fairness")
    assert fairness["records"][0]["detail"]["club"] == "Jar"
    item_1 = next(item for item in context["checklist_evidence_guide"] if item["item_id"] == 1)
    assert "participation_shortfalls" in item_1["categories"]
    assert item_1["counts"]["participation_shortfalls"] == 1


def test_shape_evidence_uses_export_verification_eligible_pool_after_withdrawal(tmp_path):
    teams = [
        {"club": club, "label": f"{club} 1", "age_group": "U10"}
        for club in ("Alfa", "Bravo", "Charlie", "Delta", "Echo")
    ]
    config = {
        "teams": teams,
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
        "parallel_games": {"U10": 3},
    }
    tournament = {
        "id": "u10-after-withdrawal",
        "date": "2026-10-10",
        "age_group": "U10",
        "arena": "Alfa Arena",
        "host_club": "Alfa",
        "start_time": "10:00",
        "teams": teams[:4],
        "games": [],
    }
    PipelineState(tmp_path).write_stage(StageName.CONFIG, config, status=StageStatus.DONE)
    PipelineState(tmp_path).write_stage(
        StageName.PLANNING,
        {"plan": {"tournaments": [tournament]}},
        status=StageStatus.DONE,
    )
    _write_export(tmp_path, fingerprint="fp-withdrawal")
    export_checkpoint = PipelineState(tmp_path).read_stage(StageName.EXPORT)
    export_checkpoint["verification_context"] = {
        "problem": {
            **config,
            "withdrawn_tournament_teams": [
                {
                    "scope": "age_group",
                    "tournament_ids": ["u10-after-withdrawal"],
                    "effective_from": "2026-10-10",
                    "club": "Echo",
                    "label": "Echo 1",
                    "age_group": "U10",
                }
            ],
        }
    }
    PipelineState(tmp_path).write_stage(
        StageName.EXPORT,
        export_checkpoint,
        status=StageStatus.DONE,
    )

    summary = build_audit_context(work_dir=tmp_path)["plan_audit_summary"]
    utilisation = summary["tournament_utilisation_summary"]

    assert utilisation["tournaments_with_byes_or_invalid_no_bye_roster"] == 0
    assert utilisation["input_constrained_shape_count"] == 1
    assert utilisation["input_constrained_shape_examples"][0]["registered_team_count"] == 4


def test_unresolved_tournament_placements_reach_the_audit_context(tmp_path):
    """issue #330: a concrete tournament-placement search failure (a real
    roster/date that could not get a participant-host arena/time) must
    reach the audit evidence -- distinct from a hosting-obligation
    deficit -- with the exact roster preserved, not just a deduplicated
    club set."""
    PipelineState(tmp_path).write_stage(
        StageName.CONFIG,
        {"round_length_minutes": {"U10": 15}, "ice_time_minutes": {"U10": 120}},
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

    assert summary["unresolved_tournament_placement_count"] == 1
    evidence = build_audit_evidence(work_dir=tmp_path, category="unresolved_tournament_placements")
    assert evidence["records"][0]["detail"]["participant_team_count"] == 2
    assert evidence["records"][0]["detail"]["participant_teams"][0]["label"] == "Frisk Asker 1"

    item_2 = next(item for item in context["checklist_evidence_guide"] if item["item_id"] == 2)
    assert "unresolved_tournament_placements" in item_2["categories"]
    assert item_2["counts"]["unresolved_tournament_placements"] == 1


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
    assert context["audit_prompt_version"] == 2
    assert context["runbook_version"]

    # Stable across repeated calls when SKILL.md hasn't changed.
    again = build_audit_context(work_dir=tmp_path)
    assert again["runbook_version"] == context["runbook_version"]


def test_context_without_any_export_has_no_fingerprint(tmp_path):
    context = build_audit_context(work_dir=tmp_path)
    assert context["export_fingerprint"] is None


def test_context_plan_comes_from_the_exported_projection_not_a_stale_planning_stage(tmp_path):
    """A canonical `season export` writes only the EXPORT stage.

    The audit overview must describe the plan the export fingerprint is
    actually bound to (the reviewed/exported projection), never a Stage 3
    planning checkpoint left behind by an earlier run. Otherwise guest
    reservations and later canonical tournaments are invisible to the audit and
    the export-consistency check compares the export against the wrong plan.
    """
    stale_plan = {
        "tournaments": [
            {
                "id": "stale-1",
                "date": "2026-10-10",
                "age_group": "U10",
                "arena": "Stale ishall",
                "host_club": "Stale",
                "start_time": "10:00",
                "teams": [{"label": "Stale 1", "club": "Stale", "age_group": "U10"}],
                "games": [],
            }
        ]
    }
    PipelineState(tmp_path).write_stage(
        StageName.PLANNING, {"plan": stale_plan}, status=StageStatus.DONE
    )

    exported_plan = {
        "tournaments": [
            {
                "id": "rvv-0007",
                "date": "2026-10-11",
                "age_group": "JU10",
                "arena": "Skien ishall",
                "host_club": "Skien",
                "start_time": "10:00",
                "teams": [{"label": "Skien", "club": "Skien", "age_group": "JU10"}],
                "games": [],
                "guest_slots": [
                    {"id": "guest:rvv-0007:1", "status": "open", "note": "external team"}
                ],
                "reserved_guest_slots": 1,
            },
            {
                "id": "rvv-0008",
                "date": "2026-10-11",
                "age_group": "U11",
                "arena": "Tonsberghallen",
                "host_club": "Tønsberg",
                "start_time": "10:00",
                "teams": [{"label": "Tønsberg 1", "club": "Tønsberg", "age_group": "U11"}],
                "games": [],
            },
        ]
    }
    export_dir = tmp_path / "export"
    export_dir.mkdir(exist_ok=True)
    PipelineState(tmp_path).write_stage(
        StageName.EXPORT,
        {
            "export_dir": str(export_dir),
            "output_files": {},
            "verify_result": {"ok": True, "violations": []},
            "export_fingerprint": "fp-canonical",
            "reviewed_plan": exported_plan,
        },
        status=StageStatus.DONE,
    )

    context = build_audit_context(work_dir=tmp_path)
    summary = context["plan_audit_summary"]
    assert summary["tournament_count"] == 2
    assert summary["guest_reservation_summary"]["reserved_tournament_count"] == 1
    assert summary["guest_reservation_summary"]["open_slots"] == 1


def test_cancelled_tournaments_do_not_produce_blocking_bye_findings():
    from tournament_scheduler.pipeline.audit_context import _collect_plan_audit_facts

    teams = [{"club": club, "label": f"{club} 1", "age_group": "JU8"} for club in ("Alfa", "Bravo", "Charlie")]
    common = {
        "date": "2026-11-21",
        "age_group": "JU8",
        "arena": "Hall",
        "host_club": "Alfa",
        "start_time": "10:00",
        "teams": teams,
        "games": [],
    }
    plan = {
        "tournaments": [
            {**common, "id": "live-odd-roster"},
            {**common, "id": "cancelled-odd-roster", "cancelled": True, "cancellation_reason": "retired"},
        ]
    }

    facts = _collect_plan_audit_facts(plan_dict=plan, config_checkpoint=None, verification_problem=None)

    assert [row["id"] for row in facts["blocking_byes"]] == ["live-odd-roster"]


def test_cancelled_tournament_keeps_registered_pool_shape_evidence_without_blocking():
    from tournament_scheduler.pipeline.audit_context import _collect_plan_audit_facts

    teams = [{"club": club, "label": f"{club} 1", "age_group": "U10"} for club in ("Alfa", "Bravo", "Charlie", "Delta")]
    config = {
        "teams": teams,
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
        "parallel_games": {"U10": 3},
    }
    common = {"date": "2026-10-10", "age_group": "U10", "arena": "Hall", "host_club": "Alfa", "start_time": "10:00", "games": []}
    plan = {
        "tournaments": [
            {**common, "id": "live-short-roster", "teams": teams[:3]},
            {**common, "id": "cancelled-four-teams", "teams": teams[:4], "cancelled": True, "cancellation_reason": "ice"},
        ]
    }

    facts = _collect_plan_audit_facts(plan_dict=plan, config_checkpoint=config, verification_problem=None)

    assert [row["id"] for row in facts["blocking_byes"]] == ["live-short-roster"]
    assert [row["id"] for row in facts["input_constrained_shape_examples"]] == ["cancelled-four-teams"]


def _write_plan_with_external_conflicts(work_dir, *, host_clubs: list[str]) -> None:
    PipelineState(work_dir).write_stage(
        StageName.PLANNING,
        {
            "plan": {
                "tournaments": [],
                "unresolved_external_conflicts": [
                    {"tournament_id": f"t{i}", "host_club": club, "age_group": "U10", "date": "2026-11-01"}
                    for i, club in enumerate(host_clubs)
                ],
            }
        },
        status=StageStatus.DONE,
    )


def _write_source_summary(work_dir, source_summary: dict) -> None:
    export_dir = work_dir / "export"
    export_dir.mkdir(exist_ok=True)
    (export_dir / "evidence_bundle.json").write_text(
        json.dumps({"source_summary": source_summary}), encoding="utf-8"
    )


def test_zero_scanned_sources_leaves_every_external_conflict_unverified(tmp_path):
    _write_plan_with_external_conflicts(tmp_path, host_clubs=["Jar"] * 10)
    _write_export(tmp_path, fingerprint="fp-1")
    _write_source_summary(tmp_path, {"sources_scanned": 0, "blocked_sources": [], "club_calendar_status": {}})

    context = build_audit_context(work_dir=tmp_path)

    assert context["evidence_coverage"] == {"unverified_placements": 10, "unverified_clubs": ["Jar"]}


def test_unrelated_scanned_source_does_not_verify_an_affected_placement(tmp_path):
    # Holmen scanned fine, but the placement's own host club Jar is blocked.
    _write_plan_with_external_conflicts(tmp_path, host_clubs=["Jar"])
    _write_export(tmp_path, fingerprint="fp-1")
    _write_source_summary(
        tmp_path,
        {"sources_scanned": 1, "blocked_sources": ["Jar"], "club_calendar_status": {"Holmen": "known", "Jar": "blocked"}},
    )

    context = build_audit_context(work_dir=tmp_path)

    assert context["evidence_coverage"] == {"unverified_placements": 1, "unverified_clubs": ["Jar"]}


def test_placement_with_unrecorded_host_calendar_status_stays_unverified(tmp_path):
    # A host club absent from club_calendar_status is unknown, never known.
    _write_plan_with_external_conflicts(tmp_path, host_clubs=["Jar"])
    _write_export(tmp_path, fingerprint="fp-1")
    _write_source_summary(tmp_path, {"sources_scanned": 2, "blocked_sources": [], "club_calendar_status": {"Holmen": "known"}})

    context = build_audit_context(work_dir=tmp_path)

    assert context["evidence_coverage"]["unverified_placements"] == 1


def test_placement_verified_when_its_own_host_calendar_is_known(tmp_path):
    _write_plan_with_external_conflicts(tmp_path, host_clubs=["Jar", "Jar", "Holmen"])
    _write_export(tmp_path, fingerprint="fp-1")
    _write_source_summary(
        tmp_path,
        {"sources_scanned": 2, "blocked_sources": [], "club_calendar_status": {"Jar": "known", "Holmen": "blocked"}},
    )

    context = build_audit_context(work_dir=tmp_path)

    assert context["evidence_coverage"] == {"unverified_placements": 1, "unverified_clubs": ["Holmen"]}


def test_zero_scanned_sources_without_external_conflicts_reports_nothing_unverified(tmp_path):
    _write_export(tmp_path, fingerprint="fp-1")
    _write_source_summary(tmp_path, {"sources_scanned": 0, "blocked_sources": []})

    context = build_audit_context(work_dir=tmp_path)

    assert context["evidence_coverage"] == {"unverified_placements": 0, "unverified_clubs": []}


def test_cancelled_tournament_without_games_is_not_a_missing_duration_defect():
    from tournament_scheduler.pipeline.audit_context import _collect_plan_audit_facts

    plan = {
        "tournaments": [
            {
                "id": "rvv-live",
                "age_group": "U10",
                "arena": "Arena 1",
                "host_club": "Holmen",
                "start_time": "13:00",
                "cancelled": False,
                "teams": [{"label": "A", "club": "Jar", "age_group": "U10"}],
                "games": [{"round_number": 1}],
            },
            {
                "id": "rvv-cancelled",
                "age_group": "U10",
                "arena": "Arena 2",
                "host_club": "Kongsberg",
                "start_time": "14:00",
                "cancelled": True,
                "cancellation_reason": "team_retirement",
                "teams": [{"label": "B", "club": "Jar", "age_group": "U10"}],
                "games": [],
            },
        ]
    }

    facts = _collect_plan_audit_facts(
        plan_dict=plan,
        config_checkpoint={"ice_time_minutes": {"U10": 120}},
        verification_problem=None,
    )

    assert [row["id"] for row in facts["duration_examples"]] == ["rvv-live"]
    assert facts["missing_duration"] == []
