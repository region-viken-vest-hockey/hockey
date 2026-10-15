"""Regression coverage for BRP descriptive calendar evidence (#704)."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from tournament_scheduler.booking.calendar_bookings import booking_assessment
from tournament_scheduler.pipeline.scraper_brp_exigo import (
    _booking_groups_from_page,
    _parse_brp_exigo_day_strict,
    _run_brp_exigo_scraper,
)
from tournament_scheduler.pipeline.scraper_brp_exigo_messages import (
    BrpExigoMessageError,
    discover_message_action_id,
    fetch_booking_messages,
)
from tournament_scheduler.pipeline.stage3_helpers import _build_club_busy_intervals
from tournament_scheduler.utils.calendar_cache import CalendarCache

MESSAGE = "JU10: Skien, Jar Blå, Frisk Asker Orange, Jar hvit, Frisk Asker Svart"


def _page(*, message: str = "", scripts: str = "") -> str:
    import json

    resources = [
        {"id": 362, "name": "Ishockey - bane 1"},
        {"id": 363, "name": "Ishockey - bane 2"},
    ]
    events = [
        {
            "id": f"1194498-{resource_id}-1800785700000-1800794100000",
            "title": "Skien Ishockeyklubb",
            "start": "2027-01-24T10:15:00.000Z",
            "end": "2027-01-24T12:35:00.000Z",
            "resourceId": resource_id,
            "orderId": 1194498,
            "timeRange": "11:15-13:35",
            "message": message,
            "resources": [],
        }
        for resource_id in (362, 363)
    ]
    payload = json.dumps(
        {"resources": resources, "events": events, "date": "2027-01-24"},
        separators=(",", ":"),
    ).replace('"', '\\"')
    return scripts + f'<script>self.__next_f.push([1,"f:[\\"$\\",{payload}]"])</script>'


def _serialized(event) -> dict:
    return {
        "date": event.date,
        "name": event.name,
        "datetime": event.datetime.isoformat(),
        "duration_hours": event.duration_hours,
        "duration_minutes": event.duration_minutes,
        "location": event.location,
        "resources": [{"id": resource.id, "label": resource.label} for resource in event.resources],
        "order_id": event.order_id,
    }


def test_brp_server_action_message_survives_cache_stage3_and_assessment(tmp_path):
    page = _page()
    groups = _booking_groups_from_page(page)
    session = MagicMock()
    response = MagicMock()
    response.content = (
        '0:{"a":"$@1","f":"","b":"build"}\n'
        f'1:[["{MESSAGE}","{MESSAGE}"]]'
    ).encode()
    session.post.return_value = response

    messages = fetch_booking_messages(
        session,
        "https://skienfritidspark.brp.exigo.no/ishallen?date=2027-01-24",
        groups,
        action_id="action-id",
    )
    events = _parse_brp_exigo_day_strict(
        page, datetime(2027, 1, 24), messages=messages
    )
    assert events[0].name == f"Skien Ishockeyklubb - {MESSAGE} 11:15-13:35"
    assert events[0].order_id == "1194498"

    cache = CalendarCache(cache_dir=str(tmp_path))
    day = datetime(2027, 1, 24)
    cache.set("https://example.test/ishallen", "Skien", day, day, events)
    cached = cache.get("https://example.test/ishallen", "Skien", day, day)
    intervals = _build_club_busy_intervals(
        {"events_by_club": {"Skien": [_serialized(cached[0])]}}
    )
    event = intervals["Skien"][0]
    assert event["calendar_event"] == f"Skien Ishockeyklubb - {MESSAGE} 11:15-13:35"
    assert event["order_id"] == "1194498"
    assert event["resources"] == ["bane-1", "bane-2"]

    tournament = {
        "id": "rvv-0007",
        "host_club": "Skien",
        "arena": "Skien ishall",
        "age_group": "JU10",
        "date": "2027-01-24",
        "start_time": "11:15",
        "teams": [],
        "games": [],
    }
    problem = {
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "club_calendar_status": {"Skien": "known"},
        "club_busy_intervals": intervals,
        "ice_time_minutes": {"JU10": 140},
    }
    assessment = booking_assessment(
        problem=problem,
        plan={"tournaments": [tournament]},
        decisions=None,
        canonical_state_revision="revision",
    )
    row = assessment["tournaments"][0]
    assert row["candidates"][0]["title"] == event["calendar_event"]
    assert "event_title_age_group_matches" in row["candidates"][0]["evidence"]


def test_message_action_is_discovered_from_current_javascript_chunk():
    page_url = "https://example.test/ishallen?date=2027-01-24"
    page = _page(scripts='<script src="/_next/static/chunks/calendar.js"></script>')
    session = MagicMock()
    script = MagicMock()
    script.text = (
        'y=(0,v.createServerReference)("abcdef0123456789",v.callServer,void 0,'
        'v.findSourceMapURL,"fetchEventMessagesAction")'
    )
    session.get.return_value = script

    assert discover_message_action_id(session, page_url, page) == "abcdef0123456789"
    session.get.assert_called_once_with(
        "https://example.test/_next/static/chunks/calendar.js", timeout=15
    )


def test_message_enrichment_failure_marks_source_partial_without_losing_occupancy():
    page = _page(scripts='<script src="/_next/static/chunks/calendar.js"></script>')
    session = MagicMock()
    day_response = MagicMock(text=page)
    session.get.return_value = day_response

    with patch(
        "tournament_scheduler.pipeline.scraper_brp_exigo.requests.Session",
        return_value=session,
    ), patch(
        "tournament_scheduler.pipeline.scraper_brp_exigo.discover_message_action_id",
        return_value="action-id",
    ), patch(
        "tournament_scheduler.pipeline.scraper_brp_exigo.fetch_booking_messages",
        side_effect=BrpExigoMessageError("message contract changed"),
    ):
        events, _ = _run_brp_exigo_scraper(
            "https://example.test/ishallen",
            "Skien",
            datetime(2027, 1, 24),
            datetime(2027, 1, 24),
        )

    assert len(events) == 1
    assert events[0].name == "Skien Ishockeyklubb 11:15-13:35"
    assert events.coverage["status"] == "partial"
    assert events.coverage["navigation_complete"] is False
    assert "message contract changed" in events.coverage["exceptions"][0]
