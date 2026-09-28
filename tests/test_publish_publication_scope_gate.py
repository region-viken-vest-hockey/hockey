"""Publish-gate integration for tournament-scoped publication eligibility
(issue #541).

The deterministic publish preflight must consume the typed publication-scope
result: an incremental republish whose exact last-publication delta is
eligible is not blocked by unrelated full-season planning debt, while a held
changed interval or a missing/stale audit still blocks.
"""

from __future__ import annotations

import subprocess

from tournament_scheduler.calendar_bookings import new_manual_assertion_record
from tournament_scheduler.canonical_state import schedule_fingerprint
from tournament_scheduler.infrastructure.canonical_season_store import (
    DECISIONS_SCHEMA_VERSION,
    SEASON_STATE_SCHEMA_VERSION,
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
)
from tournament_scheduler.pipeline.audit_result import write_audit_result
from tournament_scheduler.pipeline.capability_result import CapabilityResult
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256
from tournament_scheduler.pipeline.operator_action_audit import apply_publish_audit_gate
from tournament_scheduler.published_baseline import build_baseline_record
from tournament_scheduler.pipeline.run_manifest import RunManifest
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.pipeline.verification_context import build_verification_context
from tournament_scheduler.planning_contract import extract_candidate, verify_candidate

_SEASON = "2026-2027"


def _init_repo(repo_dir) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_dir)], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Test"], check=True)
    (repo_dir / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_dir), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "init"], check=True)


def _teams() -> list[dict]:
    return [
        {"club": "Jar", "label": "Jar 1", "age_group": "U10"},
        {"club": "Skien", "label": "Skien 1", "age_group": "U10"},
        {"club": "Skien", "label": "Skien 2", "age_group": "U10"},
    ]


def _games() -> list[dict]:
    return [
        {"home": "Jar 1", "away": "Skien 1", "parallel_slot": 0, "round_number": 1},
        {"home": "Jar 1", "away": "Skien 2", "parallel_slot": 0, "round_number": 2},
        {"home": "Skien 1", "away": "Skien 2", "parallel_slot": 0, "round_number": 3},
    ]


def _tournament(tournament_id: str, *, date: str, start_time: str) -> dict:
    return {
        "id": tournament_id,
        "date": date,
        "start_time": start_time,
        "arena": "Jar Isforum",
        "host_club": "Jar",
        "age_group": "U10",
        "teams": _teams(),
        "games": _games(),
    }


def _plan(*, t2_date: str = "2026-02-05", t2_start: str = "10:00") -> dict:
    return {
        "schema_version": 1,
        "start_date": "2026-01-01",
        "end_date": "2026-04-30",
        "source": {"planner": "test"},
        "tournaments": [
            _tournament("t1", date="2026-01-05", start_time="10:00"),
            _tournament("t2", date=t2_date, start_time=t2_start),
        ],
    }


def _problem() -> dict:
    return {"teams": _teams(), "ice_time_minutes": {"U10": 120}, "parallel_games": {"U10": 2}}


def _write_canonical(root, *, plan: dict, published_plan: dict, booked_t2: bool) -> None:
    schedule = {
        "schema_version": SEASON_STATE_SCHEMA_VERSION,
        "season": _SEASON,
        "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
        "revision": schedule_fingerprint(plan),
        "fingerprint": schedule_fingerprint(plan),
        "plan_schema_version": 1,
        "plan": plan,
        "verification_context": {"problem": _problem()},
    }
    from tournament_scheduler.pipeline.export_projection_guard import tournament_projection

    baseline = build_baseline_record(
        season=_SEASON,
        publication_id="pub-1",
        canonical_revision="rev-published",
        published_at="2026-09-01T00:00:00+00:00",
        projection=tournament_projection(published_plan, _problem()),
    )
    decisions = {
        "schema_version": DECISIONS_SCHEMA_VERSION,
        "season": _SEASON,
        "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
        "schedule_fingerprint": schedule["fingerprint"],
        "actor": "tester",
        "decisions": {
            "t1": {"status": "pending_review", "placement_locked": False, "participants_locked": False, "approved_fingerprint": None},
            "t2": {"status": "pending_review", "placement_locked": False, "participants_locked": False, "approved_fingerprint": None},
        },
        "history": [],
        "season_lifecycle": {"state": "published_sealed", "published_baseline": baseline},
    }
    if booked_t2:
        t2 = next(t for t in plan["tournaments"] if t["id"] == "t2")
        decisions["manual_booking_assertions"] = [
            new_manual_assertion_record(
                tournament=t2,
                booking_status="booked",
                problem=_problem(),
                actor="tester",
                note="club confirmed by email",
                reference="email-2026-09-20",
                source_scope="tournament",
                stated_interval=None,
                asserted_at="2026-09-20T00:00:00+00:00",
                source_revision="rev-published",
            )
        ]
    CanonicalSeasonStore(root / "season").write(
        CanonicalSeasonSnapshot(season=_SEASON, schedule=schedule, decisions=decisions)
    )


def _write_reviewed_export(work_dir, *, plan: dict) -> str:
    candidate = extract_candidate({"plan": plan})
    problem = _problem()
    verify_result = verify_candidate(candidate, problem)
    fingerprint = stable_payload_sha256(candidate.get("tournaments", []))
    verification_context = build_verification_context(
        run_id=RunManifest(work_dir).read().get("run_id"),
        candidate=candidate,
        problem=problem,
        verify_result=verify_result,
    )
    export_dir = work_dir / "export"
    export_dir.mkdir(exist_ok=True)
    (export_dir / "season_plan.html").write_text("<h1>plan</h1>", encoding="utf-8")
    PipelineState(work_dir).write_stage(
        StageName.EXPORT,
        {
            "output_files": {"html": str(export_dir / "season_plan.html")},
            "export_fingerprint": fingerprint,
            "verify_result": verify_result,
            "verification_context": verification_context,
            "reviewed_plan": dict(candidate),
            "canonical_season": _SEASON,
            "canonical_revision": "rev-current",
        },
        status=StageStatus.DONE,
    )
    return fingerprint


def _write_audit(work_dir, *, status: str, export_fingerprint: str) -> None:
    payload = {
        "schema_version": 1,
        "audit_id": f"audit-{status.lower()}",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "run_id": RunManifest(work_dir).read().get("run_id") or "",
        "export_fingerprint": export_fingerprint,
        "source_fingerprints": {},
        "prompt_version": 1,
        "runbook_version": "v1",
        "backend": "test",
        "execution_mode": "headless",
        "status": status,
        "checklist_findings": [
            {
                "item_id": i,
                "question": f"q{i}",
                "finding": "ok",
                "severity": "info",
                "confidence": "high",
                "evidence": [],
                "could_not_establish": False,
            }
            for i in range(1, 10)
        ],
        "potential_missing_rule": [],
        "could_not_establish": [],
    }
    errors = write_audit_result(work_dir, payload)
    assert not errors, errors


def _bundle_result() -> CapabilityResult:
    return CapabilityResult.ok("bundle", capability="pages_publish", artifacts=[])


def _evaluate(work_dir, repo_dir):
    return apply_publish_audit_gate(
        work_dir=str(work_dir),
        repo_dir=str(repo_dir),
        bundle_result=_bundle_result(),
        with_collision_warning=lambda result: result,
    )


def test_eligible_incremental_correction_is_not_blocked_by_full_season_audit_fail(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    published = _plan()
    current = _plan(t2_date="2026-03-05", t2_start="12:00")
    _write_canonical(tmp_path, plan=current, published_plan=published, booked_t2=True)
    monkeypatch.setenv("RVV_CANONICAL_SEASON_ROOT", str(tmp_path / "season"))
    fingerprint = _write_reviewed_export(tmp_path, plan=current)
    _write_audit(tmp_path, status="FAIL", export_fingerprint=fingerprint)

    assert _evaluate(tmp_path, tmp_path) is None


def test_changed_interval_without_accepted_booking_is_blocked(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    published = _plan()
    current = _plan(t2_date="2026-03-05", t2_start="12:00")
    _write_canonical(tmp_path, plan=current, published_plan=published, booked_t2=False)
    monkeypatch.setenv("RVV_CANONICAL_SEASON_ROOT", str(tmp_path / "season"))
    fingerprint = _write_reviewed_export(tmp_path, plan=current)
    _write_audit(tmp_path, status="PASS", export_fingerprint=fingerprint)

    result = _evaluate(tmp_path, tmp_path)

    assert result is not None
    assert result.status == "blocked"
    assert any("t2" in problem for problem in result.problems)


def test_no_canonical_season_falls_back_to_full_audit_gate(tmp_path):
    _init_repo(tmp_path)
    fingerprint = _write_reviewed_export(tmp_path, plan=_plan())
    _write_audit(tmp_path, status="FAIL", export_fingerprint=fingerprint)

    result = _evaluate(tmp_path, tmp_path)

    assert result is not None
    assert result.status == "blocked"


def test_stale_audit_still_blocks_even_when_scope_eligible(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    published = _plan()
    current = _plan(t2_date="2026-03-05", t2_start="12:00")
    _write_canonical(tmp_path, plan=current, published_plan=published, booked_t2=True)
    monkeypatch.setenv("RVV_CANONICAL_SEASON_ROOT", str(tmp_path / "season"))
    _write_reviewed_export(tmp_path, plan=current)
    # No fresh audit result was written for this export.
    result = _evaluate(tmp_path, tmp_path)

    assert result is not None
    assert result.status == "blocked"
