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
        # Both workflows support manual workflow_dispatch and are callable as
        # a reusable job from the SharePoint sync routers (dispatch-only —
        # no push trigger, so they cannot loop on their own commits).
        assert "workflow_dispatch" in on_block, f"{name} should support workflow_dispatch"
        assert "workflow_call" in on_block, f"{name} should be callable from the sync routers"
        assert "push" not in on_block, f"{name} must not have a push trigger (loop risk)"
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
    # Dispatch-only: called by sharepoint-import.yml via workflow_call, or
    # manually via workflow_dispatch. No push trigger.
    on_block = workflow.data.get("on", workflow.data.get(True, {}))
    assert "workflow_call" in on_block
    assert "activity_input" in on_block["workflow_call"].get("inputs", {})
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


def test_registration_publish_delegates_to_cli_and_syncs_workbook():
    workflow = WORKFLOWS_PARSED["registration-publish"]

    assert workflow.data["permissions"] == {"contents": "write"}
    # Dispatch-only: called by sharepoint-registrations-import.yml via
    # workflow_call, or manually via workflow_dispatch. No push trigger.
    on_block = workflow.data.get("on", workflow.data.get(True, {}))
    assert "workflow_call" in on_block
    assert "csv_path" in on_block["workflow_call"].get("inputs", {})
    # workflow_dispatch inputs
    assert "csv_path" in workflow.inputs
    assert "workbook_path" in workflow.inputs
    # Canonical CLI delegation
    assert "scripts/rvv-miniputt registered-teams" in workflow.text
    assert "--publish" in workflow.text
    assert "--confirm-public" in workflow.text
    # Own concurrency group (serializes repeated registration publishes)
    assert workflow.data["concurrency"]["group"] == "registration-publish-${{ github.ref }}"
    # Sync step with loop prevention
    assert "sync_registered_teams_to_workbook" in workflow.text
    assert "[skip ci]" in workflow.text
    # Lightweight domain-specific validation instead of the full test suite
    assert "scripts/check-registration-snapshot.py" in workflow.text
    # Artifact uploads
    assert "actions/upload-artifact@v4" in workflow.text
    assert "sync-report.json" in workflow.text


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
    """Each routine workflow serializes its own repeated publishes via a
    per-ref concurrency group (activity and registration publishing use
    separate groups since they write independent Pages content)."""
    activity_wf = WORKFLOWS_PARSED["activity-publish"]
    reg_wf = WORKFLOWS_PARSED["registration-publish"]

    activity_group = activity_wf.data.get("concurrency", {})
    reg_group = reg_wf.data.get("concurrency", {})
    assert activity_group.get("group") == "routine-publish-${{ github.ref }}"
    assert reg_group.get("group") == "registration-publish-${{ github.ref }}"
    assert activity_group.get("cancel-in-progress") is False
    assert reg_group.get("cancel-in-progress") is False


def test_registration_publish_prevents_recursive_loops():
    """Workbook commit includes [skip ci], and the workflow has no push
    trigger at all (it only runs via workflow_dispatch/workflow_call), so its
    own commit to the workbook cannot re-trigger itself."""
    workflow = WORKFLOWS_PARSED["registration-publish"]

    # The commit message for workbook updates must contain [skip ci]
    assert "[skip ci]" in workflow.text
    on_block = workflow.data.get("on", workflow.data.get(True, {}))
    assert "push" not in on_block


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
            # Import workflow delegates to the canonical activity-JSON contract
            # for validation; content arrives embedded in the issue body, so
            # there is no separate download step to reimplement.
            assert "validate_content_json" in workflow.text
            assert "hashlib.sha256" in workflow.text


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
    """The router already gates on title/author before dispatch, but the
    import job re-checks the dispatched issue itself (a race-safe re-fetch,
    not a job-level `if:`) since the router's debounce window means the
    issue's state could have changed between the router's check and the
    dispatched run actually starting."""
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # Title gate
    assert '.title == "sharepoint-sync: activities"' in text
    # Author association gate
    assert "author_association" in text
    assert "OWNER" in text and "MEMBER" in text and "COLLABORATOR" in text
    # Issue must still be open.
    assert '.state == "open"' in text
    # Failing the gate aborts the run.
    assert "Issue is not an open trusted activity sync request" in text


def test_sharepoint_import_enforces_fixed_source_and_target_contract():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # Enforces exact source.
    assert "source må være 'sharepoint'" in text
    assert 'parsed.get("source") != "sharepoint"' in text
    # Enforces target_path is one of the two known-good paths, not arbitrary.
    assert "target_path matcher verken SharePoint-kildefilen eller kanonisk repository-sti" in text
    assert 'target_path not in {os.environ["SOURCE_PATH"], os.environ["CANONICAL_PATH"]}' in text


def test_sharepoint_import_hard_codes_write_destination():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # The CANONICAL_PATH env var is set at workflow level and is what the
    # importer actually writes to.
    assert "CANONICAL_PATH" in text
    assert 'CANONICAL_PATH: "inputs/activities/activities.json"' in text
    assert 'canonical_path = Path(os.environ["CANONICAL_PATH"])' in text
    assert 'canonical_path.write_bytes(normalized_bytes)' in text
    # target_path from the issue body is validated for contract compliance
    # (must equal SOURCE_PATH or CANONICAL_PATH) but is never itself used as
    # a write destination — the write always goes through CANONICAL_PATH.
    assert 'os.environ["TARGET_PATH"]' not in text


def test_sharepoint_import_supports_identifier_validation():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # Optional identifier validation via repository vars.
    assert "EXPECTED_DRIVE_ID" in text
    assert "EXPECTED_DRIVE_ITEM_ID" in text
    assert "vars.SHAREPOINT_ACTIVITIES_DRIVE_ID" in text
    assert "vars.SHAREPOINT_ACTIVITIES_DRIVE_ITEM_ID" in text
    # Mismatch produces errors.
    assert "drive_id matcher ikke forventet repository-variabel" in text
    assert "drive_item_id matcher ikke forventet repository-variabel" in text


def test_sharepoint_import_rejects_wrong_source():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # When source != "sharepoint", it's an error.
    assert "source må være" in text


def test_sharepoint_import_rejects_wrong_target_path():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # When target_path isn't one of the known-good paths, it's an error.
    assert "target_path matcher verken SharePoint-kildefilen eller kanonisk repository-sti" in text


def test_sharepoint_import_validates_content_json_contract():
    """Content arrives embedded as a `content_json=` field in the issue body
    (no external download/fetch step), and is validated against the same
    canonical activity-JSON contract used elsewhere in the pipeline before
    being written to the canonical path."""
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # content_json is required in the issue body and must be valid JSON.
    assert '"content_json"' in text
    assert "json.loads(parsed.get(\"content_json\"" in text
    assert "content_json er ikke gyldig JSON" in text
    # Canonical contract validation, shared with the routine publish path.
    assert "from tournament_scheduler.pipeline.activity_export import (" in text
    assert "validate_content_json" in text
    assert "ActivityJSONValidationError" in text
    # Normalized bytes are hashed for the audit fingerprint.
    assert "hashlib.sha256" in text


def test_sharepoint_import_never_fetches_external_url():
    """Content arrives embedded in the issue body (pushed by Power Automate),
    so the importer never makes an outbound request to SharePoint and has no
    URL/token to leak in logs or the audit commit message."""
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    assert "requests.get" not in text
    assert "download_url" not in text.lower()
    # Git commit message uses source/drive_id/drive_item_id/version, not a URL.
    assert "Importer aktivitetsdata fra SharePoint" in text
    assert "drive_id=" in text
    assert "drive_item_id=" in text


def test_sharepoint_import_commits_only_when_changed():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # The commit step is conditional on changed=true.
    assert "steps.validate.outputs.changed == 'true'" in text
    # changed is computed by byte-comparing against the existing canonical file.
    assert "canonical_path.read_bytes() != normalized_bytes" in text
    # Success comment/close.
    assert "gh issue close" in text


def test_sharepoint_import_leaves_issue_open_on_failure():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    text = workflow.text
    # Failure comment step, gated on either job not succeeding.
    assert "Kommenter ved feil" in text
    assert "env.IMPORT_RESULT != 'success' || env.PUBLISH_RESULT != 'success'" in text
    # Does NOT close on failure: gh issue close only appears in the
    # success-path step, not the failure-comment step.
    finalize_job = workflow.jobs["finalize-sync-issue"]
    steps = finalize_job["steps"]
    failure_step = next(s for s in steps if s["name"] == "Kommenter ved feil")
    assert "gh issue close" not in failure_step["run"]


def test_sharepoint_import_has_concurrency_group():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    group = workflow.data.get("concurrency", {})
    assert group.get("group") == "sharepoint-activities-import"
    assert group.get("cancel-in-progress") is True
