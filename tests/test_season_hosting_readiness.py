"""Regression: stale readiness/hosting evidence after a canonical season move.

A `season move` changes `plan.tournaments` without rerunning the planner. The
plan's descriptive snapshot (`publication_readiness`,
`unresolved_hosting_obligations`, `same_age_hosting_repairs` /
`cross_age_hosting_repairs`) and the read-only audit context must reflect the
fresh deterministic verifier result, not the pre-mutation snapshot.
"""

from __future__ import annotations

import itertools
from pathlib import Path

from tournament_scheduler.pipeline.audit_context import build_audit_context, build_audit_evidence
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.season_state import load_schedule, move_tournament, promote_from_stage3
from tournament_scheduler.testing.reviewed_export import write_reviewed_stage4_export


def _round_robin(labels: list[str]) -> list[dict]:
    return [
        {"home": home, "away": away, "parallel_slot": 0, "round_number": 1 + index // 2}
        for index, (home, away) in enumerate(itertools.combinations(labels, 2))
    ]


def _teams() -> list[dict]:
    return [{"club": club, "label": f"{club} U10", "age_group": "U10"} for club in "ABC"]


def _tournament(tid: str, date: str, host_club: str) -> dict:
    teams = _teams()
    return {
        "id": tid,
        "date": date,
        "arena": f"Arena {host_club}",
        "age_group": "U10",
        "host_club": host_club,
        "start_time": "10:00",
        "teams": teams,
        "games": _round_robin([team["label"] for team in teams]),
    }


def _promoted_season(tmp_path: Path) -> tuple[Path, Path]:
    """Two A-hosted tournaments and one C-hosted tournament; B is unhosted."""
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    plan = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [
            _tournament("u10-a-20260912", "2026-09-12", "A"),
            _tournament("u10-a-20260919", "2026-09-19", "A"),
            _tournament("u10-c-20260926", "2026-09-26", "C"),
        ],
        # Pre-mutation snapshot: B has an unresolved hosting obligation.
        "unresolved_hosting_obligations": [{"club": "B", "age_group": "U10", "reason": "no verified automatic slot"}],
        "same_age_hosting_repairs": [{"club": "B", "age_group": "U10", "status": "unresolved"}],
        "cross_age_hosting_repairs": [{"club": "B", "age_group": "U10", "status": "unresolved"}],
        "publication_readiness": {
            "status": "REVIEW_REQUIRED",
            "publishable": False,
            "reasons": [{"code": "unresolved_hosting", "count": 1}],
        },
    }
    state.write_stage(StageName.PLANNING, {"plan": plan}, status=StageStatus.DONE)
    write_reviewed_stage4_export(state)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    return work_dir, root


def test_move_refreshes_readiness_and_hosting_evidence(tmp_path: Path) -> None:
    work_dir, root = _promoted_season(tmp_path)
    before = load_schedule("2026-2027", root=root)
    problem = (before.get("verification_context") or {}).get("problem")
    assert [item["club"] for item in before["plan"]["unresolved_hosting_obligations"]] == ["B"]

    moved = move_tournament(
        season="2026-2027",
        tournament_id="u10-a-20260919",
        root=root,
        host_club="B",
        problem=problem,
        actor="mover",
    )

    moved_plan = moved["plan"]
    # The move resolved B's obligation; the descriptive snapshot must not keep
    # claiming it is unresolved.
    assert moved_plan["unresolved_hosting_obligations"] == []
    assert moved_plan["publication_readiness"]["reasons"] == []
    assert [row for row in moved_plan["same_age_hosting_repairs"] if row.get("status") == "unresolved"] == []
    assert [row for row in moved_plan["cross_age_hosting_repairs"] if row.get("status") == "unresolved"] == []


def test_audit_context_reflects_the_mutation_not_the_stale_plan(tmp_path: Path) -> None:
    work_dir, root = _promoted_season(tmp_path)
    before = load_schedule("2026-2027", root=root)
    problem = (before.get("verification_context") or {}).get("problem")

    # A `season export` after the move writes the fresh verifier result the
    # audit context must agree with.
    from tournament_scheduler.pipeline.stage4_export import run as run_export

    moved = move_tournament(
        season="2026-2027",
        tournament_id="u10-a-20260919",
        root=root,
        host_club="B",
        problem=problem,
    )
    run_export(
        {"plan": moved["plan"]},
        PipelineState(work_dir),
        export_dir=str(tmp_path / "export"),
        timestamped_export=False,
        verification_problem=problem,
        use_pipeline_metadata=False,
    )

    context = build_audit_context(work_dir=work_dir)
    verify = context["deterministic_verify_result"]
    readiness = context["publication_readiness"]

    assert verify["unresolved_hosting_obligations"] == []
    assert verify["hosting_balance_imbalances"] == []
    assert {reason["code"] for reason in readiness["reasons"]}.isdisjoint(
        {"unresolved_hosting", "hosting_balance_imbalances"}
    )
    repairs = build_audit_evidence(work_dir=work_dir, category="cross_age_hosting_repairs")
    assert repairs["matched_record_count"] == 0
