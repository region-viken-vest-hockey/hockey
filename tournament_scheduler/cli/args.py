"""Command-line parser composition for the RVV Miniputt CLI.

This module is the navigation point for parser construction. Each command family
owns its argparse definitions in ``cli/parsers``; this file only creates the
top-level command groups and calls the family owners in the order they are
registered, which fixes the order commands appear in ``--help``.

Family owners:

- status, sources, calendars: ``parsers/sources.py``
- registered-teams, activities: ``parsers/publication.py``
- run: ``parsers/pipeline.py``; logs: ``parsers/pipeline.py``
- operator: ``parsers/operator_run.py``, ``parsers/operator_gates.py``, ``parsers/operator_audit.py``
- registrations, scrape, scrape-llm, recovery, recovery-inject, scrape-merge: ``parsers/intake.py``
- season: ``parsers/season_*.py`` (lifecycle, booking, mutation, constraints, guest, review, repair)
- stage3: ``parsers/stage3.py``
- cancel, replan, adjust, review, tournament: ``parsers/schedule_change.py``
- critic, auto-adjust, verdict, candidates: ``parsers/critique.py``
- plan: ``parsers/plan_verify.py``, ``parsers/plan_ab.py``, ``parsers/plan_decision.py``
- waiver: ``parsers/waiver.py``
- export-parity: ``parsers/export_parity.py``
"""

from __future__ import annotations

import argparse

from .parsers.export_parity import add_export_parity_parser
from .parsers.operator_audit import add_operator_audit_subparsers
from .parsers.intake import add_intake_parsers
from .parsers.pipeline import add_logs_parser, add_run_parser
from .parsers.publication import add_publication_parsers
from .parsers.sources import add_source_health_parsers
from .parsers.operator_gates import add_operator_gate_parsers
from .parsers.operator_run import add_operator_run_parsers
from .parsers.season_booking import add_season_booking_parsers
from .parsers.season_constraints import add_season_constraint_parsers
from .parsers.season_guest import add_season_guest_parsers
from .parsers.season_lifecycle import add_season_lifecycle_parsers
from .parsers.season_mutation import add_season_mutation_parsers
from .parsers.season_repair import add_season_repair_parsers
from .parsers.season_review import add_season_review_parsers
from .parsers.stage3 import add_stage3_parsers
from .parsers.schedule_change import add_schedule_change_parsers
from .parsers.critique import add_critique_parsers
from .parsers.plan_verify import add_plan_verify_parsers
from .parsers.plan_ab import add_plan_ab_parsers
from .parsers.plan_decision import add_plan_decision_parsers
from .parsers.waiver import add_waiver_parsers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rvv-miniputt",
        description="RVV Miniputt — tournament scheduler pipeline CLI",
    )
    sub = parser.add_subparsers(dest="command", title="commands")

    add_source_health_parsers(sub)
    add_publication_parsers(sub)
    add_run_parser(sub)

    # operator — the goal-oriented AI operator entry point (see docs/ai-operator-product-direction.md)
    operator = sub.add_parser(
        "operator",
        help="Goal-oriented AI operator commands (thin wrapper around the portable pipeline)",
    )
    operator_sub = operator.add_subparsers(dest="operator_command")

    add_operator_run_parsers(operator_sub)
    add_operator_gate_parsers(operator_sub)
    add_operator_audit_subparsers(operator_sub)

    add_logs_parser(sub)
    add_intake_parsers(sub)

    # season — canonical Git-backed promoted season state
    season = sub.add_parser(
        "season",
        help="Promote, inspect, replan and export canonical Git-backed season state",
    )
    season_sub = season.add_subparsers(dest="season_command")
    add_season_lifecycle_parsers(season_sub)
    add_season_booking_parsers(season_sub)
    add_season_mutation_parsers(season_sub)
    add_season_constraint_parsers(season_sub)
    add_season_guest_parsers(season_sub)
    add_season_review_parsers(season_sub)
    add_season_repair_parsers(season_sub)

    add_stage3_parsers(sub)
    add_schedule_change_parsers(sub)
    add_critique_parsers(sub)

    # plan — harness-neutral candidate verification/scoring (issue #257)
    plan = sub.add_parser(
        "plan",
        help="Deterministically verify/score a Stage 3 candidate plan, independent of SeasonPlanner",
    )
    plan_sub = plan.add_subparsers(dest="plan_command", title="plan commands")
    add_plan_verify_parsers(plan_sub)
    add_plan_ab_parsers(plan_sub)
    add_plan_decision_parsers(plan_sub)

    add_waiver_parsers(sub)
    add_export_parity_parser(sub)

    return parser
