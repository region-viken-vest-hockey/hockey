"""Pipeline run and run-log commands: run and logs."""

from __future__ import annotations

import argparse


def add_run_parser(sub: argparse._SubParsersAction) -> None:
    # run
    run = sub.add_parser("run", help="Run the full pipeline (stages 1→4 + HTML)")
    run.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    run.add_argument(
        "--input",
        default="input.xlsx",
        help="Path to pipeline input workbook (default: input.xlsx)",
    )
    run.add_argument(
        "--export-dir",
        default="export",
        help="Export output directory (default: export)",
    )
    run.add_argument(
        "--resume-from",
        default="1",
        help="Resume from stage number or alias (1-4, config, scraping, planning, export)",
    )
    run.add_argument(
        "--log-level",
        default="info",
        choices=["info", "verbose"],
        help="Console/log verbosity hint (default: info)",
    )
    run.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force calendar cache refresh before Stage 2 when that stage runs",
    )
    run.add_argument(
        "--non-strict",
        action="store_true",
        help="Continue on blocked sources or warnings",
    )
    run.add_argument(
        "--allow-missing-sources",
        action="store_true",
        help="Treat blocked sources as an operator-approved skip and keep partial results",
    )
    run.add_argument(
        "--no-timestamped-export",
        dest="timestamped_export",
        action="store_false",
        help="Write exports flat into --export-dir instead of a timestamped subfolder",
    )
    run.set_defaults(timestamped_export=True)
    run.add_argument(
        "--iterations",
        type=int,
        default=1,
        metavar="N",
        help="Run Stage 3 planner N times with different random seeds and keep the best plan (default: 1)",
    )
    run.add_argument(
        "--mid-planning-critic-iterations",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Optionally run a Stage 3 checkpoint critic loop before Stage 4 export: "
            "inspect the plan, inject structured planner hints, and re-run Stage 3 up to N times "
            "(default: 0/off)"
        ),
    )
    # Headless / CI judge backend: set RVV_JUDGE_BACKEND=claude|openai|llm_bridge
    # plus the matching API key (ANTHROPIC_API_KEY / OPENAI_API_KEY) to enable
    # inter-stage LLM judgment when no harness session is present.
    # See docs/rvv-miniputt-pipeline.md §"Headless / CI usage" for details.
    run.add_argument(
        "--interactive",
        action="store_true",
        help="Run exactly one stage (starting at --resume-from), emit a DecisionContext for it "
        "as JSON, and exit — the canonical capability behind harness stage-by-stage checkpoint "
        "review (issue #260 Phase 5). See --decision-action to advance past a prior stage's "
        "checkpoint on the next invocation",
    )
    run.add_argument(
        "--new-full-run",
        dest="new_full_run",
        action="store_true",
        help="Explicitly start a new full pipeline run even though a reviewed, exported, "
        "unpromoted Stage 3/4 candidate exists. Without this, a plain run that would restart "
        "Stage 1 is refused and points to 'stage3 refine' instead of silently invalidating the "
        "reviewed candidate",
    )
    run.add_argument(
        "--decision-action",
        default=None,
        metavar="JSON",
        help="Inline JSON DecisionAction (e.g. '{\"action_id\": \"proceed\"}') deciding the "
        "outcome of the stage immediately before --resume-from. Validated against a freshly "
        "rebuilt DecisionContext for that stage before the run proceeds; only meaningful with "
        "--interactive and --resume-from > 1",
    )
    run.add_argument(
        "--decision-action-file",
        default=None,
        metavar="PATH",
        help="Path to a JSON file containing the DecisionAction, alternative to --decision-action",
    )


def add_logs_parser(sub: argparse._SubParsersAction) -> None:
    # logs
    logs = sub.add_parser("logs", help="Show structured pipeline run logs")
    logs.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    logs_sub = logs.add_subparsers(dest="logs_command")

    logs_list = logs_sub.add_parser("list", help="List recent pipeline runs")
    logs_list.add_argument("--count", type=int, default=10, help="How many recent runs to show (default: 10)")
    logs_list.add_argument("--work-dir", default=".pipeline", help=argparse.SUPPRESS)

    logs_show = logs_sub.add_parser("show", help="Show details for one run")
    logs_show.add_argument("run_id", nargs="?", default="latest", help="Run id, or 'latest' (default)")
    logs_show.add_argument("--work-dir", default=".pipeline", help=argparse.SUPPRESS)

    logs_stats = logs_sub.add_parser("stats", help="Show aggregate run statistics")
    logs_stats.add_argument("--work-dir", default=".pipeline", help=argparse.SUPPRESS)
