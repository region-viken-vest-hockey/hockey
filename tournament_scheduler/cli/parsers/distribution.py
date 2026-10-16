"""Argument parsers for the read-only ``version`` and ``capabilities`` commands.

Owned by this module so the top-level composer in ``cli/args.py`` only gains
one import and one call.
"""

from __future__ import annotations

import argparse


def add_distribution_parsers(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "version",
        help="Show the installed version and, with --manifest, its update status (read-only)",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        metavar="PATH",
        help="Local release manifest JSON to classify the installed version against (no network access)",
    )
    parser.add_argument("--json", action="store_true", help="Print the result as JSON")

    capabilities = sub.add_parser(
        "capabilities",
        help="Show application version, agent protocol range, canonical schema versions and commands (read-only)",
    )
    capabilities.add_argument("--json", action="store_true", help="Print the report as JSON on stdout")
