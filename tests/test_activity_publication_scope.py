"""The activity calendar publishes through the real publish action, without a season export (PR for the activities scope).

These tests run `_execute_publish_pages` against a real bare git remote. Nothing is mocked.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from tournament_scheduler.pipeline.operator_action import _execute_publish_pages

SEASON_FILE = "season_plan.html"
SEASON_BODY = "<html>season plan, reviewed</html>"
ACTIVITIES_JSON_OLD = '{"activities": []}'
ACTIVITIES_HTML_OLD = "<html>activities old</html>"


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=Test", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture(tmp: Path) -> tuple[Path, Path]:
    """A bare origin whose gh-pages branch holds the live site, and a work clone of it."""

    origin = tmp / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    seed = tmp / "seed"
    seed.mkdir()
    _git(seed, "init", "-q")
    _write(seed / "latest" / SEASON_FILE, SEASON_BODY)
    _write(seed / "latest" / "activities.json", ACTIVITIES_JSON_OLD)
    _write(seed / "latest" / "activities" / "index.html", ACTIVITIES_HTML_OLD)
    _git(seed, "checkout", "-q", "--orphan", "gh-pages")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "live site")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "gh-pages")

    work = tmp / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    _git(work, "checkout", "-q", "-B", "main", "origin/gh-pages")
    return origin, work


def _staged_snapshot(tmp: Path, work: Path) -> Path:
    """The staged Pages snapshot: the live site as the base, with the activity files rewritten."""

    stage = tmp / "stage"
    shutil.copytree(work / "latest", stage)
    _write(stage / "activities.json", '{"activities": [{"date": "2026-01-17"}]}')
    _write(stage / "activities" / "index.html", "<html>activities new</html>")
    return stage


def _origin_head(origin: Path) -> str:
    return _git(origin, "rev-parse", "refs/heads/gh-pages")


def _publish(tmp: Path, work: Path, stage: Path, **overrides):
    values = {
        "work_dir": str(tmp / ".pipeline"),
        "run_id": "activities-test",
        "export_dir": str(stage),
        "repo_dir": str(work),
        "branch": "gh-pages",
        "remote": "origin",
        "push": True,
        "confirm_public": True,
        "verify": False,
        "publication_scope": "activities",
    }
    values.update(overrides)
    return _execute_publish_pages(**values)


def test_activity_scope_publishes_only_the_activity_files(tmp_path):
    origin, work = _fixture(tmp_path)
    stage = _staged_snapshot(tmp_path, work)
    before = _origin_head(origin)

    result = _publish(tmp_path, work, stage)

    assert result.status in ("ok", "warning"), result.summary
    assert _origin_head(origin) != before
    live = _git(origin, "show", "refs/heads/gh-pages:latest/activities.json")
    assert "2026-01-17" in live
    assert _git(origin, "show", f"refs/heads/gh-pages:latest/{SEASON_FILE}") == SEASON_BODY


def test_activity_scope_refuses_a_changed_season_file(tmp_path):
    origin, work = _fixture(tmp_path)
    stage = _staged_snapshot(tmp_path, work)
    _write(stage / SEASON_FILE, "<html>season edited by an activity run</html>")
    before = _origin_head(origin)

    result = _publish(tmp_path, work, stage)

    assert result.status == "failed"
    assert SEASON_FILE in result.summary or any(SEASON_FILE in str(p) for p in result.problems)
    assert _origin_head(origin) == before


def test_activity_scope_refuses_a_deleted_season_file(tmp_path):
    origin, work = _fixture(tmp_path)
    stage = _staged_snapshot(tmp_path, work)
    (stage / SEASON_FILE).unlink()
    before = _origin_head(origin)

    result = _publish(tmp_path, work, stage)

    assert result.status == "failed"
    assert _origin_head(origin) == before


def test_activity_scope_refuses_an_unexpected_new_public_file(tmp_path):
    origin, work = _fixture(tmp_path)
    stage = _staged_snapshot(tmp_path, work)
    _write(stage / "extra.html", "<html>not allowed</html>")
    before = _origin_head(origin)

    result = _publish(tmp_path, work, stage)

    assert result.status == "failed"
    assert _origin_head(origin) == before


def test_activity_scope_requires_explicit_public_confirmation(tmp_path):
    origin, work = _fixture(tmp_path)
    stage = _staged_snapshot(tmp_path, work)
    before = _origin_head(origin)

    result = _publish(tmp_path, work, stage, confirm_public=False)

    assert result.status == "blocked"
    assert "--confirm-public" in result.summary
    assert _origin_head(origin) == before


def test_activity_scope_refuses_a_snapshot_changed_after_approval(tmp_path):
    from tournament_scheduler.pipeline.activity_publication import snapshot_digest

    origin, work = _fixture(tmp_path)
    stage = _staged_snapshot(tmp_path, work)
    approved = snapshot_digest(stage)
    _write(stage / "activities.json", '{"activities": [{"date": "2026-02-01"}]}')
    before = _origin_head(origin)

    result = _publish(tmp_path, work, stage, expected_export_content_sha256=approved)

    assert result.status == "failed"
    assert "digest" in result.summary
    assert _origin_head(origin) == before


def test_activity_scope_refuses_private_contact_details_in_the_activity_html(tmp_path):
    origin, work = _fixture(tmp_path)
    stage = _staged_snapshot(tmp_path, work)
    _write(stage / "activities" / "index.html", "<html>kontakt: trener@example.com</html>")
    before = _origin_head(origin)

    result = _publish(tmp_path, work, stage)

    assert result.status == "blocked"
    assert "ikke-offentlig" in result.summary
    assert _origin_head(origin) == before


def test_season_scope_still_requires_a_reviewed_stage4_export(tmp_path):
    """Regression: the season publish keeps its Stage 4 requirement."""

    origin, work = _fixture(tmp_path)
    stage = _staged_snapshot(tmp_path, work)
    before = _origin_head(origin)

    result = _publish(tmp_path, work, stage, publication_scope="season")

    assert result.status == "blocked"
    assert "no reviewed Stage 4 export" in " ".join([result.summary, *map(str, result.problems)])
    assert _origin_head(origin) == before
