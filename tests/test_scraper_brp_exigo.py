"""Coverage and fail-closed tests for the Skien BRP/Exigo scraper."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

from tournament_scheduler.pipeline.scraper_brp_exigo import _run_brp_exigo_scraper
from tournament_scheduler.pipeline.source_integrity import (
    INTEGRITY_COMPLETE,
    INTEGRITY_FAILED,
    INTEGRITY_PARTIAL,
    evaluate_source_integrity,
)


def _page(day: str, *, events: list[dict] | None = None) -> str:
    payload = json.dumps(
        {
            "resources": [{"id": 362, "name": "Ishockey - bane 1"}],
            "events": events or [],
            "date": day,
        },
        separators=(",", ":"),
    )
    escaped = payload.replace('"', '\\"')
    return f'<script>self.__next_f.push([1,"f:[\\"$\\",{escaped}]"])</script>'


def _event(day: int, *, order_id: int | None = None) -> dict:
    return {
        "id": f"event-{day}",
        "title": f"Booking {day}",
        "resourceId": 362,
        "orderId": order_id if order_id is not None else day,
        "timeRange": "10:00-12:00",
        "message": "",
        "resources": [],
    }


def _response(text: str) -> MagicMock:
    response = MagicMock()
    response.text = text
    response.raise_for_status = MagicMock()
    return response


def _scrape(responses, *, start=datetime(2026, 10, 9), end=datetime(2026, 10, 11)):
    session = MagicMock()
    session.get.side_effect = responses
    with patch(
        "tournament_scheduler.pipeline.scraper_brp_exigo.requests.Session",
        return_value=session,
    ), patch(
        "tournament_scheduler.pipeline.scraper_brp_exigo.discover_message_action_id",
        return_value="action-id",
    ), patch(
        "tournament_scheduler.pipeline.scraper_brp_exigo.fetch_booking_messages",
        side_effect=lambda _session, _url, groups, **_kwargs: [
            str(group["item"].get("message") or "") for group in groups
        ],
    ):
        events, raw = _run_brp_exigo_scraper(
            "https://example.test/ishallen?view=day", "Skien", start, end
        )
    return events, raw, session


def test_walks_every_requested_date_inclusively_and_proves_sparse_window():
    events, _raw, session = _scrape(
        [
            _response(_page("2026-10-09")),
            _response(_page("2026-10-10", events=[_event(10)])),
            _response(_page("2026-10-11")),
        ]
    )

    assert [call.args[0] for call in session.get.call_args_list] == [
        "https://example.test/ishallen?view=day&date=2026-10-09",
        "https://example.test/ishallen?view=day&date=2026-10-10",
        "https://example.test/ishallen?view=day&date=2026-10-11",
    ]
    assert len(events) == 1
    coverage = events.coverage
    assert coverage["strategy"] == "brp_exigo"
    assert coverage["status"] == INTEGRITY_COMPLETE
    assert coverage["navigation_complete"] is True
    assert coverage["requested_start"] == "2026-10-09"
    assert coverage["requested_end"] == "2026-10-11"
    assert coverage["observed_start"] == "2026-10-09"
    assert coverage["observed_end"] == "2026-10-11"
    assert coverage["event_observed_start"] == "2026-10-10"
    assert coverage["event_observed_end"] == "2026-10-10"
    assert coverage["requested_date_count"] == 3
    assert coverage["inspected_date_count"] == 3
    assert coverage["event_count"] == 1
    assert coverage["event_fingerprint"]
    assert coverage["exceptions"] == []

    integrity = evaluate_source_integrity(
        {"name": "Skien", "type": "outlook", "events": events, "event_count": 1},
        requested_start="2026-10-09",
        requested_end="2026-10-11",
    )
    assert integrity["status"] == INTEGRITY_COMPLETE
    assert integrity["coverage_proven"] is True
    assert integrity["trusted_for_negative_claim"] is True


def test_empty_dates_still_prove_the_full_requested_window():
    events, _raw, _session = _scrape(
        [
            _response(_page("2026-10-09")),
            _response(_page("2026-10-10")),
            _response(_page("2026-10-11")),
        ]
    )

    assert events == []
    assert events.coverage["status"] == INTEGRITY_COMPLETE
    assert events.coverage["navigation_complete"] is True
    assert events.coverage["observed_end"] == "2026-10-11"
    assert events.coverage["event_observed_start"] is None
    assert events.coverage["event_observed_end"] is None


def test_http_failure_marks_partial_navigation_and_keeps_other_days():
    failure = MagicMock()
    failure.raise_for_status.side_effect = RuntimeError("503 unavailable")
    events, _raw, session = _scrape(
        [
            _response(_page("2026-10-09", events=[_event(9)])),
            failure,
            _response(_page("2026-10-11", events=[_event(11)])),
        ]
    )

    assert session.get.call_count == 3
    assert len(events) == 2
    coverage = events.coverage
    assert coverage["status"] == INTEGRITY_PARTIAL
    assert coverage["navigation_complete"] is False
    assert coverage["inspected_date_count"] == 2
    assert any("2026-10-10" in error for error in coverage["exceptions"])
    integrity = evaluate_source_integrity(
        {"name": "Skien", "type": "outlook", "events": events, "event_count": 2},
        requested_start="2026-10-09",
        requested_end="2026-10-11",
    )
    assert integrity["status"] == INTEGRITY_PARTIAL
    assert integrity["coverage_proven"] is False


def test_malformed_or_wrong_date_response_fails_closed():
    events, _raw, _session = _scrape(
        [
            _response(_page("2026-10-09")),
            _response("<html>not a React flight payload</html>"),
            _response(_page("2026-10-10")),
        ]
    )

    assert events.coverage["status"] == INTEGRITY_PARTIAL
    assert events.coverage["navigation_complete"] is False
    assert len(events.coverage["exceptions"]) == 2
    assert any("mangler" in error for error in events.coverage["exceptions"])
    assert any("annen dato" in error for error in events.coverage["exceptions"])


def test_total_request_failure_is_failed_not_an_empty_complete_calendar():
    with patch(
        "tournament_scheduler.pipeline.scraper_brp_exigo.requests.Session.get",
        side_effect=OSError("network down"),
    ):
        events, _ = _run_brp_exigo_scraper(
            "https://example.test/ishallen",
            "Skien",
            datetime(2026, 10, 9),
            datetime(2026, 10, 9),
        )

    assert events == []
    assert events.coverage["status"] == INTEGRITY_FAILED
    assert events.coverage["navigation_complete"] is False
    assert events.coverage["exceptions"]


def test_stage2_preserves_skien_coverage_through_checkpoint_and_cache(tmp_path):
    from tournament_scheduler.pipeline.stage2_scraping import run
    from tournament_scheduler.pipeline.state import PipelineState

    state = PipelineState(tmp_path / "pipeline")
    config = {
        "start_date": "2026-10-09",
        "end_date": "2026-10-11",
        "teams": [{"club": "Skien", "label": "Skien U10", "age_group": "U10"}],
        "sources": [
            {
                "name": "Skien",
                "type": "outlook",
                "url": "https://skienfritidspark.brp.exigo.no/ishallen",
            }
        ],
    }
    responses = [
        _response(_page("2026-10-09")),
        _response(_page("2026-10-10", events=[_event(10)])),
        _response(_page("2026-10-11")),
    ]
    session = MagicMock()
    session.get.side_effect = responses
    with patch(
        "tournament_scheduler.pipeline.scraper_brp_exigo.requests.Session",
        return_value=session,
    ), patch(
        "tournament_scheduler.pipeline.scraper_brp_exigo.discover_message_action_id",
        return_value="action-id",
    ), patch(
        "tournament_scheduler.pipeline.scraper_brp_exigo.fetch_booking_messages",
        side_effect=lambda _session, _url, groups, **_kwargs: [
            str(group["item"].get("message") or "") for group in groups
        ],
    ):
        first = run(
            config,
            state,
            datetime(2026, 10, 9),
            datetime(2026, 10, 11),
        )

    first_source = first["sources"][0]
    assert first_source["coverage"]["strategy"] == "brp_exigo"
    assert first_source["integrity"]["status"] == INTEGRITY_COMPLETE
    assert first_source["integrity"]["coverage_proven"] is True
    assert first["club_calendar_status"]["Skien"] == "known"
    assert first["club_coverage_proven"]["Skien"] is True

    with patch(
        "tournament_scheduler.pipeline.stage2_scraping._run_brp_exigo_scraper",
        side_effect=AssertionError("fresh cache entry should avoid scraping"),
    ):
        second = run(
            config,
            state,
            datetime(2026, 10, 9),
            datetime(2026, 10, 11),
        )

    second_source = second["sources"][0]
    assert second_source["from_cache"] is True
    assert second_source["coverage"] == first_source["coverage"]
    assert second_source["integrity"]["coverage_proven"] is True
    assert second["club_calendar_status"]["Skien"] == "known"
    assert second["club_coverage_proven"]["Skien"] is True


def test_event_count_regression_still_fails_closed():
    events, _raw, _session = _scrape(
        [
            _response(_page("2026-10-09")),
            _response(_page("2026-10-10", events=[_event(10)])),
            _response(_page("2026-10-11")),
        ]
    )

    integrity = evaluate_source_integrity(
        {"name": "Skien", "type": "outlook", "events": events, "event_count": 1},
        requested_start="2026-10-09",
        requested_end="2026-10-11",
        previous_event_count=100,
    )

    assert integrity["status"] == "suspicious"
    assert integrity["coverage_proven"] is False
    assert integrity["trusted_for_negative_claim"] is False


def test_generic_outlook_without_brp_coverage_remains_unproven():
    integrity = evaluate_source_integrity(
        {
            "name": "Generic Outlook",
            "type": "outlook",
            "events": [
                {
                    "date": "10.10.2026",
                    "datetime": "2026-10-10T10:00:00",
                    "name": "Booking",
                    "duration_hours": 2.0,
                }
            ],
            "event_count": 1,
        },
        requested_start="2026-10-09",
        requested_end="2026-10-11",
    )

    assert integrity["status"] == INTEGRITY_COMPLETE
    assert integrity["coverage_proven"] is False
    assert integrity["trusted_for_negative_claim"] is False
