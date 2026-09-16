"""Tests for the bounded audit overview and selective evidence queries (issue #356)."""

from __future__ import annotations

import json

from tournament_scheduler.pipeline.audit_context import (
    AUDIT_CHECKLIST,
    build_audit_context,
    build_audit_evidence,
    build_audit_evidence_index,
)
from tournament_scheduler.pipeline.audit_evidence import (
    AUDIT_CONTEXT_MAX_SERIALIZED_BYTES,
    EVIDENCE_OVERVIEW_MAX_EXAMPLES,
    EVIDENCE_QUERY_MAX_RECORDS,
)
from tournament_scheduler.pipeline.run_manifest import RunManifest
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus


def _club(index: int) -> str:
    return f"Klubb {index:02d}"


def _production_sized_plan() -> dict:
    """A fixture large enough that unbounded lists would dominate the context."""
    clubs = [_club(i) for i in range(15)]
    age_groups = [f"U{10 + i}" for i in range(8)]
    tournaments = []
    for index in range(240):
        club = clubs[index % len(clubs)]
        age_group = age_groups[index % len(age_groups)]
        tournaments.append(
            {
                "id": f"tour-{index:04d}",
                "date": f"2027-0{(index % 9) + 1}-{(index % 27) + 1:02d}",
                "age_group": age_group,
                "arena": f"Arena {index % 12}",
                "host_club": club,
                "start_time": "10:00",
                "teams": [
                    {"label": f"T{index}-{slot}", "club": clubs[(index + slot) % len(clubs)], "age_group": age_group}
                    for slot in range(4)
                ],
                "games": [
                    {"home": f"T{index}-0", "away": f"T{index}-1", "round_number": 1},
                    {"home": f"T{index}-2", "away": f"T{index}-3", "round_number": 2},
                ],
            }
        )
    return {
        "tournaments": tournaments,
        "team_game_counts": {f"T{i}": 1 for i in range(500)},
        "club_participation_fairness": [
            {
                "age_group": age_group,
                "period": None,
                "club": club,
                "target_share": 0.25,
                "actual_share": 0.24,
            }
            for club in clubs
            for age_group in age_groups
        ],
        "unresolved_participation_shortfalls": [
            {
                "club": _club(index % len(clubs)),
                "label": f"Shortfall {index}",
                "age_group": age_groups[index % len(age_groups)],
                "actual": "3",
                "target": "5",
                "category": "participation_under_target",
                "reason": f"distinctive-shortfall-reason-{index}",
            }
            for index in range(180)
        ],
        "same_age_hosting_repairs": [
            {"club": _club(index % len(clubs)), "age_group": age_groups[index % len(age_groups)], "status": "unresolved"}
            for index in range(60)
        ],
        "cross_age_hosting_repairs": [
            {"club": _club(index % len(clubs)), "age_group": age_groups[index % len(age_groups)], "status": "repaired"}
            for index in range(60)
        ],
        "unresolved_tournament_placements": [
            {
                "age_group": age_groups[index % len(age_groups)],
                "date": f"2027-04-{(index % 27) + 1:02d}",
                "participant_clubs": [_club(0), _club(1)],
                "participant_teams": [
                    {"club": _club(0), "label": f"Placement {index} A", "age_group": age_groups[index % len(age_groups)]},
                    {"club": _club(1), "label": f"Placement {index} B", "age_group": age_groups[index % len(age_groups)]},
                ],
                "participant_team_count": 2,
                "category": "manual_tournament_placement",
                "search_attempted": True,
                "reason": "no_participant_host_slot",
            }
            for index in range(30)
        ],
    }


def _write_production_run(tmp_path) -> None:
    RunManifest(tmp_path).start_run("issue 356 fixture", run_id="run-356")
    PipelineState(tmp_path).write_stage(
        StageName.CONFIG,
        {
            "ice_time_minutes": {f"U{10 + i}": 30 for i in range(8)},
            "rounds_per_tournament": {f"U{10 + i}": 3 for i in range(8)},
            "parallel_games": {f"U{10 + i}": 3 for i in range(8)},
            "teams": [
                {"club": _club(i % 15), "label": f"Reg {i}", "age_group": f"U{10 + (i % 8)}"}
                for i in range(120)
            ],
        },
        status=StageStatus.DONE,
    )
    plan = _production_sized_plan()
    # A valid no-bye plan: give each 4-team tournament all six games so the
    # utilisation summary does not classify every tournament as a hard bye.
    for tournament in plan["tournaments"]:
        labels = [team["label"] for team in tournament["teams"]]
        pairs = [(a, b) for i, a in enumerate(labels) for b in labels[i + 1 :]]
        tournament["games"] = [
            {"home": home, "away": away, "round_number": round_number}
            for round_number, (home, away) in enumerate(pairs, start=1)
        ]
    PipelineState(tmp_path).write_stage(StageName.PLANNING, {"plan": plan}, status=StageStatus.DONE)
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    PipelineState(tmp_path).write_stage(
        StageName.EXPORT,
        {
            "export_dir": str(export_dir),
            "output_files": {},
            "verify_result": {
                "ok": True,
                "violations": [],
                "manual_participation_placements": [
                    {"club": _club(i % 15), "label": f"Manual {i}", "age_group": f"U{10 + (i % 8)}"}
                    for i in range(180)
                ],
            },
            "export_fingerprint": "fp-production",
        },
        status=StageStatus.DONE,
    )


def test_bounded_overview_stays_under_the_serialized_size_budget(tmp_path):
    _write_production_run(tmp_path)

    context = build_audit_context(work_dir=tmp_path)
    serialized = json.dumps(context, ensure_ascii=False, default=str)

    assert len(serialized.encode("utf-8")) <= AUDIT_CONTEXT_MAX_SERIALIZED_BYTES
    assert context["evidence_metrics"]["overview_budget_exceeded"] is False


def test_default_context_excludes_the_wholesale_evidence_bundle(tmp_path):
    _write_production_run(tmp_path)

    context = build_audit_context(work_dir=tmp_path)

    assert "evidence_bundle" not in context
    # The unbounded finding lists must not appear at top level or inside the
    # bounded plan summary -- only counts/examples + evidence refs.
    for key in (
        "unresolved_participation_shortfalls",
        "club_participation_fairness",
        "same_age_hosting_repairs",
        "cross_age_hosting_repairs",
        "unresolved_tournament_placements",
    ):
        assert key not in context["plan_audit_summary"]


def test_large_lists_are_not_duplicated_in_the_overview(tmp_path):
    _write_production_run(tmp_path)

    context = build_audit_context(work_dir=tmp_path)
    serialized = json.dumps(context, ensure_ascii=False, default=str)

    # A distinctive per-record detail string from the 180-entry shortfall list
    # must not be embedded repeatedly in the bounded overview.
    assert serialized.count("distinctive-shortfall-reason-179") <= 1
    participation = context["evidence_index"]["available_selectors"]["categories"]
    assert "participation_shortfalls" in participation
    assert context["plan_audit_summary"]["unresolved_participation_shortfall_count"] == 180


def test_every_checklist_item_has_queryable_evidence(tmp_path):
    _write_production_run(tmp_path)

    context = build_audit_context(work_dir=tmp_path)
    guide = {item["item_id"]: item for item in context["checklist_evidence_guide"]}

    assert [item["item_id"] for item in AUDIT_CHECKLIST] == list(range(1, 10))
    for item_id in range(1, 10):
        assert guide[item_id]["categories"], item_id


def _dict_list_lengths(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _dict_list_lengths(item, path + (key,))
    elif isinstance(value, list) and any(isinstance(item, dict) for item in value):
        yield path, len(value)


def test_no_unbounded_top_level_finding_lists_are_reintroduced(tmp_path):
    _write_production_run(tmp_path)

    context = build_audit_context(work_dir=tmp_path)
    for path, length in _dict_list_lengths(context["plan_audit_summary"], ("plan_audit_summary",)):
        assert length <= EVIDENCE_OVERVIEW_MAX_EXAMPLES, f"{'.'.join(path)} has {length} records in the bounded context"

    guide = context["checklist_evidence_guide"]
    for item in guide:
        assert list(_dict_list_lengths(item)) == []
    assert len(json.dumps(guide, ensure_ascii=False)) < 8_000


def test_large_verify_result_is_bounded_but_queryable(tmp_path):
    _write_production_run(tmp_path)

    context = build_audit_context(work_dir=tmp_path)
    assert context["deterministic_verify_result"]["ok"] is True
    assert context["deterministic_verify_result"]["manual_participation_placements_count"] == 180
    assert len(context["deterministic_verify_result"]["manual_participation_placements"]) == 5

    evidence = build_audit_evidence(work_dir=tmp_path, category="manual_participation_placements")
    assert evidence["matched_record_count"] == 180


def test_detailed_evidence_for_one_checklist_item_is_retrievable(tmp_path):
    _write_production_run(tmp_path)

    evidence = build_audit_evidence(work_dir=tmp_path, item=1)

    assert evidence["matched_record_count"] > 0
    assert all(record["checklist_item"] == 1 for record in evidence["records"])
    assert evidence["export_fingerprint"] == "fp-production"


def test_evidence_can_be_queried_by_durable_tournament_id(tmp_path):
    _write_production_run(tmp_path)

    evidence = build_audit_evidence(work_dir=tmp_path, tournament="tour-0007")

    tournament_ids = {record["tournament_id"] for record in evidence["records"]}
    assert tournament_ids == {"tour-0007"}
    assert evidence["matched_record_count"] > 0


def test_evidence_can_be_queried_by_club_and_category(tmp_path):
    _write_production_run(tmp_path)

    by_club = build_audit_evidence(work_dir=tmp_path, club="Klubb 03", category="participation_shortfalls")
    assert by_club["matched_record_count"] > 0
    assert all("Klubb 03" in record["clubs"] for record in by_club["records"])

    by_category = build_audit_evidence(work_dir=tmp_path, category="cross_age_hosting_repairs")
    assert by_category["matched_record_count"] == 60


def test_query_results_are_bound_to_the_same_run_and_export_fingerprint(tmp_path):
    _write_production_run(tmp_path)

    overview = build_audit_context(work_dir=tmp_path)["evidence_index"]
    evidence = build_audit_evidence(work_dir=tmp_path, category="participation_shortfalls")

    assert evidence["run_id"] == overview["run_id"] == "run-356"
    assert evidence["export_fingerprint"] == overview["export_fingerprint"] == "fp-production"
    assert evidence["source_fingerprints"] == overview["source_fingerprints"]


def test_stale_evidence_from_another_export_cannot_be_mixed_in(tmp_path):
    _write_production_run(tmp_path)
    (tmp_path / "export" / "evidence_bundle.json").write_text(
        json.dumps(
            {
                "source_summary": {"sources_scanned": 1, "blocked_sources": []},
                "final_operator_evidence": {
                    "run_id": "run-356",
                    "export_fingerprint": "some-other-export",
                    "unresolved_participation_shortfalls": [{"label": "STALE-OTHER-EXPORT"}],
                },
            }
        ),
        encoding="utf-8",
    )

    evidence = build_audit_evidence(work_dir=tmp_path, category="participation_shortfalls")
    serialized = json.dumps(evidence, ensure_ascii=False, default=str)

    assert "STALE-OTHER-EXPORT" not in serialized
    assert evidence["matched_record_count"] == 180


def test_broad_unresolved_query_works_without_loading_the_whole_bundle(tmp_path):
    _write_production_run(tmp_path)

    index = build_audit_evidence_index(work_dir=tmp_path)
    query = build_audit_evidence(work_dir=tmp_path, unresolved=True)

    assert query["matched_record_count"] > 0
    assert all(record["unresolved"] for record in query["records"])
    # The bounded response is far smaller than the full evidence index.
    assert len(json.dumps(query, default=str)) < len(json.dumps(index, default=str))


def test_broad_query_truncates_deterministically_with_an_explicit_flag(tmp_path):
    _write_production_run(tmp_path)

    page = build_audit_evidence(work_dir=tmp_path, category="participation_shortfalls")
    assert page["matched_record_count"] == 180
    assert page["returned_record_count"] == min(180, EVIDENCE_QUERY_MAX_RECORDS)

    limited = build_audit_evidence(work_dir=tmp_path, category="participation_shortfalls", limit=5)
    assert limited["returned_record_count"] == 5
    assert limited["truncated"] is True
    assert limited["matched_record_count"] == 180
