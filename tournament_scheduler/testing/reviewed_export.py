"""Test support for constructing a reviewed Stage 4 export checkpoint.

Promotion is bound to the provenance-carrying Stage 4 checkpoint
that Stage 4 itself writes.  Tests that exercise the canonical-season
lifecycle can use :func:`write_reviewed_stage4_export` to build a realistic
checkpoint -- with a bound verification context -- from an already-written
Stage 3 planning checkpoint, instead of hand-rolling the provenance shape.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..pipeline.fingerprints import stable_payload_sha256
from ..pipeline.state import PipelineState, StageName, StageStatus
from ..pipeline.verification_context import build_verification_context
from ..planning_contract import build_planning_problem, extract_candidate, verify_candidate


def build_problem_from_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal but real normalized problem for *candidate*.

    Folds the candidate's own roster and window into a ``planning_problem`` so
    tests exercise the provenance-bound path with a genuine problem object
    rather than the context-free verifier.
    """
    teams = [
        dict(team)
        for tournament in candidate.get("tournaments", []) or []
        for team in tournament.get("teams", []) or []
    ]
    # De-duplicate by (club, label, age_group) while preserving order.
    seen: set[tuple] = set()
    unique_teams: list[dict[str, Any]] = []
    for team in teams:
        key = (team.get("club"), team.get("label"), team.get("age_group"))
        if key in seen:
            continue
        seen.add(key)
        unique_teams.append(team)
    age_groups = sorted({str(t.get("age_group")) for t in unique_teams if t.get("age_group")})
    # The controlled workbook always carries one authoritative booking window
    # per active age group; a synthetic candidate needs the same contract so the
    # occupancy projection has a determinate duration instead of failing closed.
    ice_time_minutes = {
        age_group: int(candidate.get("ice_time_minutes", {}).get(age_group, 120))
        for age_group in age_groups
    }
    config = {
        "start_date": candidate.get("start_date"),
        "end_date": candidate.get("end_date"),
        "teams": unique_teams,
        "age_groups": age_groups,
        "ice_time_minutes": ice_time_minutes,
    }
    return build_planning_problem(
        config,
        None,
        date.fromisoformat(str(candidate["start_date"])),
        date.fromisoformat(str(candidate["end_date"])),
    )


def write_reviewed_stage4_export(
    state: PipelineState,
    *,
    problem: dict[str, Any] | None = None,
    run_id: str | None = None,
    export_dir: str | None = None,
) -> dict[str, Any]:
    """Write a done Stage 4 checkpoint bound to the Stage 3 candidate.

    When *problem* is omitted, a real normalized problem is derived from the
    candidate.  *run_id* defaults to the workspace's current run manifest id so
    promotion's run binding holds for the ordinary same-run case.
    """
    from ..pipeline.run_manifest import RunManifest

    candidate = extract_candidate(state.read_stage(StageName.PLANNING))
    resolved_problem = problem if problem is not None else build_problem_from_candidate(candidate)
    resolved_run_id = run_id if run_id is not None else RunManifest(state.work_dir).read().get("run_id")
    verify_result = verify_candidate(candidate, resolved_problem)
    export_fingerprint = stable_payload_sha256(candidate.get("tournaments", []))
    verification_context = build_verification_context(
        run_id=resolved_run_id,
        candidate=candidate,
        problem=resolved_problem,
        verify_result=verify_result,
    )
    checkpoint = {
        "generated_at": "2026-09-15T20:08:00+00:00",
        "export_dir": export_dir,
        "output_files": {},
        "errors": [],
        "verify_result": verify_result,
        "export_fingerprint": export_fingerprint,
        "verification_context": verification_context,
        "reviewed_plan": dict(candidate),
    }
    state.write_stage(StageName.EXPORT, checkpoint, status=StageStatus.DONE)
    return checkpoint
