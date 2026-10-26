"""The `activities` command stages the activity calendar and hands it to the operator publish path."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import openpyxl

from tournament_scheduler.cli import activities_command


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _repo_with_pages_latest(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "--allow-empty", "-m", "initial")
    _git(repo, "checkout", "--orphan", "gh-pages")
    (repo / "latest").mkdir()
    (repo / "latest" / "index.html").write_text("<html>latest</html>", encoding="utf-8")
    _git(repo, "add", "latest")
    _git(repo, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "pages")


def _workbook(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Årshjul"
    ws.append(["Dato", "Aktivitet"])
    ws.append(["2026-01-17", "RS U15"])
    wb.save(path)


def _args(tmp_path: Path, **overrides) -> argparse.Namespace:
    values = {
        "input": str(tmp_path / "Årshjul for aktiviteter.xlsx"),
        "export_dir": str(tmp_path / "export"),
        "repo_dir": str(tmp_path / "repo"),
        "branch": "gh-pages",
        "remote": "origin",
        "year": 2026,
        "base_latest": True,
        "publish": False,
        "push": False,
        "work_dir": str(tmp_path / ".pipeline"),
        "json": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_activities_stages_the_calendar_without_publishing(tmp_path, capsys):
    _repo_with_pages_latest(tmp_path / "repo")
    _workbook(tmp_path / "Årshjul for aktiviteter.xlsx")

    code = activities_command._cmd_activities(_args(tmp_path))

    out = capsys.readouterr().out
    assert code == 0
    assert "Ikke publisert" in out
    assert (tmp_path / "export").exists()


def test_publish_hands_the_staged_export_to_the_operator_publish_path(tmp_path, monkeypatch):
    _repo_with_pages_latest(tmp_path / "repo")
    _workbook(tmp_path / "Årshjul for aktiviteter.xlsx")
    seen: dict[str, object] = {}

    def _record(args):
        seen["operator_command"] = args.operator_command
        seen["export_dir"] = args.export_dir
        seen["run_id"] = args.run_id
        return None  # refuse without publishing; the test only checks the wiring

    monkeypatch.setattr(activities_command, "_execute_operator_publish", _record)

    code = activities_command._cmd_activities(_args(tmp_path, publish=True))

    assert code == 1
    assert seen["operator_command"] == "publish"
    assert Path(str(seen["export_dir"])).is_dir()
    assert str(seen["run_id"]).startswith("activities-")
