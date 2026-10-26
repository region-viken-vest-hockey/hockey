"""The `activities` command stages the activity calendar and publishes it through the real publish action."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from tournament_scheduler.cli import activities_command

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_ACTIVITY_JSON = REPO_ROOT / "inputs" / "activities" / "activities.json"
SEASON_FILE = "season_plan.html"
SEASON_BODY = "<html>season plan, reviewed</html>"


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


def _origin_with_live_site(tmp: Path) -> tuple[Path, Path]:
    """A bare origin whose gh-pages holds the season file and an old activity calendar."""

    origin = tmp / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    seed = tmp / "seed"
    seed.mkdir()
    _git(seed, "init", "-q")
    _write(seed / "latest" / SEASON_FILE, SEASON_BODY)
    _write(seed / "latest" / "activities.json", '{"activities": []}')
    _write(seed / "latest" / "activities" / "index.html", "<html>activities old</html>")
    _git(seed, "checkout", "-q", "--orphan", "gh-pages")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "live site")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "gh-pages")

    work = tmp / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    _git(work, "checkout", "-q", "-B", "main", "origin/gh-pages")
    return origin, work


def _args(tmp_path: Path, work: Path, **overrides) -> argparse.Namespace:
    input_copy = tmp_path / "activities.json"
    shutil.copyfile(REAL_ACTIVITY_JSON, input_copy)
    values = {
        "input": str(input_copy),
        "export_dir": str(tmp_path / "export"),
        "repo_dir": str(work),
        "branch": "gh-pages",
        "remote": "origin",
        "year": 2026,
        "base_latest": True,
        "publish": False,
        "push": True,
        "confirm_public": False,
        "dry_run": False,
        "verify": False,
        "work_dir": str(tmp_path / ".pipeline"),
        "json": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_activities_stages_the_calendar_without_publishing(tmp_path, capsys):
    origin, work = _origin_with_live_site(tmp_path)
    before = _git(origin, "rev-parse", "refs/heads/gh-pages")

    code = activities_command._cmd_activities(_args(tmp_path, work))

    assert code == 0
    assert "Ikke publisert" in capsys.readouterr().out
    assert (tmp_path / "export" / "activities" / "index.html").is_file()
    assert _git(origin, "rev-parse", "refs/heads/gh-pages") == before


def test_activities_publish_updates_only_the_activity_files_on_the_real_remote(tmp_path):
    origin, work = _origin_with_live_site(tmp_path)
    before = _git(origin, "rev-parse", "refs/heads/gh-pages")

    code = activities_command._cmd_activities(_args(tmp_path, work, publish=True, confirm_public=True))

    assert code == 0, "the activity publish must succeed through the real action"
    assert _git(origin, "rev-parse", "refs/heads/gh-pages") != before
    assert _git(origin, "show", f"refs/heads/gh-pages:latest/{SEASON_FILE}") == SEASON_BODY
    assert "activities old" not in _git(origin, "show", "refs/heads/gh-pages:latest/activities/index.html")
