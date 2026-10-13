"""Season plan page must not overflow the viewport on phone-sized widths.

Layout is checked in a real browser because overflow depends on rendered
widths (flex/grid sizing, select intrinsic widths, nowrap navbar items).
The test is skipped only when Playwright or its Chromium build is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tournament_scheduler.html.html_exporter import HtmlExporter
from tournament_scheduler.models import SeasonPlan
from tournament_scheduler.serialization.season_plan import season_plan_from_dict

MOBILE_WIDTHS = (320, 360, 390, 430)
DESKTOP_WIDTH = 1280

_LAYOUT_JS = """() => {
  const viewport = document.documentElement.clientWidth;
  const navLinks = [...document.querySelectorAll('.navbar a')].map(a => {
    const r = a.getBoundingClientRect();
    return {text: a.textContent.trim(), left: r.left, right: r.right};
  });
  return {viewport, scrollWidth: document.documentElement.scrollWidth, navLinks};
}"""


def _make_long_label_plan() -> SeasonPlan:
    """Return a U10 plan whose club, team, and arena labels are long enough to stress narrow layouts."""
    long_club = "Kongsberg Ishockeyklubb Ungdom"
    other_club = "Sandefjord Penguins Hockeyklubb"
    teams = [
        {"club": long_club, "label": f"{long_club} U10 Lag Hvit", "age_group": "U10"},
        {"club": other_club, "label": f"{other_club} U10 Lag Svart", "age_group": "U10"},
    ]
    plan_dict = {
        "start_date": "2025-10-01",
        "end_date": "2025-12-01",
        "diversity_score": 1.0,
        "pairwise_matchup_score": 1.0,
        "month_balance_score": 1.0,
        "arena_counts": {"Kongsberghallen Nordre Ishall Hovedarena": 1},
        "fairness_gate": {"status": "pass", "score": 100, "metrics": []},
        "tournaments": [
            {
                "date": "2025-10-05",
                "arena": "Kongsberghallen Nordre Ishall Hovedarena",
                "age_group": "U10",
                "host_club": long_club,
                "teams": teams,
                "games": [
                    {
                        "home": teams[0]["label"],
                        "away": teams[1]["label"],
                        "parallel_slot": 0,
                        "round_number": 1,
                    }
                ],
                "start_time": "09:00",
            }
        ],
    }
    return season_plan_from_dict(plan_dict)


def _export_season_plan(tmp_path: Path) -> Path:
    """Export the plan with every optional navbar target present so the navbar is fully populated."""
    companions = {}
    for name in ("calendars.html", "input.html", "manual_schedule.html", "season_changes.html"):
        companion = tmp_path / name
        companion.write_text("<!DOCTYPE html><html></html>", encoding="utf-8")
        companions[name] = companion
    out_path = tmp_path / "season_plan.html"
    HtmlExporter().export(
        _make_long_label_plan(),
        out_path,
        age_groups=["U10"],
        calendars_path=str(companions["calendars.html"]),
        input_html_path=str(companions["input.html"]),
        manual_schedule_path=str(companions["manual_schedule.html"]),
        changes_path=str(companions["season_changes.html"]),
    )
    return out_path


@pytest.fixture(scope="module")
def chromium():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:  # missing browser binary or sandbox limits
            pytest.skip(f"Chromium unavailable for season plan layout check: {exc}")
        yield browser
        browser.close()


@pytest.mark.parametrize("width", [*MOBILE_WIDTHS, DESKTOP_WIDTH])
def test_season_plan_has_no_page_level_horizontal_overflow(chromium, tmp_path, width):
    season_plan = _export_season_plan(tmp_path)

    page = chromium.new_page(viewport={"width": width, "height": 800})
    try:
        page.goto(season_plan.as_uri())
        layout = page.evaluate(_LAYOUT_JS)
    finally:
        page.close()

    assert layout["scrollWidth"] <= layout["viewport"], (
        f"document is {layout['scrollWidth']}px wide in a {layout['viewport']}px viewport"
    )
    assert layout["navLinks"], "navbar links should be rendered on the season plan page"
    for link in layout["navLinks"]:
        assert link["left"] >= 0 and link["right"] <= layout["viewport"], (
            f"navbar link {link['text']!r} is outside the {layout['viewport']}px viewport"
        )
