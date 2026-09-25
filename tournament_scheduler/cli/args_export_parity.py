"""Argument parser for the read-only ``export-parity`` command.

Kept in its own module so the large ``args.py`` only gains one import and one
call, mirroring ``args_audit.py``.
"""

from __future__ import annotations

import argparse


def add_export_parity_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "export-parity",
        help="Read-only XLSX/HTML artifact parity + canonical freshness check for one export",
    )
    parser.add_argument("--export-dir", required=True, help="Export directory holding season_plan.xlsx/.html")
    parser.add_argument("--basename", default="season_plan", help="Artifact base name (default: season_plan)")
    parser.add_argument("--season", default=None, help="Canonical season to compare freshness against")
    parser.add_argument(
        "--season-root",
        default=None,
        help="Canonical season root (default: inferred from the export manifest's season)",
    )
    parser.add_argument(
        "--expected-revision",
        default=None,
        help="Require the artifacts to embed this exact canonical revision",
    )
    parser.add_argument(
        "--allow-historical",
        action="store_true",
        help="Report pair parity only; do not require the current canonical revision",
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
        help="Persist export_parity.json next to the artifacts",
    )
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON")
