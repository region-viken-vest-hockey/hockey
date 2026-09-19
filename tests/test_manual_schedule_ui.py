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
    assert "document.documentElement.dataset.theme = saved === 'dark' ? 'dark' : 'light';" in html
    assert "localStorage.setItem(THEME_KEY, next)" in html


def test_participation_section_separates_intra_club_distribution_from_shortfalls() -> None:
    """A multi-team club's aggregate-complete label imbalance is rendered as
    informational evidence, not as unresolved manual planning work."""
    from tournament_scheduler.pipeline.stage4_export_manual_schedule import (
        _participation_section_html,
    )

    entries = [
        {
            "club": "Jar",
            "label": "Jar Blå",
            "age_group": "JU10",
            "half": "after_christmas",
            "actual": "3",
            "target": "4",
            "category": "participation_under_target",
            "club_pool_classification": "intra_club_distribution",
            "counts_as_unresolved_shortfall": False,
            "club_pool": {
                "registered_team_count": 2,
                "club_pool_actual": 8,
                "club_pool_target": 8,
                "classification": "intra_club_distribution",
            },
        },
        {
            "club": "Kongsberg",
            "label": "Kongsberg 1",
            "age_group": "JU10",
            "half": "after_christmas",
            "actual": "3",
            "target": "4",
            "category": "participation_under_target",
            "club_pool_classification": "single_team_deviation",
            "counts_as_unresolved_shortfall": True,
        },
    ]

    html = _participation_section_html(entries)

    assert "Fordeling mellom lag i samme klubb" in html
    assert "intern fordeling mellom lagene" in html
    assert "Klubb-pool" in html
    assert "8/8" in html
    # Both the actionable and the informational rows stay visible.
    assert "Kongsberg 1" in html
    assert "Jar Blå" in html
