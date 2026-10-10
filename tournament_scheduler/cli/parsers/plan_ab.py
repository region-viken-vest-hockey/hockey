"""Plan old-vs-new benchmark commands: ab and ab-participants."""

from __future__ import annotations

import argparse


def add_plan_ab_parsers(plan_sub: argparse._SubParsersAction) -> None:
    plan_ab = plan_sub.add_parser(
        "ab",
        help="Full-season old-vs-new benchmark: baseline SeasonPlanner (Stage 3 checkpoint) vs. "
        "the Stage 3 v2 optimizer, from the same normalized planning problem (issue #257)",
    )
    plan_ab.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory containing Stage 1-3 checkpoints (default: .pipeline)",
    )
    plan_ab.add_argument(
        "--start-date",
        default=None,
        help="Season start date (YYYY-MM-DD); defaults to the Stage 3 checkpoint's plan window",
    )
    plan_ab.add_argument(
        "--end-date",
        default=None,
        help="Season end date (YYYY-MM-DD); defaults to the Stage 3 checkpoint's plan window",
    )
    plan_ab.add_argument(
        "--iterations",
        type=int,
        default=4000,
        help="Simulated-annealing swap attempts for the new candidate (default: 4000)",
    )
    plan_ab.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Random seed for the new candidate (default: 0)",
    )
    plan_ab.add_argument(
        "--output-dir",
        default=None,
        help="Write old_candidate.json/new_candidate.json/ab_report.json to this directory",
    )
    plan_ab.add_argument(
        "--json",
        action="store_true",
        help="Print the A/B report as JSON instead of a human-readable summary",
    )
    plan_ab.add_argument(
        "--weight",
        action="append",
        dest="weights",
        default=None,
        metavar="NAME=VALUE",
        help="Override one optimizer objective weight for the new candidate "
        "(e.g. --weight gap_under_7=8.0), or just one age group's weight "
        "(e.g. --weight JU12:same_club_pairing=1.5); repeatable. See "
        "stage3_optimizer.DEFAULT_WEIGHTS for the tunable names",
    )
    plan_ab.add_argument(
        "--move-dates",
        action="store_true",
        help="Also let the new candidate's search swap two same-age-group tournaments' "
        "dates, not just teams between tournaments. Off by default",
    )
    plan_ab.add_argument(
        "--engine",
        choices=["local-search", "cp-sat"],
        default="local-search",
        help="Search engine to use for the new candidate (issue #276 Phase 1): 'local-search' "
        "(default) or 'cp-sat' (fixed-skeleton participant-only shadow engine — requires "
        "OR-Tools; --iterations/--weight/--move-dates are ignored)",
    )
    plan_ab.add_argument(
        "--solve-budget-seconds",
        type=float,
        default=30.0,
        help="For --engine cp-sat: bounded wall-clock solve budget in seconds (default: 30.0)",
    )

    plan_ab_participants = plan_sub.add_parser(
        "ab-participants",
        help="Full-season baseline-bounded participant-optimization benchmark: for each age "
        "group independently, keep the Stage 3 checkpoint's assignment unless a seeded "
        "search finds a strict, non-regressing improvement (issue #257 Task 2-4)",
    )
    plan_ab_participants.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory containing Stage 1-3 checkpoints (default: .pipeline)",
    )
    plan_ab_participants.add_argument(
        "--start-date",
        default=None,
        help="Season start date (YYYY-MM-DD); defaults to the Stage 3 checkpoint's plan window",
    )
    plan_ab_participants.add_argument(
        "--end-date",
        default=None,
        help="Season end date (YYYY-MM-DD); defaults to the Stage 3 checkpoint's plan window",
    )
    plan_ab_participants.add_argument(
        "--seeds",
        default="1,2,3,4,5",
        help="Comma-separated list of seeds/restarts to try per age group (default: 1,2,3,4,5)",
    )
    plan_ab_participants.add_argument(
        "--iterations",
        type=int,
        default=4000,
        help="Simulated-annealing swap attempts per seed per age group (default: 4000)",
    )
    plan_ab_participants.add_argument(
        "--output-dir",
        default=None,
        help="Write problem.json/old_candidate.json/new_candidate.json/ab_report.json to this directory",
    )
    plan_ab_participants.add_argument(
        "--json",
        action="store_true",
        help="Print the A/B report as JSON instead of a human-readable summary",
    )
    plan_ab_participants.add_argument(
        "--repair-schedule",
        action="store_true",
        default=True,
        help="Also run the baseline-bounded date-swap-only schedule-conflict repair pass "
        "after participant optimization, to fix hard violations like arena_interval_conflict "
        "that a participant-only optimizer cannot (default: on)",
    )
    plan_ab_participants.add_argument(
        "--no-repair-schedule",
        action="store_false",
        dest="repair_schedule",
        help="Skip the schedule-conflict repair pass; only optimize team assignments",
    )
