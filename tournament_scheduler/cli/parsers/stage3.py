"""Interactive Stage 3 session commands."""

from __future__ import annotations

import argparse


def add_stage3_parsers(sub: argparse._SubParsersAction) -> None:
    stage3 = sub.add_parser(
        "stage3",
        help="Inspect the explicit interactive Stage 3 session/state machine",
    )
    stage3_sub = stage3.add_subparsers(dest="stage3_command", title="stage3 commands")
    stage3_session = stage3_sub.add_parser(
        "session",
        help="Show one compact view of the interactive Stage 3 session for the active run",
    )
    stage3_session.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    stage3_session.add_argument("--json", action="store_true", help="Print the session/status view as JSON")
    stage3_status = stage3_sub.add_parser(
        "status",
        help="Alias for 'stage3 session' (compact interactive Stage 3 status)",
    )
    stage3_status.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    stage3_status.add_argument("--json", action="store_true", help="Print the session/status view as JSON")
    stage3_refine = stage3_sub.add_parser(
        "refine",
        help=(
            "Refine a finalized unpromoted candidate without promotion, reset "
            "or a Stage 1/2 rerun"
        ),
    )
    stage3_refine.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    stage3_refine.add_argument("--input", default=None, help="Input workbook path (defaults to the run's)")
    stage3_refine.add_argument("--finding", default=None, help="Finding id to repair (omit to list findings)")
    stage3_refine.add_argument("--option-id", default=None, help="Verified option id to apply")
    stage3_refine.add_argument("--search", action="store_true", help="Allow the bounded search dimensions")
    stage3_refine.add_argument("--dry-run", action="store_true", help="Preview the verified delta without mutating")
    stage3_refine.add_argument("--no-export", action="store_true", help="Mutate the candidate without re-running Stage 4")
    stage3_refine.add_argument("--export-dir", default=None, help="Export root (defaults to the reviewed export's root)")
    stage3_refine.add_argument("--flat-export", action="store_true", help="Write the export flat instead of timestamped")
    stage3_refine.add_argument("--non-strict", action="store_true", help="Continue on non-fatal export errors")
    stage3_refine.add_argument("--rationale", default="", help="Concise operational rationale")
    stage3_refine.add_argument("--json", action="store_true", help="Print the result as JSON")
    stage3_converge = stage3_sub.add_parser(
        "converge",
        help=(
            "Autonomously refine a REVIEW_REQUIRED unpromoted candidate toward a "
            "bounded Pareto-stable frontier instead of escalating immediately"
        ),
    )
    stage3_converge.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    stage3_converge.add_argument("--input", default=None, help="Input workbook path (defaults to the run's)")
    stage3_converge.add_argument("--finding", default=None, help="Force the first epoch to this finding id")
    stage3_converge.add_argument(
        "--option-id",
        default=None,
        help="Prefer this verified option as the next exploration baseline when it is non-dominated",
    )
    stage3_converge.add_argument("--max-epochs", type=int, default=6, help="Bounded controller epoch budget (default: 6)")
    stage3_converge.add_argument(
        "--max-no-improvement",
        type=int,
        default=2,
        help="Consecutive epochs without a new non-dominated candidate before plateau (default: 2)",
    )
    stage3_converge.add_argument("--frontier-limit", type=int, default=6, help="Bounded frontier size (default: 6)")
    stage3_converge.add_argument("--no-search", action="store_true", help="Only use cheap repair options, not bounded search")
    stage3_converge.add_argument("--no-export", action="store_true", help="Mutate candidates without re-running Stage 4")
    stage3_converge.add_argument("--export-dir", default=None, help="Export root for the batch review exports (defaults to the reviewed export's root)")
    stage3_converge.add_argument("--flat-export", action="store_true", help="Write the batch export flat instead of timestamped")
    stage3_converge.add_argument("--non-strict", action="store_true", help="Continue on non-fatal export errors")
    stage3_converge.add_argument(
        "--review-candidate",
        default=None,
        help=(
            "Retained frontier candidate ref to adopt as the review handoff "
            "before the batch export (default: record the recommended candidate)"
        ),
    )
    stage3_converge.add_argument("--dry-run", action="store_true", help="Preview the next epoch without mutating")
    stage3_converge.add_argument(
        "--ignore-audit",
        action="store_true",
        help="Do not read the persisted semantic-audit verdict for this export",
    )
    stage3_converge.add_argument("--json", action="store_true", help="Print the convergence report as JSON")
    stage3_adopt = stage3_sub.add_parser(
        "adopt",
        help="Adopt one still-valid retained frontier candidate as the current candidate",
    )
    stage3_adopt.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    stage3_adopt.add_argument("--input", default=None, help="Input workbook path (defaults to the run's)")
    stage3_adopt.add_argument("--candidate-ref", required=True, help="Retained frontier/attempt candidate ref")
    stage3_adopt.add_argument("--no-export", action="store_true", help="Adopt without re-running Stage 4")
    stage3_adopt.add_argument("--export-dir", default=None, help="Export root (defaults to the reviewed export's root)")
    stage3_adopt.add_argument("--flat-export", action="store_true", help="Write the export flat instead of timestamped")
    stage3_adopt.add_argument("--non-strict", action="store_true", help="Continue on non-fatal export errors")
    stage3_adopt.add_argument("--rationale", default="", help="Concise operational rationale")
    stage3_adopt.add_argument("--json", action="store_true", help="Print the result as JSON")
