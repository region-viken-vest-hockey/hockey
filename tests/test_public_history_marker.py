"""A changed tournament gets a marker on the season plan and links to its history (#869 item 3)."""

from __future__ import annotations

import json
from pathlib import Path

from tournament_scheduler.html.html_exporter import HtmlExporter
from tournament_scheduler.serialization.season_plan import season_plan_from_dict
from tournament_scheduler.publication.public_history import changed_tournament_ids
from tests.test_guest_slots import _spare_capacity_case

TEMPLATE = Path(__file__).resolve().parents[1] / "tournament_scheduler" / "html" / "templates" / "script_schedule.js"
TOURNAMENT_ID = "ju12-a-20261010"


def test_changed_tournament_ids_lists_only_tournaments_with_history_entries():
    history = {"tournaments": {"t1": [{"event": "changed", "changes": [{"field": "arena"}]}], "t2": []}, "labels": {}}

    assert changed_tournament_ids(history) == frozenset({"t1"})


def test_changed_tournament_ids_is_empty_when_history_is_unavailable():
    assert changed_tournament_ids(None) == frozenset()


def test_plan_flags_a_changed_tournament_and_leaves_others_unmarked(tmp_path):
    from tournament_scheduler.application.canonical_season.store_facade import reserve_guest_slot

    _work_dir, root, problem = _spare_capacity_case(tmp_path)
    schedule = reserve_guest_slot(season="2026-2027", tournament_id=TOURNAMENT_ID, root=root, problem=problem)
    plan = season_plan_from_dict(schedule["plan"])

    unmarked = json.loads(HtmlExporter._plan_to_json(plan))
    marked = json.loads(HtmlExporter._plan_to_json(plan, public_history_ids=frozenset({TOURNAMENT_ID})))

    assert all("ph" not in entry for entry in unmarked)
    flagged = [entry for entry in marked if entry.get("id") == TOURNAMENT_ID]
    assert flagged and flagged[0]["ph"] is True
    assert [entry for entry in marked if entry.get("id") != TOURNAMENT_ID and "ph" in entry] == []


def test_season_plan_card_renders_the_marker_with_a_link_to_the_history_page():
    script = TEMPLATE.read_text(encoding="utf-8")

    assert "tag--changed" in script
    assert "public_history.html#t-" in script
    assert "Endret etter publisering" in script
