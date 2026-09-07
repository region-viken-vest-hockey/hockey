from datetime import date

from tournament_scheduler.models import SeasonPlan
from tournament_scheduler.pipeline.stage4_export import _manual_schedule_html


def test_manual_schedule_uses_complete_navbar_and_persisted_theme_toggle() -> None:
    plan = SeasonPlan(start_date=date(2026, 9, 1), end_date=date(2027, 4, 30))

    html = _manual_schedule_html(
        plan,
        calendars_href="calendars.html",
        season_plan_href="season_plan.html",
        report_href="season_plan_report.html",
        input_href="input.html",
    )

    assert 'href="calendars.html"' in html
    assert 'href="season_plan.html"' in html
    assert 'href="season_plan_report.html"' in html
    assert 'href="manual_schedule.html" class="active"' in html
    assert 'href="input.html"' in html
    assert 'id="themeToggle"' in html
    assert "localStorage.getItem('rvv-theme')" in html
    assert "localStorage.setItem(THEME_KEY, next)" in html
