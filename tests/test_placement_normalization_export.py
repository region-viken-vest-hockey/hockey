"""End-to-end: a fresh pipeline export normalizes unplaced placements and the
result stays promotable.

The regression this locks in: normalization inside the Stage 4 export
chokepoint changes the plan that is actually reviewed and exported, so the
workspace's selected Stage 3 candidate must be corrected too -- otherwise
promotion refuses the reviewed handoff as a "different candidate".
"""

from __future__ import annotations

import pytest

from tournament_scheduler.pipeline.export_projection_guard import ExportProjectionError
from tournament_scheduler.pipeline.run_manifest import RunManifest
from tournament_scheduler.pipeline.stage4_export import run as run_export
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.season_state import promote_from_stage3
from tournament_scheduler.testing.reviewed_export import build_problem_from_candidate


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


def _conflicting_problem(candidate: dict) -> dict:
    problem = build_problem_from_candidate(candidate)
    problem["club_calendar_status"] = {"A": "known"}
    problem["club_busy_intervals"] = {
        "A": [
            {
                "date": "2026-09-12",
                "start": "10:30",
                "end": "12:00",
                "availability": "fixed_busy",
                "kind": "external",
                "calendar_event": "U18 kamp",
            }
        ]
    }
    problem["ice_time_minutes"] = {"U10": 120}
    problem["round_length_minutes"] = {"U10": 15}
    return problem


def _config() -> dict:
    return {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "age_groups": ["U10"],
        "ice_time_minutes": {"U10": 120},
        "round_length_minutes": {"U10": 15},
        "rounds_per_tournament": {"U10": 3},
        "parallel_games": {"U10": 2},
    }


def test_export_normalizes_fixed_conflict_and_remains_promotable(tmp_path):
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    RunManifest(work_dir).start_run("conflict run", input_fingerprint={})
    candidate = _candidate()
    problem = _conflicting_problem(candidate)

    state.write_stage(StageName.PLANNING, {"plan": candidate}, status=StageStatus.DONE)

    result = run_export(
        {"plan": candidate},
        state,
        export_dir=str(tmp_path / "export"),
        timestamped_export=False,
        verification_problem=problem,
        effective_config_override=_config(),
    )

    # The conflicting placement is removed and recorded as planning work.
    normalization = result["placement_normalization"]
    assert normalization["changed"] is True
    assert normalization["removed_tournament_ids"] == ["u10-a-20260912"]
    assert result["verify_result"]["ok"] is True
    assert result["verify_result"]["manual_external_conflict_placements"] == []

    # The workspace's selected Stage 3 candidate is corrected too, so the
    # reviewed handoff and the promotable candidate cannot diverge.
    persisted = state.read_stage(StageName.PLANNING)
    assert persisted["plan"]["tournaments"] == []
    assert persisted["plan"]["unresolved_tournament_placements"][0]["id"] == (
        "unplaced_placement:U10:2026-09-12:1"
    )

    # Promotion still succeeds and writes the normalized plan.
    schedule, _decisions = promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    assert schedule["plan"]["tournaments"] == []
    obligations = schedule["plan"]["unresolved_tournament_placements"]
    assert len(obligations) == 1
    assert obligations[0]["responsible_host"] == "A"
    assert obligations[0]["reason"] == "fixed_external_calendar_conflict"


def test_published_canonical_export_preserves_conflicting_canonical_placement(tmp_path):
    work_dir = tmp_path / ".pipeline"
    state = PipelineState(work_dir)
    RunManifest(work_dir).start_run("published canonical export", input_fingerprint={})
    candidate = _candidate()
    problem = _conflicting_problem(candidate)

    state.write_stage(StageName.PLANNING, {"plan": candidate}, status=StageStatus.DONE)
    result = run_export(
        {"plan": candidate, "canonical_state": {"season": "2026-2027", "revision": "rev-2"}},
        state,
        export_dir=str(tmp_path / "export"),
        timestamped_export=False,
        verification_problem=problem,
        effective_config_override=_config(),
        allow_placement_normalization=False,
        canonical_schedule_plan=candidate,
        published_export_guard={
            "export_id": "published-export",
            "canonical_revision": "rev-1",
            "lifecycle_status": "published",
        },
    )

    assert result.get("placement_normalization") in (None, {})
    assert result["export_projection_guard"]["summary"] == "export projection preserves canonical schedule"
    assert result["reviewed_plan"]["tournaments"][0]["id"] == "u10-a-20260912"
    assert [t["id"] for t in state.read_stage(StageName.PLANNING)["plan"]["tournaments"]] == [
        "u10-a-20260912"
    ]


def test_export_guard_rejects_unexplained_canonical_tournament_removal(tmp_path):
    work_dir = tmp_path / ".pipeline"
    state = PipelineState(work_dir)
    RunManifest(work_dir).start_run("published canonical export", input_fingerprint={})
    candidate = _candidate()
    problem = _conflicting_problem(candidate)

    with pytest.raises(ExportProjectionError) as excinfo:
        run_export(
            {"plan": candidate, "canonical_state": {"season": "2026-2027", "revision": "rev-2"}},
            state,
            export_dir=str(tmp_path / "export"),
            timestamped_export=False,
            verification_problem=problem,
            effective_config_override=_config(),
            allow_placement_normalization=True,
            canonical_schedule_plan=candidate,
            published_export_guard={"export_id": "published-export", "lifecycle_status": "published"},
        )

    report = excinfo.value.report
    assert report["canonical_delta"]["removed_tournament_ids"] == ["u10-a-20260912"]
    assert "export never repairs" in report["summary"]


def test_export_of_a_clean_plan_is_unchanged(tmp_path):
    work_dir = tmp_path / ".pipeline"
    state = PipelineState(work_dir)
    RunManifest(work_dir).start_run("clean run", input_fingerprint={})
    candidate = _candidate()
    problem = build_problem_from_candidate(candidate)
    problem["club_calendar_status"] = {"A": "known"}
    problem["club_busy_intervals"] = {}
    problem["ice_time_minutes"] = {"U10": 120}
    problem["round_length_minutes"] = {"U10": 15}

    state.write_stage(StageName.PLANNING, {"plan": candidate}, status=StageStatus.DONE)
    result = run_export(
        {"plan": candidate},
        state,
        export_dir=str(tmp_path / "export"),
        timestamped_export=False,
        verification_problem=problem,
        effective_config_override=_config(),
    )

    assert result.get("placement_normalization", {}).get("changed") is False
    assert [t["id"] for t in state.read_stage(StageName.PLANNING)["plan"]["tournaments"]] == [
        "u10-a-20260912"
    ]
