"""Argument parser for the task-oriented ``shell`` commands (operator shell, #621)."""

from __future__ import annotations

import argparse


def _add_common(parser: argparse.ArgumentParser, *, inherited: bool) -> None:
    # Subcommands must not overwrite values given before the subcommand name, so they use SUPPRESS.
    default = argparse.SUPPRESS if inherited else None
    parser.add_argument(
        "--repo",
        default=default,
        metavar="PATH",
        help="Local checkout of the season repository (default: the current directory)",
    )


def add_shell_parsers(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "shell",
        help="Operator shell: readiness view, gated repository refresh and publication handoff",
    )
    parser.add_argument("--json", action="store_true", help="Print the readiness view as JSON when no subcommand is given")
    _add_common(parser, inherited=False)
    shell_sub = parser.add_subparsers(dest="shell_command", metavar="{readiness,refresh,publish}")

    readiness = shell_sub.add_parser("readiness", help="Show version, repository, sign-in and Claude readiness (read-only)")
    _add_common(readiness, inherited=True)
    readiness.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Print the result as JSON")

    refresh = shell_sub.add_parser(
        "refresh",
        help="Preview, then apply, a fast-forward of local main to GitHub main",
    )
    _add_common(refresh, inherited=True)
    refresh.add_argument(
        "--confirm",
        default=None,
        metavar="HEAD",
        help="Apply the refresh. Pass the commit shown by the preview; without it only the preview runs",
    )

    shell_sub.add_parser("publish", help="Show how publication is handed off (never publishes from the shell)")
