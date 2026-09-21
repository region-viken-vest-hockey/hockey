"""Tests for the semantic safety-net audit gate inside publish_pages
(issue #325) — deterministic hard-fail precedence, FAIL/INCOMPLETE/stale
audits blocking, and REVIEW_REQUIRED escalating to operator review.
"""

from __future__ import annotations

import subprocess

from tournament_scheduler.pipeline.audit_result import write_audit_result
from tournament_scheduler.pipeline.operator_action import DEFAULT_REGISTRY
from tournament_scheduler.pipeline.run_manifest import RunManifest
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus


def _init_repo(repo_dir) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_dir)], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Test"], check=True)
    (repo_dir / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_dir), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "init"], check=True)


def _write_export(work_dir, *, fingerprint: str = "fp-1") -> None:
    export_dir = work_dir / "export"
    export_dir.mkdir(exist_ok=True)
    (export_dir / "season_plan.html").write_text("<h1>plan</h1>", encoding="utf-8")
    PipelineState(work_dir).write_stage(
        StageName.EXPORT,
        {"output_files": {"html": str(export_dir / "season_plan.html")}, "export_fingerprint": fingerprint},
        status=StageStatus.DONE,
    )


def _audit_findings() -> list[dict]:
    return [
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
    ]


def _write_audit_without_audit_id(
    work_dir, *, status: str, export_fingerprint: str = "fp-1", run_id: str = ""
) -> dict:
    """Persist a REVIEW_REQUIRED audit the way a harness that omits
    ``audit_id`` would submit it; ``write_audit_result`` must derive one."""
    payload = _golden_result(status=status, export_fingerprint=export_fingerprint, run_id=run_id)
    del payload["audit_id"]
    errors = write_audit_result(work_dir, payload)
    assert not errors, errors
    return payload


def _golden_result(*, status: str, export_fingerprint: str = "fp-1", run_id: str = "") -> dict:
    return {
        "schema_version": 1,
        "audit_id": f"audit-{status.lower()}",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "run_id": run_id,
        "export_fingerprint": export_fingerprint,
        "source_fingerprints": {},
        "prompt_version": 1,
        "runbook_version": "v1",
        "backend": "test",
        "execution_mode": "headless",
        "status": status,
        "checklist_findings": _audit_findings(),
        "potential_missing_rule": [],
        "could_not_independently_establish": [],
        "raw_response_ref": None,
    }


def _write_audit(work_dir, *, status: str, export_fingerprint: str = "fp-1", run_id: str = "") -> dict:
    payload = _golden_result(status=status, export_fingerprint=export_fingerprint, run_id=run_id)
    errors = write_audit_result(work_dir, payload)
    assert not errors, errors
    return payload


def _publish(tmp_path, **overrides):
    kwargs = dict(work_dir=str(tmp_path), repo_dir=str(tmp_path), push=False, confirm_public=True)
    kwargs.update(overrides)
    action = DEFAULT_REGISTRY.build("publish_pages", **kwargs)
    return DEFAULT_REGISTRY.execute(action, approved=True)


class TestNoAuditResult:
    def test_publish_blocked_without_any_audit_result(self, tmp_path):
        _init_repo(tmp_path)
        _write_export(tmp_path)
        result = _publish(tmp_path)
        assert result.status == "blocked"
        assert result.requires_human is True

    def test_routine_public_asset_update_does_not_require_season_audit(self, tmp_path):
        _init_repo(tmp_path)
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", "gh-pages"], check=True)
        latest = tmp_path / "latest"
        (latest / "activities").mkdir(parents=True)
        (latest / "season_plan.html").write_text("<h1>unchanged season</h1>", encoding="utf-8")
        (latest / "index.html").write_text("<h1>unchanged season</h1>", encoding="utf-8")
        (latest / "activities.json").write_text('{"old": true}\n', encoding="utf-8")
        (latest / "activities" / "index.html").write_text("old activity\n", encoding="utf-8")
        (latest / "_meta.json").write_text('{"run_id": "old"}\n', encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp_path), "add", "latest"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "pages"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "main"], check=True)

        export_dir = tmp_path / "routine-export"
        (export_dir / "activities").mkdir(parents=True)
        (export_dir / "season_plan.html").write_text("<h1>unchanged season</h1>", encoding="utf-8")
        (export_dir / "activities.json").write_text('{"new": true}\n', encoding="utf-8")
        (export_dir / "activities" / "index.html").write_text("new activity\n", encoding="utf-8")

        result = _publish(tmp_path, export_dir=str(export_dir), routine_public_assets=True)

        assert result.status == "ok", result.summary


class TestDeterministicHardFailTakesPrecedence:
    def test_hard_violation_blocks_even_with_a_passing_audit(self, tmp_path):
        _init_repo(tmp_path)
        _write_export(tmp_path)
        _write_audit(tmp_path, status="PASS")

        # A plan with a duplicate-participation-style hard violation: the
        # same team playing twice on the same date within one tournament.
        PipelineState(tmp_path).write_stage(
            StageName.PLANNING,
            {
                "plan": {
                    "schema_version": 1,
                    "source": {"planner": "test"},
                    "tournaments": [
                        {
                            "id": "t1",
                            "date": "2026-01-05",
                            "arena": "Jar Isforum",
                            "age_group": "U10",
                            "host_club": "Jar",
                            "teams": [{"club": "Jar", "label": "Jar 1", "age_group": "U10"}],
                            "games": [
                                {"home": "Jar 1", "away": "Jar 1", "parallel_slot": 0, "round_number": 1}
                            ],
                        }
                    ],
                }
            },
            status=StageStatus.DONE,
        )

        result = _publish(tmp_path)
        assert result.status == "blocked"
        assert "hard verifisering" in result.summary.lower()


def _hard_invalid_plan() -> dict:
    """A plan with a duplicate-participation-style hard violation: the same
    team playing itself/twice on the same date within one tournament."""
    return {
        "schema_version": 1,
        "source": {"planner": "test"},
        "tournaments": [
            {
                "id": "t1",
                "date": "2026-01-05",
                "arena": "Jar Isforum",
                "age_group": "U10",
                "host_club": "Jar",
                "teams": [{"club": "Jar", "label": "Jar 1", "age_group": "U10"}],
                "games": [{"home": "Jar 1", "away": "Jar 1", "parallel_slot": 0, "round_number": 1}],
            }
        ],
    }


def _hard_valid_plan() -> dict:
    """A minimal, hard-valid plan: two distinct teams, one legal game."""
    return {
        "schema_version": 1,
        "source": {"planner": "test"},
        "tournaments": [
            {
                "id": "t1",
                "date": "2026-01-05",
                "arena": "Jar Isforum",
                "age_group": "U10",
                "host_club": "Jar",
                "teams": [
                    {"club": "Jar", "label": "Jar 1", "age_group": "U10"},
                    {"club": "Skien", "label": "Skien", "age_group": "U10"},
                ],
                "games": [{"home": "Jar 1", "away": "Skien", "parallel_slot": 0, "round_number": 1}],
            }
        ],
    }


class TestPublishGateReadsTheExportedPlanNotStalePlanning:
    """A canonical `season export` writes only the EXPORT stage; the gate
    must never re-verify an unrelated, stale PLANNING checkpoint left behind
    by an earlier, different run (issue #398)."""

    def test_stale_planning_checkpoint_never_blocks_a_hard_valid_export(self, tmp_path):
        _init_repo(tmp_path)
        _write_export(tmp_path)
        _write_audit(tmp_path, status="PASS")

        # The EXPORT checkpoint's own reviewed_plan is hard-valid...
        export_checkpoint = PipelineState(tmp_path).read_stage(StageName.EXPORT)
        export_checkpoint["reviewed_plan"] = _hard_valid_plan()
        PipelineState(tmp_path).write_stage(StageName.EXPORT, export_checkpoint, status=StageStatus.DONE)

        # ...but a stale, unrelated PLANNING checkpoint is hard-invalid.
        PipelineState(tmp_path).write_stage(
            StageName.PLANNING, {"plan": _hard_invalid_plan()}, status=StageStatus.DONE
        )

        result = _publish(tmp_path)

        assert result.status != "blocked" or "hard verifisering" not in result.summary.lower()

    def test_hard_invalid_reviewed_plan_still_blocks(self, tmp_path):
        _init_repo(tmp_path)
        _write_export(tmp_path)
        _write_audit(tmp_path, status="PASS")

        export_checkpoint = PipelineState(tmp_path).read_stage(StageName.EXPORT)
        export_checkpoint["reviewed_plan"] = _hard_invalid_plan()
        PipelineState(tmp_path).write_stage(StageName.EXPORT, export_checkpoint, status=StageStatus.DONE)

        result = _publish(tmp_path)

        assert result.status == "blocked"
        assert "hard verifisering" in result.summary.lower()


class TestAuditFailBlocksPublication:
    def test_fail_status_blocks(self, tmp_path):
        _init_repo(tmp_path)
        _write_export(tmp_path)
        _write_audit(tmp_path, status="FAIL")

        result = _publish(tmp_path)

        assert result.status == "blocked"
        assert not (tmp_path / ".git" / "refs" / "heads" / "gh-pages").exists()


class TestIncompleteAuditNeverBecomesPass:
    def test_incomplete_status_blocks(self, tmp_path):
        _init_repo(tmp_path)
        _write_export(tmp_path)
        _write_audit(tmp_path, status="INCOMPLETE")

        result = _publish(tmp_path)

        assert result.status == "blocked"


class TestStaleAuditRejected:
    def test_audit_for_a_different_export_fingerprint_is_rejected(self, tmp_path):
        _init_repo(tmp_path)
        _write_export(tmp_path, fingerprint="fp-old")
        _write_audit(tmp_path, status="PASS", export_fingerprint="fp-old")

        # Export regenerated -> new fingerprint invalidates the old audit.
        _write_export(tmp_path, fingerprint="fp-new")

        result = _publish(tmp_path)

        assert result.status == "blocked"
        assert "foreldet" in " ".join(result.problems).lower() or "foreldet" in result.summary.lower()


class TestFreshPassProceedsToNormalApprovalFlow:
    def test_pass_status_reaches_the_confirm_public_flow(self, tmp_path):
        _init_repo(tmp_path)
        _write_export(tmp_path)
        _write_audit(tmp_path, status="PASS")

        result = _publish(tmp_path)

        assert result.status == "ok"


class TestReviewRequiredEscalatesToOperator:
    def test_review_required_raises_a_distinct_audit_question_and_blocks(self, tmp_path):
        _init_repo(tmp_path)
        _write_export(tmp_path)
        _write_audit(tmp_path, status="REVIEW_REQUIRED")

        result = _publish(tmp_path)

        assert result.status == "blocked"
        assert result.requires_human is True
        audit_questions = [q for q in RunManifest(tmp_path).all_questions() if q["type"] == "audit_review"]
        assert len(audit_questions) == 1
        assert audit_questions[0]["answered"] is False

    def test_generic_publication_approval_does_not_satisfy_audit_review(self, tmp_path):
        """A stale/unrelated --confirm-public-style 'godkjenn' answer must
        never also count as approving a REVIEW_REQUIRED audit finding."""
        _init_repo(tmp_path)
        _write_export(tmp_path)
        _write_audit(tmp_path, status="REVIEW_REQUIRED")

        result = _publish(tmp_path)
        assert result.status == "blocked"
        audit_question_id = next(
            q["id"] for q in RunManifest(tmp_path).all_questions() if q["type"] == "audit_review"
        )
        # Answer with the *publication* approval token, not the distinct
        # audit-review token — must not unblock publication.
        RunManifest(tmp_path).answer_question(audit_question_id, "godkjenn")

        result_again = _publish(tmp_path)
        assert result_again.status == "blocked"

    def test_answering_with_the_distinct_audit_review_token_unblocks_publication(self, tmp_path):
        _init_repo(tmp_path)
        _write_export(tmp_path)
        _write_audit(tmp_path, status="REVIEW_REQUIRED")

        _publish(tmp_path)  # raises the audit_review question
        audit_question_id = next(
            q["id"] for q in RunManifest(tmp_path).all_questions() if q["type"] == "audit_review"
        )
        RunManifest(tmp_path).answer_question(audit_question_id, "godkjenn revisjon")

        result = _publish(tmp_path)
        assert result.status == "ok"


class TestReviewRequiredApprovalScopedToExport:
    def test_a_missing_audit_id_is_derived_server_side(self, tmp_path):
        _init_repo(tmp_path)
        _write_export(tmp_path)
        _write_audit_without_audit_id(tmp_path, status="REVIEW_REQUIRED")

        _publish(tmp_path)

        audit_question = next(
            q for q in RunManifest(tmp_path).all_questions() if q["type"] == "audit_review"
        )
        assert "revisjon None" not in audit_question["summary"]

    def test_prior_export_approval_does_not_cover_a_newer_different_export(self, tmp_path):
        """An earlier export's approved REVIEW_REQUIRED question must never
        satisfy the gate for a later, unrelated export whose findings differ."""
        _init_repo(tmp_path)
        _write_export(tmp_path, fingerprint="fp-sept14")
        _write_audit_without_audit_id(tmp_path, status="REVIEW_REQUIRED", export_fingerprint="fp-sept14")
        _publish(tmp_path)
        first_question = next(
            q for q in RunManifest(tmp_path).all_questions() if q["type"] == "audit_review"
        )
        RunManifest(tmp_path).answer_question(first_question["id"], "godkjenn revisjon")

        # A materially different later export: its own REVIEW_REQUIRED audit
        # must raise a fresh question rather than reuse the older approval.
        _write_export(tmp_path, fingerprint="fp-sept16")
        _write_audit_without_audit_id(tmp_path, status="REVIEW_REQUIRED", export_fingerprint="fp-sept16")
        result = _publish(tmp_path)

        assert result.status == "blocked"
        audit_questions = [q for q in RunManifest(tmp_path).all_questions() if q["type"] == "audit_review"]
        assert len(audit_questions) == 2
        assert audit_questions[1]["answered"] is False
