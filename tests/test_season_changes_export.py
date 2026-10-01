"""Integration coverage for the exported season change-request page (issue #609).

A normal canonical export must write ``season_changes.html`` from the same
read-only ledger behind ``make season-changes-markdown`` and link it from the
season plan, while leaving the schedule artifacts untouched.
"""

from __future__ import annotations

from pathlib import Path
import re

from tournament_scheduler.html import SEASON_CHANGES_FILENAME
from tournament_scheduler.pipeline.stage4_export import run
from tournament_scheduler.pipeline.state import PipelineState


SEASON = "2026-2027"


def _plan() -> dict:
    return {
        "start_date": "2026-10-01",
        "end_date": "2026-11-15",
        "diversity_score": 1.0,
        "pairwise_matchup_score": 1.0,
        "month_balance_score": 1.0,
        "arena_counts": {"Arena A": 1},
        "tournaments": [
            {
                "id": "rvv-001",
                "date": "2026-10-05",
                "arena": "Arena A",
                "age_group": "U10",
                "host_club": "Frisk Asker",
                "start_time": "09:00",
                "teams": [
                    {"club": "Frisk Asker", "label": "FA1", "age_group": "U10"},
                    {"club": "Skien", "label": "S1", "age_group": "U10"},
                ],
                "games": [
                    {"home": "FA1", "away": "S1", "parallel_slot": 0, "round_number": 1}
                ],
            }
        ],
    }


def _ledger() -> dict:
    return {
        "schema_version": 1,
        "season": SEASON,
        "revision": "rev",
        "canonical_state_revision": "rev",
        "request_count": 1,
        "unresolved_count": 1,
        "requests": [
            {
                "bucket_id": "club-feedback:frisk:u10",
                "request_id": "club-feedback:frisk:u10",
                "created_at": "2026-09-01T10:00:00+00:00",
                "sources": ["Frisk Asker"],
                "actors": ["operator"],
                "notes": ["U10 unavailable"],
                "types": ["team_unavailable"],
                "affected_tournaments": ["rvv-001"],
                "affected_teams": ["Frisk Asker:FA1:U10"],
                "constraints": [],
                "mutations": [],
                "protections": [],
                "withdrawals": [],
                "status": "needs_action",
                "result_summary": "team_unavailable",
                "current_state": {},
            }
        ],
    }


def _patch_canonical_projection(monkeypatch, tmp_path: Path) -> None:
    import tournament_scheduler.canonical_baseline as canonical_baseline
    import tournament_scheduler.season_state as season_state

    monkeypatch.setattr(
        canonical_baseline,
        "resolve_canonical_state",
        lambda _config, _start, _end: {"season": SEASON, "root": str(tmp_path / "season")},
    )
    monkeypatch.setattr(season_state, "approval_report", lambda *_a, **_k: {"tournaments": []})
    monkeypatch.setattr(
        season_state,
        "booking_status_report",
        lambda *_a, **_k: {"canonical_state_revision": "rev", "tournaments": []},
    )
    monkeypatch.setattr(
        season_state,
        "calendar_booking_assessment",
        lambda *_a, **_k: {"canonical_state_revision": "rev", "tournaments": []},
    )


def _run_export(tmp_path: Path):
    plan = _plan()
    verification_problem = {
        "start_date": "2026-10-01",
        "end_date": "2026-11-15",
        "round_length_minutes": {"U10": 15},
        "ice_time_minutes": {"U10": 120},
    }
    effective_config = {
        **verification_problem,
        "canonical_season": SEASON,
        "canonical_season_root": str(tmp_path / "season"),
    }
    checkpoint = {"plan": plan, "canonical_state": {"season": SEASON, "revision": "rev"}}
    return run(
        checkpoint,
        PipelineState(tmp_path / "pipeline"),
        export_dir=tmp_path / "export",
        strict=True,
        timestamped_export=False,
        verification_problem=verification_problem,
        effective_config_override=effective_config,
        use_pipeline_metadata=False,
        public_export_context={},
        allow_placement_normalization=False,
        canonical_schedule_plan=plan,
    )


def test_export_writes_change_page_and_links_it_from_the_season_plan(tmp_path, monkeypatch):
    _patch_canonical_projection(monkeypatch, tmp_path)
    import tournament_scheduler.season_state as season_state

    monkeypatch.setattr(season_state, "change_request_ledger", lambda *_a, **_k: _ledger())

    result = _run_export(tmp_path)

    changes_path = Path(result["output_files"]["changes_html"])
    assert changes_path.name == SEASON_CHANGES_FILENAME
    page = changes_path.read_text(encoding="utf-8")
    assert "Forespurte endringer" in page
    assert "club-feedback:frisk:u10" in page
    assert "Må følges opp" in page

    season_html = Path(result["output_files"]["html"]).read_text(encoding="utf-8")
    assert f'href="{SEASON_CHANGES_FILENAME}"' in season_html
    assert "Forespurte endringer" in season_html
    # The change page is presentation-only: schedule payload is unchanged.
    assert re.search(r"const TOURNAMENTS = (.*?);\nconst TEAM_GAME_COUNTS", season_html, re.S)


def test_export_omits_change_page_and_nav_when_ledger_is_empty(tmp_path, monkeypatch):
    _patch_canonical_projection(monkeypatch, tmp_path)
    import tournament_scheduler.season_state as season_state

    monkeypatch.setattr(
        season_state,
        "change_request_ledger",
        lambda *_a, **_k: {
            **_ledger(),
            "request_count": 0,
            "unresolved_count": 0,
            "requests": [],
        },
    )

    result = _run_export(tmp_path)

    assert "changes_html" not in result["output_files"]
    assert not (Path(result["export_dir"]) / SEASON_CHANGES_FILENAME).exists()
    season_html = Path(result["output_files"]["html"]).read_text(encoding="utf-8")
    assert SEASON_CHANGES_FILENAME not in season_html
