"""A Forumbooking browser failure must not look like an empty calendar.

The scraper used to swallow every exception and return ``([], "")``. Stage 2 reads a
clean zero-event return as a genuinely empty public calendar, so a missing browser
recorded Jar as empty and a refresh would have dropped its evidence. A failure has
to propagate, so Stage 2 marks the source blocked.
"""

from __future__ import annotations

import datetime

import pytest

from tournament_scheduler.pipeline import scraper_forumbooking

URL = "https://www.forumbooking.no/schema.aspx?obj=2&schema=Jarhallen%20(ishall)&kalender=true&safarifix=true"


def test_browser_launch_failure_propagates_instead_of_returning_empty(monkeypatch):
    import playwright.sync_api as sync_api

    def _broken_launch(*_args, **_kwargs):
        raise RuntimeError("Executable doesn't exist at chromium_headless_shell")

    class _Playwright:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        @property
        def chromium(self):
            class _Chromium:
                launch = staticmethod(_broken_launch)

            return _Chromium()

    monkeypatch.setattr(sync_api, "sync_playwright", lambda: _Playwright())

    with pytest.raises(RuntimeError, match="Executable doesn't exist"):
        scraper_forumbooking._run_forumbooking_scraper(
            URL,
            "Jar",
            datetime.datetime(2026, 11, 1),
            datetime.datetime(2026, 11, 30),
        )
