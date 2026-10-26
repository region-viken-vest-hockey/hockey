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
    _write(seed / "latest" / "_meta.json", '{"run_id": "season-run", "bundle_fingerprint": "season"}')
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
    # The real snapshot builder leaves latest/_meta.json out: the shared publisher owns and regenerates it.
    (stage / "_meta.json").unlink()
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


GITHUB_URL = "https://github.com/region-viken-vest-hockey/hockey.git"
SITE_BASE = "https://region-viken-vest-hockey.github.io/hockey/"
# Paths the shared publisher is allowed to write, beyond the activity files themselves:
# its own metadata, the immutable run snapshot, and its generated index and history pages.
PUBLISHER_OWNED = ("latest/_meta.json", "index.html", "latest/index.html", "history/index.html", ".nojekyll")


def _changed_paths(origin: Path, before: str, after: str) -> set[str]:
    out = _git(origin, "diff-tree", "-r", "--name-only", before, after)
    return {line for line in out.splitlines() if line}


def _as_github_remote(origin: Path, work: Path) -> None:
    """Make the fixture look like the real GitHub remote, so the publish produces served URLs.

    `insteadOf` sends the push and fetch to the local bare origin, so nothing leaves the machine.
    """

    _git(work, "remote", "set-url", "origin", GITHUB_URL)
    _git(work, "config", f"url.{origin}.insteadOf", GITHUB_URL)


def _served_from(origin: Path):
    """A fetch that serves what is committed on gh-pages, exactly as GitHub Pages would."""

    from tournament_scheduler.pipeline.pages_verify import FetchResponse

    def fetch(url: str) -> FetchResponse:
        if not url.startswith(SITE_BASE):
            return FetchResponse(status_code=404, text="", error=None)
        rel = url[len(SITE_BASE):]
        if rel == "" or rel.endswith("/"):
            rel += "index.html"
        try:
            text = _git(origin, "show", f"refs/heads/gh-pages:{rel}")
        except subprocess.CalledProcessError:
            return FetchResponse(status_code=404, text="", error=None)
        return FetchResponse(status_code=200, text=text, error=None)

    return fetch


def test_activity_publish_changes_only_the_allowed_paths(tmp_path):
    origin, work = _fixture(tmp_path)
    stage = _staged_snapshot(tmp_path, work)
    before = _origin_head(origin)

    result = _publish(tmp_path, work, stage)

    assert result.status in ("ok", "warning"), result.summary
    changed = _changed_paths(origin, before, _origin_head(origin))
    allowed_prefixes = ("latest/activities.json", "latest/activities/", "runs/activities-test/")
    allowed = set(PUBLISHER_OWNED) | {"latest/activities.json", "latest/activities/index.html"}
    unexpected = {
        path for path in changed if path not in allowed and not path.startswith(allowed_prefixes)
    }
    assert unexpected == set(), f"publish changed paths outside the allowed set: {sorted(unexpected)}"
    assert "latest/activities.json" in changed
    assert f"latest/{SEASON_FILE}" not in changed


def test_activity_publish_is_verified_against_the_served_site(tmp_path):
    origin, work = _fixture(tmp_path)
    _as_github_remote(origin, work)
    stage = _staged_snapshot(tmp_path, work)

    result = _publish(
        tmp_path,
        work,
        stage,
        verify=True,
        fetch=_served_from(origin),
        sleep=lambda seconds: None,
        verify_max_attempts=1,
    )

    assert result.status == "ok", result.summary
    assert "verify_status=ok" in result.evidence


def test_activity_publish_without_a_public_url_is_not_reported_verified(tmp_path):
    origin, work = _fixture(tmp_path)
    stage = _staged_snapshot(tmp_path, work)

    result = _publish(tmp_path, work, stage, verify=True, verify_max_attempts=1, sleep=lambda seconds: None)

    assert result.status == "warning"
    assert "verif" in result.summary.lower()


def test_shared_publisher_refuses_when_the_base_moved_after_it_was_read(tmp_path):
    """The shared publisher must not overwrite a base that changed after the integrity check."""

    from tournament_scheduler.pipeline import pages_publish

    origin, work = _fixture(tmp_path)
    stage = _staged_snapshot(tmp_path, work)
    base_read = _origin_head(origin)

    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    _git(other, "checkout", "-q", "-B", "gh-pages", "origin/gh-pages")
    _write(other / "latest" / SEASON_FILE, "<html>season published by someone else</html>")
    _git(other, "commit", "-q", "-am", "season update")
    _git(other, "push", "-q", "origin", "gh-pages")
    moved = _origin_head(origin)

    result = pages_publish.publish(
        export_dir=str(stage),
        run_id="activities-race",
        repo_dir=str(work),
        branch="gh-pages",
        remote="origin",
        push=True,
        bundle_fingerprint="race",
        expected_base_commit=base_read,
    )

    assert result.status == "failed"
    assert _origin_head(origin) == moved


def test_activity_publish_keeps_season_content_and_regenerates_publisher_metadata(tmp_path):
    origin, work = _fixture(tmp_path)
    stage = _staged_snapshot(tmp_path, work)
    before_meta = _git(origin, "show", "refs/heads/gh-pages:latest/_meta.json")

    result = _publish(tmp_path, work, stage)

    assert result.status in ("ok", "warning"), result.summary
    after_meta = _git(origin, "show", "refs/heads/gh-pages:latest/_meta.json")
    assert after_meta != before_meta
    assert "activities-test" in after_meta
    assert _git(origin, "show", f"refs/heads/gh-pages:latest/{SEASON_FILE}") == SEASON_BODY
