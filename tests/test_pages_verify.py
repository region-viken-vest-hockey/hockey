"""Tests for tournament_scheduler.pipeline.pages_verify (issue #20)."""

from __future__ import annotations

import json

from tournament_scheduler.pipeline.pages_verify import FetchResponse, verify_publication

LATEST_URL = "https://acme.github.io/hockey/latest/"
RUN_URL = "https://acme.github.io/hockey/runs/run-1/"
HISTORY_URL = "https://acme.github.io/hockey/history/"
FINGERPRINT = "abc123"
RUN_ID = "run-1"


def _meta_response(*, fingerprint=FINGERPRINT, run_id=RUN_ID) -> FetchResponse:
    return FetchResponse(status_code=200, text=json.dumps({"bundle_fingerprint": fingerprint, "run_id": run_id}))


def _ok_page(html: str = "<html><body>plan</body></html>") -> FetchResponse:
    return FetchResponse(status_code=200, text=html)


def _history_page(*, current_run_id: str = RUN_ID, links: tuple[str, ...] = ("run-1",)) -> FetchResponse:
    rows = "".join(
        f'<tr data-run-id="{run}" data-current="{"true" if run == current_run_id else "false"}">'
        f'<td><a href="../runs/{run}/">Vis snapshot</a></td></tr>'
        for run in links
    )
    return _ok_page(f"<html><body><table>{rows}</table></body></html>")


def _served_fetch(pages: dict[str, FetchResponse] | None = None, *, history: FetchResponse | None = None):
    """Fake fetch serving meta, the latest page, the history page and any extra pages."""
    pages = dict(pages or {})

    def fetch(url):
        if url.endswith("_meta.json"):
            return _meta_response()
        if url == HISTORY_URL:
            return history if history is not None else _history_page()
        if url == "https://acme.github.io/hockey/runs/run-1/":
            return _ok_page()
        if url in pages:
            return pages[url]
        if url == LATEST_URL:
            return _ok_page()
        return FetchResponse(status_code=404, text="")

    return fetch


class _NoSleep:
    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class TestImmediateSuccess:
    def test_succeeds_on_first_attempt_with_no_links(self):
        sleep = _NoSleep()
        result = verify_publication(
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
            fetch=_served_fetch(), sleep=sleep,
        )

        assert result.status == "ok"
        assert "attempts=1" in result.evidence
        assert sleep.calls == []

    def test_verifies_working_links_to_season_plan_and_calendar_pages(self):
        pages = {
            LATEST_URL: _ok_page('<a href="season_plan.html">plan</a><a href="calendars.html">cal</a>'),
            LATEST_URL + "season_plan.html": _ok_page(),
            LATEST_URL + "calendars.html": _ok_page(),
        }

        result = verify_publication(
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
            fetch=_served_fetch(pages), sleep=_NoSleep(),
        )
        assert result.status == "ok"


class TestDelayedAvailability:
    def test_retries_until_meta_json_becomes_available(self):
        calls = {"n": 0}
        served = _served_fetch()

        def fetch(url):
            if url.endswith("_meta.json"):
                calls["n"] += 1
                if calls["n"] < 3:
                    return FetchResponse(status_code=404, text="")
            return served(url)

        sleep = _NoSleep()
        result = verify_publication(
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
            max_attempts=5, retry_delay_seconds=1.5, fetch=fetch, sleep=sleep,
        )

        assert result.status == "ok"
        assert "attempts=3" in result.evidence
        assert sleep.calls == [1.5, 1.5]  # slept between the two failed attempts, not after success


class TestStalePage:
    def test_a_different_bundle_fingerprint_never_counts_as_success(self):
        def fetch(url):
            if url.endswith("_meta.json"):
                return _meta_response(fingerprint="some-older-fingerprint")
            return _ok_page()

        result = verify_publication(
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
            max_attempts=2, fetch=fetch, sleep=_NoSleep(),
        )

        assert result.status == "warning"
        assert "fingeravtrykk" in result.problems[0]

    def test_a_different_run_id_never_counts_as_success(self):
        def fetch(url):
            if url.endswith("_meta.json"):
                return _meta_response(run_id="some-other-run")
            return _ok_page()

        result = verify_publication(
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
            max_attempts=2, fetch=fetch, sleep=_NoSleep(),
        )

        assert result.status == "warning"
        assert "run_id" in result.problems[0]


class TestBrokenAssets:
    def test_a_broken_link_is_reported_and_not_counted_as_verified(self):
        def fetch(url):
            if url.endswith("_meta.json"):
                return _meta_response()
            if url == LATEST_URL:
                return _ok_page('<a href="season_plan.html">plan</a>')
            return FetchResponse(status_code=404, text="")

        result = verify_publication(
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
            max_attempts=1, fetch=fetch, sleep=_NoSleep(),
        )

        assert result.status == "warning"
        assert "season_plan.html" in result.problems[0]


class TestHistoryPage:
    def test_missing_history_page_is_not_counted_as_verified(self):
        result = verify_publication(
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
            max_attempts=1, fetch=_served_fetch(history=FetchResponse(status_code=404, text="")),
            sleep=_NoSleep(),
        )

        assert result.status == "warning"
        assert "historikksiden" in result.problems[0]

    def test_history_that_does_not_mark_this_run_current_is_not_verified(self):
        result = verify_publication(
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
            max_attempts=1,
            fetch=_served_fetch(history=_history_page(current_run_id="some-other-run", links=("run-1",))),
            sleep=_NoSleep(),
        )

        assert result.status == "warning"
        assert "gjeldende" in result.problems[0]

    def test_a_broken_run_link_in_history_is_reported(self):
        result = verify_publication(
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
            max_attempts=1, fetch=_served_fetch(history=_history_page(links=("run-1", "run-gone"))),
            sleep=_NoSleep(),
        )

        assert result.status == "warning"
        assert "run-gone" in result.problems[0]

    def test_history_url_is_derived_from_the_latest_url(self):
        from tournament_scheduler.pipeline.pages_verify import _history_url

        assert _history_url(LATEST_URL) == HISTORY_URL


class TestTimeout:
    def test_reports_warning_not_blocked_after_the_window_elapses(self):
        def fetch(url):
            return FetchResponse(status_code=0, text="", error="connection refused")

        sleep = _NoSleep()
        result = verify_publication(
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
            max_attempts=4, retry_delay_seconds=2.0, fetch=fetch, sleep=sleep,
        )

        assert result.status == "warning"
        assert result.requires_human is False
        assert "attempts=4" in result.evidence
        assert len(sleep.calls) == 3  # slept between attempts, not after the last one
