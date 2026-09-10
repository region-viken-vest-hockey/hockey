"""Tests for tournament_scheduler.pipeline.pages_publish (issue #17)."""

from __future__ import annotations

import subprocess
from pathlib import Path


from tournament_scheduler.pipeline.pages_publish import (
    bundle_fingerprint,
    diff_latest,
    list_publication_history,
    publish,
    resolve_urls,
    rollback_to_run,
    target_fingerprint,
)


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _init_repo_with_remote(tmp_path: Path) -> Path:
    """Create a bare 'remote' repo and a local clone with one commit on main.

    Returns the local repo path.
    """
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(["init", "--bare", "-b", "main"], cwd=remote)

    local = tmp_path / "local"
    local.mkdir()
    _git(["init", "-b", "main"], cwd=local)
    _git(["config", "user.email", "test@example.com"], cwd=local)
    _git(["config", "user.name", "Test"], cwd=local)
    (local / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "README.md"], cwd=local)
    _git(["commit", "-m", "initial commit"], cwd=local)
    _git(["remote", "add", "origin", str(remote)], cwd=local)
    _git(["push", "origin", "main"], cwd=local)
    return local


def _write_export_bundle(export_dir: Path, *, content: str = "<h1>Season plan</h1>") -> None:
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "season_plan.html").write_text(content, encoding="utf-8")
    (export_dir / "season_plan.ics").write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n", encoding="utf-8")


def _worktree_at(local: Path, branch: str, dest: Path) -> None:
    _git(["worktree", "add", str(dest), branch], cwd=local)


class TestPublishValidation:
    def test_missing_export_dir_returns_failed(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        result = publish(export_dir=str(tmp_path / "nope"), run_id="run-1", repo_dir=str(local), push=False)
        assert result.status == "failed"

    def test_not_a_git_repo_returns_failed(self, tmp_path):
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        result = publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(non_repo), push=False)
        assert result.status == "failed"


class TestPublishCreatesBranch:
    def test_initial_publish_creates_orphan_branch_with_expected_layout(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        result = publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local))

        assert result.status == "ok"
        assert any("commit_sha=" in e for e in result.evidence)

        check = tmp_path / "check"
        _worktree_at(local, "gh-pages", check)
        assert (check / "index.html").exists()
        assert (check / ".nojekyll").exists()
        assert (check / "latest" / "season_plan.html").read_text(encoding="utf-8") == "<h1>Season plan</h1>"
        assert (check / "runs" / "run-1" / "season_plan.html").exists()

        # Pushed to the remote too, not just committed locally.
        remote_check = tmp_path / "remote_check"
        _git(["clone", str(tmp_path / "remote.git"), str(remote_check)], cwd=tmp_path)
        _git(["checkout", "gh-pages"], cwd=remote_check)
        assert (remote_check / "latest" / "season_plan.html").exists()

    def test_does_not_force_push_and_preserves_main_branch(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local))

        # main is untouched — publishing must not check out or rewrite it.
        branch_proc = subprocess.run(
            ["git", "branch", "--show-current"], cwd=local, capture_output=True, text=True, check=True
        )
        assert branch_proc.stdout.strip() == "main"
        assert (local / "README.md").exists()


class TestPublishUpdatesLatestAndRetainsHistory:
    def test_second_publish_updates_latest_and_keeps_previous_run_dir(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"

        _write_export_bundle(export_dir, content="<h1>v1</h1>")
        publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local))

        _write_export_bundle(export_dir, content="<h1>v2</h1>")
        result = publish(export_dir=str(export_dir), run_id="run-2", repo_dir=str(local))
        assert result.status == "ok"

        check = tmp_path / "check2"
        _worktree_at(local, "gh-pages", check)
        assert (check / "latest" / "season_plan.html").read_text(encoding="utf-8") == "<h1>v2</h1>"
        # Historical retention: run-1's snapshot must still be there, untouched.
        assert (check / "runs" / "run-1" / "season_plan.html").read_text(encoding="utf-8") == "<h1>v1</h1>"
        assert (check / "runs" / "run-2" / "season_plan.html").read_text(encoding="utf-8") == "<h1>v2</h1>"


class TestPublishIdempotency:
    def test_republishing_identical_content_for_same_run_is_a_noop(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        first = publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local))
        second = publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local))

        assert first.status == "ok"
        assert second.status == "ok"
        first_sha = next(e for e in first.evidence if e.startswith("commit_sha=")).split("=", 1)[1]
        second_sha = next(e for e in second.evidence if e.startswith("commit_sha=")).split("=", 1)[1]
        assert first_sha == second_sha
        assert "Ingen endringer" in second.summary


class TestPublishPushFailure:
    def test_push_failure_is_reported_but_commit_is_not_lost(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        # First publish succeeds and creates the branch + remote.
        publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local))

        # Break the remote so the next push fails, without touching local history.
        _git(["remote", "set-url", "origin", str(tmp_path / "does-not-exist.git")], cwd=local)

        _write_export_bundle(export_dir, content="<h1>v2</h1>")
        result = publish(export_dir=str(export_dir), run_id="run-2", repo_dir=str(local))

        assert result.status == "failed"
        assert "push" in result.summary.lower()

        # The commit exists locally on gh-pages even though the push failed.
        log_proc = subprocess.run(
            ["git", "log", "gh-pages", "--oneline"], cwd=local, capture_output=True, text=True, check=True
        )
        assert "run-2" in log_proc.stdout


class TestPublishNoPush:
    def test_push_false_commits_locally_without_a_remote(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        result = publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)

        assert result.status == "ok"
        branches = subprocess.run(
            ["git", "branch", "--list", "gh-pages"], cwd=local, capture_output=True, text=True, check=True
        )
        assert "gh-pages" in branches.stdout

    def test_pages_urls_derived_from_a_github_style_remote(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        _git(["remote", "set-url", "origin", "git@github.com:acme/hockey.git"], cwd=local)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        result = publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)

        assert result.status == "ok"
        assert "https://acme.github.io/hockey/latest/" in result.artifacts
        assert "https://acme.github.io/hockey/runs/run-1/" in result.artifacts


class TestBundleFingerprint:
    def test_identical_content_fingerprints_identically(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        _write_export_bundle(a, content="<h1>x</h1>")
        _write_export_bundle(b, content="<h1>x</h1>")
        assert bundle_fingerprint(str(a)) == bundle_fingerprint(str(b))

    def test_changed_content_changes_the_fingerprint(self, tmp_path):
        a = tmp_path / "a"
        _write_export_bundle(a, content="<h1>v1</h1>")
        fp1 = bundle_fingerprint(str(a))
        _write_export_bundle(a, content="<h1>v2</h1>")
        fp2 = bundle_fingerprint(str(a))
        assert fp1 != fp2

    def test_added_file_changes_the_fingerprint(self, tmp_path):
        a = tmp_path / "a"
        _write_export_bundle(a)
        fp1 = bundle_fingerprint(str(a))
        (a / "extra.html").write_text("<p>extra</p>", encoding="utf-8")
        fp2 = bundle_fingerprint(str(a))
        assert fp1 != fp2


class TestTargetFingerprint:
    def test_same_target_fingerprints_identically(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        fp1 = target_fingerprint(repo_dir=str(local), branch="gh-pages", remote="origin", run_id="run-1")
        fp2 = target_fingerprint(repo_dir=str(local), branch="gh-pages", remote="origin", run_id="run-1")
        assert fp1 == fp2

    def test_different_run_id_changes_the_fingerprint(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        fp1 = target_fingerprint(repo_dir=str(local), branch="gh-pages", remote="origin", run_id="run-1")
        fp2 = target_fingerprint(repo_dir=str(local), branch="gh-pages", remote="origin", run_id="run-2")
        assert fp1 != fp2

    def test_different_branch_changes_the_fingerprint(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        fp1 = target_fingerprint(repo_dir=str(local), branch="gh-pages", remote="origin", run_id="run-1")
        fp2 = target_fingerprint(repo_dir=str(local), branch="gh-pages-staging", remote="origin", run_id="run-1")
        assert fp1 != fp2


class TestResolveUrls:
    def test_returns_none_without_a_github_style_remote(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        urls = resolve_urls(repo_dir=str(local), remote="origin", run_id="run-1")
        assert urls is None

    def test_returns_urls_for_a_github_style_remote(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        _git(["remote", "set-url", "origin", "git@github.com:acme/hockey.git"], cwd=local)
        urls = resolve_urls(repo_dir=str(local), remote="origin", run_id="run-1")
        assert urls == ("https://acme.github.io/hockey/latest/", "https://acme.github.io/hockey/runs/run-1/")


class TestDiffLatest:
    def test_everything_is_an_addition_when_branch_does_not_exist_yet(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        diff = diff_latest(str(export_dir), repo_dir=str(local), branch="gh-pages")

        # index.html is included even though the bundle didn't have one of its
        # own — diff_latest mirrors _copy_bundle's season_plan.html fallback.
        assert diff["add"] == ["latest/index.html", "latest/season_plan.html", "latest/season_plan.ics"]
        assert diff["update"] == []
        assert diff["remove"] == []

    def test_never_writes_anything(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        diff_latest(str(export_dir), repo_dir=str(local), branch="gh-pages")

        branches = subprocess.run(
            ["git", "branch", "--list", "gh-pages"], cwd=local, capture_output=True, text=True, check=True
        )
        assert branches.stdout.strip() == ""

    def test_detects_update_and_removal_against_a_published_branch(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir, content="<h1>v1</h1>")
        publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)

        # Change season_plan.html and drop season_plan.ics from the next bundle.
        (export_dir / "season_plan.ics").unlink()
        (export_dir / "season_plan.html").write_text("<h1>v2</h1>", encoding="utf-8")

        diff = diff_latest(str(export_dir), repo_dir=str(local), branch="gh-pages")

        # index.html changes too — it's a copy of season_plan.html.
        assert diff["update"] == ["latest/index.html", "latest/season_plan.html"]
        assert diff["remove"] == ["latest/season_plan.ics"]
        assert diff["add"] == []


class TestMetaJson:
    def test_writes_meta_json_with_run_id_and_fingerprint(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False, bundle_fingerprint="fp1")

        check = tmp_path / "check"
        _worktree_at(local, "gh-pages", check)
        import json

        latest_meta = json.loads((check / "latest" / "_meta.json").read_text())
        run_meta = json.loads((check / "runs" / "run-1" / "_meta.json").read_text())
        assert latest_meta == {"run_id": "run-1", "bundle_fingerprint": "fp1"}
        assert run_meta == {"run_id": "run-1", "bundle_fingerprint": "fp1"}

    def test_identical_content_and_fingerprint_republish_is_still_a_noop(self, tmp_path):
        """_meta.json must not carry a timestamp or other varying field — it would break idempotency."""
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        first = publish(
            export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False, bundle_fingerprint="fp1"
        )
        second = publish(
            export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False, bundle_fingerprint="fp1"
        )

        first_sha = next(e for e in first.evidence if e.startswith("commit_sha=")).split("=", 1)[1]
        second_sha = next(e for e in second.evidence if e.startswith("commit_sha=")).split("=", 1)[1]
        assert first_sha == second_sha
        assert "Ingen endringer" in second.summary


class TestRollbackToRun:
    def test_rollback_fails_when_branch_does_not_exist(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        result = rollback_to_run(run_id="run-1", repo_dir=str(local), push=False)
        assert result.status == "failed"

    def test_rollback_fails_when_run_was_never_published(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)
        publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)

        result = rollback_to_run(run_id="run-does-not-exist", repo_dir=str(local), push=False)
        assert result.status == "failed"

    def test_rollback_restores_latest_to_an_earlier_run_without_deleting_history(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"

        _write_export_bundle(export_dir, content="<h1>v1</h1>")
        publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False, bundle_fingerprint="fp1")

        _write_export_bundle(export_dir, content="<h1>v2 (bad)</h1>")
        publish(export_dir=str(export_dir), run_id="run-2", repo_dir=str(local), push=False, bundle_fingerprint="fp2")

        result = rollback_to_run(run_id="run-1", repo_dir=str(local), push=False)
        assert result.status == "ok"
        assert "rolled_back_to_run_id=run-1" in result.evidence

        check = tmp_path / "check"
        _worktree_at(local, "gh-pages", check)
        assert (check / "latest" / "season_plan.html").read_text(encoding="utf-8") == "<h1>v1</h1>"
        # Immutable history for both runs must still be intact.
        assert (check / "runs" / "run-1" / "season_plan.html").read_text(encoding="utf-8") == "<h1>v1</h1>"
        assert (check / "runs" / "run-2" / "season_plan.html").read_text(encoding="utf-8") == "<h1>v2 (bad)</h1>"

    def test_rollback_to_the_run_already_published_is_a_noop(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)
        publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)

        first = rollback_to_run(run_id="run-1", repo_dir=str(local), push=False)
        assert first.status == "ok"
        assert "ingen tilbakerulling" in first.summary.lower()


class TestPublicationHistory:
    def test_empty_history_when_branch_does_not_exist(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        assert list_publication_history(repo_dir=str(local)) == []

    def test_lists_publish_and_rollback_events_newest_first(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"

        _write_export_bundle(export_dir, content="<h1>v1</h1>")
        publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)
        _write_export_bundle(export_dir, content="<h1>v2</h1>")
        publish(export_dir=str(export_dir), run_id="run-2", repo_dir=str(local), push=False)
        rollback_to_run(run_id="run-1", repo_dir=str(local), push=False)

        history = list_publication_history(repo_dir=str(local))

        assert [(h["kind"], h["run_id"]) for h in history] == [
            ("rollback", "run-1"),
            ("publish", "run-2"),
            ("publish", "run-1"),
        ]
