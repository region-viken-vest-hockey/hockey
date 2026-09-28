from __future__ import annotations

from datetime import datetime

from tournament_scheduler.models import CalendarEvent
from tournament_scheduler.pipeline.scraper_event_helpers import (
    _events_to_dicts,
    _group_events_by_club,
)


def _event(location: str | None) -> CalendarEvent:
    return CalendarEvent(
        date="01.11.2026",
        name="Serierunde U12",
        datetime=datetime(2026, 11, 1, 15, 0),
        duration_hours=1 + 20 / 60,
        location=location,
    )


def test_frisk_asker_source_identity_sets_askerhallen_for_ambiguous_locations():
    rows = _events_to_dicts(
        [
            _event("FA Jentegarderoben - Stavanger 5"),
            _event("1 og 2"),
            _event(None),
            _event("Varner Arena"),
        ],
        club_name="Frisk Asker",
        source_arena="Askerhallen",
    )

    assert [row["arena"] for row in rows] == ["Askerhallen"] * 4
    assert rows[0]["location"] == "FA Jentegarderoben - Stavanger 5"
    assert rows[0]["resource_label_classification"] == "fa_resource_label"
    assert rows[1]["resource_label_classification"] == "numbered_resource_label"
    assert "location" not in rows[2]
    assert rows[3]["resource_label_classification"] == "varner_like_label"


def test_varner_named_source_does_not_become_frisk_asker_evidence():
    rows = _events_to_dicts([_event("Varner Arena")], club_name="Varner Arena")
    assert rows[0].get("arena") is None

    grouped = _group_events_by_club([{"name": "Varner Arena", "events": rows}])
    assert "Frisk Asker" not in grouped


def test_two_frisk_sources_keep_their_configured_source_arena_identity():
    asker_rows = _events_to_dicts(
        [_event("1 og 2")],
        club_name="Frisk Asker",
        source_arena="Askerhallen",
    )
    varner_rows = _events_to_dicts(
        [_event("1 og 2")],
        club_name="Frisk Asker",
        source_arena="Varner Arena",
    )

    grouped = _group_events_by_club(
        [
            {"name": "Frisk Asker", "events": asker_rows},
            {"name": "Frisk Asker Varner", "events": varner_rows},
        ]
    )

    assert [row["arena"] for row in grouped["Frisk Asker"]] == ["Askerhallen", "Varner Arena"]
    assert asker_rows[0]["resource_label_classification"] == "numbered_resource_label"
    assert "resource_label_classification" not in varner_rows[0]
