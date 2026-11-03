"""Club-wide booking acceptance commands (reviewed, token-bound)."""

from __future__ import annotations

import argparse


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    parser.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    parser.add_argument("--source-id", required=True, help="Active club booking source assertion id")
    parser.add_argument("--actor", default=None, help="Operator identity")
    parser.add_argument("--note", default="", help="Audited rationale")
    parser.add_argument("--reference", default="", help="Source reference, e.g. email id/date/sender")
    parser.add_argument(
        "--expected-revision",
        default=None,
        help="Fail closed unless the canonical-state revision matches",
    )
    parser.add_argument("--json", action="store_true", help="Print structured JSON")


def add_season_acceptance_parsers(season_sub: argparse._SubParsersAction) -> None:
    preview = season_sub.add_parser(
        "club-booking-acceptance-preview",
        help=(
            "Read-only preview of the per-tournament interpretations linked to one active club "
            "booking source, with conflicts and the reviewed token needed to accept them"
        ),
    )
    _common(preview)

    apply = season_sub.add_parser(
        "club-booking-acceptance-apply",
        help=(
            "Record acceptance of the exact previewed interpretations; requires the reviewed token "
            "and never rewrites assertions or booking evidence"
        ),
    )
    _common(apply)
    apply.add_argument(
        "--reviewed-token",
        default=None,
        help="Token printed by club-booking-acceptance-preview for the exact reviewed state",
    )
    apply.add_argument("--dry-run", action="store_true", help="Validate without writing canonical state")
