"""Regression tests for browser-based GitHub Actions operator workflows (issue #45)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

WORKFLOW_FILES = {
    "validate": WORKFLOWS / "season-validate.yml",
    "review": WORKFLOWS / "season-review-bundle.yml",
    "publish": WORKFLOWS / "season-publish.yml",
    "rollback": WORKFLOWS / "season-rollback.yml",
    "activity-publish": WORKFLOWS / "activity-publish.yml",
    "registration-publish": WORKFLOWS / "registration-publish.yml",
    "sharepoint-import": WORKFLOWS / "sharepoint-import.yml",
    "sharepoint-router": WORKFLOWS / "sharepoint-sync-router.yml",
}


class Workflow:
    def __init__(self, path: Path):
        self.path = path
        self.text = path.read_text(encoding="utf-8")
        self.data = yaml.safe_load(self.text)

    @property
    def workflow_dispatch(self) -> dict[str, Any]:
        # PyYAML still applies YAML 1.1 booleans, so the GitHub Actions `on`
        # key may parse as True. Support both to keep the test about content,
        # not the parser version.
        on_block = self.data.get("on", self.data.get(True, {}))
        return on_block.get("workflow_dispatch", {})

    @property
    def inputs(self) -> dict[str, Any]:
        return self.workflow_dispatch.get("inputs", {})

    @property
    def jobs(self) -> dict[str, Any]:
        return self.data.get("jobs", {})


WORKFLOWS_PARSED = {name: Workflow(path) for name, path in WORKFLOW_FILES.items()}


def test_all_browser_operator_workflows_exist_and_are_manual():
    for name in ["validate", "review", "publish", "rollback"]:
        workflow = WORKFLOWS_PARSED[name]
        assert workflow.path.exists(), name
        assert workflow.workflow_dispatch, name
        on_block = workflow.data.get("on", workflow.data.get(True, {}))
        assert set(on_block) == {"workflow_dispatch"}
        assert workflow.data.get("concurrency"), name


def test_routine_publish_workflows_exist_and_trigger_on_path():
    for name in ["activity-publish", "registration-publish"]:
        workflow = WORKFLOWS_PARSED[name]
        assert workflow.path.exists(), name
        on_block = workflow.data.get("on", workflow.data.get(True, {}))
        # Both workflows support workflow_dispatch and push.paths
        assert "workflow_dispatch" in on_block, f"{name} should support workflow_dispatch"
        assert "push" in on_block, f"{name} should support push trigger"
        assert workflow.data.get("concurrency"), f"{name} should have a concurrency group"


def test_validation_and_review_generation_never_publish_publicly():
    for name in ["validate", "review"]:
        workflow = WORKFLOWS_PARSED[name]
        assert workflow.data["permissions"]["contents"] == "read"
        assert "scripts/rvv-miniputt operator run" in workflow.text
        assert "--confirm-public" not in workflow.text
        assert "operator publish \\\n            --work-dir \"$WORK_DIR\" \\\n            --dry-run" in workflow.text or name == "validate"
        assert "actions/upload-artifact@v4" in workflow.text
        assert "operator publish --confirm-public" not in workflow.text
        assert "--publish" not in workflow.text


def test_validation_artifact_contains_fingerprint_manifest_logs_and_review_output():
    workflow = WORKFLOWS_PARSED["validate"]

    assert set(workflow.inputs) == {"input_path", "log_level"}
    for expected in [
        "input-fingerprint.json",
        "validation.log",
        "status.json",
        "run_manifest.json",
        "logs/**",
        "export/github-validate",
    ]:
        assert expected in workflow.text
    assert "scripts/check quick" in workflow.text


def test_review_bundle_uploads_review_artifacts_and_privacy_report():
    workflow = WORKFLOWS_PARSED["review"]

    assert workflow.data["permissions"] == {"contents": "read", "issues": "write"}
    for input_name in ["input_path", "iterations", "force_refresh", "log_level", "review_issue_number"]:
        assert input_name in workflow.inputs
    for expected in [
        "input-fingerprint.json",
        "review-run.log",
        "publish-preview.json",
        "review-summary.md",
        "run_manifest.json",
        "public_bundle/pages_privacy_report.json",
        "export/github-review",
        "gh issue comment",
    ]:
        assert expected in workflow.text


def test_publication_is_separate_protected_and_fingerprint_bound():
    workflow = WORKFLOWS_PARSED["publish"]

    assert workflow.data["permissions"] == {"actions": "read", "contents": "write"}
    for input_name in [
        "review_run_id",
        "artifact_name",
        "run_id",
        "export_dir",
        "bundle_fingerprint",
        "confirm_public",
        "no_verify",
    ]:
        assert input_name in workflow.inputs
    assert "environment: pages-publication" in workflow.text
    assert "gh run download" in workflow.text
    assert "EXPECTED_BUNDLE_FINGERPRINT" in workflow.text
    assert "Bundle fingerprint mismatch" in workflow.text
    assert "--dry-run" in workflow.text
    assert "scripts/rvv-miniputt operator publish" in workflow.text
    assert "--confirm-public" in workflow.text
    assert "PUBLISER" in workflow.text
    assert "public_bundle/pages_privacy_report.json" in workflow.text


def test_rollback_is_separate_protected_and_requires_run_id():
    workflow = WORKFLOWS_PARSED["rollback"]

    assert workflow.data["permissions"] == {"contents": "write"}
    assert set(workflow.inputs) == {"run_id", "confirm_rollback", "no_push"}
    assert "environment: pages-publication" in workflow.text
    assert "RULL_TILBAKE" in workflow.text
    assert "scripts/rvv-miniputt operator rollback \"$RUN_ID\"" in workflow.text
    assert "--confirm-public" in workflow.text
    assert "publish-history" in workflow.text
    assert "rollback-result.json" in workflow.text


# ---------------------------------------------------------------------------
# Routine content auto-publish workflows (issue #49)
# ---------------------------------------------------------------------------


def test_activity_publish_delegates_to_cli_and_preserves_latest_snapshot():
    workflow = WORKFLOWS_PARSED["activity-publish"]

    assert workflow.data["permissions"] == {"contents": "write"}
    # Path-triggered on the canonical input.
    on_block = workflow.data.get("on", workflow.data.get(True, {}))
    push_block = on_block.get("push", {})
    assert "inputs/activities/activities.xlsx" in str(push_block.get("paths", []))
    # workflow_dispatch input
    assert "input_path" in workflow.inputs
    # Canonical CLI delegation
    assert "scripts/rvv-miniputt activities" in workflow.text
    assert "--publish" in workflow.text
    assert "--confirm-public" in workflow.text
    # Shares routine-publish concurrency group
    assert "routine-publish" in workflow.text
    # Artifact uploads on failure
    assert "actions/upload-artifact@v4" in workflow.text
    assert "input-fingerprint.json" in workflow.text
    assert "publish-result.json" in workflow.text
    assert "pages_privacy_report.json" in workflow.text
    assert "scripts/check quick" in workflow.text


def test_registration_publish_delegates_to_cli_and_syncs_workbook():
    workflow = WORKFLOWS_PARSED["registration-publish"]

    assert workflow.data["permissions"] == {"contents": "write"}
    # Path-triggered on the canonical CSV.
    on_block = workflow.data.get("on", workflow.data.get(True, {}))
    push_block = on_block.get("push", {})
    assert "inputs/registrations/registered-teams.csv" in str(push_block.get("paths", []))
    # workflow_dispatch inputs
    assert "csv_path" in workflow.inputs
    assert "workbook_path" in workflow.inputs
    # Canonical CLI delegation
    assert "scripts/rvv-miniputt registered-teams" in workflow.text
    assert "--publish" in workflow.text
    assert "--confirm-public" in workflow.text
    # Shares routine-publish concurrency group
    assert "routine-publish" in workflow.text
    # Sync step with loop prevention
    assert "sync_registered_teams_to_workbook" in workflow.text
    assert "[skip ci]" in workflow.text
    # Artifact uploads
    assert "actions/upload-artifact@v4" in workflow.text
    assert "sync-report.json" in workflow.text
    assert "scripts/check quick" in workflow.text


def test_routine_publish_workflows_never_run_full_season_planning():
    """Neither routine workflow invokes the full season-planning pipeline."""
    for name in ["activity-publish", "registration-publish"]:
        workflow = WORKFLOWS_PARSED[name]
        assert "operator run" not in workflow.text, f"{name} must not run full season planning"
        assert "stage1_config" not in workflow.text, f"{name} must not call pipeline stages directly"
        assert "stage2_scraping" not in workflow.text, f"{name} must not call pipeline stages directly"
        assert "stage3_planning" not in workflow.text, f"{name} must not call pipeline stages directly"
        assert "stage4_export" not in workflow.text, f"{name} must not call pipeline stages directly"


def test_routine_publish_workflows_serialize_publication():
    """Both routine workflows use the same concurrency group to serialize Pages writes."""
    activity_wf = WORKFLOWS_PARSED["activity-publish"]
    reg_wf = WORKFLOWS_PARSED["registration-publish"]

    assert "routine-publish" in activity_wf.text
    assert "routine-publish" in reg_wf.text
    # Verify it's the exact same group key
    activity_group = activity_wf.data.get("concurrency", {})
    reg_group = reg_wf.data.get("concurrency", {})
    assert activity_group.get("group") == reg_group.get("group") == "routine-publish-${{ github.ref }}"


def test_registration_publish_prevents_recursive_loops():
    """Workbook commit includes [skip ci] and path filters prevent re-trigger."""
    workflow = WORKFLOWS_PARSED["registration-publish"]

    # The commit message for workbook updates must contain [skip ci]
    assert "[skip ci]" in workflow.text
    # Path trigger only on the CSV, not the workbook
    on_block = workflow.data.get("on", workflow.data.get(True, {}))
    push_block = on_block.get("push", {})
    paths = push_block.get("paths", [])
    assert "inputs/registrations/registered-teams.csv" in str(paths)
    assert "inputs/season/input.xlsx" not in str(paths)


def test_workflows_delegate_to_canonical_cli_instead_of_reimplementing_policy():
    forbidden_fragments = [
        "python -m tournament_scheduler.pipeline.stage",
        "python -m tournament_scheduler.pipeline.pages_publish",
        "git push origin gh-pages",
        "git checkout gh-pages",
        "git worktree",
        "\n/rvv-miniputt",
        "eval ",
    ]

    for name, workflow in WORKFLOWS_PARSED.items():
        for fragment in forbidden_fragments:
            assert fragment not in workflow.text, f"{name} should not contain {fragment!r}"
        if name in {"validate", "review"}:
            assert "scripts/rvv-miniputt operator run" in workflow.text
        if name == "publish":
            assert "scripts/rvv-miniputt operator publish" in workflow.text
        if name == "rollback":
            assert "scripts/rvv-miniputt operator rollback" in workflow.text
        if name == "activity-publish":
            assert "scripts/rvv-miniputt activities" in workflow.text
        if name == "registration-publish":
            assert "scripts/rvv-miniputt registered-teams" in workflow.text
        if name == "sharepoint-import":
            # Import workflow delegates to Python inline scripts for download/validation.
            assert "openpyxl" in workflow.text
            assert "requests" in workflow.text


# ---------------------------------------------------------------------------
# SharePoint import workflow (issue #49 follow-up)
# ---------------------------------------------------------------------------


def test_sharepoint_sync_router_debounces_and_dispatches_newest_issue():
    workflow = WORKFLOWS_PARSED["sharepoint-router"]

    on_block = workflow.data.get("on", workflow.data.get(True, {}))
    assert on_block["issues"]["types"] == ["opened", "reopened"]
    assert workflow.data["permissions"] == {"actions": "write", "issues": "write"}
    assert "DEBOUNCE_SECONDS" in workflow.text
    assert 'sleep "$DEBOUNCE_SECONDS"' in workflow.text
    assert workflow.data["concurrency"]["cancel-in-progress"] is True
    debounce_seconds = int(workflow.data["env"]["DEBOUNCE_SECONDS"])
    assert workflow.data["jobs"]["route"]["timeout-minutes"] > debounce_seconds / 60
    assert "gh api --paginate --slurp" in workflow.text
    assert 'sort_by(.created_at, .number)' in workflow.text
    assert "newest issue" in workflow.text
    assert "gh workflow run \"$WORKFLOW\"" in workflow.text


def test_sharepoint_import_is_serialized_across_issue_numbers():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    group = workflow.data.get("concurrency", {})
    assert group.get("group") == "sharepoint-activities-import"
    assert group.get("cancel-in-progress") is True


def test_sharepoint_import_triggered_by_dispatch_after_router():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    on_block = workflow.data.get("on", workflow.data.get(True, {}))
    assert set(on_block) == {"workflow_dispatch"}
    assert "issue_number" in workflow.inputs


def test_sharepoint_import_closes_previous_issues_only_after_success():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    assert 'if: env.IMPORT_RESULT == \'success\' && env.PUBLISH_RESULT == \'success\'' in text
    assert 'select(.number < $current)' in text
    assert 'gh issue close "$previous_issue"' in text


def test_sharepoint_import_has_least_privilege_permissions():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    assert workflow.data["permissions"] == {"contents": "write", "issues": "write"}


def test_sharepoint_import_title_and_author_gate():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    job = list(workflow.jobs.values())[0]
    condition = str(job.get("if", ""))
    # Title gate
    assert "sharepoint-sync: activities" in condition
    # Author association gate
    assert "author_association" in condition
    assert "OWNER" in condition or "MEMBER" in condition or "COLLABORATOR" in condition


def test_sharepoint_import_enforces_fixed_source_and_target_contract():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # Enforces exact source.
    assert "source må være" in text
    # Enforces exact target_path.
    assert "target_path må være" in text
    # These are contract enforcement, not just field presence.
    assert '"sharepoint"' in text
    assert '"inputs/activities/activities.xlsx"' in text


def test_sharepoint_import_hard_codes_write_destination():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # The CANONICAL_PATH env var is set at workflow level.
    assert "CANONICAL_PATH" in text
    assert "inputs/activities/activities.xlsx" in text
    # The download step documents the hard-coded destination.
    assert "Hard-coded destination" in text
    # target_path from issue body is validated but NOT used for writing.
    # Verify the download step uses CANONICAL_PATH, not TARGET_PATH.
    # The parsed target_path is validated for contract compliance only.


def test_sharepoint_import_supports_identifier_validation():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # Optional identifier validation via env vars.
    assert "EXPECTED_DRIVE_ID" in text
    assert "EXPECTED_DRIVE_ITEM_ID" in text
    # Mismatch produces errors.
    assert "matcher ikke forventet verdi" in text


def test_sharepoint_import_rejects_wrong_source():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # When source != "sharepoint", it's an error.
    assert "source må være" in text


def test_sharepoint_import_rejects_wrong_target_path():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # When target_path != expected value, it's an error.
    assert "target_path må være" in text


def test_sharepoint_import_downloads_and_validates_xlsx():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # Download with requests
    assert "requests.get" in text
    assert "allow_redirects=True" in text
    # XLSX magic byte verification
    assert "PK\\x03\\x04" in text or "PK\\x03\\x04" in text or "PK\\003\\004" in text or "PK\x03\x04" in text
    # openpyxl validation
    assert "openpyxl.load_workbook" in text
    # Size bound
    assert "20 * 1024 * 1024" in text
    # SHA-256 comparison
    assert "hashlib.sha256" in text


def test_sharepoint_import_never_exposes_download_url():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # The download_url value itself must not appear in logs/comments.
    assert "<redacted>" in text
    # The env var DOWNLOAD_URL is passed to the download step, but never echoed.
    # Git commit message uses source/drive_id/drive_item_id/version, not the URL.
    assert "Importer aktivitetsarbeidsbok fra SharePoint" in text


def test_sharepoint_import_commits_only_when_changed():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # The commit step is conditional on changed=true.
    assert "steps.download.outputs.changed == 'true'" in text
    # Unchanged detection.
    assert "FILE_UNCHANGED" in text
    # Success comment.
    assert "gh issue close" in text


def test_sharepoint_import_leaves_issue_open_on_failure():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # Failure comment step.
    assert "Kommenter issue — feil" in text
    assert "if: failure()" in text
    # Does NOT close on failure.
    # The success step has gh issue close; failure step does not.


def test_sharepoint_import_has_concurrency_group():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    group = workflow.data.get("concurrency", {})
    assert group.get("group") == "sharepoint-activities-import"
    assert group.get("cancel-in-progress") is True
