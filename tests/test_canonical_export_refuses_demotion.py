"""`season export` must ask Stage 4 to refuse a demotion of canonical tournaments (#823).

The refusal itself is tested at the Stage 4 boundary in
test_placement_normalization_export.py. This test pins the wiring: a canonical
season export passes refuse_placement_demotion=True, so the refusal applies to
the path a publication actually uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_refresh_calendar_evidence import _promote
from tournament_scheduler.cli.rvv_cli import main

SEASON = "2026-2027"


class _Captured(Exception):
    pass


def test_season_export_requests_the_demotion_refusal(tmp_path: Path, monkeypatch) -> None:
    import tournament_scheduler.pipeline.stage4_export as stage4

    root = _promote(tmp_path)
    seen: dict = {}

    def capture(*_args, **kwargs):
        seen.update(kwargs)
        raise _Captured

    monkeypatch.setattr(stage4, "run", capture)

    with pytest.raises(_Captured):
        main(
            [
                "season",
                "export",
                "--season",
                SEASON,
                "--work-dir",
                str(tmp_path / ".pipeline"),
                "--root",
                str(root),
                "--export-dir",
                str(tmp_path / "canonical-export"),
                "--flat",
            ]
        )

    assert seen.get("refuse_placement_demotion") is True
