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


# ---------------------------------------------------------------------------
# Chronological presentation ordering
# ---------------------------------------------------------------------------

def _plan_with_tournaments(specs: list[dict]) -> SeasonPlan:
    """Build a plan with tournaments in exactly the given (possibly shuffled) order."""
    tournaments = []
    for spec in specs:
        teams = spec.get(
            "teams",
            [
                {"club": spec.get("host_club", "Kongsberg"), "label": "Vert U10A", "age_group": spec.get("age_group", "U10")},
                {"club": "Skien", "label": "Skien U10A", "age_group": spec.get("age_group", "U10")},
            ],
        )
        tournaments.append(
            {
                "id": spec["id"],
                "date": spec["date"],
                "arena": spec.get("arena", "Kongsberghallen"),
                "age_group": spec.get("age_group", "U10"),
                "host_club": spec.get("host_club", "Kongsberg"),
                "start_time": spec.get("start_time"),
                "cancelled": spec.get("cancelled", False),
                "requires_host_confirmation": spec.get("requires_host_confirmation", False),
                "teams": teams,
                "games": [
                    {
                        "home": teams[0]["label"],
                        "away": teams[1]["label"],
                        "parallel_slot": 0,
                        "round_number": 1,
                    }
                ],
            }
        )
    plan_dict = {
        "start_date": "2026-10-01",
        "end_date": "2027-03-31",
        "diversity_score": 1.0,
        "pairwise_matchup_score": 1.0,
        "month_balance_score": 1.0,
        "arena_counts": {},
        "fairness_gate": {"status": "pass", "score": 100, "metrics": []},
        "tournaments": tournaments,
    }
    return season_plan_from_dict(plan_dict)


def _team_key(team: dict) -> str:
    return "\u001f".join([team.get("c") or "", team.get("g") or "", team.get("l") or ""])


def _filter_embedded(tournaments: list[dict], *, age: str = "", club: str = "", team: str = "") -> list[dict]:
    """Mirror the browser filter predicates so ordering can be asserted in Python."""

    def has_club(t: dict) -> bool:
        if not club:
            return True
        if t.get("h") == club:
            return True
        return any((p.get("c") or "") == club for p in t.get("p", []))

    def has_team(t: dict) -> bool:
        if not team:
            return True
        return any(_team_key(p) == team for p in t.get("p", []))

    result = []
    for t in tournaments:
        if age and t.get("g") != age:
            continue
        if not has_club(t):
            continue
        if not has_team(t):
            continue
        result.append(t)
    return result


class TestChronologicalTournamentOrdering:
    """season_plan.html must not depend on incidental plan.tournaments order."""

    def test_shuffled_input_renders_chronologically(self, tmp_path):
        shuffled = [
            {"id": "t-mar", "date": "2027-03-14"},
            {"id": "t-nov", "date": "2026-11-08"},
            {"id": "t-jan", "date": "2027-01-10"},
            {"id": "t-oct", "date": "2026-10-18"},
        ]
        html = _export_schedule_html(_plan_with_tournaments(shuffled), tmp_path)
        dates = [t["d"] for t in _embedded_tournaments(html)]
        assert dates == ["2026-10-18", "2026-11-08", "2027-01-10", "2027-03-14"]

    def test_dec_sorts_before_jan_and_jan_before_mar_of_next_year(self, tmp_path):
        specs = [
            {"id": "t-mar", "date": "2027-03-14"},
            {"id": "t-jan", "date": "2027-01-10"},
            {"id": "t-dec", "date": "2026-12-05"},
        ]
        html = _export_schedule_html(_plan_with_tournaments(specs), tmp_path)
        dates = [t["d"] for t in _embedded_tournaments(html)]
        assert dates.index("2026-12-05") < dates.index("2027-01-10") < dates.index("2027-03-14")

    def test_filtered_results_stay_chronological(self, tmp_path):
        specs = [
            {"id": "t1", "date": "2027-03-14", "age_group": "U10", "host_club": "Kongsberg"},
            {"id": "t2", "date": "2026-11-08", "age_group": "U11", "host_club": "Skien"},
            {"id": "t3", "date": "2027-01-10", "age_group": "U10", "host_club": "Skien"},
            {"id": "t4", "date": "2026-10-18", "age_group": "U10", "host_club": "Kongsberg"},
        ]
        tournaments = _embedded_tournaments(_export_schedule_html(_plan_with_tournaments(specs), tmp_path))

        for kwargs in ({"age": "U10"}, {"club": "Kongsberg"}, {"age": "U10", "club": "Kongsberg"}):
            filtered = _filter_embedded(tournaments, **kwargs)
            dates = [t["d"] for t in filtered]
            assert dates == sorted(dates), f"filter {kwargs} broke chronological order: {dates}"

        team_key = _team_key(tournaments[-1]["p"][0])
        filtered = _filter_embedded(tournaments, team=team_key)
        dates = [t["d"] for t in filtered]
        assert dates == sorted(dates)

    def test_same_date_order_is_deterministic(self, tmp_path):
        specs = [
            {"id": "b", "date": "2026-11-08", "start_time": "10:00", "age_group": "U10", "arena": "Alfa"},
            {"id": "c", "date": "2026-11-08", "start_time": "09:00", "age_group": "U10", "arena": "Beta"},
            {"id": "a", "date": "2026-11-08", "start_time": "09:00", "age_group": "U10", "arena": "Alfa"},
        ]
        plan = _plan_with_tournaments(specs)
        first = [t["id"] for t in _embedded_tournaments(_export_schedule_html(plan, tmp_path))]
        second = [t["id"] for t in _embedded_tournaments(_export_schedule_html(plan, tmp_path))]
        assert first == ["a", "c", "b"]
        assert first == second

    def test_cancelled_and_confirmation_required_use_same_date_order(self, tmp_path):
        specs = [
            {"id": "later", "date": "2027-03-14"},
            {"id": "cancelled", "date": "2026-11-08", "cancelled": True},
            {"id": "confirm", "date": "2026-10-18", "requires_host_confirmation": True},
        ]
        html = _export_schedule_html(_plan_with_tournaments(specs), tmp_path)
        tournaments = _embedded_tournaments(html)
        ids = [t["id"] for t in tournaments]
        assert ids == ["confirm", "cancelled", "later"]
        assert tournaments[0].get("rhc") is True
        assert tournaments[1].get("cx") is True

    def test_browser_has_defensive_chronological_sort(self, tmp_path):
        html = _export_schedule_html(_plan_with_tournaments([{"id": "x", "date": "2026-10-18"}]), tmp_path)
        assert "function compareTournaments(a, b)" in html
        assert "TOURNAMENTS.sort(compareTournaments)" in html
