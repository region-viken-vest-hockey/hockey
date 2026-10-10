"""Calendar source health commands: status, sources and calendars."""

from __future__ import annotations

import argparse


def add_source_health_parsers(sub: argparse._SubParsersAction) -> None:
    # status
    status = sub.add_parser("status", help="Show checkpoint/log status for the pipeline work directory")
    status.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    status.add_argument(
        "--json",
        action="store_true",
        help="Print the AI-operator run manifest as JSON instead of the human-readable summary",
    )

    # sources
    sources = sub.add_parser("sources", help="Calendar source health commands")
    sources_sub = sources.add_subparsers(dest="sources_command")
    sources_status = sources_sub.add_parser(
        "status",
        help="Show per-source health: reachability, event counts, cache age, and suggested recovery actions",
    )
    sources_status.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    sources_status.add_argument(
        "--json",
        action="store_true",
        help="Print source health as a JSON array of capability results instead of a human-readable summary",
    )

    # calendars
    cal = sub.add_parser("calendars", help="Calendar viewer commands")
    cal.add_argument(
        "--refresh",
        action="store_true",
        help="Force full re-scrape: clear all caches, re-scrape, regenerate HTML",
    )
    cal.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
