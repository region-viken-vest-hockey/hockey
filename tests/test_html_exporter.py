"""Tests asserting that the consolidated hero section is correct after
removing the separate 'Min ærlige dom' judgment section."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


from tournament_scheduler.html import CANCELLED_TOURNAMENTS_FILENAME
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


def _embedded_heatmap(html: str) -> dict:
    match = re.search(r"const HEATMAP = (.*?);\nconst HEATMAP_WEEKS", html, re.S)
    assert match, "HEATMAP JSON should be embedded in schedule HTML"
    return json.loads(match.group(1))


def _heatmap_items_by_id(html: str) -> dict[str, dict]:
    heatmap = _embedded_heatmap(html)
    return {
        item["tournament_id"]: item
        for week in heatmap.values()
        for club in week.values()
        for item in club
    }


_SHARED_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "tournament_scheduler"
    / "html"
    / "templates"
    / "script_shared.js"
)
_SCHEDULE_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "tournament_scheduler"
    / "html"
    / "templates"
    / "script_schedule.js"
)


def _shared_operational_helpers_js() -> str:
    """Extract the shared operational-state helpers from the shipped bundle."""

    source = _SHARED_TEMPLATE.read_text(encoding="utf-8")
    names = ("operationalStateOf", "operationalStateLabel", "bookingStatusLabel", "escapeHtml")
    parts = []
    for name in names:
        match = re.search(rf"function {name}\([^)]*\) \{{.*?\n\}}", source, re.S)
        assert match, f"{name} must exist in the shared template"
        parts.append(match.group(0))
    return "\n".join(parts) + "\n"


def _heatmap_render_js() -> str:
    """Extract the shared heatmap renderer (helpers + IIFE) for a Node run."""

    source = _SHARED_TEMPLATE.read_text(encoding="utf-8")
    start = source.index("// --- Shared operational booking vocabulary")
    end = source.index("(function() {\n  var THEME_KEY", start)
    return source[start:end]


def _render_heatmap(
    tournaments: list[dict],
    heatmap: dict,
    *,
    clubs: list[str],
    weeks: list[str],
    colors: dict,
    theme: str = "dark",
) -> str:
    """Run the shipped heatmap renderer against a minimal DOM stub.

    Returns the rendered body HTML joined with every element the renderer
    created (legend spans), so tests can assert on both sinks.
    """

    node = shutil.which("node")
    if node is None:
        pytest.fail("node is required to execute the shipped heatmap template")

    script = (
        "const TOURNAMENTS = "
        + json.dumps(tournaments)
        + ";\nconst HEATMAP_WEEKS = "
        + json.dumps(weeks)
        + ";\nconst HEATMAP_CLUBS = "
        + json.dumps(clubs)
        + ";\nconst HEATMAP = "
        + json.dumps(heatmap)
        + ";\nconst HEATMAP_CLUB_COLORS_BY_THEME = "
        + json.dumps(colors)
        + ";\n"
        "var __bodyHtml = '';\n"
        "var __created = [];\n"
        "function __stubEl() { return {style: {}, appendChild: function() {}, innerHTML: '', className: '', textContent: ''}; }\n"
        "var document = {documentElement: {dataset: {theme: '"
        + theme
        + "'}},\n"
        "  getElementById: function(id) {\n"
        "    if (id === 'heatmapHead') return {innerHTML: ''};\n"
        "    if (id === 'heatmapBody') return {set innerHTML(v) {__bodyHtml = v;}, get innerHTML() {return __bodyHtml;}};\n"
        "    if (id === 'heatmapLegend') return {appendChild: function() {}};\n"
        "    return null;\n"
        "  },\n"
        "  createElement: function() { var el = __stubEl(); __created.push(el); return el; }\n"
        "};\n"
        + _heatmap_render_js()
        + "\nconsole.log(JSON.stringify({body: __bodyHtml, created: __created.map(function(e) {"
        "return {className: e.className, textContent: e.textContent, innerHTML: e.innerHTML, cssText: e.style.cssText}; })}));\n"
    )
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    parts = [data["body"]]
    for element in data["created"]:
        parts.extend(
            [
                str(element.get("className") or ""),
                str(element.get("textContent") or ""),
                str(element.get("innerHTML") or ""),
                str(element.get("cssText") or ""),
            ]
        )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBookingStatusRendering:
    def test_schedule_exposes_booking_status_filters_cards_and_heatmap_items(self, tmp_path):
        plan_dict = {
            "start_date": "2025-10-01",
            "end_date": "2025-12-01",
            "tournaments": [
                {
                    "id": "t-booked",
                    "date": "2025-10-05",
                    "arena": "Kongsberghallen",
                    "age_group": "U10",
                    "host_club": "Kongsberg",
                    "teams": [
                        {"club": "Kongsberg", "label": "K1", "age_group": "U10"},
                        {"club": "Skien", "label": "S1", "age_group": "U10"},
                    ],
                    "games": [{"home": "K1", "away": "S1", "parallel_slot": 0, "round_number": 1}],
                    "start_time": "09:00",
                },
                {
                    "id": "t-missing",
                    "date": "2025-10-05",
                    "arena": "Kongsberghallen",
                    "age_group": "U12",
                    "host_club": "Kongsberg",
                    "teams": [
                        {"club": "Kongsberg", "label": "K2", "age_group": "U12"},
                        {"club": "Skien", "label": "S2", "age_group": "U12"},
                    ],
                    "games": [{"home": "K2", "away": "S2", "parallel_slot": 0, "round_number": 1}],
                    "start_time": "11:00",
                },
            ],
        }
        plan = season_plan_from_dict(plan_dict)
        exporter = HtmlExporter()
        out_path = tmp_path / "season_plan.html"
        exporter.export(
            plan,
            out_path,
            age_groups=["U10", "U12"],
            pipeline_meta={
                "booking_status": {
                    "counts": {"confirmed_booked": 1, "confirmed_not_booked": 1, "unknown": 0, "needs_attention": 1},
                    "tournaments": [
                        {"tournament_id": "t-booked", "status": "confirmed_booked", "needs_attention": False},
                        {"tournament_id": "t-missing", "status": "confirmed_not_booked", "needs_attention": True},
                    ],
                }
            },
        )
        html = out_path.read_text(encoding="utf-8")
        embedded = {row["id"]: row for row in _embedded_tournaments(html)}
        assert embedded["t-booked"]["bs"] == "confirmed_booked"
        assert embedded["t-missing"]["ba"] is True
        assert 'id="filterBooking"' in html
        assert "IKKE BOOKET" in html
        # The heatmap exposes only operational states, never the weaker raw
        # detailed status, and shares the cards' legend vocabulary.
        assert "heatmap-booking-action_required" in html
        assert "må følges opp" in html
        assert "booket · låst" in html
        assert "heatmap-booking-confirmed_not_booked" not in html

    def test_heatmap_primary_state_matches_card_operational_state(self, tmp_path):
        """The heatmap's top-level cell state must equal the card's ``obs``.

        The heatmap must never fall back to the raw detailed ``status`` (e.g.
        ``manually_booked`` or ``ambiguous``) as a competing primary state.
        """

        cases = [
            # (id, detailed status, operational state, needs_attention, assessment)
            ("t-manual", "manually_booked", "booked", False, None),
            ("t-ambiguous", "ambiguous", "not_booked", True, None),
            ("t-stale", "stale", "action_required", True, None),
            ("t-reject", "manually_not_booked", "action_required", True, None),
            ("t-changed", "unknown", "changed_slot_review", True, "proposed_changed_slot"),
            ("t-presumed", "unknown", "presumed_unscheduled", False, "presumed_unscheduled"),
            ("t-unknown", "not_checkable", "unknown", False, "not_checkable"),
        ]
        tournaments = []
        bookings = []
        for index, (tid, status, operational, attention, assessment) in enumerate(cases):
            tournaments.append(
                {
                    "id": tid,
                    "date": f"2025-10-{index + 5:02d}",
                    "arena": "Arena A",
                    "age_group": "U10",
                    "host_club": "A",
                    "teams": [
                        {"club": "A", "label": "A1", "age_group": "U10"},
                        {"club": "B", "label": "B1", "age_group": "U10"},
                    ],
                    "games": [{"home": "A1", "away": "B1", "parallel_slot": 0, "round_number": 1}],
                    "start_time": "10:00",
                }
            )
            row = {
                "tournament_id": tid,
                "status": status,
                "operational_state": operational,
                "needs_attention": attention,
            }
            if assessment:
                row["booking_assessment_classification"] = assessment
            bookings.append(row)

        plan_dict = {
            "start_date": "2025-10-01",
            "end_date": "2025-12-01",
            "tournaments": tournaments,
        }
        out_path = tmp_path / "season_plan.html"
        HtmlExporter().export(
            season_plan_from_dict(plan_dict),
            out_path,
            age_groups=["U10"],
            pipeline_meta={"booking_status": {"tournaments": bookings}},
        )
        html = out_path.read_text(encoding="utf-8")
        cards = {row["id"]: row for row in _embedded_tournaments(html)}
        items = _heatmap_items_by_id(html)

        assert set(items) == set(cards)
        for tid, item in items.items():
            card = cards[tid]
            assert item["operational_state"] == card["obs"], tid
            assert item["booking_status"] == card["bs"], tid
            assert item["needs_attention"] == card["ba"], tid
        # A manual email-only confirmation is booked/locked, not a weaker
        # heatmap "manually_booked" state.
        assert items["t-manual"]["operational_state"] == "booked"
        assert items["t-manual"]["booking_status"] == "manually_booked"
        assert items["t-reject"]["operational_state"] == "action_required"

    def test_heatmap_renders_operational_states_and_escapes_text(self):
        """Run the shipped heatmap renderer against a minimal DOM stub.

        Dynamic age-group/club text must reach the HTML sink escaped, and the
        emitted classes must be the shared operational states.
        """

        hostile = '<img src=x onerror=alert(1)>'
        tournaments = [
            {"id": "t1", "obs": "booked", "bs": "manually_booked", "ba": False},
            {"id": "t2", "obs": "action_required", "bs": "stale", "ba": True},
        ]
        heatmap = {
            "2025-W40": {
                "Holmen": [
                    {
                        "age_group": hostile,
                        "tournament_id": "t1",
                        "operational_state": "booked",
                        "booking_status": "manually_booked",
                        "needs_attention": False,
                    },
                    {
                        "age_group": "U12",
                        "tournament_id": "t2",
                        "operational_state": "action_required",
                        "booking_status": "stale",
                        "needs_attention": True,
                    },
                ]
            }
        }
        colors = {
            "dark": {"Holmen": {"bg": "#111111", "text": "#ffffff"}},
            "light": {"Holmen": {"bg": "#eeeeee", "text": "#111111"}},
        }
        rendered = _render_heatmap(
            tournaments, heatmap, clubs=["Holmen"], weeks=["2025-W40"], colors=colors
        )
        assert "&lt;img src=x onerror=alert(1)&gt;" in rendered
        assert "<img" not in rendered
        assert "heatmap-booking-booked" in rendered
        assert "heatmap-booking-action_required" in rendered
        assert "BOOKET · LÅST" in rendered
        assert "MÅ RE-BEKREFTES" in rendered
        assert "må følges opp" in rendered

    def test_heatmap_rejects_invalid_club_colors(self):
        """Club colours are CSS, not HTML: constrain them to the hex format."""

        tournaments = [{"id": "t1", "obs": "booked", "bs": "manually_booked", "ba": False}]
        heatmap = {
            "2025-W40": {
                "Holmen": [
                    {
                        "age_group": "U10",
                        "tournament_id": "t1",
                        "operational_state": "booked",
                        "booking_status": "manually_booked",
                        "needs_attention": False,
                    }
                ]
            }
        }
        colors = {
            "dark": {
                "Holmen": {
                    "bg": "#zzzzzz",
                    "text": "red; background-image:url(javascript:alert(1))",
                }
            },
            "light": {"Holmen": {"bg": "#eeeeee", "text": "#111111"}},
        }
        rendered = _render_heatmap(
            tournaments, heatmap, clubs=["Holmen"], weeks=["2025-W40"], colors=colors
        )
        assert "#zzzzzz" not in rendered
        assert "javascript:alert" not in rendered
        assert "background-image" not in rendered
        # Invalid values fall back to the theme default colours.
        assert "#2a2a2a" in rendered
        assert "#999" in rendered

    def test_schedule_booking_provenance_and_negative_evidence_states(self, tmp_path):
        def _tournament(tid, age_group, start_time):
            return {
                "id": tid,
                "date": "2025-10-05",
                "arena": "Askerhallen",
                "age_group": age_group,
                "host_club": "Frisk Asker",
                "teams": [
                    {"club": "Frisk Asker", "label": f"{age_group}1", "age_group": age_group},
                    {"club": "Skien", "label": f"S{age_group}", "age_group": age_group},
                ],
                "games": [
                    {
                        "home": f"{age_group}1",
                        "away": f"S{age_group}",
                        "parallel_slot": 0,
                        "round_number": 1,
                    }
                ],
                "start_time": start_time,
            }

        plan_dict = {
            "start_date": "2025-10-01",
            "end_date": "2025-12-01",
            "tournaments": [
                _tournament("t-changed", "U10", "10:00"),
                _tournament("t-absent", "U12", "12:00"),
            ],
        }
        exporter = HtmlExporter()
        out_path = tmp_path / "season_plan.html"
        exporter.export(
            season_plan_from_dict(plan_dict),
            out_path,
            age_groups=["U10", "U12"],
            pipeline_meta={
                "booking_status": {
                    "counts": {"changed_slot_review": 1, "presumed_unscheduled": 1},
                    "tournaments": [
                        {
                            "tournament_id": "t-changed",
                            "status": "unknown",
                            "operational_state": "changed_slot_review",
                            "booking_assessment_classification": "proposed_changed_slot",
                            "canonical_interval": {"date": "2025-10-05", "start_time": "10:00", "end_time": "12:00"},
                            "latest_observed_interval": {
                                "date": "2025-10-05",
                                "start": "13:00",
                                "end": "15:00",
                                "title": "Miniputt U10",
                                "actionable": True,
                            },
                        },
                        {
                            "tournament_id": "t-absent",
                            "status": "unknown",
                            "operational_state": "presumed_unscheduled",
                            "booking_assessment_classification": "presumed_unscheduled",
                            "canonical_interval": {"date": "2025-10-05", "start_time": "12:00", "end_time": "14:00"},
                            "negative_evidence": {
                                "reason": "complete_trusted_calendar_window_without_plausible_match",
                                "source_event_count": 3,
                            },
                        },
                    ],
                }
            },
        )
        html = out_path.read_text(encoding="utf-8")
        embedded = {row["id"]: row for row in _embedded_tournaments(html)}

        assert embedded["t-changed"]["obs"] == "changed_slot_review"
        assert embedded["t-changed"]["bac"] == "proposed_changed_slot"
        assert embedded["t-changed"]["boi"]["s"] == "13:00"
        assert embedded["t-absent"]["obs"] == "presumed_unscheduled"
        assert embedded["t-absent"]["bne"]["c"] == 3
        # The compact export key for the observed interval is `boi`; a negative
        # row must not carry it.
        assert "boi" not in embedded["t-absent"]
        assert '<option value="changed_slot_review">' in html
        assert '<option value="presumed_unscheduled">' in html
        assert "ENDRET TID" in html
        assert "TROLIG IKKE SATT OPP" in html

    def test_schedule_embeds_distinct_approval_and_booking_states(self, tmp_path):
        plan_dict = {
            "start_date": "2025-10-01",
            "end_date": "2025-12-01",
            "tournaments": [
                {
                    "id": "holmen-booked",
                    "date": "2025-11-01",
                    "arena": "Holmen ishall",
                    "age_group": "U11",
                    "host_club": "Holmen",
                    "teams": [
                        {"club": "Holmen", "label": "H1", "age_group": "U11"},
                        {"club": "Jar", "label": "J1", "age_group": "U11"},
                    ],
                    "games": [{"home": "H1", "away": "J1", "parallel_slot": 0, "round_number": 1}],
                    "start_time": "13:30",
                },
                {
                    "id": "sandefjord-pending",
                    "date": "2025-11-02",
                    "arena": "Sandefjord ishall",
                    "age_group": "U8",
                    "host_club": "Sandefjord Penguins",
                    "teams": [
                        {"club": "Sandefjord Penguins", "label": "SP1", "age_group": "U8"},
                        {"club": "Skien", "label": "S1", "age_group": "U8"},
                    ],
                    "games": [{"home": "SP1", "away": "S1", "parallel_slot": 0, "round_number": 1}],
                    "start_time": "09:00",
                },
            ],
        }
        exporter = HtmlExporter()
        out_path = tmp_path / "season_plan.html"
        exporter.export(
            season_plan_from_dict(plan_dict),
            out_path,
            age_groups=["U8", "U11"],
            pipeline_meta={
                "approval_status": {
                    "tournaments": [
                        {"tournament_id": "holmen-booked", "status": "approved", "placement_locked": True},
                        {"tournament_id": "sandefjord-pending", "status": "approved", "placement_locked": True},
                    ]
                },
                "booking_status": {
                    "tournaments": [
                        {
                            "tournament_id": "holmen-booked",
                            "status": "manually_booked",
                            "operational_state": "booked",
                            "operational_lock": True,
                            "authority": "manual_club_confirmation",
                        },
                        {
                            "tournament_id": "sandefjord-pending",
                            "status": "ambiguous",
                            "operational_state": "not_booked",
                            "operational_lock": False,
                            "needs_attention": False,
                        },
                    ]
                },
            },
        )
        html = out_path.read_text(encoding="utf-8")
        embedded = {row["id"]: row for row in _embedded_tournaments(html)}
        assert "booket · låst" in html
        assert "GODKJENT' + (t.apl ? ' · LÅST' : '')" not in html
        assert embedded["holmen-booked"]["ap"] == "approved"
        assert embedded["holmen-booked"]["apl"] is True
        assert embedded["holmen-booked"]["obs"] == "booked"
        assert embedded["holmen-booked"]["bs"] == "manually_booked"
        assert embedded["holmen-booked"]["obl"] is True
        assert embedded["sandefjord-pending"]["ap"] == "approved"
        assert embedded["sandefjord-pending"]["obs"] == "not_booked"
        assert embedded["sandefjord-pending"]["bs"] == "ambiguous"
        assert embedded["sandefjord-pending"]["obl"] is False

    def test_schedule_embeds_manual_queue_work_item(self, tmp_path):
        plan_dict = {
            "start_date": "2025-10-01",
            "end_date": "2025-12-01",
            "tournaments": [
                {
                    "id": "t-reject",
                    "date": "2025-11-01",
                    "arena": "Holmen ishall",
                    "age_group": "U11",
                    "host_club": "Holmen",
                    "teams": [
                        {"club": "Holmen", "label": "H1", "age_group": "U11"},
                        {"club": "Jar", "label": "J1", "age_group": "U11"},
                    ],
                    "games": [{"home": "H1", "away": "J1", "parallel_slot": 0, "round_number": 1}],
                    "start_time": "13:30",
                }
            ],
        }
        out_path = tmp_path / "season_plan.html"
        HtmlExporter().export(
            season_plan_from_dict(plan_dict),
            out_path,
            age_groups=["U11"],
            pipeline_meta={
                "booking_status": {
                    "tournaments": [
                        {
                            "tournament_id": "t-reject",
                            "status": "manually_not_booked",
                            "operational_state": "action_required",
                            "needs_attention": True,
                            "manual_work": {
                                "reason_code": "explicit_rejection",
                                "owner": "Holmen",
                                "action": "book_or_reconfirm",
                                "source": {"reference": "email:1", "note": "host rejected the slot"},
                                "proposed_alternatives": [
                                    {
                                        "date": "2025-11-01",
                                        "start": "14:00",
                                        "end": "16:00",
                                        "title": "Miniputt U11",
                                        "relation": "same_date_time_shift",
                                        "event_fingerprint": "fp-1",
                                        "covers_current_interval": False,
                                    }
                                ],
                                "resolution": {
                                    "clears_when": "accepted_booking_assertion_or_valid_calendar_association",
                                },
                            },
                        }
                    ],
                }
            },
        )
        html = out_path.read_text(encoding="utf-8")
        embedded = {row["id"]: row for row in _embedded_tournaments(html)}
        queue_item = embedded["t-reject"]["bq"]
        assert queue_item["r"] == "explicit_rejection"
        assert queue_item["o"] == "Holmen"
        assert queue_item["ac"] == "book_or_reconfirm"
        assert "src" not in queue_item
        # Event titles are free-form and stay in the private report; the public
        # queue carries only the observed interval.
        assert queue_item["alt"] == [
            {"d": "2025-11-01", "s": "14:00", "e": "16:00"}
        ]
        assert queue_item["cw"] == "accepted_booking_assertion_or_valid_calendar_association"
        # The public plan must never carry the private source evidence or the
        # calendar event title that sit next to the work item in the report.
        assert "email:1" not in html
        assert "host rejected the slot" not in html
        assert "Miniputt U11" not in html

    def test_legacy_payload_fallback_prefers_accepted_confirmation(self):
        """A legacy payload without ``obs`` must not let retained provisional
        metadata demote an accepted confirmation.

        The fallback lives in the shipped template, so evaluate the real
        function with Node instead of re-implementing its precedence here.
        """

        node = shutil.which("node")
        if node is None:
            pytest.fail("node is required to execute the shipped template fallback")

        source = _SHARED_TEMPLATE.read_text(encoding="utf-8")
        match = re.search(r"function operationalStateOf\(t\) \{.*?\n\}", source, re.S)
        assert match, "operationalStateOf must exist in the shipped shared template"

        cases = [
            # Accepted confirmation wins over retained provisional metadata.
            ("accepted_plus_manual_reason", {"bs": "manually_booked", "mb": "provisional"}, "booked"),
            ("accepted_plus_host_flag", {"bs": "confirmed_booked", "rhc": True}, "booked"),
            # An explicit canonical state still wins when present.
            ("obs_wins", {"obs": "not_booked", "bs": "manually_booked", "mb": "provisional"}, "not_booked"),
            # Without a confirmation the provisional flags are manual work.
            ("manual_without_confirmation", {"mb": "provisional"}, "action_required"),
            ("host_flag_without_confirmation", {"rhc": True, "bs": "ambiguous"}, "action_required"),
            ("rejected", {"bs": "manually_not_booked"}, "action_required"),
            ("missing", {}, ""),
        ]
        script = (
            match.group(0)
            + "\nconst cases = "
            + json.dumps(cases)
            + ";\nconsole.log(JSON.stringify(cases.map(function(c){return [c[0], operationalStateOf(c[1]), c[2]];})));\n"
        )
        completed = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
        for name, got, expected in json.loads(completed.stdout):
            assert got == expected, f"{name}: expected {expected!r}, got {got!r}"

    def test_booking_details_render_manual_queue_work_item(self):
        """The shipped template renders the manual-queue reason, source and action."""

        node = shutil.which("node")
        if node is None:
            pytest.fail("node is required to execute the shipped template fallback")

        source = _SCHEDULE_TEMPLATE.read_text(encoding="utf-8")
        start = source.index("function bookingAuthorityLabel")
        end = source.index("\nfunction render()", start)
        script = _shared_operational_helpers_js() + source[start:end] + """
var item = {
  obs: 'action_required',
  bs: 'manually_not_booked',
  ba: true,
  bq: {
    r: 'explicit_rejection',
    o: 'Holmen',
    ac: 'book_or_reconfirm',
    cw: 'accepted_booking_assertion_or_valid_calendar_association',
    alt: [{d: '2025-11-01', s: '14:00', e: '16:00', t: 'Private club name'}]
  }
};
console.log(buildBookingDetails(item));
"""
        completed = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
        rendered = completed.stdout
        assert "Manuell kø:" in rendered
        assert "avvist av vert" in rendered
        assert "Ansvarlig:" in rendered
        assert "Holmen" in rendered
        assert "2025-11-01 14:00" in rendered
        # A hand-built/legacy payload title must never be rendered publicly.
        assert "Private club name" not in rendered
        assert "Kalenderobservasjoner" in rendered
        assert "Neste handling:" in rendered
        assert "booking-set" in rendered

    def test_booking_details_escapes_markup_like_values(self):
        """Markup-like host/calendar text must render as inert text, not HTML."""

        node = shutil.which("node")
        if node is None:
            pytest.fail("node is required to execute the shipped template fallback")

        source = _SCHEDULE_TEMPLATE.read_text(encoding="utf-8")
        start = source.index("function bookingAuthorityLabel")
        end = source.index("\nfunction render()", start)
        script = _shared_operational_helpers_js() + source[start:end] + """
var item = {
  obs: 'action_required',
  bs: 'manually_not_booked',
  ba: true,
  mb: '<img src=x onerror=alert(1)>',
  hcr: '</li><script>alert(2)</script>',
  rhc: true,
  bq: {
    r: 'explicit_rejection',
    o: '<img src=x onerror=alert(3)>',
    ac: 'book_or_reconfirm',
    cw: 'accepted_booking_assertion_or_valid_calendar_association',
    alt: [{d: '<b>2025-11-01</b>', s: '14:00', e: '16:00', t: '<b>evil</b>'}]
  }
};
console.log(buildBookingDetails(item));
"""
        completed = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
        rendered = completed.stdout
        assert "<img" not in rendered
        assert "<script" not in rendered
        assert "<b>" not in rendered
        assert "&lt;img" in rendered
        assert "&lt;script" in rendered
        # Interval values are escaped too; the omitted title never renders.
        assert "&lt;b&gt;2025-11-01&lt;/b&gt;" in rendered
        assert "evil" not in rendered


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
                "cancellation_reason": spec.get("cancellation_reason", ""),
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

    def test_cancelled_leaves_active_timeline_but_keeps_its_order_on_cancelled_page(self, tmp_path):
        specs = [
            {"id": "later", "date": "2027-03-14"},
            {"id": "cancelled", "date": "2026-11-08", "cancelled": True},
            {"id": "confirm", "date": "2026-10-18", "requires_host_confirmation": True},
        ]
        html = _export_schedule_html(_plan_with_tournaments(specs), tmp_path)
        active = _embedded_tournaments(html)
        assert [t["id"] for t in active] == ["confirm", "later"]
        assert active[0].get("rhc") is True

        cancelled_html = (tmp_path / CANCELLED_TOURNAMENTS_FILENAME).read_text(encoding="utf-8")
        cancelled = _embedded_tournaments(cancelled_html)
        assert [t["id"] for t in cancelled] == ["cancelled"]
        assert cancelled[0].get("cx") is True

    def test_browser_has_defensive_chronological_sort(self, tmp_path):
        html = _export_schedule_html(_plan_with_tournaments([{"id": "x", "date": "2026-10-18"}]), tmp_path)
        assert "function compareTournaments(a, b)" in html
        assert "TOURNAMENTS.sort(compareTournaments)" in html


class TestCancelledTournamentSplit:
    """Cancelled tournaments stay canonical but leave the active season page."""

    def _mixed_plan(self) -> SeasonPlan:
        return _plan_with_tournaments(
            [
                {"id": "active-1", "date": "2026-10-18", "age_group": "U10", "host_club": "Kongsberg"},
                {
                    "id": "cancelled-1",
                    "date": "2026-11-08",
                    "age_group": "U11",
                    "host_club": "Skien",
                    "cancelled": True,
                    "cancellation_reason": "Regionalt sperret helg",
                },
            ]
        )

    def _export(self, tmp_path: Path):
        out_path = tmp_path / "season_plan.html"
        HtmlExporter().export(self._mixed_plan(), out_path, age_groups=["U10", "U11"])
        return out_path, tmp_path / CANCELLED_TOURNAMENTS_FILENAME

    def test_active_page_excludes_cancelled_from_payload_and_counts(self, tmp_path):
        out_path, _ = self._export(tmp_path)
        html = out_path.read_text(encoding="utf-8")
        assert [t["id"] for t in _embedded_tournaments(html)] == ["active-1"]
        # The cancelled id must not leak into the active page payload/options.
        assert "cancelled-1" not in html
        assert '<strong id="totalTournaments">1</strong> turneringer' in html
        assert "av <strong>1</strong> turneringer" in html

    def test_cancelled_page_renders_reason_and_navigation(self, tmp_path):
        out_path, cancelled_path = self._export(tmp_path)
        assert cancelled_path.exists()
        cancelled_html = cancelled_path.read_text(encoding="utf-8")
        cancelled = _embedded_tournaments(cancelled_html)
        assert [t["id"] for t in cancelled] == ["cancelled-1"]
        assert cancelled[0]["cx"] is True
        assert cancelled[0]["cr"] == "Regionalt sperret helg"
        assert "Regionalt sperret helg" in cancelled_html
        assert 'href="season_plan.html"' in cancelled_html

        html = out_path.read_text(encoding="utf-8")
        assert f'href="{CANCELLED_TOURNAMENTS_FILENAME}"' in html
        assert "Avlyste turneringer" in html

        # The dedicated history view must expose the canonical tournament id,
        # which the active cards do not show. It is rendered client-side from
        # the shipped template, so assert the template contract here.
        schedule_js = (
            Path(__file__).resolve().parents[1]
            / "tournament_scheduler"
            / "html"
            / "templates"
            / "script_schedule.js"
        ).read_text(encoding="utf-8")
        assert "t.cx ? '<span class=\"tag tag--id\">ID: '" in schedule_js

    def test_no_cancelled_page_or_nav_link_when_all_active(self, tmp_path):
        plan = _plan_with_tournaments([{"id": "only", "date": "2026-10-18"}])
        out_path = tmp_path / "season_plan.html"
        HtmlExporter().export(plan, out_path, age_groups=["U10"])
        assert not (tmp_path / CANCELLED_TOURNAMENTS_FILENAME).exists()
        html = out_path.read_text(encoding="utf-8")
        assert "Avlyste turneringer" not in html
        assert CANCELLED_TOURNAMENTS_FILENAME not in html

    def test_stale_cancelled_page_removed_when_none_remain(self, tmp_path):
        self._export(tmp_path)
        assert (tmp_path / CANCELLED_TOURNAMENTS_FILENAME).exists()
        plan = _plan_with_tournaments([{"id": "only", "date": "2026-10-18"}])
        HtmlExporter().export(plan, tmp_path / "season_plan.html", age_groups=["U10"])
        assert not (tmp_path / CANCELLED_TOURNAMENTS_FILENAME).exists()

    def test_cancelled_only_plan_renders_empty_active_page(self, tmp_path):
        plan = _plan_with_tournaments(
            [{"id": "cancelled-only", "date": "2026-11-08", "cancelled": True, "cancellation_reason": "Avlyst"}]
        )
        out_path = tmp_path / "season_plan.html"
        HtmlExporter().export(plan, out_path, age_groups=None)
        assert _embedded_tournaments(out_path.read_text(encoding="utf-8")) == []
        cancelled_html = (tmp_path / CANCELLED_TOURNAMENTS_FILENAME).read_text(encoding="utf-8")
        assert [t["id"] for t in _embedded_tournaments(cancelled_html)] == ["cancelled-only"]


class TestSeasonChangesNav:
    """The season export links to the change-request page only when it exists."""

    def test_changes_nav_link_present_when_page_exists(self, tmp_path: Path):
        from tournament_scheduler.html import SEASON_CHANGES_FILENAME

        changes_path = tmp_path / SEASON_CHANGES_FILENAME
        changes_path.write_text("<h1>Forespurte endringer</h1>", encoding="utf-8")
        out_path = tmp_path / "season_plan.html"
        HtmlExporter().export(
            _make_minimal_plan(), out_path, age_groups=["U10"], changes_path=str(changes_path)
        )
        html = out_path.read_text(encoding="utf-8")
        assert f'href="{SEASON_CHANGES_FILENAME}"' in html
        assert "Forespurte endringer" in html

    def test_changes_nav_link_absent_without_page(self, tmp_path: Path):
        from tournament_scheduler.html import SEASON_CHANGES_FILENAME

        out_path = tmp_path / "season_plan.html"
        HtmlExporter().export(_make_minimal_plan(), out_path, age_groups=["U10"])
        html = out_path.read_text(encoding="utf-8")
        assert SEASON_CHANGES_FILENAME not in html
        assert "Forespurte endringer" not in html


class TestSharedNavbarStatus:
    """The shared navbar status must combine plan-local and source counts."""

    def _export(self, tmp_path: Path, pipeline_meta: dict) -> str:
        plan = _make_minimal_plan()
        out_path = tmp_path / "season_plan.html"
        HtmlExporter().export(plan, out_path, pipeline_meta=pipeline_meta, age_groups=["U10"])
        return out_path.read_text(encoding="utf-8")

    def test_navbar_shows_plan_counts_and_source_counts(self, tmp_path):
        html = self._export(
            tmp_path,
            {
                "generated_at": "2026-09-18T13:56:09+00:00",
                "scrape_updated_at": "2026-09-18T13:56:09+00:00",
                "source_count": 9,
                "total_events": 8038,
            },
        )
        assert "1 turneringer" in html
        assert "1 kamper" in html
        assert "2 lag" in html
        assert "9 kilder" in html
        assert "8038 hendelser" in html

    def test_navbar_omits_zero_source_counts(self, tmp_path):
        html = self._export(tmp_path, {"generated_at": "2026-09-18T13:56:09+00:00"})
        assert "1 turneringer" in html
        assert "0 kilder" not in html
        assert "0 hendelser" not in html
