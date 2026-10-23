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


def test_season_export_refuses_a_demotion_end_to_end_and_writes_no_export(tmp_path: Path) -> None:
    """The real `season export` refuses a conflicting canonical season (#823).

    The refusal must leave no export file and no change to canonical state. The
    Stage 4 checkpoint is marked failed, which is bookkeeping and not an export.
    """
    import hashlib

    from tests.test_published_season_sealing import _problem, _tournament, _write_canonical

    root = tmp_path / "season"
    tournaments = [_tournament("rvv-1", "2026-10-11", "10:00", "Alpha Arena", "Alpha")]
    tournaments[0]["games"] = [
        {"home": "Alpha1", "away": "G-rvv-1", "parallel_slot": 0, "round_number": 1}
    ]
    problem = _problem({"tournaments": tournaments})
    problem["clubs"] = {"Alpha": "Alpha Arena"}
    problem["club_calendar_status"] = {"Alpha": "known"}
    problem["club_busy_intervals"] = {
        "Alpha": [
            {
                "date": "2026-10-11",
                "start": "10:00",
                "end": "11:20",
                "availability": "fixed_busy",
                "calendar_event": "Serierunde U10",
            }
        ]
    }
    _write_canonical(root, tournaments, problem=problem)

    def digest() -> dict[str, str]:
        return {
            str(file.relative_to(root)): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in sorted(root.rglob("*"))
            if file.is_file()
        }

    before = digest()
    export_dir = tmp_path / "canonical-export"
    rc = main(
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
            str(export_dir),
            "--flat",
        ]
    )

    assert rc != 0
    assert not export_dir.exists() or not any(export_dir.iterdir())
    assert digest() == before
