"""Tests for registered-team Pages staging and CLI/Make entrypoints."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tournament_scheduler.pipeline.registered_teams import (
    default_registered_teams_run_id,
    prepare_registered_teams_latest_export,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write_csv(path: Path) -> Path:
    path.write_text("club,label,age_group,email\nJar,Jar 1,U10,person@example.com\n", encoding="utf-8")
    return path


def _repo_with_pages_latest(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "--allow-empty", "-m", "initial")
    main_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    _git(repo, "checkout", "--orphan", "gh-pages")
    for child in repo.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            import shutil

            shutil.rmtree(child)
        else:
            child.unlink()
    latest = repo / "latest"
    latest.mkdir()
    (latest / "season_plan.html").write_text("<h1>Current plan</h1>", encoding="utf-8")
    (latest / "activities.json").write_text('{"activities": []}', encoding="utf-8")
    (latest / "_meta.json").write_text('{"old": true}', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "pages")
    _git(repo, "checkout", main_branch)


class TestRegisteredTeamsPublish:
    def test_prepare_registered_teams_latest_export_overlays_current_latest_snapshot(self, tmp_path):
        repo = tmp_path / "repo"
        _repo_with_pages_latest(repo)
        export_dir = tmp_path / "staged"

        result = prepare_registered_teams_latest_export(
            csv_path=_write_csv(tmp_path / "teams.csv"),
            export_dir=export_dir,
            repo_dir=str(repo),
            branch="gh-pages",
            generated_at="2026-07-30T12:00:00Z",
        )

        assert result["base_file_count"] == 2
        assert (export_dir / "season_plan.html").read_text(encoding="utf-8") == "<h1>Current plan</h1>"
        assert (export_dir / "activities.json").exists()
        assert not (export_dir / "_meta.json").exists()
        assert (export_dir / "registered-teams" / "pameldte-lag.html").exists()
        assert (export_dir / "registered-teams" / "pameldte-lag.json").exists()
        assert (export_dir / "registered-teams" / "validation-report.json").exists()
        public_payload = json.loads((export_dir / "registered-teams" / "pameldte-lag.json").read_text())
        assert public_payload["total_teams"] == 1

    def test_default_run_id_is_namespaced_for_idempotent_pages_runs(self):
        assert default_registered_teams_run_id().startswith("registered-teams-")

    def test_cli_generates_without_base_latest_for_local_preview(self, tmp_path):
        csv_path = _write_csv(tmp_path / "teams.csv")
        export_dir = tmp_path / "review"

        proc = subprocess.run(
            [
                "python3",
                "-m",
                "tournament_scheduler.cli.rvv_cli",
                "registered-teams",
                "--csv",
                str(csv_path),
                "--export-dir",
                str(export_dir),
                "--generated-at",
                "2026-07-30T12:00:00Z",
                "--no-base-latest",
            ],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert (export_dir / "registered-teams" / "pameldte-lag.html").exists()
        assert "Ikke publisert" in proc.stdout

    def test_make_help_lists_registered_team_targets(self):
        proc = subprocess.run(["make", "help"], capture_output=True, text=True, timeout=30)

        assert proc.returncode == 0
        assert "make registered-teams CSV=" in proc.stdout
        assert "make registered-teams-publish CSV=" in proc.stdout

    def test_operator_docs_include_standalone_registered_team_url(self):
        docs_text = Path("docs/rvv-miniputt-pipeline.md").read_text(encoding="utf-8")
        readme_text = Path("README.md").read_text(encoding="utf-8")
        public_url = "https://region-viken-vest-hockey.github.io/hockey/latest/registered-teams/pameldte-lag.html"

        assert "make registered-teams CSV=" in docs_text
        assert "make registered-teams-publish CSV=" in docs_text
        assert public_url in docs_text
        assert public_url in readme_text
