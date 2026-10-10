"""Plan verify, score and optimize commands."""

from __future__ import annotations

import argparse


def add_plan_verify_parsers(plan_sub: argparse._SubParsersAction) -> None:
    plan_verify = plan_sub.add_parser(
        "verify",
        help="Check a candidate plan against hard planning requirements",
    )
    plan_verify.add_argument(
        "candidate",
        help="Path to a candidate.json, or a Stage 3 checkpoint file containing a 'plan' key",
    )
    plan_verify.add_argument(
        "--problem",
        default=None,
        help="Path to a planning_problem.json for full hard-constraint checks "
        "(registered teams, calendar validity, participation targets, manual restrictions). "
        "Without it, only self-consistency checks run.",
    )
    plan_verify.add_argument(
        "--json",
        action="store_true",
        help="Print the verification result as JSON instead of a human-readable report",
    )

    plan_score = plan_sub.add_parser(
        "score",
        help="Compute deterministic quality metrics for a candidate plan",
    )
    plan_score.add_argument(
        "candidate",
        help="Path to a candidate.json, or a Stage 3 checkpoint file containing a 'plan' key",
    )
    plan_score.add_argument(
        "--problem",
        default=None,
        help="Path to a planning_problem.json to also report club x age-group hosting "
        "coverage (issue #266). Without it, hosting metrics only include the "
        "club-level counts/spread.",
    )
    plan_score.add_argument(
        "--json",
        action="store_true",
        help="Print the score report as JSON instead of a human-readable summary",
    )

    plan_optimize = plan_sub.add_parser(
        "optimize",
        help="Generic local-search repair pass over a candidate's existing tournament "
        "skeleton (issue #257 Stage 3 v2 optimizer, explicit opt-in — never run implicitly)",
    )
    plan_optimize.add_argument(
        "candidate",
        help="Path to a candidate.json, or a Stage 3 checkpoint file containing a 'plan' key",
    )
    plan_optimize.add_argument(
        "--problem",
        default=None,
        help="Path to a planning_problem.json (used to size tournaments per age group; "
        "falls back to inferring capacity from the candidate's own games)",
    )
    plan_optimize.add_argument(
        "--iterations",
        type=int,
        default=4000,
        help="Simulated-annealing swap attempts (default: 4000)",
    )
    plan_optimize.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Random seed for reproducible output (default: 0)",
    )
    plan_optimize.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write the optimized candidate.json to this path instead of stdout",
    )
    plan_optimize.add_argument(
        "--weight",
        action="append",
        dest="weights",
        default=None,
        metavar="NAME=VALUE",
        help="Override one objective weight (e.g. --weight gap_under_7=8.0), or just one "
        "age group's weight (e.g. --weight JU12:same_club_pairing=1.5); repeatable. "
        "See stage3_optimizer.DEFAULT_WEIGHTS for the tunable names",
    )
    plan_optimize.add_argument(
        "--move-dates",
        action="store_true",
        help="Also let the search swap two same-age-group tournaments' dates (arena/host/"
        "teams stay put), not just teams between tournaments. Off by default — the "
        "skeleton (dates/arenas/hosts) is otherwise taken as given",
    )
    plan_optimize.add_argument(
        "--engine",
        choices=["local-search", "cp-sat"],
        default="local-search",
        help="Search engine to use (issue #276 Phase 1): 'local-search' (default, existing "
        "simulated-annealing repair pass) or 'cp-sat' (fixed-skeleton participant-only "
        "shadow engine — requires OR-Tools; --iterations/--weight/--move-dates are ignored)",
    )
    plan_optimize.add_argument(
        "--solve-budget-seconds",
        type=float,
        default=30.0,
        help="For --engine cp-sat: bounded wall-clock solve budget in seconds (default: 30.0)",
    )
