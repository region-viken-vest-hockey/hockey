"""Shared option groups for the RVV Miniputt parser families (transport only)."""

from __future__ import annotations

import argparse


MANUAL_PLACEMENT_OPT_IN_HELP = (
    "Explicitly allow this action to introduce a known manual/fixed-busy placement "
    "or unresolved placement work (provisional placement); not the default"
)
HOST_CONFIRMATION_OPT_IN_HELP = (
    "Explicitly allow a new host-confirmation dependency (movable_busy allocation) "
    "for this action; not the default"
)


def add_operational_opt_in_flags(parser: argparse.ArgumentParser) -> None:
    """Add the shared explicit opt-ins for provisional/manual canonical placements."""

    parser.add_argument(
        "--allow-manual-placement",
        action="store_true",
        help=MANUAL_PLACEMENT_OPT_IN_HELP,
    )
    parser.add_argument(
        "--allow-host-confirmation",
        action="store_true",
        help=HOST_CONFIRMATION_OPT_IN_HELP,
    )


def add_regression_acceptance_flags(parser: argparse.ArgumentParser) -> None:
    """Add the explicit operator acceptance of named team-schedule regressions."""

    parser.add_argument(
        "--accept-team-regression",
        dest="accept_team_regressions",
        action="append",
        default=None,
        metavar="TEAM=CODE",
        help=(
            "Explicitly accept one named material regression for one affected team, "
            "e.g. 'Sandefjord=more_gaps_under_7_days' (repeatable); only when the "
            "operator has accepted that exact trade-off; not the default"
        ),
    )
    parser.add_argument(
        "--accept-regression-reason",
        default=None,
        help="Mandatory operator reason recorded with any accepted team-schedule regression",
    )


def add_reviewed_consequence_flag(parser: argparse.ArgumentParser) -> None:
    """Add the reviewed-dry-run consequence acceptance for one exact plan."""

    parser.add_argument(
        "--accept-reviewed-consequences",
        default=None,
        metavar="TOKEN",
        help=(
            "Explicitly accept exactly the material consequences of the matching reviewed "
            "dry-run (its verdict.review.token), applying the same plan unchanged; requires "
            "--accept-regression-reason and refuses a stale/different candidate"
        ),
    )
