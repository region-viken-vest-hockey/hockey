"""Live external-source checks (network), explicitly opt-in.

These verify that a public upstream service the pipeline depends on is still
reachable and still serves the shape the deterministic source parser expects.
They are deliberately *not* part of the quick or full lane: an upstream outage
or markup change must never block ordinary PR gating.

Marked ``live``. The conftest hook skips every ``live`` test unless
``RVV_LIVE_TESTS=1`` is set, which ``scripts/check live`` and the scheduled
live CI job do. BookUp here is a public deterministic source -- there is no
authentication, MFA or manual-login category anywhere in this repository.
"""

from __future__ import annotations

import pytest
import requests

pytestmark = pytest.mark.live

# Tønsberg ishall on the public BookUp booking portal. ``_run_bookup_scraper``
# navigates to this index page and then resolves the ``app.html`` SPA iframe.
TONSBERG_BOOKUP_URL = "https://www.bookup.no/utleie/Index/860"
_USER_AGENT = "Mozilla/5.0 (compatible; rvv-miniputt-live-check)"


def test_tonsberg_bookup_public_index_is_reachable() -> None:
    response = requests.get(TONSBERG_BOOKUP_URL, timeout=30, headers={"User-Agent": _USER_AGENT})

    assert response.status_code == 200
    body = response.text.lower()
    # The deterministic scraper depends on the SPA iframe being present; a
    # portal redesign that drops it should surface here, not as a mysterious
    # empty scrape weeks later.
    assert "app.html" in body
    assert "bookup" in body
