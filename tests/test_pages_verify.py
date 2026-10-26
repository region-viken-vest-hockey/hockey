"""Tests for tournament_scheduler.pipeline.pages_verify (issue #20)."""

from __future__ import annotations

import json

from tournament_scheduler.pipeline.pages_verify import FetchResponse, verify_publication

LATEST_URL = "https://acme.github.io/hockey/latest/"
RUN_URL = "https://acme.github.io/hockey/runs/run-1/"
HISTORY_URL = "https://acme.github.io/hockey/history/"
FINGERPRINT = "abc123"
RUN_ID = "run-1"
PUBLISHED_AT = "2026-10-21T12:00:00+00:00"


def _meta_response(*, fingerprint=FINGERPRINT, run_id=RUN_ID) -> FetchResponse:
    return FetchResponse(status_code=200, text=json.dumps({"bundle_fingerprint": fingerprint, "run_id": run_id}))


def _ok_page(html: str = "<html><body>plan</body></html>") -> FetchResponse:
    return FetchResponse(status_code=200, text=html)


def _history_page(
    *,
    current_run_id: str = RUN_ID,
    links: tuple[str, ...] = ("run-1",),
    fingerprint: str = FINGERPRINT,
    published_at: str = PUBLISHED_AT,
    season_run: str | None = None,
    season_scope: str = "season",
) -> FetchResponse:
    rows = "".join(
        f'<tr data-run-id="{run}" data-fingerprint="{fingerprint if run == current_run_id else "older"}" '
        f'data-published-at="{published_at if run == current_run_id else "older"}" '
        f'data-current="{"true" if run == current_run_id else "false"}"'
        f'{f" data-scope=\"{season_scope}\" data-season-current=\"true\"" if run == season_run else ""}>'
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
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID, published_at=PUBLISHED_AT,
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
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID, published_at=PUBLISHED_AT,
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
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID, published_at=PUBLISHED_AT,
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
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID, published_at=PUBLISHED_AT,
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
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID, published_at=PUBLISHED_AT,
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
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID, published_at=PUBLISHED_AT,
            max_attempts=1, fetch=fetch, sleep=_NoSleep(),
        )

        assert result.status == "warning"
        assert "season_plan.html" in result.problems[0]


class TestHistoryPage:
    def test_missing_history_page_is_not_counted_as_verified(self):
        result = verify_publication(
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID, published_at=PUBLISHED_AT,
            max_attempts=1, fetch=_served_fetch(history=FetchResponse(status_code=404, text="")),
            sleep=_NoSleep(),
        )

        assert result.status == "warning"
        assert "historikksiden" in result.problems[0]

    def test_history_that_does_not_mark_this_run_current_is_not_verified(self):
        result = verify_publication(
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID, published_at=PUBLISHED_AT,
            max_attempts=1,
            fetch=_served_fetch(history=_history_page(current_run_id="some-other-run", links=("run-1",))),
            sleep=_NoSleep(),
        )

        assert result.status == "warning"
        assert "gjeldende" in result.problems[0]

    def test_history_with_two_current_rows_is_not_verified(self):
        page = _history_page(links=("run-1",)).text.replace(
            "</table>", '<tr data-run-id="run-1" data-current="true"></tr></table>'
        )
        result = verify_publication(
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID, published_at=PUBLISHED_AT,
            max_attempts=1, fetch=_served_fetch(history=_ok_page(page)), sleep=_NoSleep(),
        )

        assert result.status == "warning"
        assert "eneste gjeldende" in result.problems[0]

    def test_cached_history_from_an_earlier_publication_of_the_same_run_is_not_verified(self):
        result = verify_publication(
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID, published_at=PUBLISHED_AT,
            max_attempts=1, fetch=_served_fetch(history=_history_page(fingerprint="older-publication")),
            sleep=_NoSleep(),
        )

        assert result.status == "warning"
        assert "fingeravtrykk" in result.problems[0]

    def test_cached_history_from_the_first_publication_of_the_same_content_is_not_verified(self):
        # Publish A, publish B, roll back to A: same fingerprint, new publication time.
        result = verify_publication(
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
            published_at=PUBLISHED_AT,
            max_attempts=1,
            fetch=_served_fetch(history=_history_page(published_at="2026-10-19T08:00:00+00:00")),
            sleep=_NoSleep(),
        )

        assert result.status == "warning"
        assert "publisert" in result.problems[0]

    def test_a_broken_run_link_in_history_is_reported(self):
        result = verify_publication(
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID, published_at=PUBLISHED_AT,
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
            latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID, published_at=PUBLISHED_AT,
            max_attempts=4, retry_delay_seconds=2.0, fetch=fetch, sleep=sleep,
        )

        assert result.status == "warning"
        assert result.requires_human is False
        assert "attempts=4" in result.evidence
        assert len(sleep.calls) == 3  # slept between attempts, not after the last one


SEASON_POINTER_URL = LATEST_URL + "_season_meta.json"
SEASON_1 = {"run_id": RUN_ID, "bundle_fingerprint": "fp-season-1"}


def _pointer_fetch(served: dict | None, history: FetchResponse | None = None):
    """Serve the season pointer as *served* (None = absent), everything else as a good publication.

    The history page marks the served season as the season, unless *history* says otherwise.
    """
    if history is None:
        history = _history_page(season_run=served["run_id"] if served else None)
    base = _served_fetch(history=history)

    def fetch(url):
        if url == SEASON_POINTER_URL:
            if served is None:
                return FetchResponse(status_code=404, text="")
            return FetchResponse(status_code=200, text=json.dumps(served))
        return base(url)

    return fetch


def test_verification_passes_when_the_season_pointer_is_unchanged():
    result = verify_publication(
        latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
        published_at=PUBLISHED_AT, max_attempts=1, fetch=_pointer_fetch(SEASON_1), sleep=_NoSleep(),
        check_season_pointer=True, expected_season_pointer=SEASON_1,
    )

    assert result.status == "ok", result.summary


def test_verification_fails_when_the_season_pointer_moved():
    result = verify_publication(
        latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
        published_at=PUBLISHED_AT, max_attempts=2, fetch=_pointer_fetch({"run_id": "season-2", "bundle_fingerprint": "fp-season-2"}),
        sleep=_NoSleep(), check_season_pointer=True, expected_season_pointer=SEASON_1,
    )

    assert result.status != "ok", result.summary
    assert any("sesong" in problem.lower() for problem in result.problems), result.problems


def test_verification_fails_when_an_absent_season_pointer_appears():
    result = verify_publication(
        latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
        published_at=PUBLISHED_AT, max_attempts=1, fetch=_pointer_fetch(SEASON_1), sleep=_NoSleep(),
        check_season_pointer=True, expected_season_pointer=None,
    )

    assert result.status != "ok", result.summary


def test_verification_does_not_check_the_season_pointer_unless_asked():
    result = verify_publication(
        latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
        published_at=PUBLISHED_AT, max_attempts=1, fetch=_pointer_fetch({"run_id": "other", "bundle_fingerprint": "x"}),
        sleep=_NoSleep(),
    )

    assert result.status == "ok", result.summary


def test_verification_passes_when_the_history_marks_the_pointer_season():
    result = verify_publication(
        latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
        published_at=PUBLISHED_AT, max_attempts=1, fetch=_pointer_fetch(SEASON_1, history=_history_page(season_run="run-1")),
        sleep=_NoSleep(), check_season_pointer=True, expected_season_pointer=SEASON_1,
    )

    assert result.status == "ok", result.summary


def test_verification_fails_when_the_history_marks_another_season():
    result = verify_publication(
        latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
        published_at=PUBLISHED_AT, max_attempts=1,
        fetch=_pointer_fetch(SEASON_1, history=_history_page(links=("run-1", "run-2"), season_run="run-2")),
        sleep=_NoSleep(), check_season_pointer=True, expected_season_pointer=SEASON_1,
    )

    assert result.status != "ok", result.summary
    assert any("sesong" in problem.lower() for problem in result.problems), result.problems


def test_verification_fails_when_the_season_marker_sits_on_a_non_season_row():
    result = verify_publication(
        latest_url=LATEST_URL, run_url=RUN_URL, bundle_fingerprint=FINGERPRINT, run_id=RUN_ID,
        published_at=PUBLISHED_AT, max_attempts=1,
        fetch=_pointer_fetch(SEASON_1, history=_history_page(season_run="run-1", season_scope="activities")),
        sleep=_NoSleep(), check_season_pointer=True, expected_season_pointer=SEASON_1,
    )

    assert result.status != "ok", result.summary
