"""Tests for the public publication history page (issue #798)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tournament_scheduler.pipeline.pages_history import (
    PublicationHistoryError,
    compose_history,
    render_history_page,
    write_history_page,
)
from tournament_scheduler.pipeline.pages_publish import publish, rollback_to_run
from tests.test_pages_publish import _init_repo_with_remote, _write_export_bundle


def _read_history(local: Path, branch: str = "gh-pages") -> str:
    proc = subprocess.run(
        ["git", "show", f"{branch}:history/index.html"],
        cwd=local, capture_output=True, text=True, check=True,
    )
    return proc.stdout


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _git_out(local: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=local, capture_output=True, text=True, check=True).stdout.strip()


def _current_run_ids(page: str) -> list[str]:
    """Run ids of rows marked current, from the page's row attributes."""
    import re

    return re.findall(r'data-run-id="([^"]+)"[^>]*data-current="true"', page)


class TestComposeHistory:
    def test_first_publication_is_the_only_current_entry(self):
        entries = compose_history([], run_id="run-1", kind="publish", date="2026-10-21T12:00:00+00:00")

        assert entries == [
            {"date": "2026-10-21T12:00:00+00:00", "kind": "publish", "run_id": "run-1", "fingerprint": ""}
        ]

    def test_new_publication_is_prepended_newest_first(self):
        prior = [{"date": "2026-10-20T10:00:00+00:00", "kind": "publish", "run_id": "run-1"}]

        entries = compose_history(prior, run_id="run-2", kind="publish", date="2026-10-21T12:00:00+00:00")

        assert [entry["run_id"] for entry in entries] == ["run-2", "run-1"]

    def test_a_publication_with_a_changed_run_is_always_recorded_even_for_the_newest_run_id(self):
        prior = [{"date": "2026-10-20T10:00:00+00:00", "kind": "rollback", "run_id": "run-1"}]

        entries = compose_history(prior, run_id="run-1", kind="publish", date="2026-10-21T12:00:00+00:00")

        assert [(entry["kind"], entry["run_id"]) for entry in entries] == [("publish", "run-1"), ("rollback", "run-1")]

    def test_rollback_is_recorded_as_a_new_entry_for_the_restored_run(self):
        prior = [
            {"date": "2026-10-21T12:00:00+00:00", "kind": "publish", "run_id": "run-2"},
            {"date": "2026-10-20T10:00:00+00:00", "kind": "publish", "run_id": "run-1"},
        ]

        entries = compose_history(prior, run_id="run-1", kind="rollback", date="2026-10-22T08:00:00+00:00")

        assert [(entry["kind"], entry["run_id"]) for entry in entries] == [
            ("rollback", "run-1"),
            ("publish", "run-2"),
            ("publish", "run-1"),
        ]


class TestRenderHistoryPage:
    def test_only_the_first_entry_is_marked_current(self):
        page = render_history_page([
            {"date": "2026-10-21T12:00:00+00:00", "kind": "publish", "run_id": "run-2"},
            {"date": "2026-10-20T10:00:00+00:00", "kind": "publish", "run_id": "run-1"},
        ])

        assert _current_run_ids(page) == ["run-2"]
        assert 'href="../runs/run-1/"' in page

    def test_run_ids_and_dates_are_html_escaped(self):
        page = render_history_page([
            {"date": "2026-10-21T12:00:00+00:00", "kind": "publish", "run_id": '<script>"x"</script>'},
        ])

        assert "<script>" not in page
        assert "&lt;script&gt;" in page

    def test_empty_log_says_so_instead_of_inventing_entries(self):
        page = render_history_page([])

        assert "Ingen publiseringer er registrert" in page
        assert "<tr" not in page


class TestHistoryPageFromPublishing:
    def test_publish_writes_history_listing_the_new_run_as_current(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        result = publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)

        assert result.status == "ok"
        assert _current_run_ids(_read_history(local)) == ["run-1"]

    def test_each_later_publish_moves_the_current_marker_and_keeps_history(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)
        _write_export_bundle(export_dir, content="<h1>Changed</h1>")
        publish(export_dir=str(export_dir), run_id="run-2", repo_dir=str(local), push=False)

        page = _read_history(local)
        assert _current_run_ids(page) == ["run-2"]
        assert page.index('data-run-id="run-2"') < page.index('data-run-id="run-1"')

    def test_rollback_records_a_rollback_entry_pointing_at_the_restored_run(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)
        _write_export_bundle(export_dir, content="<h1>Changed</h1>")
        publish(export_dir=str(export_dir), run_id="run-2", repo_dir=str(local), push=False)
        result = rollback_to_run(run_id="run-1", repo_dir=str(local), push=False)

        assert result.status == "ok"
        page = _read_history(local)
        assert _current_run_ids(page) == ["run-1"]
        assert "Tilbakerullet til" in page
        assert page.count('data-run-id="run-1"') == 2

    def test_identical_republish_keeps_commit_and_history_bytes_identical(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)
        head_before = _git_out(local, "rev-parse", "gh-pages")
        page_before = _read_history(local)
        result = publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)

        assert result.status == "ok"
        assert _git_out(local, "rev-parse", "gh-pages") == head_before
        assert _read_history(local) == page_before

    def test_rollback_then_republish_of_the_rolled_back_run_records_a_new_publication(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)
        _write_export_bundle(export_dir, content="<h1>Changed</h1>")
        publish(export_dir=str(export_dir), run_id="run-2", repo_dir=str(local), push=False)
        rollback_to_run(run_id="run-1", repo_dir=str(local), push=False)
        _write_export_bundle(export_dir, content="<h1>Corrected</h1>")
        result = publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)

        assert result.status == "ok"
        page = _read_history(local)
        assert _current_run_ids(page) == ["run-1"]
        assert page.count('data-run-id="run-1"') == 3
        assert "Publisert" in page.split('data-current="true"')[1].split("</tr>")[0]

    def test_unreadable_existing_log_raises_instead_of_dropping_history(self):
        class _Git:
            def __call__(self, args, cwd):
                returncode = 0 if args[:2] == ["rev-parse", "--verify"] else 1
                return subprocess.CompletedProcess(args, returncode, stdout="", stderr="boom")

        with pytest.raises(PublicationHistoryError):
            write_history_page(Path("."), run_id="run-1", kind="publish", git=_Git(), record=True)

    def test_legacy_branch_without_history_page_gets_backfilled_on_identical_republish(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)
        publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)

        # Simulate a gh-pages branch published before the history page existed.
        worktree = tmp_path / "legacy-wt"
        _run(["worktree", "add", str(worktree), "gh-pages"], cwd=local)
        _run(["rm", "-q", "history/index.html"], cwd=worktree)
        _run(["-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-q", "-m", "legacy"], cwd=worktree)
        _run(["worktree", "remove", "--force", str(worktree)], cwd=local)
        assert "history/index.html" not in _git_out(local, "ls-tree", "-r", "--name-only", "gh-pages")

        result = publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)

        assert result.status == "ok"
        assert _git_out(local, "log", "-1", "--format=%s", "gh-pages") == "Add publication history page"
        page = _read_history(local)
        assert _current_run_ids(page) == ["run-1"]
        # Backfill records no new publication: the log still has exactly one publish of run-1.
        assert _git_out(local, "log", "--format=%s", "gh-pages").count("Publish run run-1") == 1

    def test_reported_publication_time_is_the_committed_log_time(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        result = publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)

        published_at = next(e.split("=", 1)[1] for e in result.evidence if e.startswith("published_at="))
        assert published_at == _git_out(local, "log", "-1", "--date=iso-strict", "--format=%ad", "gh-pages")
        assert f'data-published-at="{published_at}" data-current="true"' in _read_history(local)

    def test_history_page_contains_no_local_export_paths(self, tmp_path):
        local = _init_repo_with_remote(tmp_path)
        export_dir = tmp_path / "export"
        _write_export_bundle(export_dir)

        publish(export_dir=str(export_dir), run_id="run-1", repo_dir=str(local), push=False)

        page = _read_history(local)
        assert str(tmp_path) not in page
        assert "export" not in page.lower()
