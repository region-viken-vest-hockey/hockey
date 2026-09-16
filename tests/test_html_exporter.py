"""Tests asserting that the consolidated hero section is correct after
removing the separate 'Min ærlige dom' judgment section."""

from __future__ import annotations

import json
import re
from pathlib import Path


from tournament_scheduler.html.html_exporter import HtmlExporter
from tournament_scheduler.models import SeasonPlan
from tournament_scheduler.serialization.season_plan import season_plan_from_dict


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_minimal_plan() -> SeasonPlan:
    """Return a minimal SeasonPlan with one U10 tournament."""
    plan_dict = {
        "start_date": "2025-10-01",
        "end_date": "2025-12-01",
        "diversity_score": 1.0,
        "pairwise_matchup_score": 1.0,
        "month_balance_score": 1.0,
        "arena_counts": {"Kongsberghallen": 1},
        "fairness_gate": {
            "status": "pass",
            "score": 100,
            "metrics": [
                {
                    "label": "Kamper per lag",
                    "value": 0,
                    "threshold": 2,
                    "status": "pass",
                    "score": 100,
                    "unit": "",
                    "detail": "Lik kampfordeling.",
                },
            ],
        },
        "tournaments": [
            {
                "date": "2025-10-05",
                "arena": "Kongsberghallen",
                "age_group": "U10",
                "host_club": "Kongsberg",
                "teams": [
                    {"club": "Kongsberg", "label": "Kongsberg U10A", "age_group": "U10"},
                    {"club": "Skien", "label": "Skien U10A", "age_group": "U10"},
                ],
                "games": [
                    {
                        "home": "Kongsberg U10A",
                        "away": "Skien U10A",
                        "parallel_slot": 0,
                        "round_number": 1,
                    }
                ],
                "start_time": "09:00",
            }
        ],
    }
    return season_plan_from_dict(plan_dict)


def _export_report_html(tmp_path: Path) -> str:
    """Export a minimal plan and return the report HTML string."""
    plan = _make_minimal_plan()
    exporter = HtmlExporter()
    out_path = tmp_path / "season_plan.html"
    exporter.export(plan, out_path, age_groups=["U10"])
    report_path = tmp_path / "season_plan_report.html"
    return report_path.read_text(encoding="utf-8")


def _make_multi_kongsberg_plan() -> SeasonPlan:
    """Return a plan with multiple Kongsberg U10 teams in separate tournaments."""
    teams = [
        {"club": "Kongsberg", "label": "Kongsberg 1", "age_group": "U10"},
        {"club": "Kongsberg", "label": "Kongsberg 2", "age_group": "U10"},
        {"club": "Kongsberg", "label": "Kongsberg 3", "age_group": "U10"},
        {"club": "Skien", "label": "Skien 1", "age_group": "U10"},
    ]
    plan_dict = {
        "start_date": "2025-10-01",
        "end_date": "2025-12-01",
        "diversity_score": 1.0,
        "pairwise_matchup_score": 1.0,
        "month_balance_score": 1.0,
        "arena_counts": {"Kongsberghallen": 2},
        "fairness_gate": {"status": "pass", "score": 100, "metrics": []},
        "tournaments": [
            {
                "date": "2025-10-05",
                "arena": "Kongsberghallen",
                "age_group": "U10",
                "host_club": "Kongsberg",
                "teams": [teams[0], teams[3]],
                "games": [{"home": "Kongsberg 1", "away": "Skien 1", "parallel_slot": 0, "round_number": 1}],
            },
            {
                "date": "2025-10-12",
                "arena": "Kongsberghallen",
                "age_group": "U10",
                "host_club": "Kongsberg",
                "teams": [teams[1], teams[3]],
                "games": [{"home": "Kongsberg 2", "away": "Skien 1", "parallel_slot": 0, "round_number": 1}],
            },
            {
                "date": "2025-10-19",
                "arena": "Kongsberghallen",
                "age_group": "U10",
                "host_club": "Kongsberg",
                "teams": [teams[2], teams[3]],
                "games": [{"home": "Kongsberg 3", "away": "Skien 1", "parallel_slot": 0, "round_number": 1}],
            },
        ],
    }
    return season_plan_from_dict(plan_dict)


def _export_schedule_html(plan: SeasonPlan, tmp_path: Path) -> str:
    exporter = HtmlExporter()
    out_path = tmp_path / "season_plan.html"
    exporter.export(plan, out_path, age_groups=["U10"])
    return out_path.read_text(encoding="utf-8")


def _embedded_tournaments(html: str) -> list[dict]:
    match = re.search(r"const TOURNAMENTS = (.*?);\nconst TEAM_GAME_COUNTS", html, re.S)
    assert match, "TOURNAMENTS JSON should be embedded in schedule HTML"
    return json.loads(match.group(1))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTeamFilter:
    """The season schedule exposes exact team filtering for club review."""

    def test_schedule_has_team_filter(self, tmp_path):
        html = _export_schedule_html(_make_multi_kongsberg_plan(), tmp_path)
        assert 'id="filterTeam"' in html
        assert "Alle lag" in html

    def test_serializes_exact_participant_identities_for_same_club_same_age_group(self, tmp_path):
        html = _export_schedule_html(_make_multi_kongsberg_plan(), tmp_path)
        tournaments = _embedded_tournaments(html)
        identities = {
            (team["c"], team["g"], team["l"])
            for tournament in tournaments
            for team in tournament["p"]
        }
        assert ("Kongsberg", "U10", "Kongsberg 1") in identities
        assert ("Kongsberg", "U10", "Kongsberg 2") in identities
        assert ("Kongsberg", "U10", "Kongsberg 3") in identities

    def test_client_filter_uses_exact_team_identity_not_club_inference(self, tmp_path):
        html = _export_schedule_html(_make_multi_kongsberg_plan(), tmp_path)
        assert "function teamKey(team)" in html
        assert "tournamentHasTeam(t, team)" in html
        assert "teamKey(team) === selectedTeamKey" in html
        assert "populateTeamOptions(true)" in html


class TestNoJudgmentSection:
    """The separate 'Min ærlige dom' section must not appear in the report."""

    def test_old_section_heading_absent(self, tmp_path):
        html = _export_report_html(tmp_path)
        assert "Min ærlige dom" not in html, (
            "The old judgment section heading should not appear in the report HTML"
        )

    def test_old_section_id_absent(self, tmp_path):
        html = _export_report_html(tmp_path)
        assert 'id="opinionatedJudgment"' not in html, (
            "The old opinionatedJudgment section id should not appear in the report HTML"
        )

    def test_report_judgment_placeholder_not_in_output(self, tmp_path):
        html = _export_report_html(tmp_path)
        assert "$REPORT_JUDGMENT$" not in html, (
            "$REPORT_JUDGMENT$ placeholder should be fully substituted or removed"
        )


class TestHeroSectionRemoved:
    """The 'Kort svar'/'Kan planen brukes?' hero must not appear in the report.

    Removed at the user's request: the hero's yes/no verdict wasn't useful,
    and the report now starts directly at the rules table.
    """

    def test_hero_class_absent(self, tmp_path):
        html = _export_report_html(tmp_path)
        assert 'class="report-hero' not in html, "Hero div should no longer be rendered"

    def test_hero_verdict_language_absent(self, tmp_path):
        html = _export_report_html(tmp_path)
        assert "Kort svar" not in html
        assert "Kan planen brukes?" not in html
        assert "Ja — planen kan brukes" not in html
        assert "Nei — planen bør stoppes" not in html

    def test_report_overview_starts_at_rules_section(self, tmp_path):
        html = _export_report_html(tmp_path)
        assert html.index('id="reportOverview"') < html.index('id="rulesTable"')
        assert html.index('id="rulesTable"') - html.index('id="reportOverview"') < 200, (
            "Nothing but the rules section should follow reportOverview's opening tag"
        )


class TestJudgmentCardsRemoved:
    """The judgment cards (and their toggle) lived inside the removed hero and
    must not appear in the report either."""

    def test_no_judgment_cards_present(self, tmp_path):
        html = _export_report_html(tmp_path)
        assert 'class="judgment-card"' not in html, "Judgment cards unexpectedly found in report HTML"

    def test_toggle_absent(self, tmp_path):
        html = _export_report_html(tmp_path)
        assert 'class="judgment-toggle"' not in html
        assert 'Vis hvorfor' not in html


class TestReglerPageSimplification:
    """issue #305: season_plan_report.html becomes a focused Regler view."""

    def test_navbar_label_is_regler(self, tmp_path):
        html = _export_report_html(tmp_path)
        assert 'Regler</a>' in html
        assert 'Rapport</a>' not in html

    def test_page_title_and_heading_are_regler(self, tmp_path):
        html = _export_report_html(tmp_path)
        assert "<title>Regler" in html
        assert "<h1>Regler" in html

    def test_percentage_quality_score_removed(self, tmp_path):
        html = _export_report_html(tmp_path)
        assert "Hvor god er planen?" not in html
        assert "keyMetrics" not in html

    def test_duplicate_summary_sections_removed(self, tmp_path):
        html = _export_report_html(tmp_path)
        for removed_id in (
            "ageGroupSummary",
            "clubReviewSummary",
            "tournamentReviewTable",
            "ruleTransparency",
            "advisoryChecks",
            "detailedDiagnosticsIntro",
            "detaljerAccordion",
        ):
            assert f'id="{removed_id}"' not in html, f"{removed_id} should have been removed"

    def test_rules_sections_present(self, tmp_path):
        html = _export_report_html(tmp_path)
        assert "Hard krav" in html
        assert "Påkrevde forpliktelser" in html
        assert "Myke kvalitetsmål" in html

    def test_compact_summary_present(self, tmp_path):
        html = _export_report_html(tmp_path)
        assert "Harde krav:" in html
        assert "Uløste forpliktelser:" in html
        assert "Myke kvalitetsvarsler:" in html


class TestRulesModelBugFixes:
    """issue #305: concrete wording/semantics fixes on the Regler page."""

    def test_arena_collision_wording_describes_intervals_not_same_day(self, tmp_path):
        # The fixed wording itself is covered at the fairness_scoring unit
        # level (test_fairness_scoring_wording.py); here we only assert the
        # old, incorrect same-day-ban wording never reaches the page.
        html = _export_report_html(tmp_path)
        assert "Ingen dobbeltbooking av samme arena samme dag" not in html

    def test_participation_target_split_into_hard_and_obligation(self, tmp_path):
        html = _export_report_html(tmp_path)
        assert "Deltakelsesmål må ikke overskrides" in html
        assert "Lag under sitt mål for antall turneringsdeltakelser" in html
