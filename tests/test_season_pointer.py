"""The season identity survives activity publishes (latest/_season_meta.json).

These run the real shared publisher against a local bare remote. Nothing is mocked.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

from tests.test_activity_publication_scope import _fixture, _publish
from tournament_scheduler.pipeline import pages_publish

SEASON_POINTER = "latest/_season_meta.json"


def _show(origin, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "show", f"refs/heads/gh-pages:{path}"],
        cwd=origin,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def _stage(tmp_path: Path, origin: Path, name: str) -> Path:
    """Stage the activity snapshot from the live gh-pages tree, as the activity command does.

    The snapshot builder copies every live file under latest/ except _meta.json, which the
    shared publisher regenerates. The activity files are rewritten.
    """

    archive = subprocess.run(
        ["git", "archive", "--format=tar", "refs/heads/gh-pages", "latest"],
        cwd=origin,
        capture_output=True,
        check=True,
    ).stdout
    root = tmp_path / name
    root.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(root, filter="data")
    stage = root / "stage"
    shutil.move(root / "latest", stage)
    (stage / "_meta.json").unlink()
    (stage / "activities.json").write_text('{"activities": [{"date": "2026-01-17"}]}', encoding="utf-8")
    (stage / "activities").mkdir(exist_ok=True)
    (stage / "activities" / "index.html").write_text("<html>activities new</html>", encoding="utf-8")
    return stage


def _publish_season(tmp_path, work, stage, run_id: str, fingerprint: str):
    return pages_publish.publish(
        export_dir=str(stage),
        run_id=run_id,
        repo_dir=str(work),
        branch="gh-pages",
        remote="origin",
        push=True,
        bundle_fingerprint=fingerprint,
    )


def test_season_publish_writes_the_season_pointer(tmp_path):
    origin, work = _fixture(tmp_path)
    stage = _stage(tmp_path, origin, "season")

    result = _publish_season(tmp_path, work, stage, "season-1", "fp-season-1")

    assert result.status in ("ok", "warning"), result.summary
    pointer = _show(origin, SEASON_POINTER)
    assert pointer is not None, "a season publish must write latest/_season_meta.json"
    assert json.loads(pointer) == {"run_id": "season-1", "bundle_fingerprint": "fp-season-1"}


def test_activity_publish_keeps_the_season_pointer_and_moves_the_deployment(tmp_path):
    origin, work = _fixture(tmp_path)
    season_stage = _stage(tmp_path, origin, "season")
    assert _publish_season(tmp_path, work, season_stage, "season-1", "fp-season-1").status in ("ok", "warning")
    stage = _stage(tmp_path, origin, "activity")
    pointer_before = _show(origin, SEASON_POINTER)
    assert pointer_before is not None

    result = _publish(tmp_path, work, stage)

    assert result.status in ("ok", "warning"), result.summary
    assert _show(origin, SEASON_POINTER) == pointer_before
    deployment = json.loads(_show(origin, "latest/_meta.json"))
    assert deployment["run_id"] == "activities-test"


def test_season_publish_after_an_activity_publish_replaces_the_season_pointer(tmp_path):
    origin, work = _fixture(tmp_path)
    season_stage = _stage(tmp_path, origin, "season")
    assert _publish_season(tmp_path, work, season_stage, "season-1", "fp-season-1").status in ("ok", "warning")
    activity_stage = _stage(tmp_path, origin, "activity")
    assert _publish(tmp_path, work, activity_stage).status in ("ok", "warning")
    stage = _stage(tmp_path, origin, "season2")

    result = _publish_season(tmp_path, work, stage, "season-2", "fp-season-2")

    assert result.status in ("ok", "warning"), result.summary
    pointer = _show(origin, SEASON_POINTER)
    assert pointer is not None, "a season publish must write latest/_season_meta.json"
    assert json.loads(pointer) == {"run_id": "season-2", "bundle_fingerprint": "fp-season-2"}


def test_season_publish_keeps_run_snapshots_free_of_the_pointer(tmp_path):
    origin, work = _fixture(tmp_path)
    stage = _stage(tmp_path, origin, "season")

    assert _publish_season(tmp_path, work, stage, "season-1", "fp-season-1").status in ("ok", "warning")

    assert _show(origin, "runs/season-1/_season_meta.json") is None
    assert _show(origin, "runs/season-1/_meta.json") is not None


def test_activity_run_snapshot_is_free_of_the_pointer(tmp_path):
    origin, work = _fixture(tmp_path)
    season_stage = _stage(tmp_path, origin, "season")
    assert _publish_season(tmp_path, work, season_stage, "season-1", "fp-season-1").status in ("ok", "warning")
    stage = _stage(tmp_path, origin, "activity")

    assert _publish(tmp_path, work, stage).status in ("ok", "warning")

    assert _show(origin, "runs/activities-test/_season_meta.json") is None


def test_republishing_an_existing_activity_run_is_not_a_snapshot_mismatch(tmp_path):
    """A pointer in the staged bundle must not make an unchanged, already published run look altered."""

    origin, work = _fixture(tmp_path)
    season_stage = _stage(tmp_path, origin, "season")
    assert _publish_season(tmp_path, work, season_stage, "season-1", "fp-season-1").status in ("ok", "warning")
    stage = _stage(tmp_path, origin, "activity")
    assert "_season_meta.json" in {p.name for p in stage.iterdir()}, "the staged bundle must carry the pointer"
    kwargs = {
        "export_dir": str(stage),
        "run_id": "activities-rerun",
        "repo_dir": str(work),
        "branch": "gh-pages",
        "remote": "origin",
        "push": True,
        "bundle_fingerprint": "fp-activity",
        "publication_scope": "activities",
    }
    first = pages_publish.publish(**kwargs)
    assert first.status in ("ok", "warning"), first.summary

    second = pages_publish.publish(**kwargs)

    assert second.status in ("ok", "warning"), second.summary
    assert "annet innhold" not in second.summary


def test_preview_reports_the_season_pointer_for_a_season_publish(tmp_path):
    origin, work = _fixture(tmp_path)
    stage = _stage(tmp_path, origin, "season")
    assert _publish_season(tmp_path, work, stage, "season-1", "fp-season-1").status in ("ok", "warning")
    preview_stage = _stage(tmp_path, origin, "preview")

    plan = pages_publish.diff_latest(
        str(preview_stage), repo_dir=str(work), branch="gh-pages", publication_scope="season"
    )

    assert "latest/_season_meta.json" in plan["update"] + plan["add"]


def test_preview_does_not_report_the_season_pointer_removed_by_an_activity_publish(tmp_path):
    origin, work = _fixture(tmp_path)
    stage = _stage(tmp_path, origin, "season")
    assert _publish_season(tmp_path, work, stage, "season-1", "fp-season-1").status in ("ok", "warning")
    preview_stage = _stage(tmp_path, origin, "preview")

    (preview_stage / "_season_meta.json").unlink()

    plan = pages_publish.diff_latest(
        str(preview_stage), repo_dir=str(work), branch="gh-pages", publication_scope="activities"
    )

    assert "latest/_season_meta.json" not in plan["remove"]
    assert "latest/_season_meta.json" not in plan["update"] + plan["add"]


def test_activity_publish_never_rewinds_the_live_season_pointer(tmp_path):
    """A staged snapshot taken before a season publish must not write its older pointer back."""

    origin, work = _fixture(tmp_path)
    season_stage = _stage(tmp_path, origin, "season")
    assert _publish_season(tmp_path, work, season_stage, "season-1", "fp-season-1").status in ("ok", "warning")
    stale_stage = _stage(tmp_path, origin, "activity")
    (stale_stage / SEASON_POINTER.removeprefix("latest/")).write_text(
        json.dumps({"run_id": "season-0", "bundle_fingerprint": "fp-season-0"}), encoding="utf-8"
    )
    assert _publish_season(tmp_path, work, _stage(tmp_path, origin, "season2"), "season-2", "fp-season-2").status in (
        "ok",
        "warning",
    )

    result = pages_publish.publish(
        export_dir=str(stale_stage),
        run_id="activities-stale",
        repo_dir=str(work),
        branch="gh-pages",
        remote="origin",
        push=True,
        bundle_fingerprint="fp-activity",
        publication_scope="activities",
    )

    assert result.status in ("ok", "warning"), result.summary
    assert json.loads(_show(origin, SEASON_POINTER)) == {"run_id": "season-2", "bundle_fingerprint": "fp-season-2"}
