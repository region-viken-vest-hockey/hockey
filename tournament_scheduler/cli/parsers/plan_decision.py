"""Plan decision-context, decide and problem commands (Stage 3 optimizer as an LLM-directed decision)."""

from __future__ import annotations

import argparse


def add_plan_decision_parsers(plan_sub: argparse._SubParsersAction) -> None:
    # plan decision-context / plan decide — Stage 3 optimizer as an
    # LLM-directed decision (issue #260 Phase 4). Turns a "plan ab"/
    # "plan ab-participants" report into a DecisionContext, and validates +
    # records + (when accepted) executes the LLM's chosen DecisionAction,
    # instead of a Python quality heuristic deciding automatically.
    plan_decision_context = plan_sub.add_parser(
        "decision-context",
        help="Build the DecisionContext for an old-vs-new Stage 3 A/B report, for an "
        "LLM/agent controller to choose apply_candidate/keep_baseline/optimize_plan/"
        "request_operator (issue #260 Phase 4)",
    )
    plan_decision_context.add_argument(
        "ab_report",
        help="Path to an ab_report.json written by 'plan ab'/'plan ab-participants' "
        "(--output-dir)",
    )
    plan_decision_context.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory, used to default --run-id from the active run "
        "manifest (default: .pipeline)",
    )
    plan_decision_context.add_argument(
        "--run-id",
        default=None,
        help="Run id to stamp on the DecisionContext; defaults to the active run "
        "manifest's run_id",
    )
    plan_decision_context.add_argument(
        "--baseline-ref",
        default=None,
        help="Reference (e.g. path) to the baseline candidate, carried through unchanged",
    )
    plan_decision_context.add_argument(
        "--candidate-ref",
        default=None,
        help="Reference (e.g. path) to the new candidate, carried through unchanged",
    )
    plan_decision_context.add_argument(
        "--objective",
        default=None,
        help="Override the default decision objective text",
    )
    plan_decision_context.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write the DecisionContext JSON to this path instead of stdout",
    )

    plan_decide = plan_sub.add_parser(
        "decide",
        help="Validate, record, and (when accepted) execute a DecisionAction against an "
        "old-vs-new Stage 3 A/B report (issue #260 Phase 4)",
    )
    plan_decide.add_argument(
        "ab_report",
        help="Path to an ab_report.json written by 'plan ab'/'plan ab-participants' "
        "(--output-dir)",
    )
    plan_decide.add_argument(
        "--action",
        required=False,
        default=None,
        choices=["apply_candidate", "keep_baseline", "optimize_plan", "request_operator"],
        help="The DecisionAction to validate and execute. Required unless --decision-action/"
        "--decision-action-file is given instead",
    )
    plan_decide.add_argument(
        "--decision-action",
        default=None,
        metavar="JSON",
        help="Inline JSON DecisionAction (e.g. '{\"action_id\": \"optimize_plan\", \"arguments\": "
        "{\"iterations\": 8000, \"weights\": {\"gap_under_7\": 8.0}}}'), an alternative to "
        "--action/--target/--candidate/--question that lets the LLM/agent pass genuinely "
        "parameterized optimize_plan search settings in one structured payload instead of "
        "CLI flags choosing them (issue #260 P1). Overrides --action and friends when given",
    )
    plan_decide.add_argument(
        "--decision-action-file",
        default=None,
        metavar="PATH",
        help="Path to a JSON file containing the DecisionAction, alternative to --decision-action",
    )
    plan_decide.add_argument(
        "--rationale",
        default=None,
        help="Concise rationale to record for this decision (never chain-of-thought)",
    )
    plan_decide.add_argument(
        "--target",
        default=None,
        help="Optional DecisionAction target (e.g. age group), recorded for audit",
    )
    plan_decide.add_argument(
        "--candidate",
        default=None,
        help="Path to the new candidate.json; required for --action apply_candidate, which "
        "writes it into the Stage 3 checkpoint's plan. For --action optimize_plan, an "
        "optional starting point to re-optimize from (defaults to the Stage 3 "
        "checkpoint's baseline)",
    )
    plan_decide.add_argument(
        "--question",
        default=None,
        help="Question to record for --action request_operator",
    )
    plan_decide.add_argument(
        "--output-dir",
        default=None,
        help="For --action optimize_plan: write old_candidate.json/new_candidate.json/"
        "ab_report.json from the re-run to this directory, so the result can be fed "
        "back into 'plan decision-context' (required for optimize_plan)",
    )
    plan_decide.add_argument(
        "--problem",
        default=None,
        help="For --action optimize_plan: path to a planning_problem.json (falls back to "
        "inferring capacity from the candidate's own games)",
    )
    plan_decide.add_argument(
        "--iterations",
        type=int,
        default=4000,
        help="For --action optimize_plan: simulated-annealing swap attempts (default: 4000)",
    )
    plan_decide.add_argument(
        "--seed",
        type=int,
        default=0,
        help="For --action optimize_plan: random seed for reproducible output (default: 0)",
    )
    plan_decide.add_argument(
        "--weight",
        action="append",
        dest="weights",
        default=None,
        metavar="NAME=VALUE",
        help="For --action optimize_plan: override one objective weight (e.g. "
        "--weight gap_under_7=8.0), or just one age group's weight (e.g. "
        "--weight JU12:same_club_pairing=1.5); repeatable",
    )
    plan_decide.add_argument(
        "--move-dates",
        action="store_true",
        help="For --action optimize_plan: also let the search swap two same-age-group "
        "tournaments' dates, not just teams between tournaments. Off by default",
    )
    plan_decide.add_argument(
        "--engine",
        choices=["local-search", "cp-sat"],
        default="local-search",
        help="For --action optimize_plan: search engine to re-run (issue #276 Phase 1). "
        "'local-search' (default) or 'cp-sat' (requires OR-Tools)",
    )
    plan_decide.add_argument(
        "--solve-budget-seconds",
        type=float,
        default=30.0,
        help="For --action optimize_plan with --engine cp-sat: bounded wall-clock solve "
        "budget in seconds (default: 30.0)",
    )
    plan_decide.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory whose Stage 3 checkpoint/run manifest this decision "
        "applies to (default: .pipeline)",
    )
    plan_decide.add_argument(
        "--run-id",
        default=None,
        help="Run id to stamp on the DecisionContext; defaults to the active run "
        "manifest's run_id",
    )
    plan_decide.add_argument(
        "--baseline-ref",
        default=None,
        help="Reference (e.g. path) to the baseline candidate, carried through unchanged",
    )
    plan_decide.add_argument(
        "--candidate-ref",
        default=None,
        help="Reference (e.g. path) to the new candidate, carried through unchanged "
        "(defaults to --candidate for apply_candidate)",
    )
    plan_decide.add_argument(
        "--objective",
        default=None,
        help="Override the default decision objective text",
    )
    plan_decide.add_argument(
        "--json",
        action="store_true",
        help="Also print the DecisionResult as JSON",
    )

    plan_problem = plan_sub.add_parser(
        "problem",
        help="Emit a normalized planning_problem.json from the Stage 1/2 checkpoints",
    )
    plan_problem.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    plan_problem.add_argument(
        "--start-date",
        default=None,
        help="Season start date (YYYY-MM-DD); defaults to the Stage 3 checkpoint's plan window",
    )
    plan_problem.add_argument(
        "--end-date",
        default=None,
        help="Season end date (YYYY-MM-DD); defaults to the Stage 3 checkpoint's plan window",
    )
    plan_problem.add_argument(
        "--output",
        default=None,
        help="Write the planning_problem.json to this path instead of stdout",
    )
