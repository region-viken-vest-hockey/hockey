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


def _write_audit(work_dir, *, status: str, export_fingerprint: str = "fp-1", run_id: str = "") -> dict:
    payload = {
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
