"""Plan critique, auto-adjust, verdict and candidate commands."""

from __future__ import annotations

import argparse


def add_critique_parsers(sub: argparse._SubParsersAction) -> None:
    # critic
    critic = sub.add_parser(
        "critic",
        help="Run the plan critic on an existing Stage 3 checkpoint and print issues",
    )
    critic.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )

    # auto-adjust
    auto_adjust = sub.add_parser(
        "auto-adjust",
        help=(
            "Automatically apply auto-fixable critic issues (arena-day collisions, "
            "hosting clumps) in a loop until resolved or max iterations reached"
        ),
    )
    auto_adjust.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    auto_adjust.add_argument(
        "--export-dir",
        default="export",
        help="Export output directory (default: export)",
    )
    auto_adjust.add_argument(
        "--max-iterations",
        type=int,
        default=3,
        help="Maximum number of adjustment iterations (default: 3)",
    )
    auto_adjust.add_argument(
        "--no-timestamped-export",
        dest="timestamped_export",
        action="store_false",
        help="Write exports flat into --export-dir instead of a timestamped subfolder",
    )
    auto_adjust.set_defaults(timestamped_export=True)

    # verdict
    verdict = sub.add_parser(
        "verdict",
        help="Read the Stage 3 checkpoint and print the tone (strong/mixed/rough) and key scores",
    )
    verdict.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )

    # candidates
    candidates = sub.add_parser(
        "candidates",
        help="Compare Stage 3 plan candidates (from --iterations > 1): reproducibility "
        "metadata, ranking, and the most consequential trade-offs vs. the runner-up",
    )
    candidates.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    candidates.add_argument(
        "--json",
        action="store_true",
        help="Print candidates as JSON instead of a human-readable comparison",
    )
