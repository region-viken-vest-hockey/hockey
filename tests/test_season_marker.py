"""The season identity is shown in history, moves only with season rollbacks, and is never guessed for legacy runs.

These run the real shared publisher against a local bare remote. Nothing is mocked.
"""

from __future__ import annotations

import json
import re
import subprocess

from tests.test_season_pointer import SEASON_POINTER, _fixture, _publish, _publish_season, _show, _stage
from tournament_scheduler.pipeline import pages_publish

HISTORY = "history/index.html"


def _season_marked(origin) -> list[str]:
    page = _show(origin, HISTORY) or ""
    return re.findall(r'data-run-id="([^"]+)"[^>]*data-season-current="true"', page)


def _current(origin) -> list[str]:
    page = _show(origin, HISTORY) or ""
    return re.findall(r'data-run-id="([^"]+)"[^>]*data-current="true"', page)


def _add_legacy_run(tmp_path, origin, run_id: str) -> None:
    """Push a publication that predates publication scopes: subject 'Publish run <id>', no pointer."""
    legacy = tmp_path / "legacy"
    subprocess.run(["git", "clone", "-q", "--branch", "gh-pages", str(origin), str(legacy)], check=True)
    (legacy / "runs" / run_id).mkdir(parents=True)
    (legacy / "runs" / run_id / "index.html").write_text("legacy", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=legacy, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=Test", "commit", "-q", "-m", f"Publish run {run_id}"],
        cwd=legacy,
        check=True,
    )
    subprocess.run(["git", "push", "-q", "origin", "gh-pages"], cwd=legacy, check=True)


def test_the_season_marker_stays_on_the_season_after_an_activity_publish(tmp_path):
    origin, work = _fixture(tmp_path)
    assert _publish_season(tmp_path, work, _stage(tmp_path, origin, "season"), "season-1", "fp-season-1").status in (
        "ok",
        "warning",
    )
    assert _season_marked(origin) == ["season-1"]

    assert _publish(tmp_path, work, _stage(tmp_path, origin, "activity")).status in ("ok", "warning")

    assert _season_marked(origin) == ["season-1"]
    assert _current(origin) == ["activities-test"]


def test_an_activity_rollback_keeps_the_season_marker(tmp_path):
    origin, work = _fixture(tmp_path)
    assert _publish_season(tmp_path, work, _stage(tmp_path, origin, "season"), "season-1", "fp-season-1").status in (
        "ok",
        "warning",
    )
    assert _publish(tmp_path, work, _stage(tmp_path, origin, "activity")).status in ("ok", "warning")

    result = pages_publish.rollback_to_run(
        run_id="activities-test", repo_dir=str(work), branch="gh-pages", remote="origin", push=True
    )

    assert result.status in ("ok", "warning"), result.summary
    assert _season_marked(origin) == ["season-1"]


def test_a_season_rollback_moves_the_season_pointer_and_marker(tmp_path):
    origin, work = _fixture(tmp_path)
    assert _publish_season(tmp_path, work, _stage(tmp_path, origin, "s1"), "season-1", "fp-season-1").status in (
        "ok",
        "warning",
    )
    assert _publish_season(tmp_path, work, _stage(tmp_path, origin, "s2"), "season-2", "fp-season-2").status in (
        "ok",
        "warning",
    )

    result = pages_publish.rollback_to_run(
        run_id="season-1", repo_dir=str(work), branch="gh-pages", remote="origin", push=True
    )

    assert result.status in ("ok", "warning"), result.summary
    assert json.loads(_show(origin, SEASON_POINTER)) == {"run_id": "season-1", "bundle_fingerprint": "fp-season-1"}
    assert _season_marked(origin) == ["season-1"]


def test_a_rollback_to_a_legacy_run_is_refused_and_changes_nothing(tmp_path):
    origin, work = _fixture(tmp_path)
    assert _publish_season(tmp_path, work, _stage(tmp_path, origin, "s1"), "season-1", "fp-season-1").status in (
        "ok",
        "warning",
    )
    _add_legacy_run(tmp_path, origin, "legacy-1")
    before_pointer = _show(origin, SEASON_POINTER)
    before_head = subprocess.run(
        ["git", "rev-parse", "refs/heads/gh-pages"], cwd=origin, capture_output=True, text=True, check=True
    ).stdout

    result = pages_publish.rollback_to_run(
        run_id="legacy-1", repo_dir=str(work), branch="gh-pages", remote="origin", push=True
    )

    assert result.status == "failed", result.summary
    assert _show(origin, SEASON_POINTER) == before_pointer
    assert subprocess.run(
        ["git", "rev-parse", "refs/heads/gh-pages"], cwd=origin, capture_output=True, text=True, check=True
    ).stdout == before_head


def test_history_reports_a_legacy_entry_as_unclassified_instead_of_guessing_its_season(tmp_path):
    origin, work = _fixture(tmp_path)
    _add_legacy_run(tmp_path, origin, "legacy-1")
    assert _publish_season(tmp_path, work, _stage(tmp_path, origin, "s1"), "season-1", "fp-season-1").status in (
        "ok",
        "warning",
    )

    page = _show(origin, HISTORY) or ""
    legacy_row = re.search(r'<tr data-run-id="legacy-1"[^>]*>.*?</tr>', page).group(0)

    assert 'data-scope=""' in legacy_row
    assert "data-season-current" not in legacy_row
    assert "Uklassifisert" in legacy_row


def test_a_run_id_published_under_another_scope_is_refused(tmp_path):
    origin, work = _fixture(tmp_path)
    season_stage = _stage(tmp_path, origin, "season")
    assert _publish_season(tmp_path, work, season_stage, "season-1", "fp-season-1").status in ("ok", "warning")
    activity_stage = _stage(tmp_path, origin, "activity")

    result = pages_publish.publish(
        export_dir=str(activity_stage),
        run_id="season-1",
        repo_dir=str(work),
        branch="gh-pages",
        remote="origin",
        push=True,
        publication_scope="activities",
    )

    assert result.status == "failed", result.summary
    assert "season-1" in _show(origin, "history/index.html")
    assert _season_marked(origin) == ["season-1"]


def test_the_same_bundle_cannot_be_published_under_a_second_scope(tmp_path):
    origin, work = _fixture(tmp_path)
    stage = _stage(tmp_path, origin, "bundle")
    first = pages_publish.publish(
        export_dir=str(stage), run_id="run-1", repo_dir=str(work), branch="gh-pages", remote="origin",
        push=True, bundle_fingerprint="fp-1", publication_scope="activities",
    )
    assert first.status in ("ok", "warning"), first.summary
    head_before = subprocess.run(
        ["git", "rev-parse", "refs/heads/gh-pages"], cwd=origin, capture_output=True, text=True, check=True
    ).stdout

    second = pages_publish.publish(
        export_dir=str(stage), run_id="run-1", repo_dir=str(work), branch="gh-pages", remote="origin",
        push=True, bundle_fingerprint="fp-1", publication_scope="season",
    )

    assert second.status == "failed", second.summary
    assert _show(origin, SEASON_POINTER) is None, "a refused publish must not write the season pointer"
    assert subprocess.run(
        ["git", "rev-parse", "refs/heads/gh-pages"], cwd=origin, capture_output=True, text=True, check=True
    ).stdout == head_before
