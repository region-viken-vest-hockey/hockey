"""Shared helpers for operator shell tests. Offline: local git repositories only."""

from __future__ import annotations

from pathlib import Path

from tests.repository_sync_support import INSTALLED, SEASON_REL, commit_all, write_schedule

SEASON_ROOT = Path(SEASON_REL)


def make_diverged(checkout: Path, bare: Path, tmp_path: Path, advance) -> None:
    """Commit locally and remotely so local ``main`` and ``origin/main`` both move."""
    write_schedule(checkout, "local-only")
    commit_all(checkout, "local change")
    advance(bare, tmp_path, "colleague-diverge", "remote-only")


def make_dirty(checkout: Path) -> None:
    schedule = checkout / SEASON_REL / "schedule.json"
    schedule.write_text(schedule.read_text(encoding="utf-8") + " \n", encoding="utf-8")


__all__ = ["INSTALLED", "SEASON_ROOT", "make_dirty", "make_diverged"]
