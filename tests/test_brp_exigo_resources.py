"""BRP/Exigo parser keeps every resource a booking occupies.

The live payload writes a booking that spans both rinks as one row per
resource. Those rows share ``orderId`` and differ only in ``resourceId``.
"""

from __future__ import annotations

import json
from datetime import datetime

from tournament_scheduler.models_calendar import SourceResource
from tournament_scheduler.pipeline.scraper_brp_exigo import _parse_brp_exigo_day


def _page(resources, events):
    payload = json.dumps(
        {"resources": resources, "events": events, "date": "2026-09-05"}, separators=(",", ":")
    )
    # Mirror the escaped React flight payload the scraper matches on.
    escaped = payload.replace('"', '\\"')
    return f'<script>self.__next_f.push([1,"f:[\\"$\\",{escaped}]"])</script>'


def _item(event_id, order_id, resource_id, title, *, time_range="10:00-12:00"):
    return {
        "id": f"{order_id}-{resource_id}-{event_id}",
        "title": title,
        "resourceId": resource_id,
        "orderId": order_id,
        "timeRange": time_range,
        "message": "",
        "resources": [],
    }


_BANES = [
    {"id": 362, "name": "Ishockey - bane 1"},
    {"id": 363, "name": "Ishockey - bane 2"},
]


def test_booking_spanning_both_resources_is_one_event_with_all_labels():
    page = _page(
        _BANES,
        [
            _item(1, 1192286, 362, "Skien Ishockeyklubb"),
            _item(1, 1192286, 363, "Skien Ishockeyklubb"),
        ],
    )

    events = _parse_brp_exigo_day(page, datetime(2026, 9, 5))

    assert len(events) == 1
    assert events[0].location == "Ishockey - bane 1 + Ishockey - bane 2"


def test_single_resource_booking_keeps_its_primary_label():
    page = _page(_BANES, [_item(1, 1, 363, "Hockeyskole")])

    events = _parse_brp_exigo_day(page, datetime(2026, 9, 5))

    assert events[0].location == "Ishockey - bane 2"


def test_same_titled_bookings_from_different_orders_stay_separate():
    page = _page(
        _BANES,
        [
            _item(1, 100, 362, "Hockeyskole"),
            _item(2, 200, 363, "Hockeyskole"),
        ],
    )

    events = _parse_brp_exigo_day(page, datetime(2026, 9, 5))

    assert sorted(event.location for event in events) == ["Ishockey - bane 1", "Ishockey - bane 2"]


def test_rows_without_order_id_are_not_merged_by_title():
    page = _page(
        _BANES,
        [
            {"id": "a", "title": "Hockeyskole", "resourceId": 362, "timeRange": "10:00-12:00", "message": ""},
            {"id": "b", "title": "Hockeyskole", "resourceId": 363, "timeRange": "10:00-12:00", "message": ""},
        ],
    )

    events = _parse_brp_exigo_day(page, datetime(2026, 9, 5))

    assert len(events) == 2


def test_spanning_booking_keeps_order_identity_and_resource_ids():
    page = _page(
        _BANES,
        [
            _item(1, 1192286, 362, "Skien Ishockeyklubb"),
            _item(1, 1192286, 363, "Skien Ishockeyklubb"),
        ],
    )

    event = _parse_brp_exigo_day(page, datetime(2026, 9, 5))[0]

    assert event.order_id == "1192286"
    assert event.resources == (
        SourceResource(id="362", label="Ishockey - bane 1"),
        SourceResource(id="363", label="Ishockey - bane 2"),
    )
    assert event.location == "Ishockey - bane 1 + Ishockey - bane 2"


def test_single_resource_booking_has_one_structured_resource():
    page = _page(_BANES, [_item(1, 1, 363, "Hockeyskole")])

    event = _parse_brp_exigo_day(page, datetime(2026, 9, 5))[0]

    assert event.resources == (SourceResource(id="363", label="Ishockey - bane 2"),)
    assert event.order_id == "1"


def test_calendar_cache_roundtrip_keeps_structured_resources(tmp_path):
    from tournament_scheduler.utils.calendar_cache import CalendarCache

    page = _page(
        _BANES,
        [
            _item(1, 1192286, 362, "Skien Ishockeyklubb"),
            _item(1, 1192286, 363, "Skien Ishockeyklubb"),
        ],
    )
    events = _parse_brp_exigo_day(page, datetime(2026, 9, 5))
    cache = CalendarCache(cache_dir=str(tmp_path))
    start = end = datetime(2026, 9, 5)

    cache.set("https://example.test/ishallen", "Skien", start, end, events)
    cached = cache.get("https://example.test/ishallen", "Skien", start, end)

    assert cached[0].resources == events[0].resources
    assert cached[0].order_id == "1192286"
