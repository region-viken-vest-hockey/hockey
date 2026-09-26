"""Deterministic BookUp browser/parser coverage without credentials.

Tønsberg's BookUp source is a public, deterministic SPA: no login, MFA or
session handoff is involved. These tests drive the real parser helpers
(:func:`_parse_bookup_timegrid`, :func:`_bookup_navigate_to_date`,
:func:`_is_own_club_youth_booking`) against a fake Playwright ``frame`` so the
DOM-reading/decoding logic stays covered locally. A live reachability check of
the public upstream service is classified separately under the ``live`` marker
(see ``tests/test_live_sources.py``), not here.
"""

from __future__ import annotations

import json
from datetime import datetime

from tournament_scheduler.models import CalendarEvent
from tournament_scheduler.pipeline.scraper_bookup import (
    _bookup_coverage_record,
    _bookup_navigate_to_date,
    _bookup_visible_dates,
    _deduplicate_bookup_events,
    _is_own_club_youth_booking,
    _parse_bookup_timegrid,
)


class _FakeEvent:
    def __init__(self, frame, *, data_start: str, data_full: str, leietaker: str, formal: str):
        self._frame = frame
        self._meta = {"dataStart": data_start, "dataFull": data_full}
        self._leietaker = leietaker
        self._formal = formal

    def evaluate(self, script: str) -> str:
        return json.dumps(self._meta)

    def click(self, **_kwargs) -> None:
        # Clicking an event reveals its contract detail in the modal.
        self._frame.modal_title = self._leietaker
        self._frame.modal_subtitle = self._formal


class _EventList:
    def __init__(self, events: list[_FakeEvent]):
        self._events = events

    def count(self) -> int:
        return len(self._events)

    def nth(self, index: int) -> _FakeEvent:
        return self._events[index]


class _Column:
    def __init__(self, events: list[_FakeEvent]):
        self._events = events

    def locator(self, selector: str) -> _EventList:
        assert "fc-time-grid-event" in selector
        return _EventList(self._events)


class _ColumnList:
    def __init__(self, columns: list[_Column]):
        self._columns = columns

    def count(self) -> int:
        return len(self._columns)

    def nth(self, index: int) -> _Column:
        return self._columns[index]


class _TextLocator:
    def __init__(self, text: str | None):
        self._text = text

    def count(self) -> int:
        return 0 if self._text is None else 1

    @property
    def first(self) -> "_TextLocator":
        return self

    def inner_text(self) -> str:
        return self._text or ""

    def click(self, **_kwargs) -> None:
        pass


class _CloseLocator(_TextLocator):
    def __init__(self) -> None:
        super().__init__("")


class _FakeTimegridFrame:
    """Minimal stand-in for the Playwright frame used by the BookUp parser."""

    def __init__(self, dates: list[str], columns: list[_Column]):
        self._dates = dates
        self._columns = columns
        self.modal_title: str | None = None
        self.modal_subtitle: str | None = None

    def evaluate(self, script: str) -> str:
        if "fc-day-header" in script:
            return json.dumps(self._dates)
        raise AssertionError(f"unexpected evaluate script in parser: {script[:80]}")

    def locator(self, selector: str):
        if "fc-content-skeleton" in selector or "fc-content-col" in selector:
            return _ColumnList(self._columns)
        if "#viewModal .title" in selector:
            return _TextLocator(self.modal_title)
        if "#viewModal .sub-title" in selector:
            return _TextLocator(self.modal_subtitle)
        if ".view-contract-close" in selector:
            return _CloseLocator()
        raise AssertionError(f"unexpected locator selector: {selector}")

    def wait_for_timeout(self, _milliseconds: int) -> None:
        pass


class TestIsOwnClubYouthBooking:
    def test_detects_host_clubs_own_u_team_booking(self) -> None:
        assert _is_own_club_youth_booking(
            "Leietaker:Tønsberg ishall Formål:U-lag trening", "Tønsberg"
        )

    def test_external_rental_is_not_own_youth_booking(self) -> None:
        assert not _is_own_club_youth_booking(
            "Leietaker:Holmen Hockey Formål:Bredde", "Tønsberg"
        )

    def test_other_formal_is_not_own_youth_booking(self) -> None:
        assert not _is_own_club_youth_booking(
            "Leietaker:Tønsberg ishall Formål:Kamp", "Tønsberg"
        )

    def test_title_without_contract_fields_is_not_own_youth_booking(self) -> None:
        assert not _is_own_club_youth_booking("Booket", "Tønsberg")


class TestDeduplicateBookupEvents:
    def test_preserves_same_day_distinct_booked_intervals(self) -> None:
        events = [
            CalendarEvent("17.10.2026", "Booket", datetime(2026, 10, 17, 9, 0), 1.0),
            CalendarEvent("17.10.2026", "Booket", datetime(2026, 10, 17, 14, 15), 2.0),
            CalendarEvent("17.10.2026", "Booket", datetime(2026, 10, 17, 14, 15), 2.0),
        ]

        unique = _deduplicate_bookup_events(events)

        assert [(e.datetime.strftime("%H:%M"), e.duration_hours) for e in unique] == [
            ("09:00", 1.0),
            ("14:15", 2.0),
        ]


class TestParseBookupTimegrid:
    def test_parses_date_time_and_duration_per_column(self) -> None:
        frame = _FakeTimegridFrame(["2026-10-05", "2026-10-06"], [])
        columns = [
            _Column(
                [
                    _FakeEvent(
                        frame,
                        data_start="18:00",
                        data_full="18:00-20:00",
                        leietaker="Holmen Hockey",
                        formal="Trening",
                    )
                ]
            ),
            _Column(
                [
                    _FakeEvent(
                        frame,
                        data_start="09:30",
                        data_full="09:30-10:30",
                        leietaker="Jar",
                        formal="Kamp",
                    )
                ]
            ),
        ]
        frame._columns = columns

        events = _parse_bookup_timegrid(frame)

        assert [(e.date, e.datetime.strftime("%H:%M"), e.duration_hours) for e in events] == [
            ("05.10.2026", "18:00", 2.0),
            ("06.10.2026", "09:30", 1.0),
        ]
        assert events[0].name == "Holmen Hockey (Trening)"
        assert events[1].name == "Jar (Kamp)"

    def test_skips_host_clubs_own_youth_booking_when_club_name_given(self) -> None:
        frame = _FakeTimegridFrame(["2026-10-05"], [])
        frame._columns = [
            _Column(
                [
                    _FakeEvent(
                        frame,
                        data_start="17:00",
                        data_full="17:00-18:00",
                        leietaker="Tønsberg ishall",
                        formal="U-lag",
                    ),
                    _FakeEvent(
                        frame,
                        data_start="18:00",
                        data_full="18:00-19:00",
                        leietaker="Ekstern klubb",
                        formal="Trening",
                    ),
                ]
            )
        ]

        events = _parse_bookup_timegrid(frame, club_name="Tønsberg")

        assert len(events) == 1
        assert events[0].name == "Ekstern klubb (Trening)"

    def test_can_skip_contract_detail_clicks_for_bounded_live_refresh(self) -> None:
        frame = _FakeTimegridFrame(["2026-10-05"], [])
        frame._columns = [
            _Column(
                [
                    _FakeEvent(
                        frame,
                        data_start="17:00",
                        data_full="17:00-18:00",
                        leietaker="Tønsberg ishall",
                        formal="U-lag",
                    )
                ]
            )
        ]

        events = _parse_bookup_timegrid(frame, club_name="Tønsberg", read_details=False)

        assert len(events) == 1
        assert events[0].datetime.strftime("%H:%M") == "17:00"
        assert events[0].name == "Booket"
        assert frame.modal_title is None

    def test_returns_empty_without_day_headers(self) -> None:
        frame = _FakeTimegridFrame([], [])
        assert _parse_bookup_timegrid(frame) == []

    def test_falls_back_to_default_duration_for_malformed_range(self) -> None:
        frame = _FakeTimegridFrame(["2026-10-05"], [])
        frame._columns = [
            _Column(
                [
                    _FakeEvent(
                        frame,
                        data_start="",
                        data_full="not-a-range",
                        leietaker="",
                        formal="",
                    )
                ]
            )
        ]

        events = _parse_bookup_timegrid(frame)

        assert len(events) == 1
        assert events[0].duration_hours == 1.0
        assert events[0].name == "Booket"


class _FakeNavigationFrame:
    def __init__(self, weeks: list[list[str]]):
        self._weeks = weeks
        self._index = 0
        self.clicks: list[str] = []

    def evaluate(self, script: str) -> str:
        assert "[data-date]" in script
        return json.dumps(self._weeks[self._index])

    def locator(self, selector: str):
        button = "prev" if "prev" in selector else "next"
        if button == "prev":
            raise AssertionError("test fixture does not expect a prev click")
        frame = self

        class _Button:
            def count(self) -> int:
                return 1

            @property
            def first(self):
                return self

            def click(self, **_kwargs) -> None:
                frame.clicks.append("next")
                if frame._index < len(frame._weeks) - 1:
                    frame._index += 1

        return _Button()

    def wait_for_timeout(self, _milliseconds: int) -> None:
        pass


class TestBookupNavigateToDate:
    def test_no_click_when_target_already_visible(self) -> None:
        from datetime import datetime

        frame = _FakeNavigationFrame([["2026-10-05", "2026-10-11"]])
        _bookup_navigate_to_date(frame, datetime(2026, 10, 7))
        assert frame.clicks == []

    def test_clicks_next_until_target_visible(self) -> None:
        from datetime import datetime

        frame = _FakeNavigationFrame(
            [
                ["2026-10-05", "2026-10-11"],
                ["2026-10-12", "2026-10-18"],
                ["2026-10-19", "2026-10-25"],
            ]
        )
        _bookup_navigate_to_date(frame, datetime(2026, 10, 20))
        assert frame.clicks == ["next", "next"]


class TestBookupCoverageRecord:
    """P1: coverage is only `complete` once the requested window was inspected."""

    def test_complete_only_when_start_and_end_were_inspected(self) -> None:
        from datetime import date

        from tournament_scheduler.pipeline.source_integrity import INTEGRITY_COMPLETE

        record = _bookup_coverage_record(
            [date(2026, 10, 5), date(2026, 10, 11), date(2026, 10, 26), date(2026, 11, 1)],
            datetime(2026, 10, 5),
            datetime(2026, 11, 1),
            [],
        )

        assert record["status"] == INTEGRITY_COMPLETE
        assert record["navigation_complete"] is True
        assert record["exceptions"] == []

    def test_early_end_of_window_exit_is_partial(self) -> None:
        from datetime import date

        from tournament_scheduler.pipeline.source_integrity import INTEGRITY_PARTIAL

        record = _bookup_coverage_record(
            [date(2026, 10, 5), date(2026, 10, 11)],
            datetime(2026, 10, 5),
            datetime(2026, 11, 30),
            [],
        )

        assert record["status"] == INTEGRITY_PARTIAL
        assert record["navigation_complete"] is False
        assert any("slutt" in reason for reason in record["exceptions"])

    def test_failed_start_navigation_is_partial(self) -> None:
        from datetime import date

        from tournament_scheduler.pipeline.source_integrity import INTEGRITY_PARTIAL

        record = _bookup_coverage_record(
            [date(2026, 10, 26), date(2026, 11, 1)],
            datetime(2026, 10, 5),
            datetime(2026, 11, 1),
            [],
        )

        assert record["status"] == INTEGRITY_PARTIAL
        assert any("start" in reason for reason in record["exceptions"])

    def test_navigation_exception_is_partial(self) -> None:
        from datetime import date

        from tournament_scheduler.pipeline.source_integrity import INTEGRITY_PARTIAL

        record = _bookup_coverage_record(
            [date(2026, 10, 5), date(2026, 11, 1)],
            datetime(2026, 10, 5),
            datetime(2026, 11, 1),
            ["week navigation stopped early"],
        )

        assert record["status"] == INTEGRITY_PARTIAL
        assert record["exceptions"] == ["week navigation stopped early"]

    def test_visible_dates_are_read_from_the_dom(self) -> None:
        from datetime import date

        frame = _FakeNavigationFrame([["2026-10-05", "2026-10-11"]])

        assert _bookup_visible_dates(frame) == [date(2026, 10, 5), date(2026, 10, 11)]
