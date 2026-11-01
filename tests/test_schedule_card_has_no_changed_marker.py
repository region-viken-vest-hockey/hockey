"""The season plan card carries no "changed after publication" tag; history lives on its own page."""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_guest_slots import _spare_capacity_case
from tournament_scheduler.html.html_exporter import HtmlExporter
from tournament_scheduler.application.canonical_season.store_facade import reserve_guest_slot
from tournament_scheduler.serialization.season_plan import season_plan_from_dict

TEMPLATE = Path(__file__).resolve().parents[1] / "tournament_scheduler" / "html" / "templates" / "script_schedule.js"


def test_schedule_script_does_not_render_a_changed_after_publication_tag():
    script = TEMPLATE.read_text(encoding="utf-8")

    assert "Endret etter publisering" not in script
    assert "tag--changed" not in script


def test_plan_json_carries_no_public_history_flag(tmp_path):
    _work_dir, root, problem = _spare_capacity_case(tmp_path)
    schedule = reserve_guest_slot(
        season="2026-2027", tournament_id="ju12-a-20261010", root=root, problem=problem
    )
    plan = season_plan_from_dict(schedule["plan"])

    entries = json.loads(HtmlExporter._plan_to_json(plan))

    assert entries and all("ph" not in entry for entry in entries)
