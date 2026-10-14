"""Regression test for #704: BRP event message survives full evidence path."""

from datetime import datetime
from unittest.mock import MagicMock

from tournament_scheduler.pipeline.scraper_brp_exigo import _parse_brp_exigo_day_strict
from tournament_scheduler.pipeline.stage3_helpers import _build_club_busy_intervals
from tournament_scheduler.booking.calendar_bookings import iter_events
from tournament_scheduler.booking.interval_facts import event_fingerprint
from tournament_scheduler.utils.calendar_cache import CalendarCache


def _page(resources, events):
    import json
    payload = json.dumps(
        {"resources": resources, "events": events, "date": "2027-01-24"}, separators=(",", ":")
    )
    escaped = payload.replace('"', '\\"')
    return f'<script>self.__next_f.push([1,"f:[\\"$\\",{escaped}]"])</script>'


def _item(event_id, order_id, resource_id, title, *, time_range="11:15-13:35", message=""):
    return {
        "id": f"{order_id}-{resource_id}-{event_id}",
        "title": title,
        "resourceId": resource_id,
        "orderId": order_id,
        "timeRange": time_range,
        "message": message,
        "resources": [],
    }


_BANES = [
    {"id": 362, "name": "Ishockey - bane 1"},
    {"id": 363, "name": "Ishockey - bane 2"},
]


def test_brp_event_message_survives_full_evidence_path(tmp_path):
    """Regression for #704: BRP title+message survives scrape, cache, Stage 3, and assessment."""
    # BRP event with a descriptive message (like JU10: Skien, Jar Blå, ...)
    page = _page(
        _BANES,
        [
            _item(
                1,
                1194498,
                362,
                "Skien Ishockeyklubb",
                time_range="11:15-13:35",
                message="",  # Empty in initial page load
            ),
            _item(
                1,
                1194498,
                363,
                "Skien Ishockeyklubb",
                time_range="11:15-13:35",
                message="",  # Empty in initial page load
            ),
        ],
    )

    # Mock the server action response
    mock_session = MagicMock()
    mock_response = MagicMock()
    # Next.js flight response format: 0:... 1:[["message1"],["message2"]]
    mock_response.text = '0:{"a":"$@1","f":"","b":"kNYIBfnUcAko1ZiTgS_-W"}\n1:[["JU10: Skien, Jar Blå, Frisk Asker Orange, Jar hvit, Frisk Asker Svart"],[]]'
    mock_response.raise_for_status = MagicMock()
    mock_session.post.return_value = mock_response

    # Parse with message fetching
    events = _parse_brp_exigo_day_strict(
        page,
        datetime(2027, 1, 24),
        session=mock_session,
        day_url="https://skienfritidspark.brp.exigo.no/ishallen?date=2027-01-24",
    )

    # 1. Scraper preserves message in CalendarEvent.name (from server action)
    assert len(events) == 1
    assert events[0].name == "Skien Ishockeyklubb - JU10: Skien, Jar Blå, Frisk Asker Orange, Jar hvit, Frisk Asker Svart 11:15-13:35"
    assert events[0].order_id == "1194498"
    assert len(events[0].resources) == 2

    # 2. Cache roundtrip preserves order_id and resources
    cache = CalendarCache(cache_dir=str(tmp_path))
    start = end = datetime(2027, 1, 24)
    cache.set("https://example.test/ishallen", "Skien", start, end, events)
    cached = cache.get("https://example.test/ishallen", "Skien", start, end)
    assert cached[0].order_id == "1194498"
    assert len(cached[0].resources) == 2

    # 3. Stage 3 busy intervals preserve order_id and calendar_event (with message)
    # Need to pass serialized events (as Stage 2 checkpoint format)
    serialized_events = []
    for e in cached:
        serialized_events.append({
            "date": e.date,
            "name": e.name,
            "datetime": e.datetime.isoformat(),
            "duration_hours": e.duration_hours,
            "duration_minutes": e.duration_minutes,
            "location": e.location,
            "resources": [{"id": r.id, "label": r.label} for r in e.resources],
            "order_id": e.order_id,
        })
    intervals = _build_club_busy_intervals({"events_by_club": {"Skien": serialized_events}})
    skien_intervals = intervals["Skien"]
    assert len(skien_intervals) == 1
    entry = skien_intervals[0]
    assert entry["order_id"] == "1194498"
    assert "JU10: Skien, Jar Blå, Frisk Asker Orange, Jar hvit, Frisk Asker Svart" in entry["calendar_event"]
    # Resources are resolved to club-specific IDs via registry
    assert entry["resources"] == ["bane-1", "bane-2"]

    # 4. Fingerprint includes order_id (distinguishes same-title different-order bookings)
    fp = event_fingerprint({"club": "Skien", **entry})
    # A different order_id should produce a different fingerprint
    entry2 = dict(entry)
    entry2["order_id"] = "9999999"
    fp2 = event_fingerprint({"club": "Skien", **entry2})
    assert fp != fp2

    # 5. iter_events (used by booking assessment) exposes order_id and calendar_event
    problem = {"club_busy_intervals": intervals}
    iterated = list(iter_events(problem))
    assert len(iterated) == 1
    assert iterated[0]["order_id"] == "1194498"
    assert "JU10: Skien, Jar Blå, Frisk Asker Orange, Jar hvit, Frisk Asker Svart" in iterated[0]["calendar_event"]


def test_brp_event_message_fallback_when_server_action_fails(tmp_path):
    """If server action fails, scraper falls back to empty message (no crash)."""
    page = _page(
        _BANES,
        [
            _item(
                1,
                1194498,
                362,
                "Skien Ishockeyklubb",
                time_range="11:15-13:35",
                message="",
            ),
            _item(
                1,
                1194498,
                363,
                "Skien Ishockeyklubb",
                time_range="11:15-13:35",
                message="",
            ),
        ],
    )

    # Mock session that raises on post
    mock_session = MagicMock()
    mock_session.post.side_effect = Exception("Network error")

    events = _parse_brp_exigo_day_strict(
        page,
        datetime(2027, 1, 24),
        session=mock_session,
        day_url="https://skienfritidspark.brp.exigo.no/ishallen?date=2027-01-24",
    )

    # Should still parse events, just without messages
    assert len(events) == 1
    assert events[0].name == "Skien Ishockeyklubb 11:15-13:35"
    assert events[0].order_id == "1194498"


def test_unclassified_intervals_preserve_order_id():
    """Unclassified intervals also preserve order_id."""
    from tournament_scheduler.booking.calendar_availability import unclassified_intervals

    intervals = {
        "Skien": [
            {
                "date": "2027-01-24",
                "start": "11:15",
                "end": "13:35",
                "calendar_event": "Skien Ishockeyklubb - JU10: Skien, Jar Blå 11:15-13:35",
                "availability": "fixed_busy",
                "order_id": "1194498",
                "resources": ["362", "363"],
            }
        ]
    }
    unclassified = unclassified_intervals(intervals)
    assert len(unclassified) == 1
    assert unclassified[0]["order_id"] == "1194498"
    assert "JU10: Skien, Jar Blå" in unclassified[0]["calendar_event"]