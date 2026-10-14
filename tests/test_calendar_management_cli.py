"""CLI regressions for canonical-season calendar management commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from tournament_scheduler.application.canonical_season import store_facade
from tournament_scheduler.cli.rvv_cli import main


@pytest.mark.parametrize(
    ("work_dir_args", "expected_work_dir"),
    [
        ([], ".pipeline"),
        (["--work-dir", "custom-work"], "custom-work"),
    ],
)
def test_refresh_calendars_dispatches_concrete_work_dir_without_canonical_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    work_dir_args: list[str],
    expected_work_dir: str,
) -> None:
    dispatched: dict[str, object] = {}

    def fake_refresh_calendars(**kwargs: object) -> dict[str, object]:
        dispatched.update(kwargs)
        return {"dry_run": True}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(store_facade, "refresh_calendars", fake_refresh_calendars)

    assert (
        main(
            [
                "season",
                "refresh-calendars",
                "--season",
                "2026-2027",
                "--dry-run",
                "--json",
                *work_dir_args,
            ]
        )
        == 0
    )
    assert dispatched["work_dir"] == expected_work_dir
    assert dispatched["dry_run"] is True
    assert not (tmp_path / "season").exists()
    assert not (tmp_path / "export").exists()
