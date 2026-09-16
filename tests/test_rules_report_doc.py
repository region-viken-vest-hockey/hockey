from datetime import datetime
from pathlib import Path

import pytest

from tournament_scheduler import rules_report as rules_report_module
from tournament_scheduler.models import CalendarEvent
from tournament_scheduler.rules_report import render_rules_markdown
from tournament_scheduler.testing.canonical_input import build_canonical_planner



@pytest.mark.slow
def test_rules_report_markdown_matches_committed_doc(canonical_input_data):
    clubs = sorted({team["club"] for team in canonical_input_data["teams"]})
    planner, _, _ = build_canonical_planner(
        events_by_club={
            clubs[0]: [
                CalendarEvent(
                    date="01.10.2026",
                    name=f"{clubs[0]} hallbooking",
                    datetime=datetime(2026, 10, 1, 11, 0),
                    duration_hours=2.0,
                )
            ]
        },
        rounds_per_tournament_for_age_group=canonical_input_data.get("rounds_per_tournament", {}),
        # The committed markdown is a reproducible snapshot: never derive it
        # from ambient `.pipeline` run state (generated, machine-specific).
        use_plan_cache=False,
        build_plan=True,
    )

    expected = Path("docs/rvv-miniputt-rules-report.md").read_text(encoding="utf-8")
    generated = render_rules_markdown(planner)

    assert generated == expected


def test_render_rules_markdown_uses_structured_report(monkeypatch):
    marker = "Unique planner-derived rule"

    def fake_rules_report(planner):
        assert planner == "planner-sentinel"
        return [
            {
                "regel": marker,
                "forklaring": "contains | a pipe",
                "kategori": "Advarsel",
            }
        ]

    monkeypatch.setattr(rules_report_module, "rules_report", fake_rules_report)

    generated = render_rules_markdown("planner-sentinel")

    assert marker in generated
    assert "contains \\| a pipe" in generated
