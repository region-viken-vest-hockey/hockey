"""Regression tests for browser-based GitHub Actions operator workflows."""

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
    def on(self) -> dict[str, Any]:
        # PyYAML applies YAML 1.1 booleans, so GitHub Actions' `on` key may
        # parse as True. Keep the tests about workflow behavior, not parser
        # dialect details.
        return self.data.get("on", self.data.get(True, {}))

    @property
    def workflow_dispatch(self) -> dict[str, Any]:
        return self.on.get("workflow_dispatch", {})

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
        assert set(workflow.on) == {"workflow_dispatch"}
        assert workflow.data.get("concurrency"), name


def test_validation_and_review_generation_never_publish_publicly():
    for name in ["validate", "review"]:
        workflow = WORKFLOWS_PARSED[name]
        assert workflow.data["permissions"]["contents"] == "read"
        assert "scripts/rvv-miniputt operator run" in workflow.text
        assert "--confirm-public" not in workflow.text
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
# Reusable routine content publishers
# ---------------------------------------------------------------------------


def test_routine_publish_workflows_are_manual_and_reusable():
    for name in ["activity-publish", "registration-publish"]:
        workflow = WORKFLOWS_PARSED[name]
        assert workflow.path.exists(), name
        assert "workflow_dispatch" in workflow.on
        assert "workflow_call" in workflow.on
        assert "push" not in workflow.on
        assert workflow.data.get("concurrency"), name


def test_activity_publish_delegates_to_cli_and_preserves_latest_snapshot():
    workflow = WORKFLOWS_PARSED["activity-publish"]

    assert workflow.data["permissions"] == {"contents": "write"}
    assert workflow.inputs["input_path"]["default"] == "inputs/activities/activities.json"
    assert "activity_input" in workflow.on["workflow_call"]["inputs"]
    assert "activity_input_format" in workflow.on["workflow_call"]["inputs"]
    assert "scripts/rvv-miniputt activities" in workflow.text
    assert "--publish" in workflow.text
    assert "--confirm-public" in workflow.text
    assert "normalize_activity_json" in workflow.text
    assert workflow.data["concurrency"]["group"] == "routine-publish-${{ github.ref }}"
    for expected in [
        "actions/upload-artifact@v4",
        "input-fingerprint.json",
        "publish-result.json",
        "pages_privacy_report.json",
    ]:
        assert expected in workflow.text


def test_registration_publish_delegates_to_cli_and_syncs_workbook():
    workflow = WORKFLOWS_PARSED["registration-publish"]

    assert workflow.data["permissions"] == {"contents": "write"}
    assert workflow.inputs["csv_path"]["default"] == "inputs/registrations/registered-teams.csv"
    assert workflow.inputs["workbook_path"]["default"] == "inputs/season/input.xlsx"
    assert "csv_path" in workflow.on["workflow_call"]["inputs"]
    assert "workbook_path" in workflow.on["workflow_call"]["inputs"]
    assert "scripts/rvv-miniputt registered-teams" in workflow.text
    assert "--publish" in workflow.text
    assert "--confirm-public" in workflow.text
    assert "sync_registered_teams_to_workbook" in workflow.text
    assert "[skip ci]" in workflow.text
    assert workflow.data["concurrency"]["group"] == "registration-publish-${{ github.ref }}"
    for expected in ["actions/upload-artifact@v4", "sync-report.json", "pages_privacy_report.json"]:
        assert expected in workflow.text


def test_routine_publish_workflows_never_run_full_season_planning():
    for name in ["activity-publish", "registration-publish"]:
        workflow = WORKFLOWS_PARSED[name]
        assert "operator run" not in workflow.text
        assert "stage1_config" not in workflow.text
        assert "stage2_scraping" not in workflow.text
        assert "stage3_planning" not in workflow.text
        assert "stage4_export" not in workflow.text


def test_bot_generated_content_updates_cannot_retrigger_publishers():
    for name in ["activity-publish", "registration-publish"]:
        workflow = WORKFLOWS_PARSED[name]
        assert "push" not in workflow.on
        assert "[skip ci]" in workflow.text


def test_workflows_delegate_to_canonical_cli_instead_of_reimplementing_season_policy():
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
        elif name == "publish":
            assert "scripts/rvv-miniputt operator publish" in workflow.text
        elif name == "rollback":
            assert "scripts/rvv-miniputt operator rollback" in workflow.text
        elif name == "activity-publish":
            assert "scripts/rvv-miniputt activities" in workflow.text
        elif name == "registration-publish":
            assert "scripts/rvv-miniputt registered-teams" in workflow.text
        elif name == "sharepoint-import":
            # The importer validates its transport JSON, then hands publication
            # to the reusable activity publisher. It does not own season policy.
            assert "validate_content_json" in workflow.text
            assert "uses: ./.github/workflows/activity-publish.yml" in workflow.text


# ---------------------------------------------------------------------------
# SharePoint activity import/router
# ---------------------------------------------------------------------------


def test_sharepoint_sync_router_debounces_and_dispatches_newest_issue():
    workflow = WORKFLOWS_PARSED["sharepoint-router"]

    assert workflow.on["issues"]["types"] == ["opened", "reopened"]
    assert workflow.data["permissions"] == {"actions": "write", "issues": "write"}
    assert "DEBOUNCE_SECONDS" in workflow.text
    assert 'sleep "$DEBOUNCE_SECONDS"' in workflow.text
    assert workflow.data["concurrency"]["cancel-in-progress"] is True
    debounce_seconds = int(workflow.data["env"]["DEBOUNCE_SECONDS"])
    assert workflow.data["jobs"]["route"]["timeout-minutes"] > debounce_seconds / 60
    assert "gh api --paginate --slurp" in workflow.text
    assert "sort_by(.created_at, .number)" in workflow.text
    assert "newest issue" in workflow.text
    assert "gh workflow run \"$WORKFLOW\"" in workflow.text


def test_sharepoint_import_is_serialized_and_dispatched_after_router():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]

    assert set(workflow.on) == {"workflow_dispatch"}
    assert "issue_number" in workflow.inputs
    assert workflow.data["concurrency"] == {
        "group": "sharepoint-activities-import",
        "cancel-in-progress": True,
    }


def test_sharepoint_import_has_least_privilege_permissions():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]
    assert workflow.data["permissions"] == {"contents": "write", "issues": "write"}


def test_sharepoint_import_validates_trusted_open_sync_issue():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]
    text = workflow.text

    assert '.title == "sharepoint-sync: activities"' in text
    assert '.state == "open"' in text
    assert "author_association" in text
    for association in ["OWNER", "MEMBER", "COLLABORATOR"]:
        assert association in text


def test_sharepoint_import_enforces_source_and_optional_target_contract():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]
    text = workflow.text

    assert "source må være 'sharepoint'" in text
    assert 'SOURCE_PATH: "inputs/activities/activities.xlsx"' in text
    assert 'CANONICAL_PATH: "inputs/activities/activities.json"' in text
    assert "target_path matcher verken SharePoint-kildefilen eller kanonisk repository-sti" in text


def test_sharepoint_import_supports_identifier_validation():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]
    text = workflow.text

    assert "EXPECTED_DRIVE_ID" in text
    assert "EXPECTED_DRIVE_ITEM_ID" in text
    assert "drive_id matcher ikke forventet repository-variabel" in text
    assert "drive_item_id matcher ikke forventet repository-variabel" in text


def test_sharepoint_import_validates_embedded_json_and_reuses_activity_publisher():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]
    text = workflow.text

    assert 'required = {"source", "content_json"}' in text
    assert "json.loads" in text
    assert "validate_content_json" in text
    assert "hashlib.sha256" in text
    assert 'Path(os.environ["CANONICAL_PATH"])' in text
    assert "uses: ./.github/workflows/activity-publish.yml" in text
    assert "activity_input_format: json" in text
    # The current transport is content JSON in the trusted issue; the importer
    # must not follow arbitrary download URLs or parse XLSX itself.
    assert "download_url" not in text
    assert "requests.get" not in text
    assert "openpyxl.load_workbook" not in text


def test_sharepoint_import_commits_only_when_canonical_json_changed():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]
    text = workflow.text

    assert "steps.validate.outputs.changed == 'true'" in text
    assert "canonical_path.write_bytes(normalized_bytes)" in text
    assert 'git add "$CANONICAL_PATH"' in text
    assert "gh issue close" in text


def test_sharepoint_import_closes_previous_issues_only_after_success():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]
    text = workflow.text

    assert "if: env.IMPORT_RESULT == 'success' && env.PUBLISH_RESULT == 'success'" in text
    assert "select(.number < $current)" in text
    assert 'gh issue close "$previous_issue"' in text


def test_sharepoint_import_reports_failure_without_closing_current_issue():
    workflow = WORKFLOWS_PARSED["sharepoint-import"]
    finalize_steps = workflow.jobs["finalize-sync-issue"]["steps"]
    failure_step = next(step for step in finalize_steps if step.get("name") == "Kommenter ved feil")

    assert failure_step["if"] == "env.IMPORT_RESULT != 'success' || env.PUBLISH_RESULT != 'success'"
    assert "gh issue comment" in failure_step["run"]
    assert "gh issue close" not in failure_step["run"]
