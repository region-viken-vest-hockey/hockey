#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

python3 - <<'PY'
from datetime import datetime
from pathlib import Path

from tournament_scheduler.models import CalendarEvent
from tournament_scheduler.rules_report import render_rules_markdown
from tournament_scheduler.testing.canonical_input import build_canonical_planner, load_canonical_input_data


canonical_input = load_canonical_input_data()
clubs = sorted({team["club"] for team in canonical_input["teams"]})
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
    rounds_per_tournament_for_age_group=canonical_input.get("rounds_per_tournament", {}),
    # Reproducible snapshot: do not read ambient `.pipeline` run state.
    use_plan_cache=False,
    build_plan=True,
)

Path("docs/rvv-miniputt-rules-report.md").write_text(render_rules_markdown(planner), encoding="utf-8")
PY

python3 -m pytest tests/test_rules_report_doc.py tests/test_season_planner.py -q
