"""Regression tests: read-only ``season`` ``--json`` commands emit parseable stdout.

Each handler previously failed before printing: one referenced a non-existent
``args.work_dir`` and another imported a function from a module that no longer
exposed it.
"""

from __future__ import annotations

import json

from tournament_scheduler.application.canonical_season import store_facade
from tournament_scheduler.cli.rvv_cli import main


def _fake_holiday_report(season, root, include_released=False):
    return {
        "season": season,
        "active_count": 1,
        "holiday_date_exceptions": [
            {
                "date": "2026-12-20",
                "status": "active",
                "reason": "Operator confirmed",
                "derived_holiday_policy_reason": "Weekend before: Andre juledag",
            }
        ],
    }


def _fake_infeasibility_report(season, root, include_superseded=False):
    return {
        "canonical_state_revision": "rev-test",
        "current_proof_count": 0,
        "active_proof_count": 0,
        "unproven_obligation_ids": [],
        "proofs": [],
    }


def test_holiday_date_exceptions_json_is_parseable_stdout(monkeypatch, capsys) -> None:
    monkeypatch.setattr(store_facade, "holiday_date_exception_report", _fake_holiday_report)

    assert main(["season", "holiday-date-exceptions", "--season", "2026-2027", "--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["active_count"] == 1
    assert report["holiday_date_exceptions"][0]["date"] == "2026-12-20"


def test_holiday_date_exceptions_text_output_lists_entries(monkeypatch, capsys) -> None:
    monkeypatch.setattr(store_facade, "holiday_date_exception_report", _fake_holiday_report)

    assert main(["season", "holiday-date-exceptions", "--season", "2026-2027"]) == 0

    output = capsys.readouterr().out
    assert "Holiday-date exceptions 2026-2027" in output
    assert "2026-12-20" in output


def test_infeasibility_report_json_is_parseable_stdout(monkeypatch, capsys) -> None:
    monkeypatch.setattr(store_facade, "placement_infeasibility_report", _fake_infeasibility_report)

    assert main(["season", "infeasibility-report", "--season", "2026-2027", "--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["canonical_state_revision"] == "rev-test"
    assert report["proofs"] == []
